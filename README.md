# youtube-insights — hermes plugin

> Aligned with the mentoring of **Dr. Allen Harper, AI Cyber Value Creator** — join the community at [AI Cyber Value Creators on Skool](https://www.skool.com/ai-cyber-value-creators).


YouTube competitive intelligence for [Hermes Agent](https://hermes-agent.nousresearch.com):
track competitor channels, pull transcripts, rank videos by **views-per-hour
(VPH)** with trend direction, and mine transcripts into a **deduplicated,
searchable insight knowledge base** — with a dashboard tab (Trends + Insights
pages), agent tools, slash commands, and a daily cron pipeline.

This is the hermes port of the intelligence core of a former proprietary
plugin, released under MIT. Content production (scripts, images, campaigns)
is intentionally out of scope — pair it with
[digital-marketing-pro](https://github.com/indranilbanerjee/digital-marketing-pro)
for marketing execution; its agents can consume this plugin's insights via
the `yt_search_insights` / `yt_trending` tools and the workspace files.

## Install

```bash
hermes plugins install harperaa/hermes-plugin-long-form --enable
```

You'll be prompted for `TRANSCRIPT_API_KEY`
([transcriptapi.com](https://transcriptapi.com)). Manage it later in the
dashboard under **Settings → Environment**.

## What you get

**Dashboard tab** (`Long Form`):
- **Research** (first tab) — the niche research & channel teardown engine
  (see below): followed-channel list at the top, niche setup, a tiered
  pipeline (free daily snapshots → depth-first crawl → precision enrichment
  → transcripts/comments → scoring → formats), an outlier register (D2) with
  demand map (D1), the format library (D3), the cross-niche **format gap
  report** (D4), per-channel teardowns (D5), and a credit ledger.
- **Trends** — sortable video table (thumbnail, title, channel, published,
  duration, views, VPH, trend sparkline), stat cards, tracked-channel
  manager, Refresh (fetch) and Analyze (queue insight extraction) buttons.
- **Insights** — full-text search (SQLite FTS5/BM25), category filter,
  most-sourced/most-recent sort, expandable cards with source videos,
  timestamps, and quotes; pagination; delete.

**Agent tools** (toolset `youtube_insights`):
`yt_add_channel`, `yt_remove_channel`, `yt_list_channels`,
`yt_fetch_videos`, `yt_trending`, `yt_trigger_analysis`,
`yt_add_insight`, `yt_search_insights`, `yt_lint_script`, `yt_research`
(actions: `status`, `snapshot`, `crawl`, `enrich`, `transcripts`,
`comments`, `score`, `packaging`, `formats`, `profile`, `report`,
`pipeline`, `doctor`, `gaps`).

**Slash commands:** `/yt` (summary), `/yt-analyze` (queue analysis).

**Skills** (load with `skill_view("youtube-insights:<name>")`):
`youtube-video-analyst`, `youtube-gap-finder`, `ideal-mechanics`,
`youtube-planner` (+ standalone dashboard scripts), `digest-url-liveness-gate`.

**Cron:** `hermes youtube-insights setup-cron --apply` installs the daily
03:00 intelligence refresh (fetch → trigger analysis → analyst skill →
insights), the 06:00/18:00 content pipeline, and the **free 03:30 research
snapshot** (`yt_research snapshot`) that builds the exact daily view history
the research engine's age-normalisation and inflection detection depend on.

## YouTube Research engine

Answers one question with evidence instead of intuition: *what should I make
next that has a real chance of working, and why did it work for the people
it already worked for?* Providers: [transcriptapi.com](https://transcriptapi.com)
(`TRANSCRIPT_API_KEY`, required) and [Apify](https://apify.com)
(`APIFY_API_TOKEN`, optional — exact metrics and comment bodies for the
shortlist only). No YouTube Data API dependency.

Design rules that are load-bearing in the code:

- **Baseline-relative, never absolute** — every video is scored against the
  trailing median of its own channel's previous 20 uploads in the same
  long/short bucket (`yti_rs_outliers.py`).
- **Negative evidence is mandatory** — formats are ranked by the Wilson 95%
  lower bound of their hit rate, never by hit count, and every format stores
  its misses (`yti_rs_formats.py`).
- **Signal decays** per niche (`signal_half_life_days`).
- **Own the history** — `GET /youtube/channel/latest` is free and exact, so
  the daily snapshot costs nothing (`yti_rs_snapshot.py`).
- **Cheap pass, then precision pass** — discovery on approximate data
  (`"3.4M views"`, `"2 years ago"`), exact numbers only for the shortlist.
- **Fetched data is not memory** — raw payloads are kept; every score is
  recomputable from raw rows.
- **Secrets never touch disk or logs** — header auth only, redacting log
  filter, `Secrets.__repr__` → `***`, quarantine instead of zero-defaults.

Pipeline (each step idempotent and resumable; run from the tab, the
`yt_research` tool, or the cron):

| Tier | Job | Provider | Cost |
|---|---|---|---|
| 0 | `snapshot` | TranscriptAPI RSS | free |
| 1 | `crawl` (depth-first, barren-branch pruning, credit cap, resume) | TranscriptAPI | ~1 credit / 20-100 rows |
| 2 | `enrich` | Apify | per result, shortlist only |
| 3 | `transcripts`, `comments` | TranscriptAPI / Apify | shortlist only |
| — | `score`, `packaging`, `formats`, `profile`, `report` | local | free |

Deliverables are written as markdown to `workspace/research/reports/<date>/`
(D1 demand map, D2 outlier register, D3 format library, D4 format gap
report, D5 channel teardowns) and are browsable on the Artifacts tab. The
research database is `research.db` next to `data.db`.

## How insights dedup works

New insights retrieve candidates via FTS5, merge automatically at Jaccard
word overlap ≥ 0.7 (the new source video is linked to the existing insight),
and for borderline matches optionally ask the host LLM (`ctx.llm`) — no
external embedding service required.

## Data layout

Everything lives in `~/.hermes/plugins-data/youtube-insights/`:

```
data.db                        # SQLite: videos, snapshots, insights (FTS5), queue
research.db                    # SQLite: research engine (channels, videos, snapshots,
                               #   transcripts, comments, scores, packaging, formats,
                               #   channel_profiles, crawl_*, api_usage, quarantine)
workspace/youtube/{date}/{channel}/{video}/
    transcript.json|txt        # full transcript with timestamps
    metadata/{ts}.json         # view-count snapshots (feeds VPH + sparklines)
    analysis.md                # analyst skill output
workspace/insights/{id}.md     # one markdown file per insight
workspace/research/reports/{date}/D1..D5-*.md   # research deliverables
```

## Development

```bash
git clone https://github.com/harperaa/hermes-plugin-long-form
ln -s "$PWD/youtube-insights" ~/.hermes/plugins/youtube-insights
hermes plugins enable youtube-insights
python -m pytest            # 160+ unit tests, no network needed
```

## License

MIT — see [LICENSE](LICENSE).
