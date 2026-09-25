import sys
from typing import Optional
from datetime import datetime, timezone

from src.clients.deepseek_client import DeepSeekClient, BudgetExceeded
from src.clients.scrappa_client import ScrappaClient
from src.clients.seranking_client import SERankingClient
from src.clients import supabase_client as db
from src.clients.usage import UsageTracker
from src.config import settings
from src.models.schemas import DemandMetrics, SerpSignal, Website
from src.pipeline.niche_discovery import discover_niches
from src.pipeline.seed_generation import generate_seeds
from src.pipeline.discovery import discover_keywords
from src.pipeline.normalize import normalize_keyword, dedup_key, exact_dedup
from src.pipeline.demand_validation import validate_demand
from src.pipeline.relevance_filter import filter_relevant
from src.pipeline.semantic_dedup import semantic_dedup
from src.pipeline.serp_validation import check_serp
from src.pipeline.seo_judge import judge_keywords_batch


def _log(website_id: int, keyword_id: Optional[int], stage: str, input_data: dict, output_data: dict) -> None:
    db.log_agent_call(
        {
            "website_id": website_id,
            "keyword_id": keyword_id,
            "stage": stage,
            "input": input_data,
            "output": output_data,
        }
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_effort(what: str, fn, *args, **kwargs) -> None:
    """Run a cleanup DB write without letting it mask the error being handled.

    The failure handlers below write to Supabase - but Supabase is often
    exactly what failed (a DNS blip once left a run stuck at 'running'
    forever, because finish_run() raised while handling the original error).
    """
    try:
        fn(*args, **kwargs)
    except Exception as cleanup_error:  # noqa: BLE001 - deliberately swallowed
        print(f"warning: {what} failed during cleanup: {cleanup_error}", file=sys.stderr)


def _ensure_niches(deepseek: DeepSeekClient, website: Website) -> None:
    """Called only when there's no active niche left to process - tops up the
    niche list, checking what already exists so it doesn't invent a reworded
    duplicate of a niche that's already there."""
    existing_names = db.get_active_niche_names(website.website_id)
    result = discover_niches(deepseek, website.category, existing_names)
    _log(
        website.website_id, None, "niche_discovery",
        {"category": website.category, "existing_niches": existing_names},
        result.model_dump(),
    )
    for name in result.new_niches:
        db.create_niche(website.website_id, name, source="seed")


def run_for_website(website: Website) -> dict:
    """Processes exactly ONE niche per call (whichever has waited longest in
    the rotation) - so a single run never burns the token budget across
    every niche at once, and every niche gets a turn over time. Judges
    every demand-validated candidate rather than stopping at the daily
    shortlist target (see the Stage 6/7 comment below) -
    MAX_KEYWORDS_PER_SITE_PER_DAY now only throttles how many shortlisted
    keywords the article pipeline PUBLISHES per day, not how many get
    discovered/judged here."""
    usage = UsageTracker()
    deepseek = DeepSeekClient(usage=usage)

    niche = db.get_next_niche(website.website_id)
    if niche is None:
        _ensure_niches(deepseek, website)
        niche = db.get_next_niche(website.website_id)
    if niche is None:
        return {"run_id": None, "niche": None, "candidates_found": 0, "shortlisted_count": 0}

    run = db.start_run(website.website_id, niche.niche_id)
    run_id = run["run_id"]
    candidates_found = 0
    shortlisted_count = 0
    judged_count = 0

    try:
        seranking = SERankingClient(usage=usage)
        scrappa = ScrappaClient(usage=usage)

        existing = db.get_existing_keywords(website.website_id, niche.niche_id)
        existing_normalized = {dedup_key(k) for k in existing}

        # Stage 1 - seed generation, scoped to this one niche
        seeds = generate_seeds(deepseek, website.category, niche.name)
        _log(
            website.website_id, None, "seed_generation",
            {"category": website.category, "niche": niche.name},
            seeds.model_dump(),
        )

        # Stage 2 - discovery expansion across three measured sources
        # (similar / questions / related - see discovery.py for the
        # per-source cost and hit-rate rationale). Source attribution is kept
        # per keyword so the dashboard and future tuning can see which source
        # actually produced the winners.
        raw_candidates: list[str] = []
        sources_by_keyword: dict[str, set[str]] = {}
        for seed in seeds.seed_keywords:
            discovered = discover_keywords(scrappa, seranking, seed)
            per_source: dict[str, int] = {}
            for c in discovered:
                raw_candidates.append(c.keyword)
                sources_by_keyword.setdefault(c.keyword, set()).update(c.source)
                for s in c.source:
                    per_source[s] = per_source.get(s, 0) + 1
            _log(
                website.website_id, None, "discovery",
                {"seed_keyword": seed},
                {"count": len(discovered), "by_source": per_source,
                 "sample": [c.keyword for c in discovered[:15]]},
            )

        # Stage 3 - exact dedup, then cap at SE Ranking's real batch limit
        survivors = exact_dedup(raw_candidates, existing_normalized)[:5000]
        candidates_found = len(survivors)
        _log(
            website.website_id, None, "exact_dedup",
            {"raw_candidate_count": len(raw_candidates), "existing_count": len(existing_normalized)},
            {"survivor_count": len(survivors), "dropped": len(raw_candidates) - len(survivors)},
        )

        # Stage 3's exact_dedup is word-order/stemmed matching only - every
        # survivor is "kept" directly here. The LLM semantic dedup pass runs
        # later, after demand validation (see semantic_dedup call below), so
        # it has real search volume to pick a representative from each group.
        kept: list[str] = survivors
        rows_by_keyword: dict[str, dict] = {}

        if survivors:
            stored_rows = db.upsert_keywords_bulk(
                [
                    {
                        "website_id": website.website_id,
                        "run_id": run_id,
                        "niche_id": niche.niche_id,
                        "keyword": candidate,
                        "normalized_keyword": normalize_keyword(candidate),
                        "source": sorted(sources_by_keyword.get(candidate, set())),
                        "status": "deduped",
                        "last_updated": _now(),
                    }
                    for candidate in survivors
                ]
            )
            rows_by_keyword = {row["keyword"]: row for row in stored_rows}

        if kept:
            # Stage 5 - demand validation. ONE batched call, up to SE Ranking's
            # 5000-keyword limit - this is now the only thing SE Ranking is
            # used for in this pipeline.
            demand_results = validate_demand(seranking, kept)
            demand_by_keyword = {d.keyword: d for d in demand_results}
            with_volume = [d for d in demand_results if d.search_volume]
            _log(
                website.website_id, None, "demand_validation",
                {"keyword_count": len(kept)},
                {
                    "total_returned": len(demand_results),
                    "with_real_volume": len(with_volume),
                    "top_by_volume": sorted(
                        (d.model_dump() for d in with_volume),
                        key=lambda d: d["search_volume"], reverse=True,
                    )[:30],
                },
            )

            has_data = [c for c in kept if demand_by_keyword.get(c) and demand_by_keyword[c].search_volume]

            # Relevance gate. Must run BEFORE ranking: sorting by difficulty
            # ascending actively surfaces off-topic keywords first (an
            # irrelevant query faces no competition here, so it looks "easy"),
            # which previously filled the entire judge budget with job-listing
            # and image-search noise.
            relevant = filter_relevant(deepseek, website.category, niche.name, has_data)
            _log(
                website.website_id, None, "relevance_filter",
                {"candidates_in": len(has_data)},
                {"kept": len(relevant), "dropped": len(has_data) - len(relevant),
                 "sample_dropped": [k for k in has_data if k not in set(relevant)][:20]},
            )

            # Semantic dedup. Exact_dedup (Stage 3) only catches word-order/
            # plural/gerund variants - it can't see that "solo travel tips",
            # "solo travel tips for beginners" and "solo travel tips for
            # introverts" would all produce the same article. This LLM pass
            # catches that: within each proposed group the highest-volume
            # keyword survives (real volume is known now, post demand
            # validation) and the rest are marked rejected, not deleted -
            # see semantic_dedup.py for why this is a separate mechanism from
            # normalize.py's deterministic dedup rather than a replacement.
            deduped, dup_pairs = semantic_dedup(
                deepseek, website.category, niche.name, relevant,
                {c: demand_by_keyword[c].search_volume or 0 for c in relevant},
            )
            if dup_pairs:
                db.upsert_keywords_bulk(
                    [
                        {
                            "website_id": website.website_id,
                            "niche_id": niche.niche_id,
                            "keyword": dup_kw,
                            "normalized_keyword": normalize_keyword(dup_kw),
                            "status": "rejected",
                            "judge_rationale": f"[auto] semantic duplicate of {kept_kw!r} (LLM dedup)",
                            "last_updated": _now(),
                        }
                        for dup_kw, kept_kw in dup_pairs
                    ]
                )
            _log(
                website.website_id, None, "semantic_dedup",
                {"candidates_in": len(relevant)},
                {"kept": len(deduped), "dropped": len(dup_pairs), "pairs": dup_pairs[:30]},
            )

            # Rank by real data before spending any SERP/LLM calls.
            #
            # Sorting difficulty-ascending (the previous approach) was actively
            # counterproductive: the lowest-difficulty keywords are typically
            # the lowest-demand and weakest-intent ones, so the judge spent its
            # budget on vol=10 noise while genuine candidates sat further down.
            # In run 16 the one approved keyword ranked #12 that way; under
            # volume-descending it ranks #5.
            #
            # Difficulty and volume are both applied as cutoffs instead of
            # sort keys - a low-authority site does not win difficulty>40
            # terms (every such candidate in run 16 was judged and rejected),
            # and a keyword below min_search_volume_to_judge doesn't justify
            # a dedicated article regardless of how winnable it looks
            # (measured 2026-09-25: 24 of 100 shortlisted keywords had
            # volume<=20 with no code-level floor to stop them).
            too_hard = [c for c in deduped
                        if (demand_by_keyword[c].difficulty or 100) > settings.max_difficulty_to_judge]
            too_low_volume = [c for c in deduped
                               if (demand_by_keyword[c].search_volume or 0) < settings.min_search_volume_to_judge]
            excluded = set(too_hard) | set(too_low_volume)
            winnable = [c for c in deduped if c not in excluded]
            ranked = sorted(winnable, key=lambda c: -(demand_by_keyword[c].search_volume or 0))
            candidates_to_judge = ranked[: settings.max_candidates_to_judge_per_run]
            _log(
                website.website_id, None, "candidate_ranking",
                {"relevant_count": len(deduped),
                 "difficulty_cutoff": settings.max_difficulty_to_judge,
                 "min_volume_cutoff": settings.min_search_volume_to_judge},
                {"winnable": len(winnable), "dropped_too_hard": len(too_hard),
                 "dropped_too_low_volume": len(too_low_volume),
                 "selected": candidates_to_judge[:60],
                 "cap": settings.max_candidates_to_judge_per_run},
            )

            # Stage 6 - SERP validation for EVERY demand-validated candidate,
            # no early stop. Every candidate reaching this point already
            # cost real discovery + demand-validation spend - measured
            # directly (2026-09-24): stopping once shortlisted_count hit the
            # daily target of 2 left the rest of a run's already-validated
            # candidates permanently stuck at status='deduped' with their
            # demand data discarded, un-revisitable (exact_dedup excludes
            # any keyword ever seen for this niche, any status) and
            # un-judgeable - real money spent for nothing. SERP checks are
            # on Scrappa now (~33x cheaper per check than SE Ranking's old
            # serp/classic task), which is what makes judging everyone
            # affordable.
            to_judge: list[tuple[str, DemandMetrics, SerpSignal]] = []
            keyword_id_by_kw: dict[str, int] = {}
            for candidate in candidates_to_judge:
                keyword_id = rows_by_keyword[candidate]["keyword_id"]
                keyword_id_by_kw[candidate] = keyword_id
                demand = demand_by_keyword[candidate]

                db.upsert_keyword(
                    {
                        "website_id": website.website_id,
                        "niche_id": niche.niche_id,
                        "keyword": candidate,
                        "normalized_keyword": normalize_keyword(candidate),
                        "search_volume": demand.search_volume,
                        "cpc": demand.cpc,
                        "competition": demand.competition,
                        "difficulty": demand.difficulty,
                        "intents": demand.intents,
                        "history_trend": demand.history_trend,
                        "status": "validated",
                        "last_updated": _now(),
                    }
                )

                try:
                    serp_signal = check_serp(scrappa, candidate)
                except BudgetExceeded:
                    raise
                except Exception as e:
                    _log(
                        website.website_id, keyword_id, "serp_validation",
                        {"candidate": candidate}, {"error": str(e)},
                    )
                    continue
                _log(
                    website.website_id, keyword_id, "serp_validation",
                    {"candidate": candidate},
                    serp_signal.model_dump(),
                )
                to_judge.append((candidate, demand, serp_signal))

            # Stage 7 - SEO Judge, batched (settings.judge_batch_size
            # keywords/call) to cut DeepSeek spend - the fixed system prompt
            # is paid for once per batch instead of once per keyword. See
            # seo_judge.judge_keywords_batch.
            for i in range(0, len(to_judge), settings.judge_batch_size):
                batch = to_judge[i:i + settings.judge_batch_size]
                try:
                    verdicts = judge_keywords_batch(deepseek, batch, niche.name)
                except BudgetExceeded:
                    raise  # real stop signal, not a per-batch hiccup
                except Exception as e:
                    # One bad LLM response for this batch shouldn't cost
                    # everything already validated this run - skip it and
                    # move to the next batch.
                    for candidate, demand, _serp in batch:
                        _log(
                            website.website_id, keyword_id_by_kw[candidate], "seo_judge",
                            {"candidate": candidate, "demand": demand.model_dump()},
                            {"error": str(e)},
                        )
                    continue

                for (candidate, demand, serp_signal), verdict in zip(batch, verdicts):
                    keyword_id = keyword_id_by_kw[candidate]
                    _log(
                        website.website_id, keyword_id, "seo_judge",
                        {
                            "candidate": candidate,
                            "demand": demand.model_dump(),
                            "serp": serp_signal.model_dump(),
                        },
                        verdict.model_dump(),
                    )

                    judged_count += 1
                    if verdict.approve:
                        shortlisted_count += 1

                    # Stage 8 - store final verdict. No longer capped at the
                    # daily target - every real approval gets shortlisted;
                    # the daily-2 number only governs how many the article
                    # pipeline PUBLISHES per day (websites.articles_per_day),
                    # a separate downstream throttle.
                    final_status = "shortlisted" if verdict.approve else "judged"
                    db.upsert_keyword(
                        {
                            "website_id": website.website_id,
                            "niche_id": niche.niche_id,
                            "keyword": candidate,
                            "normalized_keyword": normalize_keyword(candidate),
                            "status": final_status,
                            "judge_score": verdict.score,
                            "judge_rationale": verdict.rationale,
                            "intent_cluster": verdict.intent_cluster,
                            "last_updated": _now(),
                        }
                    )
                    _log(
                        website.website_id, keyword_id, "store",
                        {"candidate": candidate},
                        {"final_status": final_status},
                    )

        # Retire the niche after its FIRST zero-approval run. This was two
        # consecutive runs (yield genuinely varies - this pipeline has seen
        # 11 approvals and 1 approval from the same niche), but now that
        # every demand-validated candidate gets judged instead of stopping
        # early (see Stage 6/7 above), a zero-approval run means the niche
        # was judged exhaustively, not just unlucky on a small sample -
        # explicitly requested 2026-09-24, accepted alongside
        # niche_discovery.py's ability to generate fresh niches to replace
        # retired ones.
        exhausted = shortlisted_count == 0
        db.mark_niche_processed(niche.niche_id, exhausted=exhausted)

    except BudgetExceeded as e:
        _best_effort("record_api_usage", db.record_api_usage, usage.as_rows(run_id, website.website_id))
        _best_effort("mark_niche_processed", db.mark_niche_processed, niche.niche_id, exhausted=False)
        _best_effort(
            "finish_run", db.finish_run,
            run_id, "success", candidates_found, shortlisted_count,
            error_message=f"Stopped early (guardrail): {e}",
        )
        return {"run_id": run_id, "niche": niche.name, "candidates_found": candidates_found, "shortlisted_count": shortlisted_count}
    except Exception as e:
        _best_effort("record_api_usage", db.record_api_usage, usage.as_rows(run_id, website.website_id))
        _best_effort(
            "finish_run", db.finish_run,
            run_id, "failed", candidates_found, shortlisted_count, error_message=str(e),
        )
        raise

    db.record_api_usage(usage.as_rows(run_id, website.website_id))
    db.finish_run(
        run_id, "success", candidates_found, shortlisted_count,
        error_message=(
            f"Niche retired: 0 approvals from {judged_count} judged "
            f"(out of {candidates_found} candidates found)"
            if exhausted else None
        ),
    )
    return {
        "run_id": run_id, "niche": niche.name,
        "candidates_found": candidates_found, "judged": judged_count,
        "shortlisted_count": shortlisted_count, "niche_retired": exhausted,
        "usage": usage.summary(),
    }


def run_all_active_websites() -> list[dict]:
    """One niche per website per invocation. A failure on one website is logged
    to its run row and skipped rather than aborting the remaining websites -
    otherwise a single bad API response would starve every other site that day."""
    results = []
    for website in db.get_active_websites():
        try:
            results.append(run_for_website(website))
        except Exception as e:
            results.append({"website": website.name, "error": str(e)})
    return results
