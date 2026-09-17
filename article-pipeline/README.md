# Article Intelligence — article pipeline

Takes one keyword, produces one written article, and logs every intermediate
step. Originally a standalone project (`article-intelligence/`); now lives as
`article-pipeline/` inside the `seo funnel` repo, since it shares that
project's Supabase database (`website_id`/`keyword_id`, the
`published_articles` table, the automation pause switches) and API accounts
(DeepSeek, Scrappa, SE Ranking) directly rather than duplicating them.

Beyond writing the article, a run can now also (when `--website-id` is
given and `SUPABASE_URL`/`SUPABASE_KEY` are set - see `config.py`):
- **Interlink** to already-published articles on the same WordPress site
  (`pipeline/interlinking.py`).
- **Publish** the article to that site via the WordPress REST API,
  including a downloaded-and-resized (not hotlinked) 1200x600 featured
  image with a custom filename/alt text, and SEO meta title/description
  written to whichever plugin that site uses - Yoast, RankMath, or neither
  (`clients/wordpress_client.py`).
- **Backlink** older, already-live articles on the same site back to the
  new one (`pipeline/backlinking.py`) - the only step that edits content
  that's already public, since there is no draft/review stage anywhere in
  this pipeline; everything publishes live by default (`WP_PUBLISH_STATUS`).
- Get picked up automatically by `run_scheduler.py`, a daily cron
  (`.github/workflows/article_pipeline.yml`) that tops each website up to
  its own `articles_per_day` quota from its shortlisted keywords - gated by
  two independent pause switches (global `system_config.article_automation_enabled`
  and per-site `websites.article_automation_enabled`), separate from
  keyword shortlisting's own pause switches so pausing one pipeline never
  pauses the other.

Run without `--website-id` and this all still works exactly as it always
did - no database, no WordPress, just the local `output/<slug>.md`/`.html`
files. See `../supabase/migration_wordpress_publishing.sql` for the schema
this depends on, and `../dashboard/app/settings/page.tsx` for where a
website's WordPress credentials, SEO plugin, and daily quota get configured.

## The loop this project is for

1. Run the pipeline on a keyword.
2. Open `output/<slug>.html` in a browser, select all, copy, and paste the
   **rendered** page into SE Ranking's Content Editor — not the raw `.md` file.
   Pasting raw markdown text leaves every `##` as a literal character instead of
   a real heading; pasting the rendered HTML carries real `<h1>`-`<h3>` tags.
3. Read the Content Score against the brief SE Ranking generates for that
   keyword.
4. Look at the per-step logs to see which stage produced weak input, tune that
   stage's prompt, and rerun.
5. Repeat until articles score consistently well, *then* move to hosting,
   Postgres-backed keyword selection, locking, and LangGraph — the full
   architecture this project is a precursor to.

## Setup

```bash
cd article-pipeline
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

No `.env` needed here for the shared keys (`DEEPSEEK_API_KEY`, `SCRAPPA_API_KEY`,
`SERANKING_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`) - `config.py`'s
`load_dotenv()` walks up to `../.env` automatically. Only add a local `.env`
here for pipeline-only settings (`PEXELS_API_KEY`, `WP_PUBLISH_STATUS`, etc.
- see `.env.example`) if you want to override the defaults.

## Running

```bash
# Local only - no Supabase, no WordPress, no interlinking:
./.venv/bin/python run.py --keyword "best travel journal app"

# Against a real seo-funnel website (publishes to its WordPress, interlinks
# to/from its other published articles):
./.venv/bin/python run.py --keyword "best travel journal app" --website-id 1 --keyword-id 123

# Daily automation - tops every article-enabled website up to its quota:
./.venv/bin/python run_scheduler.py
```

Each run costs real API credits — DeepSeek for ~20-25 LLM calls (research/QA
plus one generation per outline section, plus a handful of condense calls for
any section that overshoots its target), one Scrappa search, one SE Ranking
batch validation. Reddit search (step 5) is free but best-effort — see below.
Nothing is spent without an explicit run.

Tuning the condense loop itself without spending a full run:

```bash
./.venv/bin/python tools/tune_condense.py logs/<run_id>
```

Re-runs only `length_control.py` against drafts already on disk (~$0.02,
DeepSeek only). Read the note at the end of
[Length: what was tried and what actually worked](#length-what-was-tried-and-what-actually-worked)
before using this on old logs — it tests the safety net in isolation, not the
generation-side fix that actually matters most.

## What a run produces

```
logs/<run_id>/
  00_serp_research.json
  01_competitor_scraping.json       (extracted text + headings per competitor, or a scrape failure)
  html/comp_01.html, comp_02.html…  (the raw HTML actually fetched, one file per successful scrape)
  02_competitor_research.json
  03_content_gap_analysis.json
  04_reddit_research.json           (raw threads found, or empty if blocked/rate-limited this run)
  05_community_research.json
  06_article_strategy.json
  07_keyword_expansion.json
  08_keyword_validation.json
  09_section_mapping.json
  10_write_section__<heading>.md    (one per outline section — the first IS the article's opening)
  NN_article_full_draft.md
  NN_seo_qa.json
  _run_summary.json                 (timing, call counts, word count, scrape/Reddit hit rate)

output/<keyword-slug>.md            (raw article - for inspection, not for pasting)
output/<keyword-slug>.html          (open in browser, copy the rendered page, paste into the scorer)
```

Every step's *raw* output is on disk — the point is to inspect exactly what a
prompt produced, not a summary of it. A weak article traces back to one numbered
file, and that file's producing prompt lives in `pipeline/<same_stage>.py`.

## Pipeline stages

| # | Stage | Module | What it does |
|---|---|---|---|
| 1 | SERP research | `pipeline/serp_research.py` | Live top organic results, People Also Ask, related searches (Scrappa) |
| 2 | Competitor page scraping | `pipeline/competitor_scraping.py`, `clients/page_scraper.py` | Fetches each competitor URL directly; extracts main text + H1-H3 headings |
| 3 | Competitor research | `pipeline/competitor_research.py` | Infers content type and angle per competitor from full content (or snippet if scraping failed) |
| 4 | Content gap analysis | `pipeline/content_gap_analysis.py` | What's underserved; one differentiation angle |
| 5 | Reddit research | `pipeline/reddit_research.py`, `clients/reddit_client.py` | Real, currently-active discussion threads for the keyword — genuine activity, not a search engine's guess |
| 6 | Community/trend research | `pipeline/community_research.py` | Synthesizes Reddit + PAA + related searches into themes, real questions, and trending angles for the title |
| 7 | Article strategy | `pipeline/article_strategy.py` | Section-by-section outline; a catchy, keyword-exact title; a focused word-count budget |
| 8 | Keyword expansion | `pipeline/keyword_expansion.py` | Up to 50 candidate long-tail keywords the outline could also target |
| 9 | Keyword validation | `pipeline/keyword_validation.py` | SE Ranking demand check; only real-volume candidates survive |
| 10 | Section/keyword mapping | `pipeline/section_mapping.py` | Assigns each validated keyword to the section it fits |
| 11 | Article writing | `pipeline/article_writer.py`, `pipeline/length_control.py` | One generation per outline section from a compact digest (not full prior prose), each measured and condensed to its target after generation |
| 12 | SEO/content QA | `pipeline/seo_qa.py` | Checks section coverage, unanswered questions, keyword usage — a sanity check, not the real score |

Stage 12 is deliberately not the final judge. The real score comes from SE
Ranking's Content Editor in the browser — an external, brief-driven target
(word count, paragraph count, heading count against competitors) that's harder
to game than an LLM grading its own homework.

## What changed after the first scored article (32/100)

The first real run scored low for reasons traceable to specific, fixed causes:

- **0/32 headings despite 30 real headings in the source** → the article was
  pasted as raw markdown text, which renders `##` as a literal character, not
  a heading. Fixed by generating a real `.html` file (step 11 → orchestrator);
  paste the *rendered* page, not the `.md` file.
- **10,885 words against a ~4,948-word brief, later 7,365, later 7,147** →
  three rounds of measurement before this actually converged; see
  [Length: what was tried and what actually worked](#length-what-was-tried-and-what-actually-worked)
  below. Landed at **+14.7% over the real SE Ranking brief**, down from +91%.
- **Primary keyword missing from the H1 and first 100 words** → the outline
  prompt now requires the exact keyword phrase in the title as a hard
  requirement, and the first section's prompt requires it verbatim in the
  first 100 words.
- **Terms like "Gossamer Gear", "LighterPack", "TOAKS Titanium" nowhere in the
  article** → traced to REI and a Reddit thread failing to scrape as
  competitors (step 2) - exactly the sources carrying that vocabulary. Not
  fully solved (scraping REI/Reddit remains best-effort), but Reddit research
  (step 5) now pulls this kind of brand/product signal independently, feeding
  both the content and the title.
- **Meta title/description generated but never surfaced** → now embedded in
  the `.html` output's `<title>` and `<meta name="description">` tags, and in
  `_run_summary.json`.

## Length: what was tried and what actually worked

Four attempts, in order, each measured against the same keyword
("backpacking gear list ultralight") before moving to the next:

1. **Prompt instruction: "match or exceed the target without padding."**
   Result: **+91%** over the outline's own budget (10,885 words vs a 5,700-word
   budget). A separate `write_intro` pass also duplicated the outline's own
   first section - two openings' worth of content. Removed; the first outline
   section is now the opening.
2. **Prompt instruction: "stay within ~10%" + a `max_tokens` cap at 1.7x
   target.** The cap **truncated sections mid-sentence** - a worse failure
   than overshoot. Raising the cap to 2.3x fixed truncation but the model
   drifted to **+73%** over target anyway (7,365 words vs a 4,250-word
   budget). Prompt-only word targets do not work; the model cannot count its
   own output.
3. **Root-caused the +73%, found three real mechanisms, not one:**
   - `generate_uncapped_if_truncated` **discards a truncated draft and
     regenerates from scratch at double the ceiling** - it doesn't continue
     the draft. Measured: with the 2.0x cap it fired on exactly 6 of 11
     sections, and those were the 6 worst overshoots (+75% to +171%); the 5
     sections that stayed under the cap ran only +16% to +45%. The cap was
     rewarding overshoot, not disciplining it.
   - Every outline section shipped with `subheadings: []`; the writer
     invented its own. Sections with 0 invented subheadings ran +16-27%;
     every section with 3+ ran +45-171%. Each invented subsection cost
     ~150-190 words independent of the section's target.
   - The prompt passed the full growing `article_so_far` as context, and it
     acted as a **format precedent that escalated with every section** -
     markdown tables appeared only after one section introduced the first
     one, and every later section inherited that shape.
4. **The actual fix, `pipeline/length_control.py`:** code counts words
   exactly (stripping markdown syntax first - a naive `.split()` on raw
   markdown overcounts by treating `##`, `**`, and table pipes as words) and
   hands the model an arithmetic delta to cut, in staged rounds capped at 30%
   per round with a retention floor that discards any round that drops
   proper nouns / prices / weights below 85% of the original (the signal
   that the model regenerated instead of edited). `article_strategy.py` now
   populates real subheadings, capped at one per 250 words of a section's
   target. The writer's prior-context is now a compact digest - headings,
   what each section covers, already-used terms - not full prose, which
   removed the format-escalation mechanism entirely.

   **Isolated condense-loop tuning against old, already-bloated drafts
   plateaued around +54-56% and was actively misleading** - it was testing
   the safety net against worst-case input the fixed generation path
   shouldn't produce anymore. The real test is the full pipeline: raw drafts
   dropped from +73% to **+20%** before any condensing (the digest +
   subheading fix is the actual causal fix), and condensing brought the
   final article to **+14.7% over the real SE Ranking brief**, using only 4
   condense calls, zero truncation, zero destructive rounds.

If you're tuning this further: **test against a fresh full pipeline run, not
against old logs.** An isolated harness against stale drafts measures the
wrong thing.

## Reddit research — real, but genuinely unstable

`clients/reddit_client.py` hits Reddit's public, unauthenticated search
endpoint (`old.reddit.com/search.json`) — no API key, no login. Confirmed
directly while building this: `www.reddit.com` 403s unconditionally regardless
of headers; `old.reddit.com` worked once, then redirected to a login wall
(`/login/?reason=lor2`) on the very next call seconds later. This is Reddit
throttling unauthenticated access, not a bug in this client - every failure
mode (bad status, a redirect, a malformed body) degrades to an empty thread
list rather than crashing the run, and step 6 works fine on PAA/related
searches alone when that happens.

**If Reddit signal matters more than "best-effort, sometimes nothing"**, the
real fix is registering a free Reddit API app (reddit.com/prefs/apps) and
using OAuth - much more stable rate limits than the public JSON endpoint, but
it's a new credential this project doesn't have yet and would need setting up
separately.

## Known v1 limitations

- **Competitor page fetching is best-effort.** Scrappa has no full-page-content
  endpoint, so `clients/page_scraper.py` fetches each competitor URL directly.
  The raw HTML is saved to `logs/<run_id>/html/` (one file per successful
  fetch) so exactly what was fetched can be inspected or reprocessed without
  hitting the network again; main text (via `trafilatura`) and H1-H3 headings
  (via `BeautifulSoup`) are extracted from it for the research step. Many
  pages block non-browser requests, render via JS, or sit behind a paywall —
  those fall back to the SERP title/snippet rather than failing the whole
  research phase. `_run_summary.json` reports the scrape success rate for
  each run; if it's consistently low, competitor research is quietly running
  on thinner input than intended.
- **Reddit research is best-effort for the same reason** — see above.
- **No cost guardrails yet.** Unlike the funnel project, this has no credit
  preflight and no budget cap. Fine for hand-run local experimentation; would
  need both before this runs unattended.
- **The writer's word-count targets are the outline's own estimate**, not
  SE Ranking's actual brief requirement — those only become known once you
  paste into the Content Editor, which happens after generation. A future
  iteration could fetch SE Ranking's content-editor targets *before* writing
  and feed them into the outline step directly.
- **A `stalled` or `no_reduction` section ships knowingly over its target.**
  `length_control.py` never expands and never guts real content to force a
  number - if a section is too dense to cut further without dropping a
  never-remove item (a price, a product name), it ships as-is. This is a
  deliberate tradeoff (protecting substance over hitting an exact count), not
  a bug; `_run_summary.json`'s `sections_stalled` count is what to watch.
- **Product/brand vocabulary competitors use (e.g. "TOAKS Titanium", "merino
  wool") is scraped but not yet reaching the article.** Measured directly:
  those terms appear dozens of times in scraped competitor HTML and zero
  times in the generated article - lost because `competitor_research.py`
  abstracts pages into `content_type`/`angle` rather than carrying concrete
  nouns forward. Diagnosed, not yet fixed.

## Repository layout

```
config.py           Settings from .env
models.py            Every stage's Pydantic schema
clients/             API wrappers: DeepSeek, Scrappa, SE Ranking, page_scraper, reddit_client
pipeline/            One module per stage, independently promptable
pipeline/length_control.py   Word counting, digest-building, and the condense loop - not a "stage", used by article_writer.py
tools/tune_condense.py       Re-runs length_control.py against drafts already on disk, no full-pipeline spend
orchestrator.py      Wires the stages together, handles per-step logging, HTML export
run.py               CLI entrypoint
logs/                Per-run step-by-step output (gitignored)
output/               Final articles - .md and .html (gitignored)
```
