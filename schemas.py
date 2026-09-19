"""Tool schemas (what the LLM sees) for the youtube-insights plugin."""

_CATEGORIES = "strategy|technical|creativity|productivity|business|psychology|trend|career"

YT_ADD_CHANNEL = {
    "name": "yt_add_channel",
    "description": (
        "Track a YouTube channel for competitive intelligence. The channel's "
        "latest videos and transcripts are pulled on the next fetch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handle": {"type": "string",
                       "description": "Channel handle, e.g. '@DanKoeTalks'"},
        },
        "required": ["handle"],
    },
}

YT_REMOVE_CHANNEL = {
    "name": "yt_remove_channel",
    "description": "Stop tracking a YouTube channel.",
    "parameters": {
        "type": "object",
        "properties": {
            "handle": {"type": "string", "description": "Channel handle to remove"},
        },
        "required": ["handle"],
    },
}

YT_LIST_CHANNELS = {
    "name": "yt_list_channels",
    "description": "List the YouTube channels currently tracked.",
    "parameters": {"type": "object", "properties": {}},
}

YT_FETCH_VIDEOS = {
    "name": "yt_fetch_videos",
    "description": (
        "Fetch the latest videos and transcripts for every tracked channel "
        "from transcriptapi.com, skipping Shorts and videos under 120 seconds. "
        "Writes transcripts and view-count snapshots into the plugin workspace. "
        "Requires TRANSCRIPT_API_KEY."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lookback_days": {
                "type": "integer",
                "description": "Only fetch transcripts for videos newer than this many days (default 30)",
            },
        },
    },
}

YT_TRENDING = {
    "name": "yt_trending",
    "description": (
        "List tracked videos ranked by views-per-hour (VPH) with trend "
        "direction (accelerating/decelerating/flat). No network calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max videos to return (default 20)"},
        },
    },
}

YT_TRIGGER_ANALYSIS = {
    "name": "yt_trigger_analysis",
    "description": (
        "Queue transcribed-but-unanalyzed videos for insight extraction "
        "(capped per run). Returns one work item per video with exact "
        "instructions: run the youtube-insights:youtube-video-analyst skill "
        "on the transcript, save analysis.md, then record 10-15 insights via "
        "yt_add_insight."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer",
                      "description": "Max videos to queue this run (default 20)"},
            "order_by": {"type": "string", "enum": ["vph", "oldest"],
                         "description": "Spend the cap on highest-VPH first (default) or oldest first"},
        },
    },
}

YT_ADD_INSIGHT = {
    "name": "yt_add_insight",
    "description": (
        "Record an insight extracted from a YouTube video transcript into the "
        "deduplicated knowledge base. Near-duplicate insights are merged and "
        "the new source is linked instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string",
                     "description": "10-20 word generalizable principle"},
            "detail": {"type": "string",
                       "description": "2-3 sentences with specific context"},
            "category": {"type": "string",
                         "description": f"One of: {_CATEGORIES}"},
            "source_video_id": {"type": "string",
                                "description": "YouTube video ID the insight came from"},
            "context": {"type": "string",
                        "description": "Direct quote from the transcript"},
            "timestamp_ref": {"type": "string",
                              "description": "Timestamp like MM:SS"},
        },
        "required": ["text", "category", "source_video_id"],
    },
}

YT_SEARCH_INSIGHTS = {
    "name": "yt_search_insights",
    "description": (
        "Full-text search the insight knowledge base (BM25-ranked), optionally "
        "filtered by category. Returns insights with their source videos."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "category": {"type": "string",
                         "description": f"Optional filter: {_CATEGORIES}"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["query"],
    },
}

YT_LINT_SCRIPT = {
    "name": "yt_lint_script",
    "description": (
        "Lint a produced script-outline markdown file against the "
        "content-creator format contract: beat word budgets vs timestamps "
        "(~150 wpm), flowing spoken lines (no terse fragments), "
        "spoken-sentence HOOK INTO NEXT lines, Visual fields, no quotation "
        "marks, no AI-isms. Pass a workspace-relative or absolute path to "
        "one script file, or a topic folder to lint all three variants. "
        "Returns {ok, findings}. A script task is NOT done until this "
        "reports ok:true for every script it produced."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "script-outline*.md file or topic folder"},
        },
        "required": ["path"],
    },
}


YT_RESEARCH = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["status", "snapshot", "crawl", "enrich", "transcripts", "comments", "score",
                     "packaging", "formats", "profile", "report", "pipeline", "doctor", "gaps"],
            "description": "Research engine action. 'snapshot' is the free daily RSS history job "
                           "(run it every day); 'pipeline' runs the whole batch; 'gaps' returns the "
                           "D4 cross-niche format gap report.",
        },
        "params": {
            "type": "object",
            "description": "Job parameters: crawl {max_credits, niches, dry_run, resume}; enrich "
                           "{max_results}; transcripts {max}; comments {max_videos, max_per_video}; "
                           "profile {channel}; report {only:[D1..D5]}.",
        },
    },
    "required": ["action"],
}
