# long-form installed

Next steps:

1. **Enable the plugin** (if you didn't pass `--enable`):

       hermes plugins enable long-form

2. **Set your transcript API key** — you should have been prompted during
   install; otherwise add `TRANSCRIPT_API_KEY=...` to `~/.hermes/.env`
   (key from https://transcriptapi.com), or set it in the dashboard under
   Settings → Environment.

3. **Track channels** — in a hermes chat: "track @DanKoeTalks on youtube",
   or use the dashboard's **YouTube Insights** tab → Tracked Channels → Add.

4. **Fetch + analyze** — click **Refresh** then **Analyze** on the Trends
   page, or run `/yt-analyze` in a chat. Install the daily 03:00 refresh job:

       hermes long-form setup-cron --apply

5. **Search your insight base** — `/yt` for a summary, the `yt_search_insights`
   tool in any conversation, or the Insights page in the dashboard.

For content production (scripts, campaigns, marketing strategy) pair this
plugin with digital-marketing-pro:
https://github.com/indranilbanerjee/digital-marketing-pro
