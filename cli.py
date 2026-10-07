"""`hermes youtube-insights ...` CLI subcommands."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

CRON_NAME = "youtube-intelligence-refresh"
CRON_SCHEDULE = "0 3 * * *"
# Prior default prompt — kept VERBATIM so the dashboard's prompt migrator can
# recognize an unmodified job and upgrade it; a mentee-customized prompt never
# matches and is never touched.
CRON_PROMPT_V1 = (
    "Run the YouTube intelligence refresh. Step 1: call the yt_fetch_videos "
    "tool to pull the latest videos and transcripts for all tracked channels. "
    "Step 2: call yt_trigger_analysis (default limit and ordering). Each "
    "queued analysis is opened as its own kanban task titled 'Analyze: "
    "<video>' which the gateway dispatcher works in a separate session — do "
    "NOT analyze any video inline in this session and do NOT load the "
    "analyst skill here. Do NOT raise the analysis limit to catch up a "
    "backlog — the cap is deliberate pacing; the long tail defers to the "
    "next daily window. Step 3: finish with a one-paragraph summary of "
    "videos fetched, Shorts skipped, and the analysis tasks queued (include "
    "each kanbanTaskId from the yt_trigger_analysis result)."
)

CRON_PROMPT = (
    "Run the YouTube intelligence refresh. Step 1: call the yt_fetch_videos "
    "tool to pull the latest videos and transcripts for all tracked channels. "
    "Step 2: call yt_trigger_analysis (default limit and ordering). Each "
    "queued analysis is opened as its own kanban task titled 'Analyze: "
    "<video>' which the gateway dispatcher works in a separate session — do "
    "NOT analyze any video inline in this session and do NOT load the "
    "analyst skill here. Do NOT raise the analysis limit to catch up a "
    "backlog — the cap is deliberate pacing; the long tail defers to the "
    "next daily window. Step 3: regenerate the viral-mechanics playbook at "
    "youtube/ideal-mechanics.md under the workspace directory reported by "
    "yt_trending (workspaceRoot): take the CURRENT top 5 videos by VPH from "
    "yt_trending, read each one's analysis.md (its viral-mechanics sections "
    "and Top 20 Insights), and consolidate them into one playbook titled "
    "'Ideal Viral Mechanics — Consolidated from Top 5 VPH Videos'. Structure "
    "it exactly as: a 'Sources (ranked by VPH)' list, then sections 1. Hook "
    "Architecture, 2. Structural Blueprint, 3. Retention Mechanics, "
    "4. Emotional Engineering, 5. Storytelling Elements, 6. Linguistic "
    "Patterns, 7. Algorithm Signals, 8. CTA Architecture, 9. Viral "
    "Coefficient, 10. Reusable Templates, 11. Implementation Playbook, and a "
    "closing 'Quick Reference: The Meta-Pattern'. Cite the source video "
    "(V1–V5 with its VPH) for every example. If fewer than 3 of the top "
    "videos have an analysis.md yet, skip this step and note why. The "
    "youtube-content-creator skill reads this exact path — do not rename it. "
    "Step 4: finish with a one-paragraph summary of videos fetched, Shorts "
    "skipped, the analysis tasks queued (include each kanbanTaskId from the "
    "yt_trigger_analysis result), and whether ideal-mechanics.md was "
    "regenerated."
)

# Third routine: the free Tier-0 research snapshot (spec P4/§3.3). Exact daily
# view trajectories for every followed/tracked channel; costs nothing.
SNAPSHOT_NAME = "youtube-research-snapshot"
SNAPSHOT_SCHEDULE = "30 3 * * *"
SNAPSHOT_PROMPT = (
    "Run the free daily YouTube research snapshot: call the yt_research tool "
    "with action 'snapshot' exactly once. It records exact view counts for the "
    "latest uploads of every followed and tracked channel into the research "
    "history (no credits are spent). Do not run any other yt_research action. "
    "Finish with one line: channels snapshotted, videos, new videos, errors."
)

# Fourth routine: the pulse. Exact views for recent high-demand videos every
# six hours, so the Supply / Demand view carries a trajectory per outlier and
# can say whether its momentum is building or fading. Free (1 Data API unit
# per 50 videos, or TranscriptAPI's free channel/latest).
PULSE_NAME = "youtube-research-pulse"
PULSE_SCHEDULE = "15 */6 * * *"
PULSE_PROMPT = (
    "Run the YouTube research pulse: call the yt_research tool with action "
    "'pulse' exactly once. It re-reads exact view counts for recent videos that "
    "are running above their channel's normal and rescores them (no credits are "
    "spent). Do not run any other yt_research action. Finish with one line: "
    "videos watched, updated, rising, falling."
)

# Second routine, ported from paperclip's "YouTube Content Pipeline"
# (0 6,18 * * *). Scope-reduced: gap analysis + concepts + scripts for
# human review. Only the graphics stage moved out (runs post-approval).
PIPELINE_NAME = "youtube-content-pipeline"
PIPELINE_SCHEDULE = "0 6,18 * * *"
# Prior default prompt — kept VERBATIM for the dashboard's prompt migrator
# (same contract as CRON_PROMPT_V1: only an unmodified job is upgraded).
PIPELINE_PROMPT_V1 = (
    "Content pipeline: turn YouTube competitive intelligence into concepts "
    "and record-ready scripts for human review. Prerequisite: the youtube-"
    "intelligence-refresh job should have run so transcripts and insights "
    "exist. "
    "Step 1 — Research: call the yt_search_insights tool with several broad "
    "queries (hooks, structure, topics, audience, offers) to map the insight "
    "landscape; note saturated vs underserved topics. Also call yt_trending "
    "to get the current top videos by VPH (its workspaceRoot field is the "
    "workspace directory all outputs go under). "
    "Step 2 — Gap analysis: load the youtube-insights:youtube-gap-finder "
    "skill and run it against the top 10 VPH videos and the insight map. "
    "Produce about 3 content concepts with net-new information gain. Save "
    "each concept as youtube/{today}/recommended/{topic-slug}/concept.md "
    "inside the workspace. "
    "Step 3 — Scripts: for EACH concept, load the youtube-insights:"
    "youtube-content-creator skill and produce the script outlines (all "
    "formats it prescribes, exact spoken lines, a Visual field per beat) "
    "under youtube/{today}/recommended/{topic-slug}/scripts/. Consult the "
    "youtube-insights:image-style-guide skill for visual direction but do "
    "NOT generate images — the graphics stage (generate-image skill) runs "
    "only after a human approves the scripts. "
    "Step 4 — Review handoff: write a summary of all concepts AND scripts "
    "(title, angle, evidence, formats produced, file paths) to "
    "youtube/{today}/recommended/SUMMARY.md and finish by printing that "
    "summary for human review."
)

PIPELINE_PROMPT_V2 = (
    "Content pipeline: turn YouTube competitive intelligence into concepts "
    "and record-ready scripts for human review. Prerequisite: the youtube-"
    "intelligence-refresh job should have run so transcripts and insights "
    "exist. "
    "Step 1 — Research: call the yt_search_insights tool with several broad "
    "queries (hooks, structure, topics, audience, offers) to map the insight "
    "landscape; note saturated vs underserved topics. Also call yt_trending "
    "to get the current top videos by VPH (its workspaceRoot field is the "
    "workspace directory all outputs go under). "
    "Step 2 — Gap analysis: load the youtube-insights:youtube-gap-finder "
    "skill and run it in Mode B (workspace sweep) against the top 10 VPH "
    "videos and the insight map. Follow the skill's Mode B output contract "
    "exactly: 3 topics with net-new information gain, each saved as THREE "
    "concept files — concepts.md, concepts-hot-take.md, and "
    "concepts-contrarian.md — in youtube/{today}/recommended/{topic-slug}/ "
    "inside the workspace (9 concept files total). "
    "Step 3 — Scripts: for EACH concept file, load the youtube-insights:"
    "youtube-content-creator skill and produce its script outline (exact "
    "spoken lines, a Visual field per beat) saved in the SAME folder as the "
    "concept file: script-outline.md, script-outline-hot-take.md, and "
    "script-outline-contrarian.md per topic (9 script files total). Consult "
    "the youtube-insights:image-style-guide skill for visual direction but "
    "do NOT generate images — the graphics stage (generate-image skill) "
    "runs only after a human approves the scripts. "
    "Step 4 — Review handoff: write a summary of all concepts AND scripts "
    "(title, angle, evidence, formats produced, file paths) to "
    "youtube/{today}/recommended/SUMMARY.md and finish by printing that "
    "summary for human review."
)

PIPELINE_PROMPT_V3 = (
    "Content pipeline: turn YouTube competitive intelligence into concepts "
    "and record-ready scripts for human review. Prerequisite: the youtube-"
    "intelligence-refresh job should have run so transcripts and insights "
    "exist. "
    "Step 0 — Company context FIRST: read "
    "$HERMES_HOME/plugins-data/ai-cyber-value-creator/company-context.md "
    "(default HERMES_HOME is ~/.hermes; in the container it is /opt/data). "
    "If it exists, extract WHO the ideal customer (ICP) is, the problems "
    "they are wrestling with right now, and the offer/positioning — every "
    "concept and script below must be written FOR that ICP: their "
    "vocabulary, their stakes, the specific problems the context lists, "
    "and how to unlock them. If the file is missing, note that in the "
    "summary and proceed on the insight landscape alone. "
    "Step 1 — Research: call the yt_search_insights tool with several "
    "queries shaped by the ICP's problems (plus hooks, structure, topics, "
    "audience, offers) to map the insight landscape; note saturated vs "
    "underserved topics FOR THIS ICP. Also call yt_trending to get the "
    "current top videos by VPH (its workspaceRoot field is the workspace "
    "directory all outputs go under). "
    "Step 2 — Gap analysis: load the youtube-insights:youtube-gap-finder "
    "skill and run it in Mode B (workspace sweep) against the top 10 VPH "
    "videos and the insight map. Pick the 3 topics where the leading "
    "videos LEAVE THE ICP'S REAL QUESTIONS UNANSWERED — what is not being "
    "said that the ICP needs — so each concept breaks through with a "
    "unique point of view and voice rather than echoing the winners. "
    "Follow the skill's Mode B output contract exactly: 3 topics with "
    "net-new information gain, each saved as THREE concept files — "
    "concepts.md, concepts-hot-take.md, and concepts-contrarian.md — in "
    "youtube/{today}/recommended/{topic-slug}/ inside the workspace (9 "
    "concept files total). ALL THREE ANGLES per topic are ICP-specific: "
    "the straight take teaches the ICP's problem directly; the hot take "
    "confronts what the ICP believes or fears about it; the contrarian "
    "take argues against the prevailing advice the leading videos give "
    "the ICP — each names the audience and speaks to their listed "
    "problems, never generic commentary. "
    "Step 3 — Scripts: for EACH concept file, load the youtube-insights:"
    "youtube-content-creator skill and produce its script outline (exact "
    "spoken lines, a Visual field per beat) saved in the SAME folder as "
    "the concept file: script-outline.md, script-outline-hot-take.md, and "
    "script-outline-contrarian.md per topic (9 script files total). "
    "Spoken lines address the ICP directly in the company context's "
    "positioning and voice. Consult the youtube-insights:image-style-"
    "guide skill for visual direction but do NOT generate images — the "
    "graphics stage (generate-image skill) runs only after a human "
    "approves the scripts. "
    "Step 4 — Review handoff: write a summary of all concepts AND scripts "
    "(title, angle, the ICP problem each unlocks, evidence, formats "
    "produced, file paths) to youtube/{today}/recommended/SUMMARY.md and "
    "finish by printing that summary for human review."
)

PIPELINE_PROMPT_V4 = (
    "Content pipeline: turn YouTube competitive intelligence into concepts "
    "and record-ready scripts for human review. Prerequisite: the youtube-"
    "intelligence-refresh job should have run so transcripts and insights "
    "exist. "
    "Step 0 — Company context FIRST: read "
    "$HERMES_HOME/plugins-data/ai-cyber-value-creator/company-context.md "
    "(default HERMES_HOME is ~/.hermes; in the container it is /opt/data). "
    "If it exists, extract WHO the ideal customer (ICP) is, the problems "
    "they are wrestling with right now, and the offer/positioning — every "
    "concept and script below must be written FOR that ICP: their "
    "vocabulary, their stakes, the specific problems the context lists, "
    "and how to unlock them. If the file is missing, note that in the "
    "summary and proceed on the insight landscape alone. "
    "Step 1 — Research: call the yt_search_insights tool with several "
    "queries shaped by the ICP's problems (plus hooks, structure, topics, "
    "audience, offers) to map the insight landscape; note saturated vs "
    "underserved topics FOR THIS ICP. Also call yt_trending to get the "
    "current top videos by VPH (its workspaceRoot field is the workspace "
    "directory all outputs go under). "
    "Step 2 — Gap analysis: load the youtube-insights:youtube-gap-finder "
    "skill and run it in Mode B (workspace sweep) against the top 10 VPH "
    "videos and the insight map. Pick the 3 topics where the leading "
    "videos LEAVE THE ICP'S REAL QUESTIONS UNANSWERED — what is not being "
    "said that the ICP needs — so each concept breaks through with a "
    "unique point of view and voice rather than echoing the winners. "
    "Follow the skill's Mode B output contract exactly: 3 topics with "
    "net-new information gain, each saved as THREE concept files — "
    "concepts.md, concepts-hot-take.md, and concepts-contrarian.md — in "
    "youtube/{today}/recommended/{topic-slug}/ inside the workspace (9 "
    "concept files total). ALL THREE ANGLES per topic are ICP-specific: "
    "the straight take teaches the ICP's problem directly; the hot take "
    "confronts what the ICP believes or fears about it; the contrarian "
    "take argues against the prevailing advice the leading videos give "
    "the ICP — each names the audience and speaks to their listed "
    "problems, never generic commentary. "
    "Step 3 — Scripts: for EACH concept file, load the youtube-insights:"
    "youtube-content-creator skill and produce its script outline (exact "
    "spoken lines, a Visual field per beat) saved in the SAME folder as "
    "the concept file: script-outline.md, script-outline-hot-take.md, and "
    "script-outline-contrarian.md per topic (9 script files total). "
    "Spoken lines address the ICP directly in the company context's "
    "positioning and voice. Consult the youtube-insights:image-style-"
    "guide skill for visual direction but do NOT generate images — the "
    "graphics stage (generate-image skill) runs only after a human "
    "approves the scripts. "
    "Step 3.5 — Format gate (yt_lint_script, NOT optional): every spoken "
    "bullet must be a full conversational sentence or two (15-35 "
    "words) that CONTINUES the previous bullet's thought — read the "
    "beat aloud; if it sounds like a list of headlines, rewrite it. "
    "Each beat's word count must match its timestamps (~150 wpm) and "
    "every HOOK INTO NEXT is a complete spoken sentence, never a "
    "title fragment. Then PROVE it: run the yt_lint_script tool on "
    "EVERY script-outline file (all 9) and fix every finding, "
    "re-running until each reports ok:true. No script is done while "
    "the linter has findings. "
    "Step 4 — Review handoff: write a summary of all concepts AND scripts "
    "(title, angle, the ICP problem each unlocks, evidence, formats "
    "produced, file paths) to youtube/{today}/recommended/SUMMARY.md and "
    "finish by printing that summary for human review."
)

PIPELINE_PROMPT_V5 = PIPELINE_PROMPT_V4 + (
    " VOICE: before writing any spoken line, follow the "
    "youtube-content-creator skill's operator-voice check "
    "(voice-profile.md + SOUL.md + memories/USER.md when present) — every "
    "script must sound like the operator, never like competitor "
    "transcripts or the insight base."
)

# The pipeline had drifted into branding EVERY topic as a coined "X OS"
# with an acronym (1 of 13 topics on 2026-08-02, 6 of 6 daily from 09-23):
# each run read the earlier folders to avoid repeats and framed the new
# topic as a sibling of those "named OSes". Nothing asked for it.
PIPELINE_PROMPT = PIPELINE_PROMPT_V5 + (
    " NAMING: name every topic, working title and folder slug after the "
    "viewer's problem or the outcome they want, in plain words they would "
    "search for. Do NOT brand topics as a coined system: no 'OS', "
    "'Operating System', 'System', 'Framework', 'Stack', 'Protocol' or "
    "'Method' label by default, no invented acronyms, and no folder slug "
    "ending in -os. Earlier recommended folders ending in -os are legacy "
    "naming — do not continue the pattern. When you survey earlier topics "
    "to avoid repeats, refer to them by subject, and set a new topic apart "
    "by what the viewer learns, never as a sibling of a named OS. A coined "
    "name is allowed only when the video truly teaches one reusable "
    "multi-step system — at most one topic per run, and even then the "
    "folder slug describes the problem."
)


def _hermes_argv() -> list[str]:
    exe = shutil.which("hermes")
    if exe:
        return [exe]
    return [sys.executable, "-m", "hermes_cli.main"]


def setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="yti_cmd")
    p_cron = sub.add_parser("setup-cron",
                            help="Install the scheduled jobs: intelligence refresh "
                                 "(daily 03:00), content pipeline (06:00/18:00) and "
                                 "the free research snapshot (03:30)")
    p_cron.add_argument("--apply", action="store_true",
                        help="Create the job now (default: print the command)")
    p_cron.add_argument("--schedule", default=CRON_SCHEDULE,
                        help=f"Cron schedule (default: '{CRON_SCHEDULE}')")
    sub.add_parser("status", help="Show channels, video counts, and insight stats")
    sub.add_parser("reindex", help="Rebuild the video DB from workspace metadata files")


def handle(args) -> int:
    cmd = getattr(args, "yti_cmd", None)
    if cmd == "setup-cron":
        jobs = [
            (args.schedule, CRON_PROMPT, CRON_NAME),
            (PIPELINE_SCHEDULE, PIPELINE_PROMPT, PIPELINE_NAME),
            (SNAPSHOT_SCHEDULE, SNAPSHOT_PROMPT, SNAPSHOT_NAME),
            (PULSE_SCHEDULE, PULSE_PROMPT, PULSE_NAME),
        ]
        rc = 0
        for schedule, prompt, name in jobs:
            argv = _hermes_argv() + [
                "cron", "create", schedule, prompt, "--name", name,
            ]
            if args.apply:
                proc = subprocess.run(argv)
                rc = rc or proc.returncode
            else:
                shown = " ".join(f'"{a}"' if " " in a else a for a in argv)
                print(f"# {name} ({schedule})\n  {shown}\n")
        if not args.apply:
            print("Re-run with --apply to create all four jobs now.")
        return rc

    try:
        from . import yti_store, yti_insights, yti_fetcher
    except ImportError:  # pragma: no cover
        import yti_store, yti_insights, yti_fetcher  # type: ignore

    if cmd == "reindex":
        conn = yti_store.connect()
        n = yti_fetcher.reindex_from_disk(conn)
        conn.close()
        print(f"Reindexed {n} metadata snapshots from workspace.")
        return 0

    # default / "status"
    conn = yti_store.connect()
    channels = yti_store.list_channels(conn)
    videos = conn.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
    transcribed = conn.execute(
        "SELECT COUNT(*) c FROM videos WHERE status IN ('transcribed','analyzing','analyzed')"
    ).fetchone()["c"]
    stats = yti_insights.insight_stats(conn)
    conn.close()
    print(json.dumps({
        "channels": channels,
        "videos": videos,
        "withTranscripts": transcribed,
        "insights": stats["totalInsights"],
        "insightSources": stats["totalSources"],
        "categories": stats["categories"],
    }, indent=2))
    return 0
