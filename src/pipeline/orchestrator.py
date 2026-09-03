import sys
from typing import Optional
from datetime import datetime, timezone

from src.clients.deepseek_client import DeepSeekClient, BudgetExceeded
from src.clients.scrappa_client import ScrappaClient
from src.clients.seranking_client import SERankingClient
from src.clients import supabase_client as db
from src.clients.usage import UsageTracker
from src.config import settings
from src.models.schemas import Website
from src.pipeline.niche_discovery import discover_niches
from src.pipeline.seed_generation import generate_seeds
from src.pipeline.discovery import discover_keywords
from src.pipeline.normalize import normalize_keyword, exact_dedup
from src.pipeline.demand_validation import validate_demand
from src.pipeline.relevance_filter import filter_relevant
from src.pipeline.serp_validation import check_serp
from src.pipeline.seo_judge import judge_keyword


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
    the rotation), and stops once the per-run keyword target is hit or the
    niche's candidates run out - so a single run never burns the token budget
    across every niche at once, and every niche gets a turn over time."""
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
    target = settings.max_keywords_per_site_per_day

    try:
        scrappa = ScrappaClient(usage=usage)
        seranking = SERankingClient(usage=usage)

        existing = db.get_existing_keywords(website.website_id, niche.niche_id)
        existing_normalized = {normalize_keyword(k) for k in existing}

        # Stage 1 - seed generation, scoped to this one niche
        seeds = generate_seeds(deepseek, website.category, niche.name)
        _log(
            website.website_id, None, "seed_generation",
            {"category": website.category, "niche": niche.name},
            seeds.model_dump(),
        )

        # Stage 2 - discovery expansion across three measured sources
        # (autocomplete / questions / related - see discovery.py for the
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

        # Dedup is exact-match only (Stage 3, above) - no LLM semantic pass.
        # Every survivor is "kept" directly; the DB's own
        # unique(website_id, normalized_keyword) constraint is the only other
        # backstop, enforced automatically by upsert_keyword below.
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

            # Rank by real data before spending any SERP/LLM calls.
            #
            # Sorting difficulty-ascending (the previous approach) was actively
            # counterproductive: the lowest-difficulty keywords are typically
            # the lowest-demand and weakest-intent ones, so the judge spent its
            # budget on vol=10 noise while genuine candidates sat further down.
            # In run 16 the one approved keyword ranked #12 that way; under
            # volume-descending it ranks #5.
            #
            # Difficulty is applied as a cutoff instead of a sort key - a
            # low-authority site does not win difficulty>40 terms, and every
            # such candidate in run 16 was judged and rejected.
            too_hard = [c for c in relevant
                        if (demand_by_keyword[c].difficulty or 100) > settings.max_difficulty_to_judge]
            winnable = [c for c in relevant if c not in set(too_hard)]
            ranked = sorted(winnable, key=lambda c: -(demand_by_keyword[c].search_volume or 0))
            candidates_to_judge = ranked[: settings.max_candidates_to_judge_per_run]
            _log(
                website.website_id, None, "candidate_ranking",
                {"relevant_count": len(relevant),
                 "difficulty_cutoff": settings.max_difficulty_to_judge},
                {"winnable": len(winnable), "dropped_too_hard": len(too_hard),
                 "selected": candidates_to_judge[:60],
                 "cap": settings.max_candidates_to_judge_per_run},
            )

            # Stage 6 - SERP validation + Stage 7 - SEO Judge
            for candidate in candidates_to_judge:
                if shortlisted_count >= target:
                    break  # hit this run's keyword target - stop, don't burn the rest of the budget

                keyword_id = rows_by_keyword[candidate]["keyword_id"]
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

                try:
                    verdict = judge_keyword(deepseek, candidate, demand, serp_signal, niche.name)
                except BudgetExceeded:
                    raise  # real stop signal, not a per-candidate hiccup
                except Exception as e:
                    # One bad LLM/API response for one candidate shouldn't cost
                    # everything already validated this run - skip it and move on.
                    _log(
                        website.website_id, keyword_id, "seo_judge",
                        {"candidate": candidate, "demand": demand.model_dump()},
                        {"error": str(e)},
                    )
                    continue

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

                # Stage 8 - store final verdict
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

        # Retire the niche after TWO consecutive runs that produced no
        # publishable keyword. One zero-approval run isn't enough evidence -
        # yield varies a lot between runs on the same niche (this pipeline has
        # seen 11 approvals and 1 approval from the same niche), so a single
        # thin run could otherwise kill a viable niche permanently. Only prior
        # 'success' runs count as evidence; crashed or budget-truncated runs
        # are ignored by last_successful_run_for_niche.
        previous = db.last_successful_run_for_niche(niche.niche_id, run_id)
        exhausted = (
            shortlisted_count == 0
            and previous is not None
            and previous["shortlisted_count"] == 0
        )
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
            f"Niche retired: 2 consecutive runs with 0 approvals "
            f"(this run: {candidates_found} candidates, {judged_count} judged)"
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
