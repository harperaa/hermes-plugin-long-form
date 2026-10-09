---
name: youtube-gap-finder
description: Competitive gap analysis for YouTube content. Supports three modes — single-source video (find gaps vs one reference video), workspace sweep (find gaps across top-performing tracked videos), and topic/URLs (ad-hoc). Produces concept files with net new information gain.
version: 3.2.0
tags:
  - youtube
  - content-strategy
  - competitive-analysis
  - gap-analysis
  - concept-generation
---

# YouTube Gap Finder

Find what everyone else is missing — then build video concepts around it.

## When to Use This Skill

- User says "find gaps", "what's missing", "differentiators", "unique angle"
- User wants video concepts that won't repeat what's already out there
- User asks for "net new information gain"
- User runs `/youtube-gap-finder` with no arguments (default workspace-sweep mode)
- A kanban task from the Trends page ✨ button names a **specific source video** (single-source mode)

## Modes

This skill has four modes. **Pick one based on the caller's inputs and do not mix them.**

### Mode A — Single-Source Video (NEW, use when a specific video is named)

Use this mode when the invocation provides a single source video — typically a kanban task created by the YouTube Trends page ✨ Generate button. Signals that you are in this mode:

- The task brief or user message names one specific video (title + videoId + URL)
- The task brief provides an explicit transcript path, video workspace directory, and output directory
- The goal is "similar but unique" content relative to that one source, not a sweep

In this mode:

- You are producing content that is **intentionally similar in topic/angle** to the named source video, but with **net new information gain** (not a rehash).
- Do NOT call the `yt_trending` tool for a ranking sweep. Do NOT scan the recommended/ folder for prior runs. Do NOT look at the last completed routine's outputs — you are not picking up where a routine left off.
- Output is **3 concept files in ONE topic folder**, not 9 across 3 topics. Use the output directory the caller provides verbatim (typically `youtube/{today}/recommended/{source-title-slug}/`). Do not invent alternate paths.

### Mode B — Workspace Sweep (default when invoked with no arguments)

The legacy default: analyze the top-performing tracked videos across the workspace, find gaps across them, and produce 3 topic folders × 3 formats = 9 concept files. Use when:

- `/youtube-gap-finder` is run with no arguments
- The goal is to discover new directions across the whole tracked set, not respond to one video

### Mode B, research-steered (when the Research tab is populated)

Before Phase 1 in Mode B, call `yt_research` with action `brief`. If it returns `ready:false`,
run Mode B exactly as below. If `ready:true`, the brief outranks the VPH ranking as the source
of topics:

- Topics come from `focus_quadrant` (recent videos far above their own channel's normal — demand
  that supply has not caught up with; prefer momentum `up` and the highest `time_adjusted`) and
  from `gaps` / `near_gaps` (formats proven in neighbouring niches with zero or few uses in the
  target). At most one of the 3 topics may come from the VPH sweep or the insight base alone.
- Working titles use one of the brief's `formats` (its `skeleton`), and the concept cites that
  format's `hits`/`uses` and `wilson_lb`.
- Every concept file based on a focus-quadrant video opens, directly under its title, with a
  **Based on outlier** block (source title, channel, url, multiple, views, age_days, momentum,
  format) copied from the brief, then states what it takes from that video and what it does
  differently for the ICP. A concept based on a gap format opens with a **Based on gap** block
  (label, proven_in, hits/uses, wilson_lb, example titles). The content-creator skill carries the
  same blocks into the script outlines.
- Phases 1.5 to 3 still run: the insight base and the ICP supply the voice, the supporting
  evidence and the "what is not being said" angle; they do not override a topic the brief
  points at.

### Mode C — Topic + URLs (ad-hoc)

User provides a topic and/or specific video URLs not yet in the workspace. Transcripts must come through the plugin's own pipeline: track the channel (`yt_add_channel`) and run `yt_fetch_videos` to pull its recent videos + transcripts, then run the same Phase 2-5 workflow. If a URL's video is outside the fetch lookback window or its channel shouldn't be tracked, say so and ask the user how to proceed — there is no ad-hoc transcript fetcher; do not invent one.

### Mode D — Topic-Only, Insights-Grounded (on-demand Generate button)

Use this mode when the caller provides a **topic** (plus optional context/guidance) and NO source video or URLs — typically a kanban task created by the Artifacts page Generate button. Signals: the task brief names a topic and an explicit output directory, and says to ground in the insight knowledge base.

In this mode:

- **Sources are the insight knowledge base plus tracked analyses.** Call `yt_search_insights` with the topic and its adjacent phrasings (3-5 queries) to pull everything the tracked winners have said about it. Where an insight's source video has an `analysis.md` on disk, read that video's **Video Summary + Top 20 Insights** for depth. Do NOT run a `yt_trending` ranking sweep and do NOT fetch new videos.
- **The caller's context/guidance is steering, not decoration** — it says who the video is for, what angle matters, or what to avoid. Honor it in every concept.
- Run Phase 1.5 → 2 → 3 → 4 as normal, scoped to the topic: the gaps you exploit are what the tracked coverage of THIS topic leaves unsaid, unexplained, or unconnected. If the insight search comes back thin (fewer than ~5 relevant insights), say so in the concepts' evidence sections and lean on synthesis + the company context rather than inventing sources.
- Output is **3 concept files in ONE topic folder** (same shape as Mode A: `concepts.md`, `concepts-hot-take.md`, `concepts-contrarian.md`). Use the output directory the caller provides verbatim. Do not invent alternate paths.

---

## Workflow

### Phase 1: Gather Sources

**Mode A (single source):**

1. **Read the source video's `analysis.md`** from the video workspace directory provided by the caller. The task's Step 0 covers this: if `analysis.md` is missing, run the `long-form:youtube-video-analyst` skill yourself against the video's transcript first (it is attached to the task for exactly this case). If no transcript exists on disk either, block the task (`kanban_block` with kind `needs_input`) explaining the transcript is missing — do NOT substitute a different video.
2. Read ONLY the **Video Summary** and **Top 20 Insights** sections (≈ first 80 lines). Skip the viral-mechanics sections.
3. Optionally (for corroborating context only): call the `yt_trending` tool (top 10 by VPH) and skim the **Video Summaries** of any videos in the same theme cluster as the source. Use them to verify that a candidate "gap" is truly a gap vs. already covered elsewhere. Do NOT treat these corroborating videos as co-equal sources — the source of record is the one named video.
4. **Report what you found** to the user, e.g.:
   ```
   Source: "This Tool Made My Coding Agent Powerful" (Brian Casel) — analysis.md loaded (82 lines).
   Corroborating context: 4 tracked videos in "coding tools" cluster.
   ```

**Mode B (workspace sweep):**

1. **Get the top 10 VPH videos** by calling the `yt_trending` tool. Each
   entry includes the video's workspace directory (under the reported
   `workspaceRoot`); its `analysis.md` lives there.
2. **For each video**, read ONLY the **Video Summary** and **Top 20 Insights** sections from its `analysis.md` file (≈ first 80 lines). Do NOT read the viral mechanics sections (Section 1-11). These are too large and not needed for gap analysis.
3. **Skip videos without `analysis.md`** — they don't have enough processed data for gap analysis.
4. **Report what you found** to the user:
   ```
   Found 7/10 videos with analysis:
   1. [3,551 VPH] Codex 5.3 vs Opus 4.6 (Nate B Jones) ✓
   2. [3,243 VPH] Multi-Agent Team with OpenClaw (Brian Casel) ✓
   ...
   Skipped 3 (no analysis.md)
   ```

**Mode C (topic + URLs):** get transcripts via the plugin pipeline (`yt_add_channel` + `yt_fetch_videos` — see the Mode C description above), then produce stub analysis (Video Summary + Top 20 Insights) for each video. Proceed as in Mode B.

**Mode D (topic-only):** no video gathering — the sources ARE the insight knowledge base. Run 3-5 `yt_search_insights` queries around the topic (the topic itself, synonyms, adjacent problems), then read the `analysis.md` Video Summary + Top 20 Insights of the videos those insights cite where present on disk. Report what you found (insight count, source spread) and proceed to Phase 1.5.

### Phase 1.5: Query the Insight Knowledge Base

Before mapping individual video insights, query the aggregated insight database to understand the landscape:

1. **Call the `yt_search_insights` tool** with broad queries for each major theme cluster you see in the video titles (e.g. "AI agents", "coding tools", "content creation", "automation"):
   - Note which topics have HIGH source counts (5+ sources = saturated, everyone covered it)
   - Note which topics have LOW source counts (1-2 sources = underserved, potential gap)
   - Note which categories have the most/fewest insights

2. **Identify insight density patterns:**
   - **Saturated**: 10+ insights on the same theme → skip unless you have a genuinely contrarian angle
   - **Emerging**: 3-5 insights with recent timestamps → trending, good for hot takes
   - **Underserved**: 1-2 insights → depth gap, good for original content
   - **Missing**: search returns nothing → insight gap, highest value

3. **Record the landscape summary** — you'll use this in Phase 3 to validate whether a "gap" is truly a gap or just something the top 10 videos missed but others covered.

### Phase 2: Map the Insight Landscape (working context — do not save to file)

For each video, extract from its **Video Summary + Top 20 Insights** only:

- List every distinct insight, claim, data point, entity, tactic, and story
- Tag each insight by theme cluster (e.g., "agent architecture", "job market", "cost optimization")
- Flag insights that could combine with insights from OTHER videos to produce a synthesized idea neither video delivered alone

### Phase 3: Find the Gaps (working context — do not save to file)

Cross-reference the video analysis insights (Phase 2) with the knowledge base landscape (Phase 1.5). For each potential gap, call `yt_search_insights` to verify it's truly a gap — not just missing from the top 10 videos but already covered in the broader database.

Compare all insight inventories across videos and identify:

#### 1. Insight Gaps (what nobody said)
- Themes, claims, or angles that ZERO videos covered
- Questions a viewer would still have after watching all of them
- Adjacent topics that connect but nobody bridged

#### 2. Synthesis Gaps (insights that combine but nobody connected)
- Insight A from Video 1 + Insight B from Video 3 = new idea C that neither delivered
- Cross-video patterns that are visible in aggregate but invisible in isolation
- **This is the highest-value gap type** — it produces genuinely novel content from existing material

#### 3. Depth Gaps (what everyone mentioned but nobody explained)
- Points name-dropped but not unpacked
- Claims made without evidence or examples
- Steps glossed over in "how-to" content

#### 4. Perspective Gaps (what viewpoint is missing)
- Is everyone giving the same type of advice?
- Is there a contrarian take with merit?
- Is there a practitioner's view vs. everyone being commentators?
- Is there an audience segment being ignored?

#### 5. Freshness Gaps (what's outdated or evolving)
- Are videos citing old data or deprecated tools?
- Is there a breaking development nobody has covered yet?

#### 6. Storytelling Gaps (what experience is missing)
- Are all videos lecture-style with no real stories?
- Could a personal experience, case study, or live demo differentiate?

### Phase 4: Build Video Concepts

**Mode A (single source):** produce **1 topic** derived from the named source video — a similar-but-unique angle — in **3 format variants**. Total: 3 concept files in the caller-provided output directory.

**Mode D (topic-only):** produce **1 topic** — the caller's topic, angled by the caller's context/guidance and the gaps found in the insight base — in **3 format variants**. Total: 3 concept files in the caller-provided output directory.

**Mode B / C:** produce **3 topics**, each exploiting a different cluster of gaps, × **3 format variants** per topic. Total: 9 concept files.

Each concept exploits a different cluster of gaps to produce a video with genuine net new information gain — content that passes YouTube's Gist Filter because it delivers insights no existing video contains.

For each concept, generate **3 separate concept files** — 3 different formats that approach the same topic from different angles:

1. **`concepts.md`** — The standard deep-dive format (as described below). Balanced, thorough, synthesized from multiple sources. This is the primary video script.

2. **`concepts-hot-take.md`** — A hot take format. Same topic, but:
   - Lead with the most provocative or surprising insight
   - Shorter (8-10 min target vs 12-16 min)
   - Strong opinion, bold predictions, name names
   - "Here's what nobody is telling you about X" energy
   - Pick the single most contrarian data point and build the video around it
   - Skip nuance in favor of impact — the goal is to spark debate

3. **`concepts-contrarian.md`** — A contrarian view format. Same topic, but:
   - Deliberately argue AGAINST the consensus position across all source videos
   - "Everyone says X, but here's why they're wrong" structure
   - Find the strongest evidence that contradicts the mainstream narrative
   - Steel-man the contrarian position with real data, not just hot air
   - Address and dismantle the strongest arguments for the consensus view
   - End with a reframe that synthesizes both sides into a higher-order insight

All 3 formats use the same concept structure below, but with different thesis angles, insight selection, and tone.

#### Naming topics (all modes)

Name each topic, its working title, and its folder slug after **the viewer's problem or the outcome they want**, in plain words they would search for — "Stop paying for vague AI briefs", not "Vague Brief Tax OS (VBTOS)".

- **Do not brand topics as a coined system.** No "OS", "Operating System", "System", "Framework", "Stack", "Protocol", or "Method" label by default, and no invented acronyms. A folder slug must not end in `-os`.
- **Earlier `-os` folders are legacy naming, not a convention.** Do not continue the pattern because prior runs used it. When you survey earlier topics to avoid repeats, refer to them by subject ("the brief-quality topic from Oct 4"), and set a new topic apart by what the viewer learns — never as a sibling of a named OS ("≠ dod-router-os").
- **Name artifacts by what they are** — "a one-page brief checklist", not a branded card with an acronym.
- **A coined name is the exception.** Use one only when the video truly teaches a single reusable multi-step system; at most one topic per run, and even then the folder slug describes the problem, not the brand.

Why: when every recommendation is "X OS", a week of videos reads as one formula, the titles stop matching what viewers search for, and the naming itself becomes the sameness the Gist Filter punishes.

For each concept file, use the following structure:

**Part 1: Video Summary (~1000 words)**

Write the summary of the NEW video that would be created. This should read like the "Video Summary" section of an analysis.md file — a flowing narrative describing:
- The central thesis and why it matters
- The key arguments and evidence
- The progression of ideas
- What the viewer learns that they cannot learn from any existing video
- How insights from multiple source videos are synthesized into something new

**Part 2: Key Insights (20+ numbered insights)**

Write at least 20 insights for this new video, in the exact same format as the source analysis.md insight format:

```
1. **Bold claim statement.** Supporting explanation with specific data points, entity names, and evidence. Connect to the broader thesis.

2. **Another bold claim.** Explanation with specifics...
```

Each insight should be one of:
- A **synthesis insight** — combining insights from 2+ source videos into a new idea neither delivered
- A **depth insight** — unpacking something source videos mentioned but never explained
- A **original insight** — filling a gap no source video touched at all
- A **contrarian insight** — challenging a consensus claim across source videos with evidence

Tag each insight with its source type in parentheses at the end: (synthesis: V1+V3), (depth: V5), (original), (contrarian: V2).

Every concept file needs at least 20 insights — this is the minimum bar for a concept worth producing. If you're running low on output capacity, prioritize completing 20 insights per file over adding extra analysis files. The 20-insight threshold matters because the downstream `youtube-content-creator` skill maps insights to beats; fewer than 20 doesn't give enough material for a full video script.

No formatting instructions, no hook templates, no beat maps, no viral mechanics. Just the concept summary and insights. The `youtube-content-creator` skill handles formatting and production.

### Phase 5: Save Output

Save concept files only. Do not save gap analysis reports, phase summaries, or intermediate work as separate files. The phases 1-3 analysis is working context that you report to the user conversationally, not files to save.

**Mode A (single source) — exactly 3 files in 1 folder:**

Use the output directory the caller provided (do not invent one). Typically:

```
youtube/{today}/recommended/{source-title-slug}/
  concepts.md               # Standard deep-dive
  concepts-hot-take.md      # Hot take format
  concepts-contrarian.md    # Contrarian view format
```

The `{source-title-slug}` comes from the source video's title — the caller will usually supply the exact path. If a folder already exists at that path from a prior run, overwrite the 3 concept files in place; do NOT create a sibling folder with a suffix and do NOT skip because "content already exists." Single-source runs are idempotent and authoritative for their source video.

**Mode D (topic-only) — exactly 3 files in 1 folder:** same shape as Mode A, in the caller-provided output directory (typically `youtube/{today}/recommended/{topic-slug}/`). Same overwrite-in-place rule.

**Mode B / C — exactly 9 files in 3 folders:**

```
youtube/{today}/recommended/
  {title-slug-1}/
    concepts.md               # Standard deep-dive
    concepts-hot-take.md       # Hot take format
    concepts-contrarian.md     # Contrarian view format
  {title-slug-2}/
    concepts.md
    concepts-hot-take.md
    concepts-contrarian.md
  {title-slug-3}/
    concepts.md
    concepts-hot-take.md
    concepts-contrarian.md
```

**Title slugs**: lowercase, hyphens for spaces, strip non-alphanumeric except hyphens, max 80 chars. Derived from the proposed video title (Mode B/C) or the source video title (Mode A). The proposed title follows the naming rule in Phase 4 — a plain problem/outcome title, so the slug never ends in `-os` or carries an invented acronym.

The `youtube-content-creator` skill will then produce a `script-outline.md` for each concept file.

## Algorithm Context

Why gap-finding matters:

- **Gist Filter** — YouTube's pre-ranking filter rejects videos too similar to existing content. Gaps = information gain = passing the filter.
- **Semantic IDs** — The algorithm maps spoken words to knowledge graph nodes. Concepts should use proper entity names, not slang.
- **GARM Brand Safety** — Original, human-produced content with clear expertise is favored over AI-generated slop.

## Design Principles

- **Insights are everything** — the concept exists to deliver insights no other video delivers. Every insight should be concrete, specific, and defensible.
- **Synthesis is king** — the highest-value concepts weave together insights from multiple source videos into ideas that are invisible in isolation but obvious in aggregate.
- **Specificity over abstraction** — use entity names, numbers, company names, study citations. Vague insights have zero information gain.
- **Respect the mode** — Mode A produces 1 topic × 3 formats; Mode B/C produces 3 topics × 3 formats. Never mix.
- **Similar-but-unique in Mode A** — the concept is intentionally adjacent to the source video's topic. The differentiation is the gaps/synthesis/contrarian angle, not the topic area.
- **Do not meander** — if invoked via a task brief that names a specific video and output directory, go straight to that directory and produce the 3 concept files. Do not hunt for prior outputs, do not reconcile with other routines, do not defer work because "a refresh is in progress."

## Integration

| Skill | How it connects |
|-------|----------------|
| `yt_add_channel` / `yt_fetch_videos` | Transcript fetching for Mode C (track the channel, then fetch) |
| `youtube-video-analyst` | Source of analysis.md files used in default mode |
| `youtube-planner` | Source of workspace video data and VPH rankings |
| `youtube-content-creator` | Takes concepts.md and produces full video scripts using ideal-mechanics.md |
