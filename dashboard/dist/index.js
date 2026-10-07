/**
 * youtube-insights — Hermes Dashboard Plugin
 *
 * Research + Trends + Insights + Artifacts pages. Trends/Insights are ported
 * from the original paperclip plugin with the same layout, panels, and
 * functions, restyled onto the hermes design system; Research is the niche
 * teardown engine (crawl → outliers → formats → gap report → teardown).
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ for React and
 * shared UI primitives; all backend calls go through SDK.fetchJSON so auth
 * works in both dashboard modes.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useCallback } = SDK.hooks;
  const C = SDK.components || {};
  const Button = C.Button || function (p) { return h("button", p, p.children); };
  const Input = C.Input || function (p) { return h("input", p); };

  function api(path, options) {
    // Delegate to the host SDK's fetchJSON so auth is handled correctly in
    // BOTH dashboard modes (loopback token header / gated cookie). Never
    // hand-roll fetch or read window.__HERMES_SESSION_TOKEN__.
    return SDK.fetchJSON("/api/plugins/youtube-insights" + path, options);
  }

  // -------------------------------------------------------------------------
  // Helpers (ports of the original formatters)
  // -------------------------------------------------------------------------

  function formatNumber(n) {
    n = Number(n) || 0;
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return n.toLocaleString();
  }

  function formatDuration(seconds) {
    if (seconds == null) return "—";
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return m + ":" + String(s).padStart(2, "0");
  }

  function formatAgo(dateStr) {
    if (!dateStr) return "";
    const diff = Date.now() - new Date(dateStr).getTime();
    const hours = Math.floor(diff / 3600000);
    if (hours < 1) return "just now";
    if (hours < 24) return hours + "h ago";
    const days = Math.floor(hours / 24);
    if (days < 30) return days + "d ago";
    return Math.floor(days / 30) + "mo ago";
  }

  const CATEGORY_COLORS = {
    strategy: "#8b5cf6",
    technical: "#3b82f6",
    creativity: "#ec4899",
    productivity: "#22c55e",
    business: "#f59e0b",
    psychology: "#06b6d4",
    trend: "#ef4444",
    career: "#a855f7",
  };

  const INSIGHT_CATEGORIES = [
    "strategy", "technical", "creativity", "productivity",
    "business", "psychology", "trend", "career",
  ];

  const TREND_COLOR = {
    accelerating: "#22c55e",
    decelerating: "#ef4444",
    flat: "#9ca3af",
  };

  // -------------------------------------------------------------------------
  // Sparkline (port of src/ui/Sparkline.tsx)
  // -------------------------------------------------------------------------

  function Sparkline(props) {
    const points = props.points || [];
    const width = props.width || 80;
    const height = props.height || 30;
    if (points.length < 2) return null;
    const pad = 2;
    const min = Math.min.apply(null, points);
    const max = Math.max.apply(null, points);
    const range = max - min || 1;
    const coords = points
      .map(function (v, i) {
        const x = pad + (i / (points.length - 1)) * (width - 2 * pad);
        const y = height - pad - ((v - min) / range) * (height - 2 * pad);
        return x.toFixed(1) + "," + y.toFixed(1);
      })
      .join(" ");
    const color = TREND_COLOR[props.direction] || TREND_COLOR.flat;
    return h("svg", { width: width, height: height, className: "yti-sparkline" },
      h("polyline", { points: coords, fill: "none", stroke: color, strokeWidth: 2 })
    );
  }

  function TrendArrow(props) {
    const d = props.direction;
    const glyph = d === "accelerating" ? "↗" : d === "decelerating" ? "↘" : "→";
    return h("span", {
      className: "yti-trend-arrow",
      style: { color: TREND_COLOR[d] || TREND_COLOR.flat },
      title: d,
    }, glyph);
  }

  function StatCard(props) {
    return h("div", { className: "yti-stat-card" },
      h("div", { className: "yti-stat-value" }, props.value),
      h("div", { className: "yti-stat-label" }, props.tip ? h(Tip, { k: props.tip }, props.label) : props.label)
    );
  }

  // -------------------------------------------------------------------------
  // ✨ per-row generation cell (paperclip parity: idle sparkle → spinner
  // while the task is open → stale after 30 min (click re-runs) → done =
  // sparkle again + a fixed-slot ↗ to the worker's chat thread / task).
  // -------------------------------------------------------------------------
  function GenerateCell(props) {
    const v = props.video;
    const g = v.generation || null;
    const st = useState(false);
    const submitting = st[0], setSubmitting = st[1];
    const isOpen = !!g && g.status === "open";
    const isStale = !submitting && !!g && g.status === "stale";
    const isDone = !!g && g.status === "done";

    const label = isOpen
      ? "Script generation in progress — spinning until the task is done"
      : isStale
      ? "Previous attempt hasn't completed in 30+ min — click to re-run"
      : isDone
      ? "Re-generate (previous run completed — click ↗ to review)"
      : "Generate a similar-but-unique video script (creates a task + chat)";

    const onClick = function () {
      if (isOpen || submitting) return;
      setSubmitting(true);
      api("/generate-content", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ videoId: v.videoId }),
      }).then(function () { setSubmitting(false); props.refresh(); })
        .catch(function (e) { setSubmitting(false); window.alert(String((e && e.message) || e)); });
    };

    const chatHref = g && g.sessionId
      ? "/chat?resume=" + encodeURIComponent(g.sessionId) : null;
    const taskHref = g && g.taskId
      ? "/kanban?task=" + encodeURIComponent(g.taskId) : null;

    function navLink(href, text, title) {
      return h("a", {
        href: href,
        onClick: function (e) { e.preventDefault(); window.location.assign(href); },
        title: title,
        className: "yti-gen-link",
      }, text);
    }

    return h("span", { style: { display: "inline-flex", alignItems: "center", gap: 8 } },
      h("button", {
        className: "yti-generate-btn",
        onClick: onClick,
        disabled: isOpen || submitting,
        title: label,
        "aria-label": label,
        style: { cursor: (isOpen || submitting) ? "wait" : "pointer" },
      }, (isOpen || submitting)
        ? h("span", { className: "yti-gen-spinner", role: "status" })
        : h("span", { "aria-hidden": true }, "✨")),
      chatHref
        ? navLink(chatHref, "chat ↗", "Open this run's conversation thread")
        : null,
      taskHref
        ? navLink(taskHref, "task ↗", isDone
            ? "Finished run — scripts attached here and on the Artifacts tab"
            : "Open this run's kanban task")
        : null
    );
  }

  // -------------------------------------------------------------------------
  // Page-size selector shared by the Trends and Insights pagers.
  // -------------------------------------------------------------------------
  var PAGE_SIZES = [30, 50, 100, 200];
  function PageSizeSelect(props) {
    return h("select", {
      className: "yti-select yti-pagesize",
      value: String(props.value),
      onChange: function (e) { props.onChange(Number(e.target.value)); },
      title: "Rows per page",
    }, PAGE_SIZES.map(function (n) {
      return h("option", { key: n, value: String(n) }, n + " / page");
    }));
  }

  // -------------------------------------------------------------------------
  // Trends view (port of YouTubeTrendsPageContent)
  // -------------------------------------------------------------------------

  function TrendsView() {
    const [data, setData] = useState(null);
    const [channels, setChannels] = useState([]);
    const [sortField, setSortField] = useState("vph");
    const [sortAsc, setSortAsc] = useState(false);
    const [page, setPage] = useState(0);
    const [pageSize, setPageSize] = useState(30);
    const [fetching, setFetching] = useState(false);
    const [analyzing, setAnalyzing] = useState(false);
    const [notice, setNotice] = useState(null);

    const refresh = useCallback(function () {
      api("/videos").then(setData).catch(function (e) {
        setNotice({ tone: "error", text: String(e) });
      });
      api("/channels").then(function (d) {
        setChannels((d && d.channels) || []);
      }).catch(function () {});
    }, []);

    useEffect(function () { refresh(); }, [refresh]);

    // Poll while any ✨ generation task is open so the spinner flips to
    // ✨ + ↗ shortly after the worker completes (paperclip parity).
    const hasOpenGeneration = !!(data && data.videos || []).some(function (v) {
      return v.generation && v.generation.status === "open";
    });
    useEffect(function () {
      if (!hasOpenGeneration) return undefined;
      const id = window.setInterval(refresh, 15000);
      return function () { window.clearInterval(id); };
    }, [hasOpenGeneration, refresh]);

    const videos = (data && data.videos) || [];
    const loading = data === null;

    const handleFetch = function () {
      setFetching(true);
      setNotice(null);
      api("/fetch", { method: "POST" })
        .then(function (res) {
          if (res && res.error) {
            setNotice({ tone: "error", text: res.error });
            setFetching(false);
            return;
          }
          setNotice({ tone: "ok", text: "Fetch started — new videos will appear as they're downloaded (~30-90s)." });
          // Poll for ~2 minutes to surface rows as the background fetch lands.
          let ticks = 0;
          const id = window.setInterval(function () {
            ticks += 1;
            refresh();
            if (ticks >= 8) {
              window.clearInterval(id);
              setFetching(false);
            }
          }, 15000);
        })
        .catch(function (e) {
          setNotice({ tone: "error", text: String(e) });
          setFetching(false);
        });
    };

    const handleAnalyze = function () {
      setAnalyzing(true);
      setNotice(null);
      api("/trigger-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
        .then(function (res) {
          const n = (res && res.triggered) || 0;
          setNotice({
            tone: "ok",
            text: n
              ? "Queued " + n + " video(s) for analysis. Run the cron job or ask the agent to work the queue (yt_trigger_analysis)."
              : "Nothing to analyze — every transcribed video is already queued or analyzed.",
          });
          refresh();
        })
        .catch(function (e) { setNotice({ tone: "error", text: String(e) }); })
        .finally(function () { setAnalyzing(false); });
    };

    const toggleSort = function (field) {
      if (sortField === field) setSortAsc(!sortAsc);
      else { setSortField(field); setSortAsc(false); }
      setPage(0);
    };

    const sorted = sortRows(videos, { key: sortField, dir: sortAsc ? "asc" : "desc" }, {
      published: function (v) { return new Date(v.published).getTime(); },
      title: function (v) { return v.title; }, channel: function (v) { return v.author; },
      trend: function (v) { return ({ accelerating: 3, flat: 2, decelerating: 1 })[v.trendDirection] || 0; },
      status: function (v) { return ({ discovered: 1, transcribed: 2, analyzing: 3, analyzed: 4 })[v.status || "discovered"] || 0; } });

    // clamp the page if the list shrank or the page size grew
    const maxPage = Math.max(0, Math.ceil(sorted.length / pageSize) - 1);
    const curPage = Math.min(page, maxPage);
    const paged = sorted.slice(curPage * pageSize, (curPage + 1) * pageSize);

    const topVph = videos.length
      ? Math.max.apply(null, videos.map(function (v) { return v.vph; }))
      : 0;

    const sortGlyph = function (field) {
      if (sortField !== field) return " ↕";
      return sortAsc ? " ▲" : " ▼";
    };

    return h("div", { className: "yti-page" },
      h("div", { className: "yti-header" },
        h("h1", { className: "yti-title" }, "YouTube Trends"),
        h("div", { className: "yti-header-actions" },
          h(Button, { size: "sm", disabled: analyzing, onClick: handleAnalyze },
            analyzing ? "Queuing…" : "Analyze"),
          h(Button, { size: "sm", disabled: fetching, onClick: handleFetch },
            fetching ? "Fetching…" : "Refresh")
        )
      ),
      h("div", { className: "yti-subtle" },
        videos.length + " videos",
        data && data.lastFetchRun
          ? " · last fetch " + formatAgo(data.lastFetchRun)
          : ""
      ),
      notice ? h("div", {
        className: "yti-notice " + (notice.tone === "error" ? "yti-notice-error" : "yti-notice-ok"),
      }, notice.text) : null,
      data && data.hasApiKey === false
        ? h("div", { className: "yti-notice yti-notice-error" },
            "TRANSCRIPT_API_KEY is not configured — create a key at ",
            h("a", { href: "https://transcriptapi.com", target: "_blank", rel: "noreferrer" }, "transcriptapi.com"),
            ", then set it on the ",
            h("a", { href: "/env" }, "Keys page"),
            " (Custom Keys → TRANSCRIPT_API_KEY) to enable fetching.")
        : null,

      // Stats
      h("div", { className: "yti-stats-row" },
        h(StatCard, { value: String(videos.length), label: "Videos Tracked" }),
        h(StatCard, { value: String(channels.length), label: "Channels" }),
        h(StatCard, { value: formatNumber(topVph), label: "Top VPH" })
      ),

      // Channel management (shared with the Research tab)
      h(ChannelManager, { channels: channels, onChannels: setChannels }),

      // Video table
      loading
        ? h("div", { className: "yti-empty" }, "Loading…")
        : h("div", { className: "yti-table-wrap" },
            h("table", { className: "yti-table" },
              h("thead", null,
                h("tr", null,
                  h("th", null, "Thumbnail"),
                  [["title", "Title", "title", ""], ["channel", "Channel", "channel", ""], ["published", "Published", "tpublished", ""],
                   ["duration", "Duration", "duration", "yti-right"], ["views", "Views", "views", "yti-right"],
                   ["vph", "VPH", "vph", "yti-right yti-strong"], ["trend", "Trend", "trend", "yti-center"],
                   ["status", "Status", "tstatus", "yti-center"]].map(function (c) {
                    return h("th", { key: c[0], className: (c[3] ? c[3] + " " : "") + "yti-sortable" + (sortField === c[0] ? " yti-sorted" : ""),
                        onClick: function () { toggleSort(c[0]); } },
                      h(Tip, { k: c[2], right: /right|center/.test(c[3]) }, c[1]),
                      h("span", { className: "yti-sort-glyph", "aria-hidden": "true" }, sortGlyph(c[0])));
                  }),
                  h("th", { className: "yti-center", title: "Generate a similar-but-unique script from this video" }, "Create")
                )
              ),
              h("tbody", null,
                paged.map(function (v) {
                  return h("tr", { key: v.videoId },
                    h("td", null,
                      h("a", { href: v.link, target: "_blank", rel: "noopener" },
                        v.thumbnail
                          ? h("img", { className: "yti-thumb", src: v.thumbnail, alt: "" })
                          : h("div", { className: "yti-thumb yti-thumb-empty" })
                      )
                    ),
                    h("td", null,
                      h("a", {
                        className: "yti-video-link",
                        href: v.link, target: "_blank", rel: "noopener",
                      }, v.title)
                    ),
                    h("td", { className: "yti-muted" }, v.author),
                    h("td", { className: "yti-muted" }, formatAgo(v.published)),
                    h("td", { className: "yti-right yti-muted" }, formatDuration(v.duration)),
                    h("td", { className: "yti-right" }, formatNumber(v.views)),
                    h("td", { className: "yti-right yti-strong" }, formatNumber(v.vph)),
                    h("td", { className: "yti-center" },
                      h("div", null,
                        h(TrendArrow, { direction: v.trendDirection }),
                        h(Sparkline, { points: v.sparklinePoints, direction: v.trendDirection })
                      ),
                      h("div", { className: "yti-pts" }, v.snapshotCount + " pts")
                    ),
                    h("td", { className: "yti-center" },
                      h("span", { className: "yti-status yti-status-" + (v.status || "discovered") },
                        v.status || "discovered")
                    ),
                    h("td", { className: "yti-center" },
                      h(GenerateCell, { video: v, refresh: refresh })
                    )
                  );
                })
              )
            ),
            sorted.length === 0
              ? h("div", { className: "yti-empty" },
                  "No videos tracked yet. Add channels above and click Refresh.")
              : null,
            sorted.length > PAGE_SIZES[0]
              ? h("div", { className: "yti-pagination" },
                  h(Button, {
                    size: "sm", disabled: curPage === 0,
                    onClick: function () { setPage(curPage - 1); },
                  }, "Previous"),
                  h("span", { className: "yti-muted yti-small" },
                    (curPage * pageSize + 1) + "–" +
                    Math.min((curPage + 1) * pageSize, sorted.length) +
                    " of " + sorted.length),
                  h(Button, {
                    size: "sm", disabled: (curPage + 1) * pageSize >= sorted.length,
                    onClick: function () { setPage(curPage + 1); },
                  }, "Next"),
                  h(PageSizeSelect, {
                    value: pageSize,
                    onChange: function (n) { setPageSize(n); setPage(0); },
                  })
                )
              : null
          )
    );
  }

  // -------------------------------------------------------------------------
  // Insights view (port of InsightsPageContent)
  // -------------------------------------------------------------------------

  function InsightsView() {
    const [search, setSearch] = useState("");
    const [category, setCategory] = useState("");
    const [sortBy, setSortBy] = useState("sources");
    const [expandedId, setExpandedId] = useState(null);
    const [page, setPage] = useState(0);
    const [listData, setListData] = useState(null);
    const [stats, setStats] = useState(null);
    const [limit, setLimit] = useState(30);

    const load = useCallback(function () {
      const params = new URLSearchParams({
        q: search, category: category, sortBy: sortBy,
        limit: String(limit), offset: String(page * limit),
      });
      api("/insights?" + params.toString()).then(setListData).catch(function () {
        setListData({ insights: [], total: 0 });
      });
    }, [search, category, sortBy, page, limit]);

    useEffect(function () {
      const id = window.setTimeout(load, search ? 250 : 0);
      return function () { window.clearTimeout(id); };
    }, [load, search]);

    useEffect(function () {
      api("/insights/stats").then(setStats).catch(function () {});
    }, []);

    const insights = (listData && listData.insights) || [];
    const total = (listData && listData.total) || 0;
    const st = stats || { totalInsights: 0, totalSources: 0, topInsight: null, categories: {} };
    const loading = listData === null;

    const handleDelete = function (insight) {
      if (!window.confirm("Delete this insight?")) return;
      api("/insights/" + encodeURIComponent(insight.id), { method: "DELETE" })
        .then(function () {
          load();
          api("/insights/stats").then(setStats).catch(function () {});
        })
        .catch(function () {});
    };

    return h("div", { className: "yti-page yti-page-narrow" },
      h("h1", { className: "yti-title yti-title-block" }, "YouTube Insights"),

      // Stats
      h("div", { className: "yti-stats-row" },
        h(StatCard, { value: String(st.totalInsights), label: "Total Insights" }),
        h(StatCard, { value: String(st.totalSources), label: "Sources Analyzed" }),
        st.topInsight ? h("div", { className: "yti-stat-card yti-stat-top" },
          h("div", { className: "yti-stat-top-text" },
            st.topInsight.text.slice(0, 60) + "…"),
          h("div", { className: "yti-stat-label" },
            "Top Insight (" + st.topInsight.sourceCount + " sources)")
        ) : null
      ),

      // Search / filter
      h("div", { className: "yti-filter-row" },
        h(Input, {
          className: "yti-search",
          value: search,
          placeholder: "Search insights...",
          onChange: function (e) { setSearch(e.target.value); setPage(0); },
        }),
        h("select", {
          className: "yti-select",
          value: category,
          onChange: function (e) { setCategory(e.target.value); setPage(0); },
        },
          h("option", { value: "" }, "All Categories"),
          INSIGHT_CATEGORIES.map(function (c) {
            return h("option", { key: c, value: c }, c);
          })
        ),
        h("select", {
          className: "yti-select",
          value: sortBy,
          onChange: function (e) { setSortBy(e.target.value); },
        },
          h("option", { value: "sources" }, "Most Sourced"),
          h("option", { value: "recent" }, "Most Recent")
        )
      ),

      // Insight cards
      loading
        ? h("div", { className: "yti-empty" }, "Loading…")
        : h("div", null,
            insights.map(function (insight) {
              const expanded = expandedId === insight.id;
              return h("div", { key: insight.id, className: "yti-card yti-insight-card" },
                h("div", { className: "yti-insight-head" },
                  h("div", { className: "yti-insight-main" },
                    h("div", {
                      className: "yti-insight-text",
                      onClick: function () {
                        setExpandedId(expanded ? null : insight.id);
                      },
                    }, insight.text),
                    insight.detail && !expanded
                      ? h("div", { className: "yti-insight-preview" },
                          insight.detail.slice(0, 120) + "…")
                      : null,
                    h("div", { className: "yti-insight-meta" },
                      h("span", {
                        className: "yti-chip",
                        style: { background: CATEGORY_COLORS[insight.category] || "#666" },
                      }, insight.category),
                      h("span", { className: "yti-muted yti-small" },
                        insight.sourceCount + " source" + (insight.sourceCount !== 1 ? "s" : "")),
                      h("span", { className: "yti-faint yti-small" },
                        formatAgo(insight.lastSeen))
                    )
                  ),
                  h(Button, {
                    size: "sm",
                    className: "yti-delete-btn",
                    onClick: function () { handleDelete(insight); },
                  }, "Delete")
                ),
                expanded ? h("div", { className: "yti-insight-expanded" },
                  insight.detail
                    ? h("div", { className: "yti-insight-detail" }, insight.detail)
                    : null,
                  (insight.sourceContexts && insight.sourceContexts.length)
                    ? insight.sourceContexts.map(function (src, i) {
                        const tsSuffix = src.timestampRef
                          ? "&t=" + src.timestampRef.replace(":", "m") + "s"
                          : "";
                        return h("div", { key: i, className: "yti-source" },
                          h("div", { className: "yti-small" },
                            h("a", {
                              className: "yti-source-link",
                              href: src.sourceUrl + tsSuffix,
                              target: "_blank", rel: "noopener",
                            }, src.title || src.sourceUrl),
                            src.author
                              ? h("span", { className: "yti-muted" }, " — " + src.author)
                              : null,
                            src.timestampRef
                              ? h("span", { className: "yti-muted yti-ts" },
                                  "[" + src.timestampRef + "]")
                              : null
                          ),
                          src.context
                            ? h("div", { className: "yti-source-quote" },
                                "“" + src.context + "”")
                            : null
                        );
                      })
                    : h("div", { className: "yti-faint yti-small" },
                        "No source details available")
                ) : null
              );
            }),
            insights.length === 0
              ? h("div", { className: "yti-empty" },
                  "No insights found. Run analysis on tracked videos to generate insights.")
              : null,
            total > PAGE_SIZES[0] ? h("div", { className: "yti-pagination" },
              h(Button, {
                size: "sm", disabled: page === 0,
                onClick: function () { setPage(page - 1); },
              }, "Previous"),
              h("span", { className: "yti-muted yti-small" },
                (page * limit + 1) + "–" + Math.min((page + 1) * limit, total) + " of " + total),
              h(Button, {
                size: "sm", disabled: (page + 1) * limit >= total,
                onClick: function () { setPage(page + 1); },
              }, "Next"),
              h(PageSizeSelect, {
                value: limit,
                onChange: function (n) { setLimit(n); setPage(0); },
              })
            ) : null
          )
    );
  }


  // -------------------------------------------------------------------------
  // Markdown renderer (same approach as the value-creator plugin's bundle)
  // -------------------------------------------------------------------------

  function mdInline(text, keyBase) {
    var out = [];
    var rest = String(text);
    var key = 0;
    var re = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*([^*]+)\*)/;
    while (rest.length) {
      var m = re.exec(rest);
      if (!m) { out.push(rest); break; }
      if (m.index > 0) out.push(rest.slice(0, m.index));
      var k = keyBase + "-" + key++;
      if (m[2] != null) out.push(h("strong", { key: k }, m[2]));
      else if (m[3] != null) out.push(h("code", { key: k }, m[3]));
      else if (m[4] != null)
        out.push(h("a", { key: k, href: m[5], target: "_blank", rel: "noreferrer" }, m[4]));
      else if (m[6] != null) out.push(h("em", { key: k }, m[6]));
      rest = rest.slice(m.index + m[1].length);
    }
    return out;
  }

  function splitTableRow(line) {
    var t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function isTableDivider(line) {
    return /^\s*\|?\s*:?-{2,}.*\|/.test(line) && /^[\s|:-]+$/.test(line);
  }

  function renderMarkdown(md) {
    var lines = String(md || "").split(/\r?\n/);
    var blocks = [];
    var i = 0;
    var key = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        blocks.push(h("hr", { key: "k" + key++, className: "yti-md-hr" }));
        i++;
        continue;
      }
      var hm = /^(#{1,6})\s+(.*)$/.exec(line);
      if (hm) {
        blocks.push(h("h" + Math.min(6, hm[1].length + 1), { key: "k" + key++ },
          mdInline(hm[2], "h" + key)));
        i++;
        continue;
      }
      if (/^```/.test(line)) {
        var code = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { code.push(lines[i]); i++; }
        i++;
        blocks.push(h("pre", { key: "k" + key++ }, h("code", null, code.join("\n"))));
        continue;
      }
      if (line.indexOf("|") >= 0 && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
        var headCells = splitTableRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].indexOf("|") >= 0 && lines[i].trim()) {
          rows.push(splitTableRow(lines[i]));
          i++;
        }
        blocks.push(
          h("table", { key: "k" + key++ },
            h("thead", null, h("tr", null, headCells.map(function (c, ci) {
              return h("th", { key: ci }, mdInline(c, "th" + ci));
            }))),
            h("tbody", null, rows.map(function (r, ri) {
              return h("tr", { key: ri }, r.map(function (c, ci) {
                return h("td", { key: ci }, mdInline(c, "td" + ri + "-" + ci));
              }));
            }))
          )
        );
        continue;
      }
      if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
        var ordered = /^\s*\d+\./.test(line);
        var items = [];
        while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
          i++;
        }
        blocks.push(
          h(ordered ? "ol" : "ul", { key: "k" + key++ }, items.map(function (it, ii) {
            return h("li", { key: ii }, mdInline(it, "li" + ii));
          }))
        );
        continue;
      }
      var para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6})\s|^```|^\s*([-*]|\d+\.)\s+/.test(lines[i]) &&
             !(lines[i].indexOf("|") >= 0 && i + 1 < lines.length && isTableDivider(lines[i + 1]))) {
        para.push(lines[i]);
        i++;
      }
      blocks.push(h("p", { key: "k" + key++ }, mdInline(para.join(" "), "p" + key)));
    }
    return h("div", { className: "yti-md" }, blocks);
  }

  // -------------------------------------------------------------------------
  // Artifacts view — port of the original Workspace Deliverables page
  // -------------------------------------------------------------------------

  var FILE_ICONS = {
    ".md": "📝", ".txt": "📄", ".json": "🔧", ".csv": "🔢",
    ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼", ".webp": "🖼", ".gif": "🖼",
    ".pdf": "📕",
  };

  function TreeEntry(props) {
    var node = props.node;
    var depth = props.depth || 0;
    var openState = useState(depth < 2 || !!props.forceOpen);
    var open = openState[0], setOpen = openState[1];
    if (node.kind === "dir") {
      // Produce status ripples up the ancestor chain (back to the date
      // folder): spinner while a script beneath is being produced, green
      // once produced. Spinner wins when both apply.
      var pst = "";
      if (props.prod) {
        if (props.prod.producing[node.relPath]) pst = "producing";
        else if (props.prod.produced[node.relPath]) pst = "produced";
      }
      return h("div", { className: "yti-tree-dir" },
        h("div", {
          className: "yti-tree-row" + (pst ? " yti-dir-" + pst : ""),
          style: { paddingLeft: (depth * 14) + "px" },
          onClick: function () { setOpen(!open); },
          title: pst === "producing" ? "A script in this folder is being produced…"
            : pst === "produced" ? "Contains a produced script" : undefined,
        },
          h("span", { className: "yti-tree-caret" }, open ? "▾" : "▸"),
          pst === "producing"
            ? h("span", { className: "yti-dir-spinner" })
            : h("span", null, "📁 "),
          h("span", { className: "yti-tree-name" },
            node.name + (pst === "produced" ? " ✓" : ""))
        ),
        open ? (node.children || []).map(function (c) {
          return h(TreeEntry, { key: c.relPath, node: c, depth: depth + 1,
                                forceOpen: props.forceOpen, query: props.query,
                                prod: props.prod,
                                selected: props.selected, onSelect: props.onSelect });
        }) : null
      );
    }
    var active = props.selected === node.relPath;
    var isHit = props.query &&
      node.name.toLowerCase().indexOf(props.query) !== -1;
    return h("div", {
      className: "yti-tree-row yti-tree-file" + (active ? " yti-tree-active" : "") +
        (isHit ? " yti-search-hit" : ""),
      style: { paddingLeft: (depth * 14 + 16) + "px" },
      onClick: function () { props.onSelect(node); },
    },
      h("span", { className: "yti-tree-name" },
        (FILE_ICONS[node.ext] || "📄") + " " + node.name),
      h("span", { className: "yti-tree-size" }, formatNumber(node.size) + "B")
    );
  }

  // Small shared modal: overlay + card, closes on overlay click or Escape.
  function YtiModal(props) {
    useEffect(function () {
      function onKey(e) { if (e.key === "Escape") props.onClose(); }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, []);
    return h("div", {
      className: "yti-modal-overlay",
      onClick: function (e) { if (e.target === e.currentTarget) props.onClose(); },
    },
      h("div", { className: "yti-modal" + (props.wide ? " yti-modal-wide" : "") }, props.children)
    );
  }

  function ArtifactsView() {
    var treeState = useState(null);
    var tree = treeState[0], setTree = treeState[1];
    var selState = useState(null);
    var sel = selState[0], setSel = selState[1];
    var fileState = useState(null);
    var file = fileState[0], setFile = fileState[1];
    var editState = useState(null); // null = viewing; string = editing buffer
    var editing = editState[0], setEditing = editState[1];
    var busyState = useState(false);
    var busy = busyState[0], setBusy = busyState[1];
    var pdfUrlState = useState(null);
    var pdfUrl = pdfUrlState[0], setPdfUrl = pdfUrlState[1];
    var qState = useState("");
    var q = qState[0], setQ = qState[1];
    var imgState = useState(false);
    var withImages = imgState[0], setWithImages = imgState[1];
    var sortState = useState(true); // newest-first by default
    var sortDesc = sortState[0], setSortDesc = sortState[1];
    var prodState = useState({});
    var produce = prodState[0], setProduce = prodState[1];
    var producedFilterState = useState(false);
    var withProduced = producedFilterState[0], setWithProduced = producedFilterState[1];
    var copiedState = useState("");
    var copied = copiedState[0], setCopied = copiedState[1];
    var iterState = useState({});
    var iterate = iterState[0], setIterate = iterState[1];
    var iterModalState = useState(false);
    var iterModal = iterModalState[0], setIterModal = iterModalState[1];
    var steeringState = useState("");
    var steering = steeringState[0], setSteering = steeringState[1];
    var topicStatesState = useState({});
    var topicStates = topicStatesState[0], setTopicStates = topicStatesState[1];
    var topicModalState = useState(false);
    var topicModal = topicModalState[0], setTopicModal = topicModalState[1];
    var topicState = useState("");
    var topic = topicState[0], setTopic = topicState[1];
    var topicCtxState = useState("");
    var topicCtx = topicCtxState[0], setTopicCtx = topicCtxState[1];
    var presentStatesState = useState({});
    var presentStates = presentStatesState[0], setPresentStates = presentStatesState[1];
    var presentModalState = useState(false);
    var presentModal = presentModalState[0], setPresentModal = presentModalState[1];
    var presTopicState = useState("");
    var presTopic = presTopicState[0], setPresTopic = presTopicState[1];
    var presOutlineState = useState("");
    var presOutline = presOutlineState[0], setPresOutline = presOutlineState[1];
    var regenStatesState = useState({});
    var regenStates = regenStatesState[0], setRegenStates = regenStatesState[1];
    var regenModalState = useState(false);
    var regenModal = regenModalState[0], setRegenModal = regenModalState[1];
    var regenFeedbackState = useState("");
    var regenFeedback = regenFeedbackState[0], setRegenFeedback = regenFeedbackState[1];
    var stylesState = useState({ styles: [], selected: "" });
    var styleCat = stylesState[0], setStyleCat = stylesState[1];
    var stylePrevState = useState(null);
    var stylePrev = stylePrevState[0], setStylePrev = stylePrevState[1];
    var styleZoomState = useState(false);
    var styleZoom = styleZoomState[0], setStyleZoom = styleZoomState[1];
    // A plain <img> can't carry the dashboard auth header — fetch the
    // preview authed and show it as a blob URL (process-diagram pattern).
    useEffect(function () {
      if (!styleCat.selected) return undefined;
      var revoke = null;
      SDK.authedFetch("/api/plugins/youtube-insights/styles/preview?id=" +
          encodeURIComponent(styleCat.selected))
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) {
          if (b) { revoke = URL.createObjectURL(b); setStylePrev(revoke); }
        })
        .catch(function () {});
      return function () { if (revoke) URL.revokeObjectURL(revoke); };
    }, [styleCat.selected]);
    var pipeState = useState({ available: false, running: false });
    var pipeline = pipeState[0], setPipeline = pipeState[1];
    // The cron scheduler starts the execution on its next tick (up to ~1 min
    // after the trigger), so hold "Running…" from the click until the state
    // endpoint actually reports it (or 2 min pass — trigger presumed lost).
    var kickedState = useState(0);
    var kickedAt = kickedState[0], setKickedAt = kickedState[1];
    var pipelineRunning = !!pipeline.running ||
      (kickedAt > 0 && Date.now() - kickedAt < 120000);
    useEffect(function () {
      if (pipeline.running && kickedAt) setKickedAt(0);
    }, [pipeline.running]);

    var loadTree = useCallback(function () {
      api("/workspace/tree").then(function (d) { setTree(d.tree || []); })
        .catch(function () { setTree([]); });
      api("/produce-states").then(function (d) { setProduce((d && d.states) || {}); })
        .catch(function () {});
      api("/iterate-states").then(function (d) { setIterate((d && d.states) || {}); })
        .catch(function () {});
      api("/topic-states").then(function (d) { setTopicStates((d && d.states) || {}); })
        .catch(function () {});
      api("/present-states").then(function (d) { setPresentStates((d && d.states) || {}); })
        .catch(function () {});
      api("/regen-states").then(function (d) { setRegenStates((d && d.states) || {}); })
        .catch(function () {});
      api("/styles").then(function (d) {
        if (d && d.styles) setStyleCat(d);
      }).catch(function () {});
      api("/pipeline-state").then(function (d) { setPipeline(d || {}); })
        .catch(function () {});
    }, []);
    useEffect(function () { loadTree(); }, [loadTree]);

    // Poll while any produce/iterate/topic run is open so spinners resolve.
    function anyOpen(map) {
      return Object.keys(map).some(function (k) {
        return map[k] && map[k].status === "open";
      });
    }
    var hasOpenProduce = anyOpen(produce);
    var hasOpenRun = hasOpenProduce || anyOpen(iterate) || anyOpen(topicStates) ||
      anyOpen(presentStates) || anyOpen(regenStates) || pipelineRunning;
    useEffect(function () {
      if (!hasOpenRun) return undefined;
      var id = window.setInterval(loadTree, 15000);
      return function () { window.clearInterval(id); };
    }, [hasOpenRun, loadTree]);

    useEffect(function () {
      setEditing(null);
      setFile(null);
      setCopied("");
      if (pdfUrl) { URL.revokeObjectURL(pdfUrl); setPdfUrl(null); }
      if (!sel) return;
      if (sel.ext === ".pdf") {
        SDK.authedFetch("/api/plugins/youtube-insights/workspace/file?path=" +
                        encodeURIComponent(sel.relPath))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var bytes = atob(d.base64 || "");
            var arr = new Uint8Array(bytes.length);
            for (var i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
            var url = URL.createObjectURL(new Blob([arr], { type: "application/pdf" }));
            setPdfUrl(url);
            setFile(d);
          })
          .catch(function () { setFile({ ok: false, error: "Could not load PDF." }); });
        return;
      }
      api("/workspace/file?path=" + encodeURIComponent(sel.relPath))
        .then(setFile)
        .catch(function (e) { setFile({ ok: false, error: String(e && e.message || e) }); });
    }, [sel && sel.relPath]);

    function save() {
      if (editing == null || !sel) return;
      setBusy(true);
      api("/workspace/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: sel.relPath, content: editing }),
      }).then(function () {
        setBusy(false);
        setFile(Object.assign({}, file, { text: editing }));
        setEditing(null);
      }).catch(function (e) {
        setBusy(false);
        alert("Save failed: " + (e && e.message || e));
      });
    }

    function copyMarkdown() {
      if (!file || file.kind !== "text") return;
      navigator.clipboard.writeText(file.text || "").then(function () {
        setCopied("md"); window.setTimeout(function () { setCopied(""); }, 1500);
      }).catch(function () { alert("Clipboard unavailable"); });
    }

    function copyRich() {
      if (!file || file.kind !== "text") return;
      var el = document.querySelector(".yti-md-rendered");
      var html = el ? el.innerHTML : "";
      var write = (window.ClipboardItem && html)
        ? navigator.clipboard.write([new window.ClipboardItem({
            "text/html": new Blob([html], { type: "text/html" }),
            "text/plain": new Blob([file.text || ""], { type: "text/plain" }),
          })])
        : navigator.clipboard.writeText(file.text || "");
      write.then(function () {
        setCopied("rich"); window.setTimeout(function () { setCopied(""); }, 1500);
      }).catch(function () { alert("Clipboard unavailable"); });
    }

    function produceScript() {
      if (!sel) return;
      api("/produce", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: sel.relPath }),
      }).then(function () { loadTree(); })
        .catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function iterateScript() {
      if (!sel) return;
      api("/iterate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: sel.relPath, steering: steering }),
      }).then(function () {
        setIterModal(false); setSteering(""); loadTree();
      }).catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function runPipeline() {
      api("/pipeline-run", { method: "POST" })
        .then(function () { setKickedAt(Date.now()); loadTree(); })
        .catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function selectStyle(id) {
      api("/styles/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: id }),
      }).then(function () {
        setStyleCat(Object.assign({}, styleCat, { selected: id }));
      }).catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function uploadStyle(fileEl) {
      var f = fileEl && fileEl.files && fileEl.files[0];
      if (!f) return;
      var reader = new FileReader();
      reader.onload = function () {
        var b64 = String(reader.result || "").split(",")[1] || "";
        api("/styles/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: f.name, dataBase64: b64 }),
        }).then(function () {
          fileEl.value = "";
          api("/styles").then(function (d) { if (d && d.styles) setStyleCat(d); });
        }).catch(function (e) { alert(String((e && e.message) || e)); });
      };
      reader.readAsDataURL(f);
    }

    function regenerateSelected(feedback) {
      if (!sel) return;
      api("/regen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: sel.relPath, feedback: feedback || "" }),
      }).then(function () {
        setRegenModal(false); setRegenFeedback(""); loadTree();
      }).catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function createPresentation() {
      if (!presTopic.trim() || !presOutline.trim()) return;
      api("/present", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: presTopic, outline: presOutline }),
      }).then(function () {
        setPresentModal(false); setPresTopic(""); setPresOutline(""); loadTree();
      }).catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function generateTopic() {
      if (!topic.trim()) return;
      api("/generate-topic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic, context: topicCtx }),
      }).then(function () {
        setTopicModal(false); setTopic(""); setTopicCtx(""); loadTree();
      }).catch(function (e) { alert(String((e && e.message) || e)); });
    }

    function flattenFiles(nodes, out) {
      nodes.forEach(function (n) {
        if (n.kind === "file") out.push(n);
        if (n.children) flattenFiles(n.children, out);
      });
      return out;
    }

    function formatMtime(iso) {
      try {
        var d = new Date(iso);
        return "last modified " + d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
          ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
      } catch (e) { return ""; }
    }

    var preview;
    if (!sel) {
      preview = h("div", { className: "yti-empty" },
        "Select a file to preview it. Pipeline outputs land under ",
        h("code", null, "youtube/{date}/recommended/"),
        " — concepts, scripts, and generated assets.");
    } else if (!file) {
      preview = h("div", { className: "yti-empty" }, "Loading…");
    } else if (file.ok === false) {
      preview = h("div", { className: "yti-empty yti-error" }, file.error || "Could not read file.");
    } else if (sel.ext === ".pdf") {
      preview = pdfUrl
        ? h("object", { data: pdfUrl, type: "application/pdf", className: "yti-pdf" },
            h("a", { href: pdfUrl, download: sel.name }, "Download " + sel.name))
        : h("div", { className: "yti-empty" }, "Loading PDF…");
    } else if (file.kind === "binary" && (file.mimeType || "").indexOf("image/") === 0) {
      preview = h("img", {
        className: "yti-artifact-img",
        src: "data:" + file.mimeType + ";base64," + file.base64,
        alt: sel.name,
      });
    } else if (file.kind === "text" && sel.ext === ".md") {
      // Markdown previews rendered by default (paperclip deliverables parity)
      preview = editing != null
        ? h("textarea", {
            className: "yti-md-editor", value: editing,
            onChange: function (e) { setEditing(e.target.value); },
          })
        : h("div", { className: "yti-md yti-md-rendered" }, renderMarkdown(file.text));
    } else if (file.kind === "text") {
      preview = editing != null
        ? h("textarea", {
            className: "yti-md-editor", value: editing,
            onChange: function (e) { setEditing(e.target.value); },
          })
        : h("pre", { className: "yti-pre" }, file.text);
    } else {
      preview = h("div", { className: "yti-empty" }, "No preview for " + sel.ext + " files.");
    }

    var canEdit = sel && file && file.kind === "text" &&
      [".md", ".txt", ".json", ".yml", ".yaml", ".csv"].indexOf(sel.ext) >= 0;
    var isScript = sel && sel.ext === ".md" && /script/i.test(sel.name);
    var prod = sel && produce[sel.relPath];
    var prodOpen = !!prod && prod.status === "open";
    var prodChat = prod && prod.sessionId
      ? "/chat?resume=" + encodeURIComponent(prod.sessionId) : null;
    var prodTask = prod && prod.taskId
      ? "/kanban?task=" + encodeURIComponent(prod.taskId) : null;
    var iter = sel && iterate[sel.relPath];
    var iterOpen = !!iter && iter.status === "open";
    var iterChat = iterOpen && iter.sessionId
      ? "/chat?resume=" + encodeURIComponent(iter.sessionId) : null;
    var topicOpen = anyOpen(topicStates);
    var presentOpen = anyOpen(presentStates);
    var isAssetImage = sel && /\.(jpg|jpeg|png|webp)$/i.test(sel.name) &&
      sel.relPath.indexOf("/assets/") !== -1;
    var isDeckPdf = sel && sel.ext === ".pdf";
    var regen = sel && regenStates[sel.relPath];
    var regenOpen = !!regen && regen.status === "open";
    var regenChat = regenOpen && regen.sessionId
      ? "/chat?resume=" + encodeURIComponent(regen.sessionId) : null;

    // Preview header (paperclip deliverables parity): path · mtime · actions
    var previewHead = sel ? h("div", { className: "yti-preview-head" },
      h("code", { className: "yti-preview-path" }, sel.relPath),
      h("span", { className: "yti-preview-actions" },
        (file && file.mtime) ? h("span", { className: "yti-mtime" }, formatMtime(file.mtime)) : null,
        isScript ? h(Button, {
          size: "sm",
          disabled: prodOpen,
          title: prodOpen
            ? "Producing — images, thumbnails, and PDF are being generated"
            : "Generate a full image set + thumbnails + PDF. Re-producing makes a NEW numbered set (assets/2, 3…) with its own N.-prefixed PDF — earlier sets are kept",
          onClick: produceScript,
          className: "yti-produce-btn" + (prodOpen ? " yti-busy" : ""),
        }, prodOpen ? "Producing…" : "Produce 🎥") : null,
        isScript ? h(Button, {
          size: "sm",
          variant: "outline",
          className: iterOpen ? "yti-busy" : undefined,
          disabled: iterOpen,
          title: iterOpen
            ? "Iterating — the script is being rewritten from your steering"
            : "Rewrite this script from its concept doc, steered by your input",
          onClick: function () { setSteering(""); setIterModal(true); },
        }, iterOpen ? "Iterating…" : "Iterate ↻") : null,
        isAssetImage ? h(Button, {
          size: "sm",
          className: regenOpen ? "yti-busy" : undefined,
          disabled: regenOpen,
          title: regenOpen
            ? "Regenerating — a fixed version of this image is being generated"
            : "Regenerate JUST this image with your feedback (e.g. a misspelling), then rebuild the deck PDF — no other image is touched",
          onClick: function () { setRegenFeedback(""); setRegenModal(true); },
        }, regenOpen ? "Regenerating…" : "Regenerate ↻") : null,
        isDeckPdf ? h(Button, {
          size: "sm",
          variant: "outline",
          className: regenOpen ? "yti-busy" : undefined,
          disabled: regenOpen,
          title: regenOpen
            ? "Rebuilding — the PDF is being reassembled"
            : "Reassemble this PDF from the newest version of every image (use after regenerating an image)",
          onClick: function () { regenerateSelected(""); },
        }, regenOpen ? "Rebuilding…" : "Rebuild PDF") : null,
        regenChat ? h("a", { className: "yti-gen-link", href: regenChat,
          onClick: function (e) { e.preventDefault(); window.location.assign(regenChat); } }, "chat ↗") : null,
        iterChat ? h("a", { className: "yti-gen-link", href: iterChat,
          onClick: function (e) { e.preventDefault(); window.location.assign(iterChat); } }, "chat ↗") : null,
        prodChat ? h("a", { className: "yti-gen-link", href: prodChat,
          onClick: function (e) { e.preventDefault(); window.location.assign(prodChat); } }, "chat ↗") : null,
        prodTask ? h("a", { className: "yti-gen-link", href: prodTask,
          onClick: function (e) { e.preventDefault(); window.location.assign(prodTask); } }, "task ↗") : null,
        canEdit ? (editing != null
          ? [h(Button, { key: "save", size: "sm", disabled: busy, onClick: save }, busy ? "Saving…" : "Save"),
             h(Button, { key: "cancel", size: "sm", variant: "outline",
               onClick: function () { setEditing(null); } }, "Cancel")]
          : h(Button, { size: "sm", variant: "outline",
              onClick: function () { setEditing(file && file.text || ""); } }, "Edit")) : null,
        (file && file.kind === "text")
          ? h(Button, { size: "sm", variant: "outline", onClick: copyMarkdown },
              copied === "md" ? "Copied!" : "Copy Markdown") : null,
        (file && file.kind === "text" && sel.ext === ".md")
          ? h(Button, { size: "sm", variant: "outline", onClick: copyRich },
              copied === "rich" ? "Copied!" : "Copy Rich") : null
      )
    ) : null;

    // Recursive sort: directories first, then names — descending by default
    // so date-named folders (YYYY-MM-DD) put the newest work on top.
    function sortTree(nodes) {
      var copy = nodes.slice().map(function (n) {
        return n.children ? Object.assign({}, n, { children: sortTree(n.children) }) : n;
      });
      copy.sort(function (a, b) {
        if ((a.kind === "dir") !== (b.kind === "dir")) return a.kind === "dir" ? -1 : 1;
        var cmp = a.name.toLowerCase() < b.name.toLowerCase() ? -1
          : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0;
        return sortDesc && a.kind === "dir" && b.kind === "dir" ? -cmp : cmp;
      });
      return copy;
    }

    var IMG_EXTS = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
    // Ancestor chains (date folder downward) for every produce run.
    var prodDirs = (function () {
      var producing = {}, produced = {};
      Object.keys(produce || {}).forEach(function (rel) {
        var st = (produce[rel] || {}).status;
        if (st !== "open" && st !== "done") return;   // stale never decorates
        var parts = rel.split("/");
        for (var i = 2; i < parts.length; i++) {
          var dir = parts.slice(0, i).join("/");
          if (st === "open") producing[dir] = 1;
          else produced[dir] = 1;
        }
      });
      // EVERY background action spins the tree, not just Produce. Decorate
      // every prefix INCLUDING the target itself, so the nearest EXISTING
      // ancestor spins even before a worker creates the folder.
      function spinPath(rel) {
        var parts = rel.split("/");
        for (var i = 2; i <= parts.length; i++) {
          producing[parts.slice(0, i).join("/")] = 1;
        }
      }
      // Outline->presentation expansions: keys are "{date}/{slug}",
      // deck lands at youtube/{date}/presentations/{slug}.
      Object.keys(presentStates || {}).forEach(function (key) {
        if ((presentStates[key] || {}).status !== "open") return;
        var bits = key.split("/");
        if (bits.length >= 2)
          spinPath("youtube/" + bits[0] + "/presentations/" +
                   bits.slice(1).join("/"));
      });
      // Topic generation (Generate ✨): keys are "{date}/{slug}",
      // scripts land at youtube/{date}/recommended/{slug}.
      Object.keys(topicStates || {}).forEach(function (key) {
        if ((topicStates[key] || {}).status !== "open") return;
        var bits = key.split("/");
        if (bits.length >= 2)
          spinPath("youtube/" + bits[0] + "/recommended/" +
                   bits.slice(1).join("/"));
      });
      // Iterate ↻: keys are the script's own relPath — spin its folders.
      Object.keys(iterate || {}).forEach(function (rel) {
        if ((iterate[rel] || {}).status !== "open") return;
        spinPath(rel.split("/").slice(0, -1).join("/"));
      });
      // Regenerate ↻ / Rebuild PDF: keys are the image/pdf relPath.
      Object.keys(regenStates || {}).forEach(function (rel) {
        if ((regenStates[rel] || {}).status !== "open") return;
        spinPath(rel.split("/").slice(0, -1).join("/"));
      });
      // 3 More 🔁 (pipeline): no per-run path — spin today's recommended
      // folder (browser-local AND UTC date, they can differ near midnight).
      if (pipelineRunning) {
        var d = new Date();
        var pad = function (n) { return (n < 10 ? "0" : "") + n; };
        var localDay = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
          pad(d.getDate());
        var utcDay = d.toISOString().slice(0, 10);
        spinPath("youtube/" + localDay + "/recommended");
        if (utcDay !== localDay) spinPath("youtube/" + utcDay + "/recommended");
      }
      return { producing: producing, produced: produced };
    })();

    function subtreeHasProduced(n) {
      if (n.kind === "file")
        return !!(produce[n.relPath] && produce[n.relPath].status === "done");
      return (n.children || []).some(subtreeHasProduced);
    }
    // "Produced": ancestor chain back to the date folders; once a folder
    // itself holds the produced script (directly or via a scripts/ child),
    // show that entire folder untouched — mirrors the With Images rules.
    function pruneToProduced(nodes) {
      var out = [];
      nodes.forEach(function (n) {
        if (n.kind !== "dir") return;
        if (!subtreeHasProduced(n)) return;
        var direct = (n.children || []).some(function (c) {
          return c.kind === "file" && produce[c.relPath] &&
            produce[c.relPath].status === "done";
        });
        var scriptsChild = (n.children || []).some(function (c) {
          return c.kind === "dir" && c.name === "scripts" && subtreeHasProduced(c);
        });
        if (direct || scriptsChild) out.push(n);
        else out.push(Object.assign({}, n, { children: pruneToProduced(n.children || []) }));
      });
      return out;
    }

    function subtreeHasImages(n) {
      if (n.kind === "file") return IMG_EXTS.indexOf(n.ext) !== -1;
      return (n.children || []).some(subtreeHasImages);
    }
    // "With Images": keep the ancestor chain back to the date folders, and
    // once a folder ITSELF holds the images (an assets/ child or direct
    // image files), show that entire folder untouched.
    function pruneToImages(nodes) {
      var out = [];
      nodes.forEach(function (n) {
        if (n.kind !== "dir") return;
        if (!subtreeHasImages(n)) return;
        var directImages = (n.children || []).some(function (c) {
          return c.kind === "file" && IMG_EXTS.indexOf(c.ext) !== -1;
        });
        var imageChildDir = (n.children || []).some(function (c) {
          return c.kind === "dir" && c.name === "assets" && subtreeHasImages(c);
        });
        if (directImages || imageChildDir) {
          out.push(n); // whole folder, contents intact
        } else {
          out.push(Object.assign({}, n, { children: pruneToImages(n.children || []) }));
        }
      });
      return out;
    }
    var query = q.trim().toLowerCase();
    function filterByQuery(nodes) {
      var out = [];
      nodes.forEach(function (n) {
        var nameHit = n.name.toLowerCase().indexOf(query) !== -1;
        if (n.kind === "file") {
          if (nameHit && (!withImages || IMG_EXTS.indexOf(n.ext) !== -1)) out.push(n);
          return;
        }
        if (nameHit) { out.push(n); return; } // folder-name match: whole folder
        var kids = filterByQuery(n.children || []);
        if (kids.length) out.push(Object.assign({}, n, { children: kids }));
      });
      return out;
    }
    var filteredTree = tree
      ? (function () {
          var t = withProduced ? pruneToProduced(tree) : tree;
          if (withImages && !query) t = pruneToImages(t);
          if (query) t = filterByQuery(withImages ? pruneToImages(t) : t);
          return t;
        })()
      : null;

    return h("div", { className: "yti-artifacts" },
      h("h1", { className: "yti-title yti-title-block" }, "Artifacts"),
      h("div", { className: "yti-artifacts-head", style: { alignItems: "flex-start" } },
        // left column: title with the style controls tucked beneath it —
        // the selector lives in the header whitespace, not its own row
        h("div", { style: { display: "flex", flexDirection: "column",
                            gap: 10, minWidth: 0 } },
          h("h2", { style: { margin: 0 } }, "Workspace Deliverables"),
          h("div", { style: { display: "flex", alignItems: "center", gap: 10,
                              flexWrap: "wrap", fontSize: 13 } },
            h("span", { style: { opacity: .75 } }, "Image style:"),
            h("select", {
              value: styleCat.selected,
              title: "Every generated slide/beat image anchors to this style (image-to-image). Applies to Produce, Regenerate, and future runs until changed.",
              style: { fontSize: 13, padding: "3px 6px", borderRadius: 6,
                       background: "transparent", color: "inherit",
                       border: "1px solid color-mix(in srgb, currentColor 25%, transparent)" },
              onChange: function (e) { selectStyle(e.target.value); },
            }, (styleCat.styles || []).map(function (s) {
              return h("option", { key: s.id, value: s.id,
                                   style: { color: "#111" } }, s.name);
            })),
            h("label", { style: { cursor: "pointer", textDecoration: "underline", opacity: .8 },
                         title: "Upload your own example image — it becomes the style anchor for every generation until you change it" },
              "Upload your own…",
              h("input", { type: "file", accept: "image/*", style: { display: "none" },
                           onChange: function (e) { uploadStyle(e.target); } }))
          )
        ),
        // preview fills the remaining header whitespace, top-aligned and big;
        // shrinks at narrow widths; click to inspect full-size
        stylePrev ? h("img", {
          src: stylePrev,
          alt: "selected style preview",
          title: "The selected style baseline — every generated image anchors to this look. Click to enlarge.",
          onClick: function () { setStyleZoom(true); },
          style: { maxHeight: 192, maxWidth: 460, minWidth: 0,
                   flex: "0 1 auto", objectFit: "contain", cursor: "zoom-in",
                   marginLeft: 12, marginRight: "auto", marginTop: -56,
                   borderRadius: 8,
                   border: "1px solid color-mix(in srgb, currentColor 25%, transparent)" },
        }) : null,
        h("div", { className: "yti-actions" },
          pipeline.available ? h(Button, {
            size: "sm",
            className: pipelineRunning ? "yti-busy" : undefined,
            disabled: pipelineRunning,
            title: pipelineRunning
              ? "Running — the content pipeline is finding gaps and writing 3 new topic script sets"
              : "Run the twice-daily content pipeline now: find fresh gaps across tracked videos and write 3 new topics x 3 scripts into today's recommended folder",
            onClick: runPipeline,
          }, pipelineRunning ? "Running…" : "3 More 🔁") : null,
          h(Button, {
            size: "sm",
            className: topicOpen ? "yti-busy" : undefined,
            disabled: topicOpen,
            title: topicOpen
              ? "Generating — a topic script set is being written into today's recommended folder"
              : "Generate a new 3-script set (standard, hot take, contrarian) on a topic of your choice, grounded in the insights database",
            onClick: function () { setTopicModal(true); },
          }, topicOpen ? "Generating…" : "Generate ✨"),
          h(Button, {
            size: "sm",
            className: presentOpen ? "yti-busy" : undefined,
            disabled: presentOpen,
            title: presentOpen
              ? "Expanding — an outline is being turned into a presentation script"
              : "Turn a talk outline into a presentation: one whiteboard slide per bullet. Expands the outline into a reviewable script; Produce then makes the images, 6 thumbnails, and PDF",
            onClick: function () { setPresentModal(true); },
          }, presentOpen ? "Expanding…" : "Outline → Presentation 🖼️"),
          h(Button, { size: "sm", variant: "outline", onClick: loadTree }, "Refresh")
        )
      ),
      iterModal && sel ? h(YtiModal, { onClose: function () { setIterModal(false); } },
        h("h3", null, "Iterate on this script"),
        h("p", { className: "yti-modal-sub" },
          "Rewrites ", h("code", null, sel.name), " in place from its concept doc — ",
          "your steering below tells the writer what to do better."),
        h("label", null, "Steering"),
        h("textarea", {
          value: steering,
          autoFocus: true,
          placeholder: "e.g. Beats 3 and 4 are thin — expand them with concrete examples. Make the hook harder-hitting. Drop the section on pricing.",
          onChange: function (e) { setSteering(e.target.value); },
        }),
        h("div", { className: "yti-modal-actions" },
          h(Button, { size: "sm", variant: "outline",
            onClick: function () { setIterModal(false); } }, "Cancel"),
          h(Button, { size: "sm", onClick: iterateScript }, "Iterate ↻"))
      ) : null,
      styleZoom && stylePrev ? h(YtiModal, { wide: true, onClose: function () { setStyleZoom(false); } },
        h("h3", null, "Selected image style"),
        h("p", { className: "yti-modal-sub" },
          "Every generated slide and beat image anchors to this look."),
        h("img", {
          src: stylePrev,
          alt: "selected style, full size",
          style: { display: "block", maxWidth: "100%", maxHeight: "74vh",
                   objectFit: "contain", borderRadius: 8 },
        }),
        h("div", { className: "yti-modal-actions" },
          h(Button, { size: "sm", variant: "outline",
            onClick: function () { setStyleZoom(false); } }, "Close"))
      ) : null,
      regenModal && sel ? h(YtiModal, { onClose: function () { setRegenModal(false); } },
        h("h3", null, "Regenerate this image"),
        h("p", { className: "yti-modal-sub" },
          "Regenerates ", h("code", null, sel.name), " only — every other ",
          "image is untouched — then rebuilds the deck PDF with the fix. ",
          "The old file is kept alongside."),
        h("label", null, "What's wrong? (your feedback steers the fix)"),
        h("textarea", {
          value: regenFeedback,
          autoFocus: true,
          placeholder: "e.g. \"AUTHENTICATION\" is misspelled as \"AUTHENTCATION\" on the left sticky note — fix the spelling, keep everything else the same.",
          onChange: function (e) { setRegenFeedback(e.target.value); },
        }),
        h("div", { className: "yti-modal-actions" },
          h(Button, { size: "sm", variant: "outline",
            onClick: function () { setRegenModal(false); } }, "Cancel"),
          h(Button, { size: "sm",
            onClick: function () { regenerateSelected(regenFeedback); } },
            "Regenerate ↻"))
      ) : null,
      presentModal ? h(YtiModal, { onClose: function () { setPresentModal(false); } },
        h("h3", null, "Outline → Presentation"),
        h("p", { className: "yti-modal-sub" },
          "Expands your outline into a presentation script — one whiteboard ",
          "slide per bullet, keeping your order exactly — into today's ",
          h("code", null, "presentations/"), " folder. Review the script, ",
          "then press Produce on it for images, 6 thumbnail options, and ",
          "the PDF."),
        h("label", null, "Topic / hook"),
        h("input", {
          value: presTopic,
          autoFocus: true,
          placeholder: "e.g. I audit my clients' vibe-coded apps — here's what I keep finding",
          onChange: function (e) { setPresTopic(e.target.value); },
        }),
        h("label", null, "Outline"),
        h("textarea", {
          value: presOutline,
          rows: 12,
          placeholder: "1. The problem\n    1. First point\n    2. Second point\n2. Why it happens\n    1. ...\n3. The solution\n    1. ...",
          onChange: function (e) { setPresOutline(e.target.value); },
        }),
        h("div", { className: "yti-modal-actions" },
          h(Button, { size: "sm", variant: "outline",
            onClick: function () { setPresentModal(false); } }, "Cancel"),
          h(Button, { size: "sm", disabled: !presTopic.trim() || !presOutline.trim(),
            onClick: createPresentation }, "Expand →"))
      ) : null,
      topicModal ? h(YtiModal, { onClose: function () { setTopicModal(false); } },
        h("h3", null, "Generate scripts on a topic"),
        h("p", { className: "yti-modal-sub" },
          "Writes a full set — standard, hot take, and contrarian — into today's ",
          h("code", null, "recommended/"), " folder, grounded in the insights ",
          "database and your company context."),
        h("label", null, "Topic"),
        h("input", {
          value: topic,
          autoFocus: true,
          placeholder: "e.g. Securing AI coding agents before they touch production",
          onChange: function (e) { setTopic(e.target.value); },
        }),
        h("label", null, "Context / guidance (optional)"),
        h("textarea", {
          value: topicCtx,
          placeholder: "Who it's for, the angle you want, anything to avoid…",
          onChange: function (e) { setTopicCtx(e.target.value); },
        }),
        h("div", { className: "yti-modal-actions" },
          h(Button, { size: "sm", variant: "outline",
            onClick: function () { setTopicModal(false); } }, "Cancel"),
          h(Button, { size: "sm", disabled: !topic.trim(),
            onClick: generateTopic }, "Generate ✨"))
      ) : null,
      h("div", { className: "yti-artifacts-body" },
        h("div", { className: "yti-tree" },
          h("div", { className: "yti-tree-tools" },
            h("input", {
              className: "yti-artifact-search",
              placeholder: "Search filenames…",
              value: q,
              onChange: function (e) { setQ(e.target.value); },
            }),
            h("div", { className: "yti-tree-chips" },
              h("button", {
                className: "yti-filter-chip" + (withImages ? " yti-filter-chip-on" : ""),
                onClick: function () { setWithImages(!withImages); },
                title: "Show only image files (generated assets and thumbnails)",
              }, "With Images"),
              h("button", {
                className: "yti-filter-chip" + (withProduced ? " yti-filter-chip-on" : ""),
                onClick: function () { setWithProduced(!withProduced); },
                title: "Show only folders containing a produced script",
              }, "Produced"),
              h("button", {
                className: "yti-filter-chip",
                onClick: function () { setSortDesc(!sortDesc); },
                title: "Toggle directory sort order",
              }, sortDesc ? "Newest first ↓" : "Oldest first ↑"))
          ),
          tree == null ? h("div", { className: "yti-empty" }, "Loading…")
          : query && filteredTree.length === 0
            ? h("div", { className: "yti-empty" }, "No files match \u201C" + q + "\u201D.")
          : tree.length === 0
            ? h("div", { className: "yti-empty" },
                "No deliverables yet. The scheduled pipeline writes concepts and ",
                "scripts to ", h("code", null, "youtube/{date}/recommended/"), ".")
            : sortTree(filteredTree).map(function (n) {
                return h(TreeEntry, { key: (query ? "q-" + query + "-" : "") + n.relPath,
                  node: n, depth: 0, forceOpen: !!query, query: query,
                  prod: prodDirs,
                  selected: sel && sel.relPath,
                  onSelect: function (node) { setSel(node); } });
              })
        ),
        h("div", { className: "yti-preview" },
          previewHead,
          preview)
      )
    );
  }

  // -------------------------------------------------------------------------
  // Shared followed-channel manager (Trends + Research use the same list and
  // the same /channels endpoints, so the two tabs can never drift).
  // -------------------------------------------------------------------------
  function ChannelManager(props) {
    const [channels, setChannels] = useState(props.channels || []);
    const [newChannel, setNewChannel] = useState("");
    const [open, setOpen] = useState(!!props.defaultOpen);
    const [err, setErr] = useState(null);
    const publish = function (list) {
      setChannels(list);
      if (props.onChannels) props.onChannels(list);
    };
    useEffect(function () {
      api("/channels").then(function (d) { publish((d && d.channels) || []); }).catch(function () {});
    }, []);   // eslint-disable-line
    const add = function (e) {
      e.preventDefault();
      const handle = newChannel.trim();
      if (!handle) return;
      api("/channels", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: handle }) })
        .then(function (d) { publish((d && d.channels) || []); setNewChannel(""); setErr(null); })
        .catch(function (e2) { setErr(String(e2)); });
    };
    const remove = function (handle) {
      api("/channels/" + encodeURIComponent(handle), { method: "DELETE" })
        .then(function (d) { publish((d && d.channels) || []); }).catch(function () {});
    };
    return h("div", { className: "yti-card yti-channels-card" },
      h("div", { className: "yti-channels-toggle", onClick: function () { setOpen(!open); } },
        (open ? "▼" : "▶") + " " + (props.title || "Tracked Channels") + " (" + channels.length + ")"),
      open ? h("div", { className: "yti-channels-body" },
        props.hint ? h("div", { className: "yti-subtle", style: { marginBottom: 8 } }, props.hint) : null,
        h("div", { className: "yti-chip-row" },
          channels.map(function (ch) {
            return h("span", { key: ch, className: "yti-chip yti-chip-channel" }, ch,
              h("span", { className: "yti-chip-remove", title: "Stop following " + ch,
                onClick: function () { remove(ch); } }, "✕"));
          })),
        h("form", { className: "yti-add-channel", onSubmit: add },
          h(Input, { value: newChannel, placeholder: "@ChannelHandle",
            onChange: function (e) { setNewChannel(e.target.value); } }),
          h(Button, { size: "sm", type: "submit" }, "Add")),
        err ? h("div", { className: "yti-notice yti-notice-error" }, err) : null
      ) : null);
  }

  // -------------------------------------------------------------------------
  // Research tab — niche crawl, outliers, formats, gap report, teardown
  // -------------------------------------------------------------------------
  const CLASS_COLOR = { strong_hit: "#22c55e", hit: "#84cc16", normal: "#9ca3af", under: "#ef4444", immature: "#6b7280" };
  const RS_PANELS = [["setup", "Setup"], ["run", "Run"], ["outliers", "Outliers"], ["supply", "Supply / Demand"], ["formats", "Formats"],
    ["gaps", "Gap Report"], ["teardown", "Teardown"], ["budget", "Budget & Reports"]];

  function fmtMult(x) { return x == null ? "—" : Number(x).toFixed(2) + "×"; }
  function fmtPct(x) { return x == null ? "—" : Math.round(x) + ""; }
  function fmtDate(iso) { return iso ? String(iso).slice(0, 10) : "—"; }
  function post(path, body) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  // -------------------------------------------------------------------------
  // Glossary: every symbol, acronym and piece of jargon on the Research tab
  // (and the Trends table) explained in full, with an example. Shown on hover
  // and on keyboard focus by <Tip>.
  // -------------------------------------------------------------------------
  const RS_TIPS = {
    // ---- outlier register columns
    title: "The video's title as YouTube shows it. Click it to open the video in a new tab.",
    channel: "The channel that published the video, shown as its @handle when known.\nExample: @dankoetalks.",
    niche: "Which of your configured niches (Setup panel) the crawl found this video under. A niche is a group of viewers who want the same outcome — not a subject.\nExample: 'ai-security' is your target niche; 'one-person-business' is an adjacent one.",
    cls: "Class — the verdict on the video compared with its own channel's normal, using × proj.\n• strong_hit: 5× normal or more\n• hit: 3× up to 5×\n• normal: between 0.4× and 3×\n• under: 0.4× or less (a flop — kept on purpose, it shows what fails)\n• immature: cannot be judged yet (fewer than 6 earlier videos on the channel, or length unknown)\nExample: a video at 6.2× on a channel that normally gets 4,000 views is a strong_hit.\nThresholds are the defaults; change them under Setup → Advanced.",
    xproj: "× proj — projected multiple. The video's views divided by its channel's normal views. 'Normal' is the median of the 20 long-form videos that channel published just before this one.\nFor a video younger than 28 days, views are first projected to what they should be at day 28 (day 1 ≈ 15% of day-28 views, day 3 ≈ 35%, day 7 ≈ 55%, day 14 ≈ 75%), so young videos are not unfairly marked down.\nExample: a 7-day-old video has 11,000 views → projected 20,000. The channel's median is 4,000 → × proj = 5.00×.",
    xraw: "× raw — raw multiple. Views divided by the channel's normal views, with NO adjustment for the video's age.\nExample: 12,000 views on a channel whose previous 20 videos have a median of 4,000 → 3.00×.\nFor videos older than 28 days this equals × proj.",
    z: "z — robust z-score. How far above its channel's normal the video sits, counted in 'typical spreads' of that channel. It is measured on the logarithm of views using the median and the MAD (median absolute deviation) instead of the average and standard deviation, so one earlier viral video cannot distort it.\nExample: z = 3.5 means 3.5 typical spreads above the channel's usual level; anything above 3 is rare for that channel.\n'—' means the channel's earlier videos all have almost identical views, so no spread can be measured.",
    views: "Views — the view count at the last time this video was observed.\nExample: 194.0K = about 194,000 views.\nIf the flag 'views≈' is shown, the number came from rounded text such as '194K views' rather than an exact count.",
    age: "Age d — age in days: how long ago the video was published, as of the last Score run.\nExample: 134 = published about four and a half months ago.",
    weight: "Weight — signal weight: how much this result still counts as evidence today. It halves every 'half-life' of the video's niche (set per niche on the Setup panel).\nFormula: 0.5 ^ (age ÷ half-life).\nExample: with a 180-day half-life, a brand-new video weighs 1.00, a 180-day-old one 0.50 and a 360-day-old one 0.25.",
    vs: "VS % — viewer-satisfaction percentile, from 0 to 100, compared with other long-form videos in the same niche. It blends three signals, each turned into a percentile rank: like rate (25%), positive-comment rate (55%) and replies per comment (20%).\nExample: 84 means this video's engagement is more positive than 84% of long-form videos in its niche.\n'—' means likes and comments have not been collected yet — run Enrich and Comments on the Run panel.",
    flags: "Flags — short notes about how trustworthy each number is and what to watch. Hover any flag in a row for its exact meaning.",
    subs: "Subs — the channel's subscriber count. A leading '~' means it is rounded (read from text such as '712K subscribers'); no mark means the exact figure from Apify. '?' means it has not been looked up yet — run Channel sizes on the Run panel.\nExample: ~61.2K = about 61,200 subscribers.\nThe default view only shows channels inside the size band from Setup (1,000 to 100,000 by default) so the lessons come from channels comparable to yours.",
    sizefilter: "Channel size — which channels' videos to show, by subscriber count. 'Comparable' uses the band from Setup → Advanced (crawl.min_subscribers to crawl.max_subscribers, 1,000 to 100,000 by default). Channels you follow are always shown, and so are channels whose size is not known yet.\nExample: choose 'Any size' to bring back a 2.6M-subscriber security channel such as Computerphile.",
    scope: "In-niche — a video counts as part of a niche when its own title mentions one of the niche's terms, or its channel is on-topic (at least 20% of the channel's long-form titles mention those terms), or you follow the channel. Videos on a channel judged off-topic are tagged 'out of niche' and hidden here; they are never deleted.\nExample: a news network that published one video about AI agents keeps that one video in 'ai-security'; its 80 other news clips are tagged out.",
    topicterms: "Topic terms — extra subject words used only to decide what belongs to the niche. Add the vocabulary your niche's channels actually use in titles.\nExample: for a security niche: hacking, exploit, malware, vulnerability. A general security channel then stays in the niche even though it rarely uses your exact seed phrases.",
    // ---- flags
    "flag:size?": "size? — this channel's subscriber count is not known yet, so the channel-size filter cannot judge it and lets it through. Run Channel sizes on the Run panel (1 TranscriptAPI credit per channel).",
    "flag:views≈": "views≈ — the view count is approximate. It was read from rounded text.\nExample: '3.4M views' could be anything from 3,350,000 to 3,449,999.\nRun Enrich on the Run panel to replace it with the exact count.",
    "flag:date≈": "date≈ — the publish date is approximate. It came from relative text such as '2 months ago' (meaning two to three months), or was estimated from how often the channel uploads.\nExample: seen as '2 months ago' on Oct 6 → published some time between early July and early August.",
    "flag:raw": "raw — the publish date is too vague (coarser than a week) to project views by age, so this video is judged on its raw multiple only.\nExample: a video dated only '1 year ago' is scored on × raw.",
    "flag:exact": "exact — views, likes, comments, duration and publish time are exact figures fetched from Apify (precision tier 2), not rounded search-result text.",
    "flag:⚠ paid?": "⚠ paid? — suspected paid traffic. Views are in the top 10% of the niche while the comment rate is in the bottom 10%, the usual signature of ad-driven views.\nExample: 2,000,000 views with 40 comments.\nSuch videos are left out of format statistics, because an ad budget says nothing about the title or format.",
    "flag:🚀 breakout": "🚀 breakout — breakout watch. The video is 14 days old or younger and still below its channel's normal (under 1×), but its positive-comment rate is in the top 20% of the niche. Videos like this often take off one to two weeks later.\nExample: a 5-day-old video at 0.6× whose comments are full of 'this is going to blow up'.",
    "flag:fade": "fade — fade watch. The video is 14 days old or younger with views in the top 20% of the niche but a comment rate in the bottom 20%. Lots of views that nobody talks about tend to tail off quickly.",
    "flag:projected": "projected — the video is younger than 28 days, so its multiple uses views projected forward to day 28 rather than today's count.\nExample: 11,000 views on day 7 is treated as 20,000 by day 28.",
    "flag:paid?": "paid? — suspected paid traffic: views in the top 10% of the niche with a comment rate in the bottom 10%.\nExample: 2,000,000 views with 40 comments.",
    // ---- classes
    "class:strong_hit": "strong_hit — the video reached 5× its channel's normal views or more (using × proj).\nExample: 22,000 views on a channel whose median is 4,000 = 5.5×.",
    "class:hit": "hit — the video reached at least 3× but less than 5× its channel's normal views.\nExample: 14,000 views on a channel whose median is 4,000 = 3.5×.",
    "class:normal": "normal — between 0.4× and 3× the channel's normal views: an ordinary result for that channel.",
    "class:under": "under — underperformer: 0.4× the channel's normal views or less. Kept on purpose, because failures show when a title format does NOT work.\nExample: 1,200 views on a channel whose median is 4,000 = 0.3×.",
    "class:immature": "immature — cannot be judged yet. Either the channel has fewer than 6 earlier long-form videos to form a baseline, or the video's length is unknown.",
    // ---- supply / demand
    published: "Published — how long ago the video went live. A leading '~' means the date is approximate.\nExample: '~ 2 mo ago' came from text like '2 months ago'.",
    xnormal: "× normal — the video's views divided by its channel's normal views (the median of that channel's previous 20 long-form videos). For videos under 28 days old, views are projected to day 28 first.\nExample: 20,000 views on a channel that normally gets 4,000 = 5.00×.",
    fresh: "Freshness — how much of the demand signal is left after time has passed. Time stands in for supply: the longer an idea has been out, the more videos about it exist. Freshness halves every half-life of the video's niche.\nExample: with a 180-day half-life, a 60-day-old video keeps 79% and a 180-day-old one keeps 50%.",
    xadj: "× time-adjusted — demand discounted for supply: × normal multiplied by Freshness. This is the ranking of the table.\nExample: a video at 10.00× that is 180 days old in a niche with a 180-day half-life scores 10 × 50% = 5.00×, the same as a brand-new video at 5.00×.",
    // ---- formats
    format: "Format — a reusable title frame, shown with its slots.\nExample: 'the new rules of {subject}' matches 'The New Rules of SaaS Pricing' and 'The New Rules of Fat Loss'.\nClick a row to see its strongest examples and what separates its hits from its misses.",
    kind: "Kind — where the format came from.\n• seeded: one of the known frames shipped with the plugin, matched by pattern.\n• mined: discovered in your data — a run of 3 to 7 title words found in at least 4 videos, on at least 3 channels, across at least 2 niches.",
    wilson: "Wilson LB — the Wilson score lower bound (95% confidence) of the format's hit rate: a cautious estimate of how often the format really produces a hit, which punishes small samples. Formats are ranked by it.\nExample: 2 hits out of 2 has a 100% hit rate but a Wilson LB of only 0.34; 40 hits out of 60 has a 67% hit rate and a Wilson LB of 0.54 — the second is the safer bet and ranks higher.",
    hitsn: "Hits / n — how many videos using this format were hits (3× their channel's normal or more), out of n scored videos that used it.\nExample: 8 / 12 means 8 of the 12 videos with this title frame were hits. Videos that cannot be scored yet are not counted in n.",
    under: "Under — how many videos using this format flopped (0.4× their channel's normal or less). The failures matter as much as the hits.\nExample: Under = 4 next to Hits / n = 8 / 12 means a third of the attempts badly underperformed.",
    medianx: "Median × — the middle multiple of all scored videos using this format: half did better, half did worse.\nExample: 2.40× means a typical video with this frame got 2.4 times its channel's normal views.",
    whit: "Weighted hit — the hit rate with recent results counting more than old ones. Each video is weighted by its signal weight (which halves every niche half-life).\nExample: 0.70 next to an unweighted 8 / 12 (0.67) means the recent uses did slightly better than the old ones.",
    channels: "Channels — how many different channels have used this format. More channels means it is a real format rather than one creator's habit.",
    niches: "Niches — which of your niches this format has been seen in. A format proven in two or more niches is transferable.",
    targetuses: "Target uses — how many videos in YOUR target niche already use this format.\nExample: 0 for a format that is proven in two other niches is a gap: nobody in your niche has tried it yet.",
    // ---- teardown
    feature: "Feature — one measurable property of the channel's videos, compared before and after the turning point. Hover a feature name for what it measures.",
    before: "Before — the feature's value across the videos published BEFORE the channel's turning point (a median, a rate between 0 and 1, or a breakdown by category).",
    after: "After — the same feature across the videos published AFTER the turning point.",
    n: "n — sample size: how many videos the before / after values are based on.\nExample: 14/22 = 14 videos before the turning point and 22 after.",
    effect: "Effect — how big the change is, on a scale where larger means a bigger shift. For medians it is the relative change (0.50 = 50% change); for rates it is the difference in points (0.40 = from 20% to 60%); for category breakdowns it is the share of videos that moved category (1.00 = completely different mix).",
    changepoint: "Changepoint — the date where the channel's typical views shifted most sharply, with its p-value: the chance a shift this large would appear by luck if nothing had changed (tested by shuffling the videos 2,000 times).\nExample: 2026-03-14 (p=0.004) means a 0.4% chance it is a fluke. Nothing is reported when p is above 0.05.",
    lift: "Lift ratio — median views after the turning point divided by median views before it.\nExample: 3.20× means a typical video now gets 3.2 times what it did before.",
    coherence: "Coherence — how similar the channel's last 20 long-form videos are to each other in topic and wording, from 0 (unrelated) to 1 (identical), measured on titles, descriptions and the first 500 transcript words.\nExample: 0.03 is a scattered channel; 0.30 is tightly focused. A hit far from the channel's usual topics is flagged as off-topic.",
    uploads: "Uploads — how many dated long-form videos from this channel the teardown analysed.",
    computed: "Computed — when this teardown was last calculated.",
    // ---- cohort-diff feature names
    "feat:title_words": "title_words — number of words in the title (median).",
    "feat:point_count": "point_count — how many numbered points the video works through, detected in the transcript ('number one', 'step 3', 'the next one').\nExample: 25 is a dense list; 4 is a short one.",
    "feat:duration_seconds": "duration_seconds — video length in seconds (median). Example: 900 = 15 minutes.",
    "feat:promise_restated_sec": "promise_restated_sec — seconds into the video before the speaker repeats what the title promised. Lower is better.\nExample: 8 means the promise is restated within the first eight seconds.",
    "feat:filler_rate": "filler_rate — filler words ('um', 'uh', 'like', 'you know') per 100 spoken words. Very low suggests a scripted read; high suggests off-the-cuff delivery.",
    "feat:like_rate": "like_rate — likes divided by views. Example: 0.045 = 45 likes per 1,000 views.",
    "feat:comment_rate": "comment_rate — comments divided by views. Example: 0.002 = 2 comments per 1,000 views.",
    "feat:vs_percentile": "vs_percentile — viewer-satisfaction percentile (0–100) within the niche; see VS % on the Outliers panel.",
    "feat:projected_multiple": "projected_multiple — views divided by the channel's normal views, age-adjusted; see × proj on the Outliers panel.",
    "feat:title_lowercase": "title_lowercase — share of titles written entirely in lower case, a deliberate 'unpolished' look.\nExample: 0.60 = 60% of titles.",
    "feat:has_promise": "has_promise — share of videos whose first 90 seconds repeat the promise made in the title.",
    "feat:has_proof": "has_proof — share of videos whose first 90 seconds give a reason to trust the speaker: numbers, client or company names, credentials, years of experience.",
    "feat:has_plan": "has_plan — share of videos whose first 90 seconds say what will be covered ('by the end of this video…', 'first we'll…').",
    "feat:has_persona": "has_persona — share of videos whose first 90 seconds name who the video is for ('if you're a founder…').",
    "feat:mismatch_risk": "mismatch_risk — share of videos where a casual, lower-case title is paired with a scripted, polished delivery, which can feel like a bait-and-switch.",
    "feat:is_short": "is_short — share of uploads that are Shorts (180 seconds or less).",
    "feat:question_title": "question_title — share of titles phrased as a question or starting with how / why / what.",
    "feat:numeral_title": "numeral_title — share of titles containing a number. Example: '7 mistakes…'.",
    "feat:uses_seeded_format": "uses_seeded_format — share of titles that match one of the known title formats on the Formats panel.",
    "feat:lead_magnet": "lead_magnet — share of videos that end by offering something free (a template, checklist, link in the description).",
    "feat:awareness_frame": "awareness_frame — what the title leads with: the outcome viewers already want (outcome_led), the method you teach (mechanism_led), both together (bridged), or neither (unclear). Uses the outcome and mechanism terms from the Setup panel.",
    "feat:structure_class": "structure_class — the shape of the video: listicle_short (2–8 points), listicle_dense (15 or more points), narrative (one story), or walkthrough (on-screen demo).",
    "feat:delivery_class": "delivery_class — how the video is delivered: scripted (few filler words, even sentences), raw (many fillers, uneven sentences) or mixed.",
    "feat:cta_kind": "cta_kind — CTA means call to action: what the viewer is asked to do near the end.\n• give: offers something free (template, checklist)\n• take: asks for something (book a call, buy, join)\n• mixed: both\n• none: no ask",
    "feat:weekday": "weekday — which days of the week the channel publishes on, as shares.",
    "feat:uploads_per_week": "uploads_per_week — average number of long-form uploads per week.",
    "feat:gap_variance_days": "gap_variance_days — how irregular the gaps between uploads are (variance, in days squared). Lower means a steadier schedule.",
    "feat:topic_terms": "topic_terms — the title words that fell away (before) and the ones that took over (after).",
    // ---- run + budget
    planniche: "Niche — the niche this row of the estimate is for.",
    seedterms: "Seed terms — how many starting search phrases the niche has on the Setup panel.",
    searchcalls: "Search calls — keyword searches the crawl will make: seed terms × phrase variants × result pages. Each costs 1 TranscriptAPI credit.\nExample: 3 seed terms × 3 variants × 2 pages = 18 calls.",
    chancalls: "Channel calls (worst) — the most channel back-catalogue pages the crawl could fetch, 1 credit each, if no branch is cut short.",
    reccalls: "Rec calls (worst) — the most 'similar videos' searches the crawl could make (one per standout video it follows), 1 credit each.",
    creditsworst: "Credits (worst) — the upper limit on credits this niche can cost if nothing is pruned. Typical runs use 30–50% of it, and the credit cap stops the run cleanly either way.",
    run: "Run — the crawl's identifier: the date and time it started plus a short random code.\nExample: crawl-20261006-141743-a93c77.",
    status: "Status — done (finished), paused (stopped at the credit cap; press Resume to continue from where it stopped), running, or aborted (the provider reported no credits left).",
    nodes: "Nodes — how many steps the crawl took. A step is one search phrase, one video or one channel explored.",
    newvideos: "New videos — videos recorded for the first time by this run.",
    newoutliers: "New outliers — videos first seen in this run that already look like standouts (about 3× their channel's usual views on the rough search-result numbers). The Score job makes the final call.",
    credits: "Credits — units charged by the data provider. TranscriptAPI charges 1 credit per successful search, channel page or transcript; the YouTube Data API counts Google's quota units (a search is 100, a channel or video lookup 1; 10,000 free per day, reset at midnight Pacific); Apify counts one per result returned. Failed calls cost nothing.",
    stopreason: "Stop reason — why the run ended: 'exhausted' (nothing left to explore) or the budget message when it reached the credit cap.",
    provider: "Provider — the service that was called: youtube (Google's free Data API: searches, channel catalogues, channel sizes — exact numbers), transcriptapi (transcripts, free daily snapshots, and discovery when no YouTube key is set or the day's quota is spent), apify (exact numbers and comments) or anthropic (optional AI analysis).",
    endpoint: "Endpoint — the specific operation called at the provider.\nExample: 'channel/latest' is the free feed of a channel's 15 newest uploads; 'search' is a keyword search.",
    calls: "Calls — how many requests were made, including failed ones.",
    failures: "Failures — requests that returned an error or no response. They are recorded but cost no credits.",
    day: "Day — the calendar day (UTC) the calls were made.",
    // ---- stat cards + misc
    "stat:channels": "Channels — how many channels have at least one long-form video in the research data.",
    "stat:videos": "Long-form videos — videos longer than 180 seconds recorded by snapshots and crawls. Shorts are stored but kept out of this tab.",
    "stat:hits": "Hits (≥3× baseline) — long-form videos that reached at least three times their own channel's normal views. '≥' means 'greater than or equal to'; 'baseline' is the median of the channel's previous 20 long-form videos.\nExample: 14,000 views on a channel whose median is 4,000 is 3.5× and counts as a hit.",
    "stat:formats": "Formats — title frames in the library: the known ones shipped with the plugin plus the ones mined from your data.",
    "stat:snapshots": "Snapshot points — individual daily view-count readings stored so far (one per video per day). They build the history that shows how fast videos grow.",
    approx: "≈ — 'approximately'. The number or date came from rounded text on a search page (Tier 1 = the cheap discovery pass), not an exact figure.\nExample: 'views≈' on 3.4M means somewhere between 3,350,000 and 3,449,999.",
    rawword: "raw — the publish date is too vague to adjust views for the video's age, so only the unadjusted (raw) multiple is used.",
    d1: "D1 — Deliverable 1, the demand map: which subjects in each niche have proven pull, built from the title words of long-form hits.",
    d15: "D1–D5 — the five reports this engine writes.\n• D1 demand map: subjects with proven pull\n• D2 outlier register: videos that beat their channel's normal\n• D3 format library: title frames with hit and failure rates\n• D4 format gap report: frames proven elsewhere and unused in your niche\n• D5 channel teardown: when a channel took off and what changed",
    tier0: "Tier 0 — the free layer. Once a day it reads each followed channel's feed of its 15 newest uploads, which gives exact view counts and publish times at no cost. Over weeks this becomes a growth history nobody sells.",
    tier1: "Tier 1 — discovery on cheap, approximate data: keyword searches and channel pages that return rounded figures like '3.4M views' and '2 years ago'. About 1 credit per 20–100 videos.",
    tier2: "Tier 2 — precision: exact views, likes, comment counts, duration and subscriber counts from Apify, bought only for the shortlist of standout videos.",
    tier3: "Tier 3 — depth: full transcripts (to analyse structure and openings) and comment text (to gauge viewer satisfaction), again only for the shortlist.",
    wilsonnote: "Wilson 95% lower bound — a cautious estimate of a format's true hit rate that shrinks when the sample is small.\nExample: 2 hits of 2 → 0.34; 40 hits of 60 → 0.54. 'n' is the number of scored videos using the format.",
    // ---- trends table
    vph: "VPH — views per hour: total views divided by the hours since the video was published. It shows how fast a video is collecting views right now relative to its age.\nExample: 96,000 views 24 hours after publishing = 4,000 VPH.",
    momentum: "Momentum — how fast a focus video is gaining views right now and whether that pace is building or fading. The pulse re-reads exact views every 6 hours (plus the daily snapshot); the latest interval's views per hour is compared with the interval before: ↗ more than 10% faster (building), ↘ more than 10% slower (fading), → within 10% (steady). On the chart the grey dots and line behind a blue dot are those earlier readings.\nExample: ↗ 1.2K/h means the video gained about 1,200 views an hour over the last reading and is accelerating; ↘ 300/h means it is slowing down.",
    trend: "Trend — the direction of the view curve across the saved snapshots: ↗ accelerating (gaining views faster than before), → flat, ↘ decelerating (slowing down). The small line is the view count over time and 'pts' is how many snapshots it is drawn from.",
    tstatus: "Status — how far the video is through the pipeline: discovered (seen, no transcript), transcribed (transcript saved), analyzing (insight extraction queued or running), analyzed (insights saved).",
    duration: "Duration — video length as minutes:seconds, taken from its transcript. '—' means no transcript yet.",
    tpublished: "Published — how long ago the video went live. Example: '3d ago'.",
  };

  function Tip(props) {
    const text = props.text || RS_TIPS[props.k];
    if (!text) return props.children == null ? null : props.children;
    return h("span", { className: "yti-tip" + (props.right ? " yti-tip-r" : "") + (props.plain ? " yti-tip-plain" : ""),
      "data-tip": text, tabIndex: 0 }, props.children);
  }

  // Sorting: one state shape for every table. Empty values always sort last.
  function useSort(key, dir) {
    const st = useState({ key: key, dir: dir || "desc" });
    const cur = st[0];
    return { key: cur.key, dir: cur.dir, toggle: function (k, first) {
      st[1](cur.key === k ? { key: k, dir: cur.dir === "asc" ? "desc" : "asc" } : { key: k, dir: first || "desc" });
    } };
  }
  function sortRows(rows, sort, getters) {
    const g = (getters && getters[sort.key]) || function (r) { return r[sort.key]; };
    const mul = sort.dir === "asc" ? 1 : -1;
    const empty = function (v) { return v == null || v === "" || (typeof v === "number" && isNaN(v)); };
    return (rows || []).slice().sort(function (a, b) {
      const x = g(a), y = g(b);
      if (empty(x) && empty(y)) return 0;
      if (empty(x)) return 1;
      if (empty(y)) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * mul;
      return String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" }) * mul;
    });
  }
  // <Th sort k label tip cls first> — a clickable, explained column header
  function Th(props) {
    const s = props.sort, on = s.key === props.k;
    const right = /yti-right/.test(props.cls || "");
    return h("th", { className: (props.cls ? props.cls + " " : "") + "yti-sortable" + (on ? " yti-sorted" : ""),
        "aria-sort": on ? (s.dir === "asc" ? "ascending" : "descending") : "none",
        title: undefined, onClick: function () { s.toggle(props.k, props.first); } },
      h(Tip, { k: props.tip, right: right }, props.label),
      h("span", { className: "yti-sort-glyph", "aria-hidden": "true" }, on ? (s.dir === "asc" ? " ▲" : " ▼") : " ↕"));
  }
  function FlagChips(props) {
    const flags = props.flags || [];
    if (!flags.length) return null;
    return flags.map(function (f, i) {
      return h(React.Fragment, { key: f }, i ? " " : null, h(Tip, { k: "flag:" + f, right: true }, f));
    });
  }

  function ClassBadge(props) {
    const c = props.cls || "immature";
    return h(Tip, { k: "class:" + c, plain: true },
      h("span", { className: "yti-rs-badge", style: { borderColor: CLASS_COLOR[c] || "#6b7280", color: CLASS_COLOR[c] || "#6b7280" } }, c));
  }
  function KV(props) {
    return h("div", { className: "yti-rs-kv" }, h("div", { className: "yti-rs-kv-k" }, h(Tip, { k: props.tip }, props.k)), h("div", { className: "yti-rs-kv-v" }, props.v));
  }
  function Field(props) {
    return h("label", { className: "yti-rs-field" + (props.wide ? " yti-rs-field-wide" : "") },
      h("span", null, props.label),
      props.textarea
        ? h("textarea", { value: props.value, rows: props.rows || 3, placeholder: props.placeholder,
            onChange: function (e) { props.onChange(e.target.value); } })
        : h("input", { value: props.value, type: props.type || "text", placeholder: props.placeholder, step: props.step,
            onChange: function (e) { props.onChange(e.target.value); } }));
  }

  // ---- Setup: the niche conversation ----------------------------------------
  // The operator never fills in the niche form: a short conversation with the
  // host model works out who they serve and saves the whole setup for them.
  function NicheInterview(props) {
    const useRef = SDK.hooks.useRef || function () { return { current: null }; };
    const [st, setSt] = useState(null);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState(null);
    const [draft, setDraft] = useState("");
    const [pending, setPending] = useState(null);     // what was just said, while the reply is on its way
    const [justDone, setJustDone] = useState(false);
    const [starting, setStarting] = useState(false);
    const logRef = useRef(null);
    useEffect(function () {
      api("/research/niche-interview").then(setSt).catch(function (e) { setErr(String((e && e.message) || e)); });
    }, []);
    useEffect(function () { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [st, busy, pending]);
    const call = function (path, body, after, restore) {
      setBusy(true); setErr(null);
      post(path, body).then(function (d) { setSt(d); setPending(null); if (after) after(d); })
        .catch(function (e) {
          setErr(String((e && e.message) || e)); setPending(null);
          if (restore) setDraft(restore);               // nothing was lost — the answer goes back in the box
        })
        .finally(function () { setBusy(false); setStarting(false); });
    };
    const begin = function () {
      const seed = draft.trim();
      setJustDone(false); setDraft(""); setPending(seed || null); setStarting(true);
      call("/research/niche-interview/start", { seed: seed }, null, seed);
    };
    const send = function () {
      const text = draft.trim();
      if (!text || busy) return;
      setDraft(""); setPending(text);
      call("/research/niche-interview/answer", { text: text }, function (d) {
        if (d.done) { setJustDone(true); if (props.onSaved) props.onSaved(d.config); }
      }, text);
    };
    const cancel = function () { call("/research/niche-interview/cancel", {}); };
    const undo = function () {
      call("/research/niche-interview/undo", {}, function (d) { setJustDone(false); if (props.onSaved) props.onSaved(d.config); });
    };
    const onKey = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (st && st.inProgress) send(); else begin(); } };
    if (!st) return h("div", { className: "yti-card" }, err ? h("div", { className: "yti-notice yti-notice-error" }, err) : h("div", { className: "yti-empty" }, "Loading…"));

    const niches = st.niches || [];
    const messages = (st.inProgress ? (st.messages || []) : []).concat(pending ? [{ role: "user", text: pending }] : []);
    const errBox = err ? h("div", { className: "yti-notice yti-notice-error", style: { marginTop: 10, marginBottom: 0 } },
      "The conversation could not continue: " + err + " — your answer is back in the box; send it again.") : null;
    const composer = function (placeholder, action, label) {
      return h("div", { className: "yti-rs-chat-compose" },
        h("textarea", { value: draft, rows: 2, placeholder: placeholder, disabled: busy, "aria-label": placeholder,
          onChange: function (e) { setDraft(e.target.value); }, onKeyDown: onKey }),
        h(Button, { size: "sm", disabled: busy || (action === send && !draft.trim()), onClick: action }, busy ? "Thinking…" : label));
    };

    if (st.inProgress || starting) {
      return h("div", { className: "yti-card" },
        h("div", { className: "yti-rs-head" },
          h("h3", { className: "yti-rs-h3" }, "Let's work out your niche"),
          h("span", { className: "yti-rs-linkish", onClick: busy ? null : cancel }, niches.length ? "Stop — keep my current setup" : "Stop")),
        h("div", { className: "yti-subtle", style: { marginBottom: 8 } },
          "Answer in your own words. When I understand who you serve, I fill in the research setup for you — no form."),
        h("div", { className: "yti-rs-chat", ref: logRef, "aria-live": "polite" },
          messages.map(function (m, i) {
            return h("div", { key: i, className: "yti-rs-msg yti-rs-msg-" + (m.role === "assistant" ? "a" : "u") }, m.text);
          }),
          busy ? h("div", { className: "yti-rs-msg yti-rs-msg-a yti-rs-msg-wait" }, "Thinking… (the last step, writing your setup, can take up to a minute)") : null),
        errBox,
        composer("Type your answer — Enter to send, Shift+Enter for a new line", st.inProgress ? send : begin, "Send"));
    }

    if (!niches.length) {
      return h("div", { className: "yti-card" },
        h("h3", { className: "yti-rs-h3" }, "Tell me who you make videos for"),
        h("div", { className: "yti-rs-chat-intro" },
          st.hasFoundation
            ? "You have already described your company in the foundation steps, so I will start from that: I will tell you what I understand about your audience and you confirm or correct it. A couple of short questions, then I set up the research for you."
            : "A few short questions about who you want to reach and what they are trying to get done — then I set up the research for you. There is no form to fill in."),
        errBox,
        composer("Optional: say anything to get started — e.g. \"I help solo founders ship AI apps without getting hacked\"",
          begin, st.hasFoundation ? "Start from my company foundation" : "Start the conversation"));
    }

    const list = function (label, tip, values, max) {
      if (!values || !values.length) return null;
      const shown = max && values.length > max ? values.slice(0, max) : values;
      const rest = values.slice(shown.length);
      return h("div", { className: "yti-rs-nline" },
        h("span", { className: "yti-rs-nlabel", title: tip }, label),
        h("span", { className: "yti-rs-nchips" },
          shown.map(function (v, i) { return h("span", { key: i, className: "yti-rs-nchip" }, v); }),
          rest.length ? h("span", { className: "yti-rs-nchip yti-rs-nchip-more", title: rest.join(", ") }, "+" + rest.length + " more") : null));
    };
    const fresh = function (d) {
      d = Number(d) || 365;
      return (d <= 200 ? "Fast-moving" : d <= 450 ? "Steady" : "Evergreen") + " — a winning idea stays useful for about " + Math.round(d) + " days";
    };
    return h("div", { className: "yti-card" },
      h("div", { className: "yti-rs-head" },
        h("h3", { className: "yti-rs-h3" }, justDone ? "Done — your research is set up" : "Your niche"),
        h("div", { className: "yti-rs-nactions" },
          st.canUndo ? h(Button, { size: "sm", variant: "outline", disabled: busy, title: "Put back the setup that was in place before the last conversation", onClick: undo }, "Undo") : null,
          h(Button, { size: "sm", variant: justDone ? "outline" : undefined, disabled: busy, onClick: begin }, busy ? "Thinking…" : "Change it by talking"),
          (justDone && props.goRun) ? h(Button, { size: "sm", onClick: props.goRun }, "Next: collect videos →") : null)),
      (st.summary && st.summary.text) ? h("div", { className: "yti-rs-chat-intro" }, st.summary.text) : null,
      errBox,
      niches.map(function (n, i) {
        return h("div", { key: i, className: "yti-rs-niche" + (n.is_target ? " yti-rs-niche-target" : "") },
          h("div", { className: "yti-rs-nhead" },
            h("b", null, n.name),
            h("span", { className: "yti-rs-nbadge" + (n.is_target ? " yti-rs-nbadge-on" : ""),
              title: n.is_target ? "Your own audience. Everything is measured for this niche first."
                : "A neighbouring audience that wants the same kind of result for a different subject. Ideas that already work there, and nobody has made for your audience yet, are your openings." },
              n.is_target ? "Your niche" : "Neighbouring audience")),
          n.note ? h("div", { className: "yti-rs-nnote" }, n.note) : null,
          list("They search for", "The searches used to find videos and channels for this audience — written the way viewers type them.", n.seed_terms),
          list("They want", "The result these viewers are already after.", n.outcome_terms),
          list("You teach", "What is actually taught or done to get them that result.", n.mechanism_terms),
          list("On-topic words", "A video counts as belonging to this niche when its channel and title use this vocabulary. It keeps unrelated videos out of the results.", n.topic_terms, 14),
          h("div", { className: "yti-rs-nline yti-muted" }, fresh(n.signal_half_life_days)));
      }));
  }

  // ---- Setup ---------------------------------------------------------------
  function ResearchSetup(props) {
    const ov = props.overview;
    const [cfg, setCfg] = useState(null);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const [doctor, setDoctor] = useState(null);
    const [doctoring, setDoctoring] = useState(false);
    const [advanced, setAdvanced] = useState(false);
    const [manual, setManual] = useState(false);
    const [keyHelp, setKeyHelp] = useState(false);
    useEffect(function () { if (ov && ov.config && !cfg) setCfg(JSON.parse(JSON.stringify(ov.config))); }, [ov]);  // eslint-disable-line
    if (!cfg) return h("div", { className: "yti-empty" }, "Loading…");
    const niches = cfg.niches || [];
    const setNiche = function (i, patch) {
      const next = niches.slice();
      next[i] = Object.assign({}, next[i], patch);
      setCfg(Object.assign({}, cfg, { niches: next }));
    };
    const terms = function (v) { return Array.isArray(v) ? v.join("\n") : (v || ""); };
    const addNiche = function () {
      setCfg(Object.assign({}, cfg, { niches: niches.concat([{ name: "", is_target: niches.length === 0,
        signal_half_life_days: 365, seed_terms: [], outcome_terms: [], mechanism_terms: [] }]) }));
    };
    const removeNiche = function (i) {
      setCfg(Object.assign({}, cfg, { niches: niches.filter(function (_, j) { return j !== i; }) }));
    };
    const setNum = function (section, key) {
      return function (v) {
        const sec = Object.assign({}, cfg[section]);
        sec[key] = v === "" ? "" : Number(v);
        setCfg(Object.assign({}, cfg, (function (o) { o[section] = sec; return o; })({})));
      };
    };
    const save = function () {
      setSaving(true); setMsg(null);
      api("/research/config", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: cfg }) })
        .then(function (d) { setCfg(d.config); setMsg({ tone: "ok", text: "Saved." }); props.reload(); })
        .catch(function (e) { setMsg({ tone: "error", text: String((e && e.message) || e) }); })
        .finally(function () { setSaving(false); });
    };
    const runDoctor = function (sample) {
      setDoctoring(true);
      post("/research/doctor", { runSample: !!sample }).then(setDoctor)
        .catch(function (e) { setDoctor({ checks: [{ name: "doctor", ok: false, detail: String(e) }] }); })
        .finally(function () { setDoctoring(false); });
    };
    const sec = (ov && ov.secrets) || {};
    return h("div", null,
      h(NicheInterview, { goRun: props.goRun, onSaved: function (config) {
        if (config) setCfg(JSON.parse(JSON.stringify(config)));
        setMsg(null); props.reload();
      } }),
      h("div", { className: "yti-rs-row", style: { marginTop: 12 } },
        h("div", { className: "yti-card yti-rs-flex1" },
          h("h3", { className: "yti-rs-h3" }, "Providers"),
          h("div", { className: "yti-rs-secrets" },
            h("span", { className: "yti-rs-dot " + (sec.youtube ? "on" : "off") }), "YOUTUBE_API_KEY ",
            h("span", { className: "yti-muted" }, sec.youtube
              ? "set — discovery runs on Google's free quota (10,000 units a day; exact views, dates and durations). TranscriptAPI is only charged for transcripts and used as the fallback."
              : "missing — recommended. Free from Google; takes discovery (searches, channel catalogues, channel sizes) off your TranscriptAPI credits and makes the numbers exact."),
            h("br"),
            h("span", { className: "yti-rs-dot " + (sec.transcriptapi ? "on" : "off") }), "TRANSCRIPT_API_KEY ",
            h("span", { className: "yti-muted" }, sec.transcriptapi
              ? (sec.youtube ? "set — transcripts, free daily snapshots, and the fallback when the daily Google quota is spent" : "set — Tier 0/1/3 (discovery, transcripts, free daily snapshots)")
              : (sec.youtube ? "missing — transcripts and the free daily snapshots need it" : "missing — required until a YouTube key is set")),
            h("br"),
            h("span", { className: "yti-rs-dot " + (sec.apify ? "on" : "off") }), "APIFY_API_TOKEN ",
            h("span", { className: "yti-muted" }, sec.apify ? "set — Tier 2/3 (exact metrics, comments)" : "missing — precision + comments stages are skipped")),
          h("div", { className: "yti-subtle", style: { marginTop: 8 } }, "Keys live only in the environment / ",
            h("a", { href: "/env" }, "Keys page"), ". They are never written to config, logs, or URLs."),
          sec.youtube ? null : h("div", { className: "yti-rs-keyhelp" },
            h("div", { className: "yti-actions" },
              h(Button, { size: "sm", onClick: function () { setKeyHelp(!keyHelp); } }, (keyHelp ? "▼ " : "▶ ") + "How to get a free YouTube API key (about 3 minutes)"),
              h("a", { className: "yti-rs-btnlink", href: "https://console.cloud.google.com/apis/library/youtube.googleapis.com", target: "_blank", rel: "noreferrer",
                title: "Opens the YouTube Data API v3 page in the Google Cloud console in a new tab" }, "Open Google Cloud console ↗"),
              h("a", { className: "yti-rs-btnlink", href: "/env", title: "Paste the key as YOUTUBE_API_KEY on the Keys page" }, "Add the key on the Keys page →")),
            keyHelp ? h("ol", { className: "yti-rs-steps-list" },
              h("li", null, h("b", null, "Open the console. "), "Use the button above, or go to console.cloud.google.com. Sign in with any Google account — a normal Gmail account is fine. No billing account is needed; the YouTube Data API's 10,000 units a day are free."),
              h("li", null, h("b", null, "Pick or create a project. "), "The project selector is at the top of the page. If you have none, choose \"New project\", name it anything (e.g. youtube-research) and wait a few seconds for it to be created, then select it."),
              h("li", null, h("b", null, "Enable the YouTube Data API v3. "), "On the API page that opened, click \"Enable\". (If you landed elsewhere: left menu → APIs & Services → Library → search \"YouTube Data API v3\" → Enable.)"),
              h("li", null, h("b", null, "Create the key. "), "Left menu → APIs & Services → Credentials → \"+ Create credentials\" (top of the page) → \"API key\". A \"Create API key\" panel opens on the right — fill it in there:",
                h("ul", { className: "yti-rs-substeps" },
                  h("li", null, h("b", null, "Name:"), " anything, e.g. youtube-research."),
                  h("li", null, h("b", null, "Select API restrictions:"), " open the dropdown and tick \"YouTube Data API v3\". The list only shows APIs already enabled in this project — if it is not there, go back to step 3 first."),
                  h("li", null, h("b", null, "Application restrictions:"), " leave \"None\". This server calls Google directly, so there is no website, IP or app to restrict to."),
                  h("li", null, "Ignore the \"Service account required\" note — that is for Gemini / Agent Platform keys, not YouTube."),
                  h("li", null, "Press ", h("b", null, "Create"), ". The key (starts with AIza…) is shown once the panel closes — copy it. You can see it again later by opening the key in the API Keys list and pressing \"Show key\"."))),
              h("li", null, h("b", null, "Paste it here. "), "Open the ", h("a", { href: "/env" }, "Keys page"), ", add a variable named ", h("code", null, "YOUTUBE_API_KEY"), " with the key as its value, and save. Then come back and press \"Doctor\" below: the check \"youtube data api\" should say the key works and show today's unit count."),
              h("li", null, h("b", null, "What it changes. "), "Discovery (searches, channel catalogues, channel sizes) moves to Google's free quota with exact numbers; a full first crawl uses roughly 5,000–7,000 units, a daily refresh about 5,000. If the day's quota runs out mid-run, the remaining calls fall back to TranscriptAPI automatically. Transcripts still need TRANSCRIPT_API_KEY.")) : null),
          h("div", { className: "yti-actions", style: { marginTop: 8 } },
            h(Button, { size: "sm", variant: "outline", disabled: doctoring, onClick: function () { runDoctor(false); } }, doctoring ? "Checking…" : "Doctor"),
            h(Button, { size: "sm", variant: "outline", disabled: doctoring || !sec.apify, title: "Also runs one Apify result to verify field names (costs one result)",
              onClick: function () { runDoctor(true); } }, "Doctor + actor sample")),
          doctor ? h("div", { className: "yti-rs-doctor" },
            (doctor.checks || []).map(function (c, i) {
              return h("div", { key: i, className: "yti-rs-check" },
                h("span", { className: "yti-rs-dot " + (c.ok ? "on" : "off") }), h("b", null, c.name), " ",
                h("span", { className: "yti-muted" }, typeof c.detail === "object" ? JSON.stringify(c.detail) : String(c.detail || "")));
            }),
            (doctor.config_problems || []).map(function (p, i) {
              return h("div", { key: "p" + i, className: "yti-rs-check" }, h("span", { className: "yti-rs-dot off" }), "config: ", p);
            })) : null)),
      h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("div", { className: "yti-channels-toggle", onClick: function () { setManual(!manual); } },
          (manual ? "▼" : "▶") + " Edit the setup by hand",
          h("span", { className: "yti-muted", style: { fontWeight: 400, marginLeft: 8, fontSize: 12 } }, "optional — the conversation above fills all of this in")),
        (ov && ov.configProblems && ov.configProblems.length && !manual) ? h("div", { className: "yti-notice yti-notice-error", style: { marginTop: 10, marginBottom: 0 } }, ov.configProblems.join(" · ")) : null,
        manual ? h("div", { style: { marginTop: 12 } },
        h("div", { className: "yti-rs-head" },
          h("h3", { className: "yti-rs-h3" }, "Niches"),
          h("div", { className: "yti-actions" },
            h(Button, { size: "sm", variant: "outline", onClick: addNiche }, "+ Niche"),
            h(Button, { size: "sm", disabled: saving, onClick: save }, saving ? "Saving…" : "Save config"))),
        h("div", { className: "yti-subtle" },
          "One target niche plus adjacent niches that share viewer INTENT (not subject matter). Seed terms in the ",
          "market's own problem language (\"lose visceral fat\", not \"visceral adiposity reduction\"). ",
          "Outcome terms = what they already want; mechanism terms = what you actually do. The D4 gap report only ",
          "exists when ≥2 niches are crawled. Channels are kept comparable to yours: the crawl only expands channels between ",
          String(cfg.crawl.min_subscribers), " and ", String(cfg.crawl.max_subscribers), " subscribers (Advanced), and only when their titles are actually about the niche."),
        msg ? h("div", { className: "yti-notice " + (msg.tone === "error" ? "yti-notice-error" : "yti-notice-ok") }, msg.text) : null,
        (ov && ov.configProblems && ov.configProblems.length) ? h("div", { className: "yti-notice yti-notice-error" }, ov.configProblems.join(" · ")) : null,
        niches.length === 0 ? h("div", { className: "yti-empty" }, "No niches yet — the conversation above sets them up, or add one here.") : null,
        niches.map(function (n, i) {
          return h("div", { key: i, className: "yti-rs-niche" + (n.is_target ? " yti-rs-niche-target" : "") },
            h("div", { className: "yti-rs-niche-head" },
              h("label", { className: "yti-rs-radio" },
                h("input", { type: "radio", name: "target", checked: !!n.is_target,
                  onChange: function () { setCfg(Object.assign({}, cfg, { niches: niches.map(function (x, j) { return Object.assign({}, x, { is_target: j === i }); }) })); } }),
                " target"),
              h("span", { className: "yti-rs-remove", onClick: function () { removeNiche(i); }, title: "Remove niche" }, "✕")),
            h("div", { className: "yti-rs-grid" },
              h(Field, { label: "Name", value: n.name || "", placeholder: "e.g. ai-security", onChange: function (v) { setNiche(i, { name: v }); } }),
              h(Field, { label: "Signal half-life (days)", type: "number", value: n.signal_half_life_days == null ? "" : n.signal_half_life_days,
                placeholder: "fast 90-150 · mid 300-400 · evergreen 540-730", onChange: function (v) { setNiche(i, { signal_half_life_days: v }); } }),
              h(Field, { label: "Seed terms (one per line)", textarea: true, rows: 4, value: terms(n.seed_terms), wide: true,
                placeholder: "how to secure ai agents\nprompt injection explained", onChange: function (v) { setNiche(i, { seed_terms: v.split("\n") }); } }),
              h(Field, { label: "Outcome terms", textarea: true, rows: 3, value: terms(n.outcome_terms), placeholder: "get hired\nmore clients",
                onChange: function (v) { setNiche(i, { outcome_terms: v.split("\n") }); } }),
              h(Field, { label: "Mechanism terms", textarea: true, rows: 3, value: terms(n.mechanism_terms), placeholder: "threat modeling\nred teaming",
                onChange: function (v) { setNiche(i, { mechanism_terms: v.split("\n") }); } }),
              h(Field, { label: h(Tip, { k: "topicterms" }, "Topic terms (what counts as in-niche; one per line or comma-separated)"), textarea: true, rows: 3, wide: true,
                value: terms(n.topic_terms), placeholder: "hacking\nexploit\nmalware\nvulnerability",
                onChange: function (v) { setNiche(i, { topic_terms: v.split("\n") }); } })));
        }),
        h("div", { className: "yti-channels-toggle", style: { marginTop: 10 }, onClick: function () { setAdvanced(!advanced); } },
          (advanced ? "▼" : "▶") + " Advanced: crawl / scoring / formats / budget"),
        advanced ? h("div", { className: "yti-rs-grid yti-rs-grid-4" },
          [["crawl", "max_depth"], ["crawl", "search_pages_per_term"], ["crawl", "channel_pages_per_channel"], ["crawl", "outliers_to_expand_per_node"],
           ["crawl", "prune_after_barren_nodes"], ["crawl", "recommendations_per_video"], ["crawl", "min_subscribers"], ["crawl", "max_subscribers"], ["crawl", "channel_relevance_min"], ["crawl", "refresh_after_days"],
           ["scoring", "baseline_window"], ["scoring", "min_baseline_videos"], ["scoring", "hit_multiple"], ["scoring", "strong_multiple"],
           ["scoring", "underperformer_multiple"], ["scoring", "breakout_watch_max_age_days"], ["scoring", "breakout_watch_comment_pct"],
           ["formats", "min_support"], ["formats", "min_distinct_channels"], ["formats", "min_distinct_niches"], ["formats", "gap_min_wilson_lb"],
           ["formats", "min_actionable_n"], ["teardown", "focus_days"], ["teardown", "focus_multiple"], ["budget", "transcriptapi_credits"], ["budget", "crawl_default_credits"], ["budget", "apify_max_results"],
           ["apify", "max_comments_per_video"]].map(function (pair) {
            const s = pair[0], k = pair[1];
            return h(Field, { key: s + k, label: s + "." + k, type: "number", step: "any", value: cfg[s][k] == null ? "" : cfg[s][k], onChange: setNum(s, k) });
          })) : null) : null));
  }

  // ---- Run -------------------------------------------------------------------
  function ResearchRun(props) {
    const ov = props.overview;
    const planSort = useSort("credits_worst_case", "desc");
    const runSort = useSort("started_at", "desc");
    const job = (ov && ov.job) || {};
    const [live, setLive] = useState(null);
    const [maxCredits, setMaxCredits] = useState("");
    const [plan, setPlan] = useState(null);
    const [msg, setMsg] = useState(null);
    const running = !!(live ? live.running : job.running);
    useEffect(function () {
      if (!running) return undefined;
      const id = window.setInterval(function () {
        api("/research/job").then(function (s) { setLive(s); if (!s.running) { window.clearInterval(id); props.reload(); } }).catch(function () {});
      }, 2500);
      return function () { window.clearInterval(id); };
    }, [running]);  // eslint-disable-line
    const st = live || job;
    const run = function (name, params) {
      setMsg(null); setPlan(null);
      post("/research/run", { job: name, params: params || {} })
        .then(function (r) {
          if (r && r.dryRun) { setPlan(r.plan); return; }
          if (r && r.alreadyRunning) { setMsg({ tone: "error", text: "A job is already running (" + r.job + ")." }); return; }
          setLive({ running: true, job: name, log: [] });
        })
        .catch(function (e) { setMsg({ tone: "error", text: String((e && e.message) || e) }); });
    };
    const btn = function (label, name, params, title, variant) {
      return h(Button, { size: "sm", variant: variant || "outline", disabled: running, title: title,
        className: running && st.job === name ? "yti-busy" : "",
        onClick: function () { run(name, params); } }, running && st.job === name ? label + "…" : label);
    };
    const cp = function (v) { return v ? Number(v) : undefined; };
    const noNiche = !(ov && ov.niches && ov.niches.length);
    const sec = (ov && ov.secrets) || {};
    return h("div", null,
      noNiche ? h("div", { className: "yti-notice yti-notice-error" }, "Configure a niche with seed terms on the Setup panel before crawling. Snapshot works without one (it uses the followed channels).") : null,
      msg ? h("div", { className: "yti-notice " + (msg.tone === "error" ? "yti-notice-error" : "yti-notice-ok") }, msg.text) : null,
      h("div", { className: "yti-card" },
        h("h3", { className: "yti-rs-h3" }, "Pipeline"),
        h("div", { className: "yti-rs-steps" },
          h("div", { className: "yti-rs-step" }, h("b", null, h(Tip, { k: "tier0" }, "Tier 0 · History (free)")),
            h("div", { className: "yti-muted yti-small" }, "Exact daily views for every followed + tracked channel. Cron this daily; it is the moat."),
            btn("Snapshot", "snapshot", {}, "Free RSS snapshot of the latest ~15 uploads per tracked channel")),
          h("div", { className: "yti-rs-step" }, h("b", null, h(Tip, { k: "tier1" }, "Tier 1 · Discovery")),
            h("div", { className: "yti-muted yti-small" },
              (ov && ov.fullPassDone)
                ? "Full pass done for this niche set. A crawl now only touches what the database has not paid for: searches run again (page 1), new channels are catalogued once, known channels refresh through the free call. It stops on its own, usually well under the cap."
                : "No full pass yet for this niche set: the next crawl runs the depth-first search to exhaustion (bounded by the monthly budget, not the per-run cap). After that, crawls are incremental and cheap.",
              (sec.youtube ? " Discovery is on the YouTube Data API's free quota." : " Add a YouTube API key on Setup to make discovery free.")),
            h("div", { className: "yti-rs-inline" },
              h("input", { className: "yti-rs-mini", placeholder: "max credits (" + (ov && ov.config ? ov.config.budget.crawl_default_credits : 150) + ")", value: maxCredits,
                onChange: function (e) { setMaxCredits(e.target.value); } }),
              btn("Dry run", "crawl", { dry_run: true }, "Plan the calls and projected credits without spending anything"),
              btn((ov && ov.fullPassDone) ? "Crawl (new nodes only)" : "Crawl to completion", "crawl", { max_credits: cp(maxCredits) },
                (ov && ov.fullPassDone) ? "Incremental crawl: only nodes not paid for yet" : "Run the DFS until the stack is empty", "default"),
              (ov && ov.fullPassDone) ? btn("Full pass again", "crawl", { max_credits: cp(maxCredits), complete: true }, "Ignore the per-run cap and run until exhausted (nodes inside the refresh window are still skipped)") : null)),
          h("div", { className: "yti-rs-step" }, h("b", null, h(Tip, { k: "tier2" }, "Tier 2 · Precision")),
            h("div", { className: "yti-muted yti-small" }, "Apify exact views / likes / comments / duration / subscribers for the shortlist only."),
            h("div", { className: "yti-rs-inline" },
              btn("Enrich", "enrich", {}, sec.apify ? "Enrich hits with exact numbers" : "APIFY_API_TOKEN missing"),
              btn("Channel sizes", "sizes", {}, "Look up subscriber counts for channels that have none (1 TranscriptAPI credit per channel, most useful first)"))),
          h("div", { className: "yti-rs-step" }, h("b", null, h(Tip, { k: "tier3" }, "Tier 3 · Depth")),
            h("div", { className: "yti-muted yti-small" },
              "Transcripts (packaging + structure) and comments (satisfaction + sentiment) — spent only on the focus quadrant: ",
              (ov && ov.config && ov.config.teardown && ov.config.teardown.focus_only === false)
                ? "off (every hit is torn down; Advanced → teardown.focus_only)"
                : "videos under " + String((ov && ov.config && ov.config.teardown && ov.config.teardown.focus_days) || 7) + " days old running at " +
                  String((ov && ov.config && ((ov.config.teardown && ov.config.teardown.focus_multiple) || (ov.config.scoring && ov.config.scoring.hit_multiple))) || 3) +
                  "× or more (change in Advanced, or pick the window on Supply / Demand)."),
            h("div", { className: "yti-rs-inline" },
              btn("Tear down focus", "teardown", {}, "Transcripts, comments (when Apify is set) and packaging for the focus quadrant", "default"),
              btn("Transcripts", "transcripts", {}, "Free caption check first; one credit per transcript (focus quadrant)"),
              btn("Comments", "comments", {}, sec.apify ? "Apify comment bodies (focus quadrant)" : "APIFY_API_TOKEN missing"),
              btn("Everything, not just focus", "transcripts", { all: true }, "Transcripts for every hit — the old behaviour; 1 credit each"))),
          h("div", { className: "yti-rs-step" }, h("b", null, "Analysis"),
            h("div", { className: "yti-muted yti-small" }, "Recompute every derived score from raw rows; mine + validate formats; write D1–D5."),
            h("div", { className: "yti-rs-inline" },
              btn("Score", "score", {}, "Baselines, maturity projection, robust z, decay, classification, satisfaction proxies"),
              btn("Packaging", "packaging", {}, "Transcript-derived features"),
              btn("Formats", "formats", {}, "Seeded + mined formats, Wilson ranking, discriminators, D4 gaps"),
              btn("Report", "report", {}, "Write D1–D5 markdown into the workspace (Artifacts tab)")))),
        h("div", { className: "yti-rs-inline", style: { marginTop: 10 } },
          btn("Run everything", "pipeline", { max_credits: cp(maxCredits) }, "Snapshot → crawl → score → enrich → transcripts → comments → score → packaging → formats → report", "default"),
          h("span", { className: "yti-muted yti-small" }, "Every step is idempotent and resumable."))),
      plan ? h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Dry run — projected ", h(Tip, { k: "tier1" }, "Tier-1"), " ", h(Tip, { k: "credits" }, "credits")),
        h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
          h(Th, { sort: planSort, k: "niche", label: "niche", tip: "planniche", first: "asc" }),
          h(Th, { sort: planSort, k: "seed_terms", label: "seed terms", tip: "seedterms", cls: "yti-right" }),
          h(Th, { sort: planSort, k: "search_calls", label: "search calls", tip: "searchcalls", cls: "yti-right" }),
          h(Th, { sort: planSort, k: "channel_calls_worst", label: "channel calls (worst)", tip: "chancalls", cls: "yti-right" }),
          h(Th, { sort: planSort, k: "recommendation_calls_worst", label: "rec calls (worst)", tip: "reccalls", cls: "yti-right" }),
          h(Th, { sort: planSort, k: "credits_worst_case", label: "credits (worst)", tip: "creditsworst", cls: "yti-right" }))),
          h("tbody", null, sortRows(plan.niches || [], planSort).map(function (n) {
            return h("tr", { key: n.niche }, h("td", null, n.niche), h("td", { className: "yti-right" }, n.seed_terms), h("td", { className: "yti-right" }, n.search_calls),
              h("td", { className: "yti-right" }, n.channel_calls_worst), h("td", { className: "yti-right" }, n.recommendation_calls_worst), h("td", { className: "yti-right yti-strong" }, n.credits_worst_case));
          }))),
        h("div", { className: "yti-subtle", style: { marginTop: 6 } }, "Total worst case: " + plan.credits_worst_case + " credits. " + plan.note)) : null,
      h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("div", { className: "yti-rs-head" },
          h("h3", { className: "yti-rs-h3" }, "Job " + (st.job ? "· " + st.job : ""),
            running ? h("span", { className: "yti-gen-spinner", style: { marginLeft: 8 } }) : null),
          h("span", { className: "yti-muted yti-small" }, st.started ? "started " + formatAgo(st.started) : "idle")),
        st.error ? h("div", { className: "yti-notice yti-notice-error" }, st.error) : null,
        st.result && !running ? h("pre", { className: "yti-rs-result" }, JSON.stringify(st.result, null, 1).slice(0, 4000)) : null,
        h("pre", { className: "yti-rs-log" }, (st.log || []).slice(-60).join("\n") || "no output yet")),
      (ov && ov.crawlRuns && ov.crawlRuns.length) ? h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Crawl runs"),
        h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
          h(Th, { sort: runSort, k: "started_at", label: "run", tip: "run" }),
          h(Th, { sort: runSort, k: "status", label: "status", tip: "status", first: "asc" }),
          h(Th, { sort: runSort, k: "nodes", label: "nodes", tip: "nodes", cls: "yti-right" }),
          h(Th, { sort: runSort, k: "videos_new", label: "new videos", tip: "newvideos", cls: "yti-right" }),
          h(Th, { sort: runSort, k: "outliers_new", label: "new outliers", tip: "newoutliers", cls: "yti-right" }),
          h(Th, { sort: runSort, k: "credits", label: "credits", tip: "credits", cls: "yti-right" }),
          h(Th, { sort: runSort, k: "stop_reason", label: "stop reason", tip: "stopreason", first: "asc" }), h("th", null, ""))),
          h("tbody", null, sortRows(ov.crawlRuns, runSort, {
            nodes: function (r) { return (r.stats || {}).nodes; }, videos_new: function (r) { return (r.stats || {}).videos_new; },
            outliers_new: function (r) { return (r.stats || {}).outliers_new; }, credits: function (r) { return (r.stats || {}).credits; },
            stop_reason: function (r) { return (r.stats || {}).stop_reason; } }).map(function (r) {
            const s = r.stats || {};
            return h("tr", { key: r.run_id }, h("td", { className: "yti-small" }, r.run_id), h("td", null, r.status),
              h("td", { className: "yti-right" }, s.nodes), h("td", { className: "yti-right" }, s.videos_new), h("td", { className: "yti-right" }, s.outliers_new),
              h("td", { className: "yti-right" }, s.credits), h("td", { className: "yti-small yti-muted" }, s.stop_reason || ""),
              h("td", null, r.status === "paused" ? h(Button, { size: "sm", variant: "outline", disabled: running,
                onClick: function () { run("crawl", { resume: r.run_id, max_credits: cp(maxCredits) }); } }, "Resume") : null));
          })))) : null,
      (ov && ov.history && ov.history.length) ? h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Recent jobs"),
        ov.history.slice(0, 8).map(function (hst, i) {
          return h("div", { key: i, className: "yti-rs-check" },
            h("span", { className: "yti-rs-dot " + (hst.error ? "off" : "on") }), h("b", null, hst.job), " ",
            h("span", { className: "yti-muted yti-small" }, formatAgo(hst.finished || hst.started) + " · " + (hst.error || JSON.stringify(hst.result || {}).slice(0, 160))));
        })) : null);
  }

  // ---- Outliers (D2) + Demand map (D1) -----------------------------------------
  function ResearchOutliers(props) {
    const ov = props.overview;
    const [niche, setNiche] = useState("");
    const [classes, setClasses] = useState(["strong_hit", "hit"]);
    const [size, setSize] = useState("band");
    const sort = useSort("projected_multiple", "desc");
    const [data, setData] = useState(null);
    const [demand, setDemand] = useState(null);
    const [showDemand, setShowDemand] = useState(false);
    useEffect(function () {
      const p = new URLSearchParams({ niche: niche, classes: classes.join(","), sort: sort.key, order: sort.dir, size: size, limit: "150" });
      api("/research/outliers?" + p.toString()).then(setData).catch(function () { setData({ rows: [], total: 0, counts: {} }); });
    }, [niche, classes, size, sort.key, sort.dir]);
    useEffect(function () { if (showDemand && !demand) api("/research/demand").then(setDemand).catch(function () {}); }, [showDemand]);  // eslint-disable-line
    const toggle = function (c) { setClasses(classes.indexOf(c) >= 0 ? classes.filter(function (x) { return x !== c; }) : classes.concat([c])); };
    const rows = (data && data.rows) || [];
    const counts = (data && data.counts) || {};
    return h("div", null,
      h("div", { className: "yti-rs-filter" },
        h("select", { className: "yti-select", value: niche, onChange: function (e) { setNiche(e.target.value); } },
          h("option", { value: "" }, "All niches"),
          ((ov && ov.niches) || []).concat(["followed"]).map(function (n) { return h("option", { key: n, value: n }, n); })),
        h(Tip, { k: "sizefilter", plain: true }, h("select", { className: "yti-select", value: size, onChange: function (e) { setSize(e.target.value); } },
          h("option", { value: "band" }, "Comparable channels (" + ((ov && ov.config && ov.config.crawl) ? formatNumber(ov.config.crawl.min_subscribers) + "–" + formatNumber(ov.config.crawl.max_subscribers) : "Setup band") + " subs)"),
          h("option", { value: "1000000" }, "Up to 1M subs"),
          h("option", { value: "any" }, "Any size"))),
        ["strong_hit", "hit", "normal", "under", "immature"].map(function (c) {
          return h("button", { key: c, className: "yti-filter-chip" + (classes.indexOf(c) >= 0 ? " yti-filter-chip-on" : ""),
            onClick: function () { toggle(c); } }, h(Tip, { k: "class:" + c, plain: true }, c + (counts[c] != null ? " " + counts[c] : "")));
        }),
        h("button", { className: "yti-filter-chip" + (showDemand ? " yti-filter-chip-on" : ""), onClick: function () { setShowDemand(!showDemand); } }, h(Tip, { k: "d1", plain: true }, "D1 Demand map")),
        h("span", { className: "yti-muted yti-small" }, "Click any column header to sort; hover it for what it means.")),
      showDemand ? h("div", { className: "yti-card", style: { marginBottom: 12 } },
        h("h3", { className: "yti-rs-h3" }, "D1 — Demand map"),
        !demand ? h("div", { className: "yti-empty" }, "Loading…") :
        h("div", null,
          h("div", { className: "yti-subtle" }, "Subjects with proven pull: title terms across " + demand.hit_videos + " hit videos, weighted by projected multiple × signal weight (≥2 channels)."),
          Object.keys(demand.subjects || {}).map(function (n) {
            return h("div", { key: n, style: { marginBottom: 8 } }, h("b", null, n), h("div", { className: "yti-chip-row" },
              demand.subjects[n].map(function (s) { return h("span", { key: s.term, className: "yti-chip", title: "weight " + s.weight + " · " + s.channels + " channels" }, s.term + " " + s.weight); })));
          }),
          (demand.search_terms || []).length ? h("div", null, h("b", null, "Search terms by outlier yield"), h("div", { className: "yti-chip-row" },
            demand.search_terms.slice(0, 30).map(function (t, i) { return h("span", { key: i, className: "yti-chip" }, t.term + " · " + t.yield); }))) : null,
          (demand.requests || []).length ? h("div", { style: { marginTop: 8 } }, h("b", null, "Viewer requests mined from comments"),
            h("ul", { className: "yti-rs-ul" }, demand.requests.map(function (r, i) { return h("li", { key: i }, r.topic + " (" + r.n + ")"); }))) : null)) : null,
      data == null ? h("div", { className: "yti-empty" }, "Loading…") :
      rows.length === 0 ? h("div", { className: "yti-empty" }, "No scored videos match. Crawl, then Score, on the Run panel.") :
      h("div", { className: "yti-table-wrap" },
        h("div", { className: "yti-subtle" }, data.total + " long-form, ", h(Tip, { k: "scope" }, "in-niche"), " videos (showing " + rows.length + ") · ", h(Tip, { k: "approx" }, "≈"), " marks approximate ", h(Tip, { k: "tier1" }, "Tier-1"), " numbers · ", h(Tip, { k: "rawword" }, "raw"), " = date too coarse to project"),
        h("table", { className: "yti-table yti-rs-table" },
          h("thead", null, h("tr", null, h("th", null, ""),
            h(Th, { sort: sort, k: "title", label: "Title", tip: "title", first: "asc" }),
            h(Th, { sort: sort, k: "channel", label: "Channel", tip: "channel", first: "asc" }),
            h(Th, { sort: sort, k: "subs", label: "Subs", tip: "subs", cls: "yti-right" }),
            h(Th, { sort: sort, k: "niche", label: "Niche", tip: "niche", first: "asc" }),
            h(Th, { sort: sort, k: "class", label: "Class", tip: "cls" }),
            h(Th, { sort: sort, k: "projected_multiple", label: "× proj", tip: "xproj", cls: "yti-right yti-strong" }),
            h(Th, { sort: sort, k: "multiple", label: "× raw", tip: "xraw", cls: "yti-right" }),
            h(Th, { sort: sort, k: "z", label: "z", tip: "z", cls: "yti-right" }),
            h(Th, { sort: sort, k: "views", label: "Views", tip: "views", cls: "yti-right" }),
            h(Th, { sort: sort, k: "age", label: "Age d", tip: "age", cls: "yti-right", first: "asc" }),
            h(Th, { sort: sort, k: "weight", label: "Weight", tip: "weight", cls: "yti-right" }),
            h(Th, { sort: sort, k: "vs", label: "VS %", tip: "vs", cls: "yti-right" }),
            h(Th, { sort: sort, k: "flags", label: "Flags", tip: "flags", cls: "yti-rs-flagcol" }))),
          h("tbody", null, rows.map(function (r) {
            const flags = [];
            if (r.views_approx) flags.push("views≈");
            if (r.published_approx) flags.push("date≈");
            if (r.raw_only) flags.push("raw");
            if (r.organic_flag === "suspect_paid") flags.push("⚠ paid?");
            if (r.breakout_watch) flags.push("🚀 breakout");
            if (r.fade_watch) flags.push("fade");
            if (r.precision_tier >= 2) flags.push("exact");
            if (r.subscriber_count == null && !r.is_tracked) flags.push("size?");
            return h("tr", { key: r.video_id },
              h("td", null, r.thumbnail_url ? h("img", { className: "yti-thumb yti-rs-thumb", src: r.thumbnail_url, alt: "" }) : null),
              h("td", null, h("a", { className: "yti-video-link", href: "https://www.youtube.com/watch?v=" + r.video_id, target: "_blank", rel: "noopener" }, r.title)),
              h("td", { className: "yti-muted yti-small" }, r.handle || r.channel_title || r.channel_id),
              h("td", { className: "yti-right yti-muted yti-small" }, r.subscriber_count == null ? "?" : (r.subscriber_approx ? "~" : "") + formatNumber(r.subscriber_count)),
              h("td", { className: "yti-muted yti-small" }, r.niche),
              h("td", null, h(ClassBadge, { cls: r["class"] })),
              h("td", { className: "yti-right yti-strong" }, fmtMult(r.projected_multiple)),
              h("td", { className: "yti-right yti-muted" }, fmtMult(r.multiple)),
              h("td", { className: "yti-right yti-muted" }, r.log_mad_z == null ? "—" : Number(r.log_mad_z).toFixed(1)),
              h("td", { className: "yti-right" }, formatNumber(r.views)),
              h("td", { className: "yti-right yti-muted" }, r.age_days == null ? "—" : Math.round(r.age_days)),
              h("td", { className: "yti-right yti-muted" }, r.signal_weight == null ? "—" : Number(r.signal_weight).toFixed(2)),
              h("td", { className: "yti-right yti-muted" }, fmtPct(r.vs_percentile)),
              h("td", { className: "yti-small yti-muted" }, h(FlagChips, { flags: flags })));
          })))));
  }

  // ---- Supply / Demand ---------------------------------------------------------
  // Demand (y) = multiple of the channel's normal views; supply (x) = time
  // since publish, newest on the left. The longer an idea has been out, the
  // more of it exists, so the same multiple is worth less: the top-left
  // quadrant (recent AND high demand) is where to look.
  const SD_W = 1040, SD_H = 500, SD_M = { l: 58, r: 18, t: 20, b: 46 };
  // x axis: log-style (log of 1 + days) so the newest days get the room;
  // nothing older than three months is shown
  const SD_X_MAX = 91.31;
  const SD_X_TICKS = [[0, "today"], [1, "1 d"], [2, "2 d"], [3, "3 d"], [4, "4 d"], [5, "5 d"], [6, "6 d"], [7, "7 d"],
    [14, "2 wk"], [21, "3 wk"], [28, "4 wk"], [60.87, "2 mo"], [91.31, "3 mo"]];
  const SD_RECENTS = [[7, "week"], [14, "2 weeks"], [28, "4 weeks"], [60.87, "2 months"]];
  const SD_THRESHOLDS = [2, 3, 5, 10];
  const SD_Y_MIN = 0.25;

  function sdHash01(str) {           // deterministic 0..1 per video id
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 10000) / 10000;
  }
  function sdAgeLabel(d) {
    if (d < 1) return "today";
    if (d < 60) return Math.round(d) + " d";
    if (d < 730) return Math.round(d / 30.44) + " mo";
    return (d / 365.25).toFixed(1) + " y";
  }
  function sdNice125(x) {            // next 1-2-5 step at or above x
    const p = Math.pow(10, Math.floor(Math.log10(x)));
    const f = x / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function sdPalette() {             // validated pair per surface (dataviz validator)
    let dark = true;
    try {
      const bg = getComputedStyle(document.documentElement).getPropertyValue("--background-base").trim();
      const m = /^#?([0-9a-f]{6})$/i.exec(bg);
      if (m) {
        const n = parseInt(m[1], 16);
        const lum = (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
        dark = lum < 0.5;
      }
    } catch (e) { /* keep dark */ }
    return dark ? { focus: "#3987e5", context: "#6f7782", surface: "var(--background-base, #0b0f14)" }
                : { focus: "#2a78d6", context: "#8d939c", surface: "var(--background-base, #fcfcfb)" };
  }

  function momentumText(mo) {
    if (!mo || mo.vph == null) return "No momentum reading yet.";
    const pace = formatNumber(Math.round(mo.vph)) + " views per hour over the latest interval";
    if (mo.dir === "up") return "Momentum building: " + pace + ", more than 10% faster than the interval before (" + mo.n + " readings).";
    if (mo.dir === "down") return "Momentum fading: " + pace + ", more than 10% slower than the interval before (" + mo.n + " readings).";
    if (mo.dir === "flat") return "Momentum steady: " + pace + ", within 10% of the interval before (" + mo.n + " readings).";
    return "Latest pace: " + pace + " (" + mo.n + " readings — one more is needed to say whether it is building or fading).";
  }

  function SupplyDemandView(props) {
    const ov = props.overview;
    const useMemo = SDK.hooks.useMemo || function (f) { return f(); };
    const useRef = SDK.hooks.useRef || function () { return { current: null }; };
    const [data, setData] = useState(null);
    const [niche, setNiche] = useState("");
    const [size, setSize] = useState("band");
    const [recent, setRecent] = useState(7);          // default focus: 3× and under one week
    const [threshold, setThreshold] = useState(null);
    const [td, setTd] = useState(null);                // "tear down these" request state
    const [trails, setTrails] = useState(true);        // trajectory behind each focus dot
    const tearDown = function (n, days, mult) {
      setTd({ busy: true });
      post("/research/run", { job: "teardown", params: { focus_days: days, focus_multiple: mult } })
        .then(function (r) {
          if (r && r.alreadyRunning) { setTd({ text: "A job is already running (" + r.job + ")." }); return; }
          setTd({ text: "Tearing down " + n + " video" + (n === 1 ? "" : "s") + ": transcripts, comments and packaging — progress is on the Run tab, results on Teardown." });
        })
        .catch(function (e) { setTd({ text: String((e && e.message) || e) }); });
    };
    const [hover, setHover] = useState(null);
    const sdSort = useSort("adj", "desc");
    const wrapRef = useRef(null);
    useEffect(function () {
      api("/research/supply-demand").then(function (d) {
        setData(d);
        setThreshold(function (t) { return t == null ? (d.hit_multiple || 3) : t; });
      }).catch(function () { setData({ points: [], half_life: {}, niches: [] }); });
    }, []);
    const thr = threshold == null ? 3 : threshold;
    const pal = useMemo(sdPalette, []);

    // one model for chart, legend, quadrant counts and table — the numbers always agree
    const model = useMemo(function () {
      if (!data) return null;
      const hl = data.half_life || {};
      const pts = [];
      let maxAge = 0;
      (data.points || []).forEach(function (p) {
        if (niche && p.n !== niche) return;
        // channel size: followed channels and channels of unknown size always pass
        if (size !== "any" && p.sub != null && !p.fol) {
          const hi = size === "band" ? (data.max_subs || 0) : Number(size);
          const lo = size === "band" ? (data.min_subs || 0) : 0;
          if ((hi && p.sub > hi) || p.sub < lo) return;
        }
        const age = p.a + (p.s ? sdHash01(p.id) * p.s : 0);     // somewhere inside its known range
        if (age > maxAge) maxAge = age;
        const half = hl[p.n] || data.default_half_life || 365;
        const fresh = Math.pow(0.5, age / half);
        pts.push({ p: p, age: age, fresh: fresh, adj: p.m * fresh });
      });
      const xMax = SD_X_MAX;
      const inRange = pts.filter(function (q) { return q.age <= xMax; });
      let yTop = thr * 2;
      inRange.forEach(function (q) { if (q.p.m > yTop) yTop = q.p.m; });
      yTop = sdNice125(yTop);
      const iw = SD_W - SD_M.l - SD_M.r, ih = SD_H - SD_M.t - SD_M.b;
      const lmin = Math.log(SD_Y_MIN), lmax = Math.log(yTop);
      const X = function (age) { return SD_M.l + (Math.log(1 + Math.max(0, age)) / Math.log(1 + xMax)) * iw; };
      const Y = function (m) { return SD_M.t + (1 - (Math.log(Math.max(m, SD_Y_MIN)) - lmin) / (lmax - lmin)) * ih; };
      const q = { focus: [], oldHigh: 0, recentLow: 0, oldLow: 0, below: 0 };
      const drawn = [];
      inRange.forEach(function (d) {
        const isRecent = d.age <= recent, isHigh = d.p.m >= thr;
        d.focus = isRecent && isHigh;
        if (d.focus) q.focus.push(d);
        else if (isHigh) q.oldHigh += 1;
        else if (isRecent) q.recentLow += 1;
        else q.oldLow += 1;
        if (d.p.m < SD_Y_MIN) { q.below += 1; return; }
        d.x = X(d.age); d.y = Y(d.p.m);
        // earlier readings of the same video (pulse + daily snapshots): only
        // the focus quadrant gets a trail — the rest would be noise
        d.trail = null;
        if (d.focus && d.p.h && d.p.h.length) {
          d.trail = d.p.h.filter(function (pt) { return pt[0] <= xMax; })
            .map(function (pt) { return { x: X(pt[0]), y: Y(Math.max(pt[1], SD_Y_MIN)), a: pt[0], m: pt[1] }; });
          if (!d.trail.length) d.trail = null;
        }
        drawn.push(d);
      });
      q.focus.sort(function (a, b) { return b.adj - a.adj; });
      const recentTotal = q.focus.length + q.recentLow;
      return { pts: drawn, q: q, xMax: xMax, yTop: yTop, X: X, Y: Y, iw: iw, ih: ih, total: inRange.length, recentTotal: recentTotal };
    }, [data, niche, size, recent, thr]);

    // the marks layer is memoised: hovering must not rebuild thousands of circles
    const marks = useMemo(function () {
      if (!model) return null;
      const ctx = [], foc = [], trl = [];
      model.pts.forEach(function (d) {
        const approx = d.p.s > 0;
        if (d.focus && trails && d.trail) {
          const pts = d.trail.map(function (t) { return t.x.toFixed(1) + "," + t.y.toFixed(1); });
          pts.push(d.x.toFixed(1) + "," + d.y.toFixed(1));
          trl.push(h("g", { key: "t" + d.p.id },
            h("polyline", { points: pts.join(" "), fill: "none", stroke: pal.context, strokeWidth: 1.2, strokeOpacity: 0.7 }),
            d.trail.map(function (t, i) {
              return h("circle", { key: i, cx: t.x.toFixed(1), cy: t.y.toFixed(1), r: 2.4, fill: pal.context, fillOpacity: 0.55,
                stroke: pal.surface, strokeWidth: 1 });
            })));
        }
        if (d.focus) {
          foc.push(h("circle", { key: d.p.id, cx: d.x.toFixed(1), cy: d.y.toFixed(1), r: 4.5,
            fill: approx ? pal.surface : pal.focus, stroke: approx ? pal.focus : pal.surface,
            strokeWidth: approx ? 2 : 1.5 }));
        } else {
          ctx.push(h("circle", { key: d.p.id, cx: d.x.toFixed(1), cy: d.y.toFixed(1), r: 2.6,
            fill: pal.context, fillOpacity: approx ? 0.12 : 0.5,
            stroke: approx ? pal.context : "none", strokeOpacity: 0.55, strokeWidth: 1 }));
        }
      });
      return h("g", null, h("g", null, ctx), h("g", null, trl), h("g", null, foc));
    }, [model, pal, trails]);

    if (!data) return h("div", { className: "yti-empty" }, "Loading…");
    if (!(data.points || []).length) {
      return h("div", { className: "yti-empty" }, "No scored long-form videos yet. Crawl, then Score, on the Run panel.");
    }
    const m = model, q = m.q;

    // axes
    const yTicks = [];
    [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000].forEach(function (t) { if (t <= m.yTop && t >= SD_Y_MIN) yTicks.push(t); });
    const x0 = SD_M.l, x1 = SD_W - SD_M.r, y0 = SD_M.t, y1 = SD_H - SD_M.b;
    const xr = m.X(Math.min(recent, m.xMax)), yt = m.Y(thr), yn = m.Y(1);

    // direct labels: the ten strongest in the focus quadrant, each skipped when
    // its box would collide with one already placed or leave the plot
    const labels = [];
    const boxes = [];
    q.focus.slice(0, 10).forEach(function (d) {
      if (d.p.m < SD_Y_MIN) return;
      const text = d.p.t.length > 38 ? d.p.t.slice(0, 37) + "…" : d.p.t;
      const w = text.length * 6.1 + 6;
      const right = d.x + 9 + w < x1;
      const bx = right ? d.x + 9 : d.x - 9 - w, by = d.y - 8;
      const box = { x: bx, y: by, w: w, h: 15 };
      if (by < y0 || boxes.some(function (b) { return !(box.x > b.x + b.w || box.x + box.w < b.x || box.y > b.y + b.h || box.y + box.h < b.y); })) return;
      boxes.push(box);
      labels.push(h("text", { key: d.p.id, x: right ? d.x + 9 : d.x - 9, y: d.y + 4, textAnchor: right ? "start" : "end",
        className: "yti-sd-label" }, text));
    });

    const onMove = function (e) {
      const svg = e.currentTarget, rect = svg.getBoundingClientRect();
      const sx = SD_W / rect.width, sy = SD_H / rect.height;
      const px = (e.clientX - rect.left) * sx, py = (e.clientY - rect.top) * sy;
      const lim = 24 * sx;                                 // 24 screen px, nearest point wins
      let best = null, bd = lim * lim;
      for (let i = 0; i < m.pts.length; i++) {
        const d = m.pts[i], dx = d.x - px, dy = d.y - py, dd = dx * dx + dy * dy;
        if (dd < bd || (dd === bd && best && d.focus && !best.focus)) { bd = dd; best = d; }
      }
      if (!best) { if (hover) setHover(null); return; }
      if (!hover || hover.d !== best) setHover({ d: best, left: best.x / sx, top: best.y / sy, w: rect.width });
    };
    const openVideo = function (d) { window.open("https://www.youtube.com/watch?v=" + encodeURIComponent(d.p.id), "_blank", "noopener"); };
    const hd = hover && hover.d;
    const pct = m.recentTotal ? Math.round(100 * q.focus.length / m.recentTotal) : 0;
    const halfNote = Object.keys(data.half_life || {}).filter(function (n) { return !niche || n === niche; })
      .map(function (n) { return n + " " + Math.round(data.half_life[n]) + " d"; }).join(" · ");
    const ageText = function (d) { return (d.p.s > 0 ? "~" : "") + sdAgeLabel(d.age); };

    return h("div", null,
      h("div", { className: "yti-rs-filter" },
        h("select", { className: "yti-select", value: niche, onChange: function (e) { setNiche(e.target.value); setHover(null); } },
          h("option", { value: "" }, "All niches"),
          (data.niches || []).map(function (n) { return h("option", { key: n, value: n }, n); })),
        h(Tip, { k: "sizefilter", plain: true }, h("select", { className: "yti-select", value: size, onChange: function (e) { setSize(e.target.value); setHover(null); } },
          h("option", { value: "band" }, "Comparable channels (" + formatNumber(data.min_subs || 0) + "–" + formatNumber(data.max_subs || 0) + " subs)"),
          h("option", { value: "1000000" }, "Up to 1M subs"),
          h("option", { value: "any" }, "Any size"))),
        h("select", { className: "yti-select", value: recent, title: "What counts as recent (low supply)",
            onChange: function (e) { setRecent(Number(e.target.value)); setHover(null); } },
          SD_RECENTS.map(function (o) { return h("option", { key: o[0], value: o[0] }, "Recent = last " + o[1]); })),
        h("select", { className: "yti-select", value: thr, title: "What counts as high demand",
            onChange: function (e) { setThreshold(Number(e.target.value)); setHover(null); } },
          SD_THRESHOLDS.map(function (t) { return h("option", { key: t, value: t }, "High demand = " + t + "× normal or more"); })),
        h("label", { className: "yti-sd-toggle", title: "Draw each focus video's earlier readings as grey dots joined to its current blue dot — the path it took to get here. Readings come from the pulse (every 6 hours) and the daily snapshot." },
          h("input", { type: "checkbox", checked: trails, onChange: function (e) { setTrails(e.target.checked); } }), " Trend lines")),

      h("div", { className: "yti-card yti-sd-card" },
        h("div", { className: "yti-rs-head" },
          h("h3", { className: "yti-rs-h3" }, "Demand against supply"),
          h("div", { className: "yti-sd-legend" },
            h("span", null, h("i", { className: "yti-sd-key", style: { background: pal.focus } }), "Recent + high demand (" + q.focus.length + ")"),
            trails ? h("span", null, h("i", { className: "yti-sd-key yti-sd-key-trail", style: { borderColor: pal.context } }), "earlier readings (trend line)") : null,
            h("span", null, h("i", { className: "yti-sd-key", style: { background: pal.context } }), "Everything else (" + (m.total - q.focus.length) + ")"),
            h("span", null, h("i", { className: "yti-sd-key yti-sd-key-hollow", style: { borderColor: pal.context } }), "date approximate"))),
        h("div", { className: "yti-subtle", style: { marginBottom: 8 } },
          q.focus.length + " of " + m.recentTotal + " videos from the last " + (SD_RECENTS.filter(function (o) { return o[0] === recent; })[0] || [0, Math.round(recent) + " days"])[1] + " (" + pct + "%) are running at " + thr + "× their channel's normal views or more. ",
          "Each dot is one long-form, in-niche video from the last 3 months; time since publish stands in for supply. ",
          q.focus.length ? h(Button, { size: "sm", variant: "outline", disabled: !!(td && td.busy), style: { marginLeft: 6 },
              title: "Pull transcripts and comments for exactly these videos (one TranscriptAPI credit per transcript), then compute their packaging. Nothing outside this quadrant is paid for.",
              onClick: function () { tearDown(q.focus.length, recent, thr); } },
              (td && td.busy) ? "Starting…" : "Tear down these " + q.focus.length) : null,
          (td && td.text) ? h("span", { className: "yti-muted", style: { marginLeft: 8 } }, td.text) : null),
        h("div", { className: "yti-sd-wrap", ref: wrapRef },
          h("svg", { className: "yti-sd-svg", viewBox: "0 0 " + SD_W + " " + SD_H, role: "img",
              "aria-label": "Scatter of long-form videos: multiple of normal views against time since publish. " +
                q.focus.length + " recent high-demand videos are highlighted; they are listed in the table below.",
              onPointerMove: onMove, onPointerLeave: function () { setHover(null); },
              onClick: function () { if (hd) openVideo(hd); }, style: { cursor: hd ? "pointer" : "default" } },
            // focus quadrant wash
            h("rect", { x: x0, y: y0, width: Math.max(0, xr - x0), height: Math.max(0, yt - y0), fill: pal.focus, fillOpacity: 0.09 }),
            // recessive grid + ticks
            yTicks.map(function (t) {
              const y = m.Y(t);
              return h("g", { key: "y" + t },
                h("line", { x1: x0, x2: x1, y1: y, y2: y, className: "yti-sd-grid" }),
                h("text", { x: x0 - 8, y: y + 4, textAnchor: "end", className: "yti-sd-tick" }, t + "×"));
            }),
            SD_X_TICKS.map(function (tk, i) {
              const x = m.X(tk[0]);
              return h("g", { key: "x" + i },
                i ? h("line", { x1: x, x2: x, y1: y0, y2: y1, className: "yti-sd-grid" }) : null,
                h("line", { x1: x, x2: x, y1: y1, y2: y1 + 4, className: "yti-sd-axis" }),
                h("text", { x: x, y: y1 + 17, textAnchor: i === 0 ? "start" : i === SD_X_TICKS.length - 1 ? "end" : "middle", className: "yti-sd-tick" }, tk[1]));
            }),
            h("line", { x1: x0, x2: x1, y1: y1, y2: y1, className: "yti-sd-axis" }),
            h("line", { x1: x0, x2: x0, y1: y0, y2: y1, className: "yti-sd-axis" }),
            // reference lines: normal, the demand threshold, the recent boundary
            h("line", { x1: x0, x2: x1, y1: yn, y2: yn, className: "yti-sd-ref" }),
            h("text", { x: x0 + 8, y: yn - 5, className: "yti-sd-note" }, "1× = the channel's normal"),
            h("line", { x1: x0, x2: x1, y1: yt, y2: yt, className: "yti-sd-ref" }),
            h("line", { x1: xr, x2: xr, y1: y0, y2: y1, className: "yti-sd-ref" }),
            marks,
            // quadrant captions (text tokens, never the series colour)
            h("text", { x: x0 + 8, y: y0 + 15, className: "yti-sd-quad yti-sd-quad-strong" }, "Recent · high demand — " + q.focus.length),
            xr < x1 - 190 ? h("text", { x: x1 - 8, y: y0 + 15, textAnchor: "end", className: "yti-sd-quad" }, "Older · high demand, supply has caught up — " + q.oldHigh) : null,
            h("text", { x: x0 + 8, y: y1 - 8, className: "yti-sd-quad" }, "Recent · ordinary — " + q.recentLow),
            xr < x1 - 190 ? h("text", { x: x1 - 8, y: y1 - 8, textAnchor: "end", className: "yti-sd-quad" }, "Older · ordinary — " + q.oldLow) : null,
            labels,
            hd ? h("circle", { cx: hd.x, cy: hd.y, r: hd.focus ? 7.5 : 6, fill: "none", stroke: "currentColor", strokeWidth: 1.5 }) : null,
            // axis titles
            h("text", { x: (x0 + x1) / 2, y: SD_H - 6, textAnchor: "middle", className: "yti-sd-title" }, "← newer · time since published, log scale (supply builds with time) · older →"),
            h("text", { x: 14, y: (y0 + y1) / 2, textAnchor: "middle", transform: "rotate(-90 14 " + ((y0 + y1) / 2) + ")", className: "yti-sd-title" }, "Demand: views ÷ channel's normal (log)")),
          hd ? h("div", { className: "yti-sd-tip", style: {
              left: Math.min(Math.max(8, hover.left + 14), Math.max(8, hover.w - 300)), top: Math.max(8, hover.top - 12) } },
            h("div", { className: "yti-sd-tip-val" }, fmtMult(hd.p.m), h("span", null, " normal views")),
            h("div", { className: "yti-sd-tip-sub" }, fmtMult(hd.adj) + " after discounting for " + ageText(hd) + " of supply"),
            h("div", { className: "yti-sd-tip-title" }, hd.p.t),
            h("div", { className: "yti-sd-tip-meta" }, hd.p.ch + " · " + hd.p.n),
            h("div", { className: "yti-sd-tip-meta" }, "published " + ageText(hd) + " ago" + (hd.p.s > 0 ? " (date approximate)" : "") + " · " + formatNumber(hd.p.v) + " views" +
              (hd.p.f.length ? " · " + hd.p.f.join(" ") : "")),
            hd.p.mo ? h("div", { className: "yti-sd-tip-meta" }, momentumText(hd.p.mo)) : null,
            h("div", { className: "yti-sd-tip-meta" }, "click to open")) : null),
        h("div", { className: "yti-subtle", style: { margin: "8px 0 0" } },
          "Approximate dates (“2 months ago”) are placed inside the range they can fall in. ",
          q.below ? q.below + " videos below " + SD_Y_MIN + "× are counted but not drawn. " : "",
          "Videos under 28 days old use views projected to day 28.")),

      h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Recent + high demand"),
        h("div", { className: "yti-subtle" },
          "Ranked by time-adjusted demand unless you sort another column. Time-adjusted = multiple × freshness; freshness halves every half-life of the video's niche" + (halfNote ? " (" + halfNote + ")" : "") + ", set on the Setup panel."),
        q.focus.length === 0 ? h("div", { className: "yti-empty" }, "Nothing in this quadrant with the current settings — widen Recent or lower the demand threshold.") :
        h("table", { className: "yti-table yti-rs-table" },
          h("thead", null, h("tr", null,
            h(Th, { sort: sdSort, k: "title", label: "Title", tip: "title", first: "asc" }),
            h(Th, { sort: sdSort, k: "channel", label: "Channel", tip: "channel", first: "asc" }),
            h(Th, { sort: sdSort, k: "subs", label: "Subs", tip: "subs", cls: "yti-right" }),
            h(Th, { sort: sdSort, k: "niche", label: "Niche", tip: "niche", first: "asc" }),
            h(Th, { sort: sdSort, k: "age", label: "Published", tip: "published", cls: "yti-right", first: "asc" }),
            h(Th, { sort: sdSort, k: "m", label: "× normal", tip: "xnormal", cls: "yti-right" }),
            h(Th, { sort: sdSort, k: "fresh", label: "Freshness", tip: "fresh", cls: "yti-right" }),
            h(Th, { sort: sdSort, k: "adj", label: "× time-adjusted", tip: "xadj", cls: "yti-right yti-strong" }),
            h(Th, { sort: sdSort, k: "views", label: "Views", tip: "views", cls: "yti-right" }),
            h(Th, { sort: sdSort, k: "mo", label: "Momentum", tip: "momentum", cls: "yti-right" }),
            h(Th, { sort: sdSort, k: "flags", label: "Flags", tip: "flags", cls: "yti-rs-flagcol" }))),
          h("tbody", null, sortRows(q.focus, sdSort, {
            title: function (d) { return d.p.t; }, channel: function (d) { return d.p.ch; }, niche: function (d) { return d.p.n; },
            m: function (d) { return d.p.m; }, views: function (d) { return d.p.v; }, subs: function (d) { return d.p.sub; },
            mo: function (d) { return d.p.mo && d.p.mo.vph != null ? d.p.mo.vph * (d.p.mo.dir === "down" ? 0.5 : d.p.mo.dir === "up" ? 2 : 1) : -1; },
            flags: function (d) { return d.p.f.length + (d.p.s > 0 ? 1 : 0); } }).slice(0, 60).map(function (d) {
            return h("tr", { key: d.p.id },
              h("td", null, h("a", { className: "yti-video-link", href: "https://www.youtube.com/watch?v=" + d.p.id, target: "_blank", rel: "noopener" }, d.p.t)),
              h("td", { className: "yti-muted yti-small" }, d.p.ch),
              h("td", { className: "yti-right yti-muted yti-small" }, d.p.sub == null ? "?" : formatNumber(d.p.sub)),
              h("td", { className: "yti-muted yti-small" }, d.p.n),
              h("td", { className: "yti-right yti-muted" }, ageText(d) + " ago"),
              h("td", { className: "yti-right" }, fmtMult(d.p.m)),
              h("td", { className: "yti-right yti-muted" }, Math.round(d.fresh * 100) + "%"),
              h("td", { className: "yti-right yti-strong" }, fmtMult(d.adj)),
              h("td", { className: "yti-right" }, formatNumber(d.p.v)),
              h("td", { className: "yti-right yti-small", title: d.p.mo ? momentumText(d.p.mo) : "No readings yet — the pulse (every 6 hours) and the daily snapshot build the trajectory" },
                d.p.mo && d.p.mo.vph != null
                  ? h("span", null, h("span", { className: "yti-sd-mo yti-sd-mo-" + (d.p.mo.dir || "none") }, d.p.mo.dir === "up" ? "↗" : d.p.mo.dir === "down" ? "↘" : d.p.mo.dir === "flat" ? "→" : "·"),
                      " " + formatNumber(Math.round(d.p.mo.vph)) + "/h")
                  : h("span", { className: "yti-muted" }, "—")),
              h("td", { className: "yti-small yti-muted" }, h(FlagChips, { flags: (d.p.s > 0 ? ["date≈"] : []).concat(d.p.f).concat(d.p.sub == null && !d.p.fol ? ["size?"] : []) })));
          }))),
        q.focus.length > 60 ? h("div", { className: "yti-subtle", style: { marginTop: 6 } }, "Showing the first 60 of " + q.focus.length + " in the current sort order.") : null));
  }

  // ---- Formats (D3) ------------------------------------------------------------
  function DiscriminatorList(props) {
    const ds = props.items || [];
    if (!ds.length) return h("div", { className: "yti-muted yti-small" }, "No discriminator splits with both arms n ≥ 5 yet (needs transcripts + packaging).");
    return h("ul", { className: "yti-rs-ul" }, ds.slice(0, props.max || 8).map(function (d, i) {
      return h("li", { key: i }, d.feature + " = " + d.value + ": " + d.hit_rate_with.toFixed(2) + " (n=" + d.n_with + ") vs " +
        d.hit_rate_without.toFixed(2) + " (n=" + d.n_without + ") → Δ " + (d.diff >= 0 ? "+" : "") + d.diff.toFixed(2));
    }));
  }
  function ExampleList(props) {
    return h("ul", { className: "yti-rs-ul" }, (props.items || []).map(function (e) {
      return h("li", { key: e.video_id }, h("b", null, e.multiple + "× "), h("a", { className: "yti-video-link", href: "https://www.youtube.com/watch?v=" + e.video_id, target: "_blank", rel: "noopener" }, e.title),
        h("span", { className: "yti-muted" }, " (" + e.niche + (e.approx ? ", ≈" : "") + ")"));
    }));
  }
  function ResearchFormats(props) {
    const [data, setData] = useState(null);
    const [open, setOpen] = useState(null);
    const [kind, setKind] = useState("");
    const fSort = useSort("wilson_lb", "desc");
    useEffect(function () { api("/research/formats").then(setData).catch(function () { setData({ formats: [] }); }); }, []);
    if (!data) return h("div", { className: "yti-empty" }, "Loading…");
    const rows = sortRows(data.formats.filter(function (f) { return !kind || f.kind === kind; }), fSort, {
      hits: function (f) { return f.n_total ? (f.n_hits || 0) / f.n_total : null; },
      niches: function (f) { return (f.niches || []).join(", "); } });
    return h("div", null,
      h("div", { className: "yti-rs-filter" },
        ["", "seeded", "mined"].map(function (k) { return h("button", { key: k, className: "yti-filter-chip" + (kind === k ? " yti-filter-chip-on" : ""), onClick: function () { setKind(k); } }, k || "all"); }),
        h("span", { className: "yti-muted yti-small" }, "Ranked by ", h(Tip, { k: "wilsonnote" }, "Wilson 95% lower bound"), " of hit rate — a 2-for-2 format cannot outrank a 40-for-60 one. Formats used by fewer than " + data.minActionableN + " scored videos are too thin to act on. Click a header to re-sort.")),
      rows.length === 0 ? h("div", { className: "yti-empty" }, "No formats yet — run Formats on the Run panel (after Score).") :
      h("div", { className: "yti-table-wrap" }, h("table", { className: "yti-table yti-rs-table" },
        h("thead", null, h("tr", null,
          h(Th, { sort: fSort, k: "label", label: "Format", tip: "format", first: "asc" }),
          h(Th, { sort: fSort, k: "kind", label: "Kind", tip: "kind", first: "asc" }),
          h(Th, { sort: fSort, k: "wilson_lb", label: "Wilson LB", tip: "wilson", cls: "yti-right yti-strong" }),
          h(Th, { sort: fSort, k: "hits", label: "Hits / n", tip: "hitsn", cls: "yti-right" }),
          h(Th, { sort: fSort, k: "n_under", label: "Under", tip: "under", cls: "yti-right" }),
          h(Th, { sort: fSort, k: "median_multiple", label: "Median ×", tip: "medianx", cls: "yti-right" }),
          h(Th, { sort: fSort, k: "weighted_hit_rate", label: "Weighted hit", tip: "whit", cls: "yti-right" }),
          h(Th, { sort: fSort, k: "distinct_channels", label: "Channels", tip: "channels", cls: "yti-right" }),
          h(Th, { sort: fSort, k: "niches", label: "Niches", tip: "niches", first: "asc" }),
          h(Th, { sort: fSort, k: "target_niche_uses", label: "Target uses", tip: "targetuses", cls: "yti-right" }))),
        h("tbody", null, rows.map(function (f) {
          const isOpen = open === f.format_id;
          const weak = (f.n_total || 0) < data.minActionableN;
          return [h("tr", { key: f.format_id, className: "yti-rs-clickable" + (weak ? " yti-rs-weak" : ""), onClick: function () { setOpen(isOpen ? null : f.format_id); } },
            h("td", null, (isOpen ? "▼ " : "▶ ") + f.label, f.caution ? h("span", { className: "yti-rs-caution", title: f.caution }, " ⚠") : null),
            h("td", { className: "yti-muted yti-small" }, f.kind),
            h("td", { className: "yti-right yti-strong" }, f.wilson_lb == null ? "—" : Number(f.wilson_lb).toFixed(3)),
            h("td", { className: "yti-right" }, (f.n_hits || 0) + " / " + (f.n_total || 0)),
            h("td", { className: "yti-right yti-muted" }, f.n_under || 0),
            h("td", { className: "yti-right" }, fmtMult(f.median_multiple)),
            h("td", { className: "yti-right yti-muted" }, f.weighted_hit_rate == null ? "—" : Number(f.weighted_hit_rate).toFixed(2)),
            h("td", { className: "yti-right yti-muted" }, f.distinct_channels || 0),
            h("td", { className: "yti-small yti-muted" }, (f.niches || []).join(", ")),
            h("td", { className: "yti-right" }, f.target_niche_uses || 0)),
          isOpen ? h("tr", { key: f.format_id + "-d" }, h("td", { colSpan: 10, className: "yti-rs-detail" },
            f.psychology ? h("div", null, h("b", null, "Psychology: "), f.psychology) : null,
            f.caution ? h("div", null, h("b", null, "Caution: "), f.caution) : null,
            f.known_discriminator ? h("div", null, h("b", null, "Known discriminator: "), f.known_discriminator) : null,
            f.skeleton ? h("div", null, h("b", null, "Skeleton: "), h("code", null, f.skeleton)) : null,
            h("div", { style: { marginTop: 6 } }, h("b", null, "Strongest examples")), h(ExampleList, { items: f.examples }),
            h("div", { style: { marginTop: 6 } }, h("b", null, "Discriminators (hit rate with vs without)")), h(DiscriminatorList, { items: f.discriminators }))) : null];
        }))))
    );
  }

  // ---- Gap report (D4) ------------------------------------------------------------
  function GapCard(props) {
    const r = props.row;
    return h("div", { className: "yti-card yti-rs-gap" + (r.actionable ? "" : " yti-rs-weak") },
      h("div", { className: "yti-rs-head" }, h("h4", { className: "yti-rs-h4" }, r.label),
        h("span", { className: "yti-muted yti-small" }, r.actionable ? "actionable" : "⚠ n < minimum — not actionable")),
      r.psychology ? h("div", { className: "yti-small" }, h("b", null, "Psychology: "), r.psychology) : null,
      r.caution ? h("div", { className: "yti-small yti-rs-caution" }, h("b", null, "Caution: "), r.caution) : null,
      h("div", { className: "yti-rs-kvs" },
        h(KV, { k: "Wilson LB", tip: "wilson", v: Number(r.wilson_lb).toFixed(3) }), h(KV, { k: "hits / n", tip: "hitsn", v: r.n_hits + " / " + r.n_total }),
        h(KV, { k: "under", tip: "under", v: r.n_under }), h(KV, { k: "median ×", tip: "medianx", v: fmtMult(r.median_multiple) }),
        h(KV, { k: "proven in", tip: "niches", v: (r.niches || []).join(", ") }), h(KV, { k: "target uses", tip: "targetuses", v: r.target_niche_uses })),
      h(ExampleList, { items: r.examples }),
      r.known_discriminator ? h("div", { className: "yti-small" }, h("b", null, "Known discriminator: "), r.known_discriminator) : null,
      h(DiscriminatorList, { items: r.discriminators, max: 4 }));
  }
  function ResearchGaps(props) {
    const [data, setData] = useState(null);
    useEffect(function () { api("/research/gaps").then(setData).catch(function () { setData({ gaps: [], near_gaps: [] }); }); }, []);
    if (!data) return h("div", { className: "yti-empty" }, "Loading…");
    return h("div", null,
      h("div", { className: "yti-notice yti-notice-ok" }, data.caveat),
      h("div", { className: "yti-subtle" }, "Target niche: " + (data.target_niche || "not configured") + " · gap = proven in ≥2 niches, Wilson LB ≥ " + data.min_wilson_lb + ", zero uses in the target. Recommendations are refused below n = " + data.min_actionable_n + "."),
      data.gaps.length === 0 ? h("div", { className: "yti-empty" }, "No gap formats yet — the report needs ≥2 crawled niches plus Score and Formats runs.") :
        data.gaps.map(function (r) { return h(GapCard, { key: r.format_id, row: r }); }),
      data.near_gaps.length ? h("div", null, h("h3", { className: "yti-rs-h3", style: { marginTop: 16 } }, "Near-gaps (1–2 uses in the target)"),
        data.near_gaps.map(function (r) { return h(GapCard, { key: r.format_id, row: r }); })) : null);
  }

  // ---- Teardown (D5) -----------------------------------------------------------------
  function SeriesChart(props) {
    const pts = (props.series || []).filter(function (p) { return p.views > 0; });
    if (pts.length < 3) return null;
    const W = 720, H = 160, pad = 24;
    const ys = pts.map(function (p) { return Math.log10(p.views); });
    const min = Math.min.apply(null, ys), max = Math.max.apply(null, ys), rng = (max - min) || 1;
    const x = function (i) { return pad + (i / (pts.length - 1)) * (W - 2 * pad); };
    const y = function (v) { return H - pad - ((v - min) / rng) * (H - 2 * pad); };
    const cps = props.changepoints || [];
    return h("svg", { className: "yti-rs-chart", viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" },
      cps.map(function (c, i) { return h("line", { key: i, x1: x(c.index), x2: x(c.index), y1: pad / 2, y2: H - pad / 2, stroke: "#f59e0b", strokeDasharray: "4 3" }); }),
      pts.map(function (p, i) {
        const col = CLASS_COLOR[p["class"]] || "#9ca3af";
        return h("circle", { key: p.video_id, cx: x(i), cy: y(ys[i]), r: 3, fill: col },
          h("title", null, fmtDate(p.published_at) + " · " + formatNumber(p.views) + " · " + (p["class"] || "") + " · " + p.title));
      }),
      h("text", { x: pad, y: 12, className: "yti-rs-chart-label" }, "log10 views by upload (● class colour, dashed = changepoint)"));
  }
  function ResearchTeardown(props) {
    const ov = props.overview;
    const [channels, setChannels] = useState(null);
    const [sel, setSel] = useState("");
    const [prof, setProf] = useState(null);
    const [busy, setBusy] = useState(false);
    const [q, setQ] = useState("");
    const cdSort = useSort("effect", "desc");
    const [msg, setMsg] = useState(null);
    const loadChannels = function () { api("/research/channels?limit=500").then(function (d) { setChannels(d.channels || []); }).catch(function () { setChannels([]); }); };
    useEffect(loadChannels, []);
    const load = function (id) {
      if (!id) return;
      setProf(null);
      api("/research/profile?channel=" + encodeURIComponent(id)).then(setProf).catch(function (e) { setMsg(String((e && e.message) || e)); });
    };
    useEffect(function () { if (sel) load(sel); }, [sel]);  // eslint-disable-line
    const running = !!(ov && ov.job && ov.job.running);
    const runProfile = function () {
      if (!sel) return;
      setBusy(true); setMsg(null);
      post("/research/run", { job: "profile", params: { channel: sel } }).then(function (r) {
        if (r && r.alreadyRunning) { setMsg("A job is already running."); setBusy(false); return; }
        const id = window.setInterval(function () {
          api("/research/job").then(function (s) { if (!s.running) { window.clearInterval(id); setBusy(false); if (s.error) setMsg(s.error); load(sel); loadChannels(); } }).catch(function () {});
        }, 2000);
      }).catch(function (e) { setMsg(String(e)); setBusy(false); });
    };
    const track = function (ch, on) {
      post("/research/channels/track", { channelId: ch.channel_id, tracked: on }).then(loadChannels).catch(function () {});
    };
    const list = (channels || []).filter(function (c) { return !q || ((c.handle || "") + " " + (c.title || "")).toLowerCase().indexOf(q.toLowerCase()) >= 0; });
    const p = prof && prof.profile;
    return h("div", { className: "yti-rs-teardown" },
      h("div", { className: "yti-card yti-rs-chanlist" },
        h("div", { className: "yti-rs-head" }, h("h3", { className: "yti-rs-h3" }, "Channels (" + ((channels || []).length) + ")"),
          h("input", { className: "yti-artifact-search", placeholder: "filter…", value: q, onChange: function (e) { setQ(e.target.value); } })),
        h("div", { className: "yti-subtle" }, "★ tracked = included in the free daily snapshot. Followed channels are always snapshotted."),
        channels == null ? h("div", { className: "yti-empty" }, "Loading…") :
        h("div", { className: "yti-rs-scroll" }, list.map(function (c) {
          return h("div", { key: c.channel_id, className: "yti-tree-row" + (sel === c.channel_id ? " yti-tree-active" : ""), onClick: function () { setSel(c.channel_id); } },
            h("span", { className: "yti-rs-star" + (c.is_tracked ? " on" : ""), title: c.is_tracked ? "Stop tracking" : "Track daily (free)",
              onClick: function (e) { e.stopPropagation(); track(c, !c.is_tracked); } }, c.is_tracked ? "★" : "☆"),
            h("span", { className: "yti-tree-name" }, c.handle || c.title || c.channel_id),
            h("span", { className: "yti-tree-size" }, (c.hits ? c.hits + " hits · " : "") + c.videos + " v" + (c.profiled ? " · D5" : "")));
        }))),
      h("div", { className: "yti-card yti-rs-flex1" },
        !sel ? h("div", { className: "yti-empty" }, "Pick a channel. The teardown needs ≥10 dated long-form uploads (Snapshot gives the newest 15 exactly; a crawl or Enrich gives the back-catalogue).") :
        h("div", null,
          h("div", { className: "yti-rs-head" },
            h("h3", { className: "yti-rs-h3" }, (prof && prof.channel && (prof.channel.handle || prof.channel.title)) || sel),
            h(Button, { size: "sm", disabled: busy || running, className: busy ? "yti-busy" : "", onClick: runProfile }, busy ? "Profiling…" : (p ? "Re-run teardown" : "Run teardown"))),
          msg ? h("div", { className: "yti-notice yti-notice-error" }, msg) : null,
          prof ? h(SeriesChart, { series: prof.series, changepoints: p ? [{ index: p.changepoint_index }].filter(function (c) { return c.index != null; }) : [] }) : null,
          !prof ? h("div", { className: "yti-empty" }, "Loading…") :
          !p ? h("div", { className: "yti-subtle" }, prof.series.length + " dated long-form uploads known. Run the teardown to detect the inflection point and diff the cohorts.") :
          h("div", null,
            h("div", { className: "yti-rs-kvs" },
              h(KV, { k: "uploads", tip: "uploads", v: p.n_videos }),
              h(KV, { k: "changepoint", tip: "changepoint", v: p.changepoint_date ? fmtDate(p.changepoint_date) + " (p=" + p.changepoint_p + ")" : "none (p > 0.05)" }),
              h(KV, { k: "lift ratio", tip: "lift", v: p.lift_ratio == null ? "—" : Number(p.lift_ratio).toFixed(2) + "×" }),
              h(KV, { k: "coherence", tip: "coherence", v: p.coherence_score == null ? "—" : Number(p.coherence_score).toFixed(3) }),
              h(KV, { k: "computed", tip: "computed", v: formatAgo(p.computed_at) })),
            (p.off_topic_hits || []).length ? h("div", { className: "yti-notice yti-notice-error" }, "Off-topic hits (poor model to double down on): " + p.off_topic_hits.map(function (o) { return o.title; }).join(" · ")) : null,
            (p.cohort_diff || []).length ? h("div", null, h("h4", { className: "yti-rs-h4" }, "Cohort diff (before → after, by effect size)"),
              h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
                  h(Th, { sort: cdSort, k: "feature", label: "feature", tip: "feature", first: "asc" }),
                  h(Th, { sort: cdSort, k: "before", label: "before", tip: "before" }),
                  h(Th, { sort: cdSort, k: "after", label: "after", tip: "after" }),
                  h(Th, { sort: cdSort, k: "n", label: "n", tip: "n", cls: "yti-right" }),
                  h(Th, { sort: cdSort, k: "effect", label: "effect", tip: "effect", cls: "yti-right" }))),
                h("tbody", null, sortRows(p.cohort_diff, cdSort, {
                  before: function (d) { return typeof d.before === "object" ? JSON.stringify(d.before) : d.before; },
                  after: function (d) { return typeof d.after === "object" ? JSON.stringify(d.after) : d.after; },
                  n: function (d) { return (d.n_before || 0) + (d.n_after || 0); } }).slice(0, 30).map(function (d, i) {
                  const fmt = function (v) { return typeof v === "object" ? JSON.stringify(v) : String(v); };
                  return h("tr", { key: d.feature + i }, h("td", null, h(Tip, { k: "feat:" + d.feature }, d.feature)), h("td", { className: "yti-small" }, fmt(d.before)), h("td", { className: "yti-small" }, fmt(d.after)),
                    h("td", { className: "yti-right yti-muted" }, d.n_before + "/" + d.n_after), h("td", { className: "yti-right yti-strong" }, d.effect));
                })))) : h("div", { className: "yti-subtle" }, "No significant changepoint — no cohort diff."),
            (p.doubling_down || []).length ? h("div", null, h("h4", { className: "yti-rs-h4" }, "Doubling-down candidates"),
              p.doubling_down.map(function (c) {
                return h("div", { key: c.video_id, className: "yti-rs-dd" },
                  h("b", null, c.multiple + "× "), h("a", { className: "yti-video-link", href: "https://www.youtube.com/watch?v=" + c.video_id, target: "_blank", rel: "noopener" }, c.title),
                  h("div", { className: "yti-small yti-muted" }, JSON.stringify(c.profile)),
                  (c.formats || []).map(function (f) {
                    return h("div", { key: f.format_id, className: "yti-small" }, "format: " + f.label + " · slots " + JSON.stringify(f.slots),
                      (f.proven_elsewhere || []).length ? h("ul", { className: "yti-rs-ul" }, f.proven_elsewhere.map(function (o, i) { return h("li", { key: i }, "proven in " + o.niche + " at " + o.multiple + "× with " + JSON.stringify(o.slots)); })) : null);
                  }),
                  h("div", { className: "yti-small yti-muted" }, c.framing));
              })) : null,
            p.llm_teardown ? h("div", { className: "yti-md" }, renderMarkdown(p.llm_teardown)) : null))));
  }

  // ---- Budget + reports --------------------------------------------------------------
  function ResearchBudget(props) {
    const [data, setData] = useState(null);
    const [reports, setReports] = useState(null);
    const [view, setView] = useState(null);
    const epSort = useSort("credits", "desc");
    const daySort = useSort("day", "desc");
    useEffect(function () {
      api("/research/budget").then(setData).catch(function () { setData({}); });
      api("/research/reports").then(setReports).catch(function () { setReports({ files: [] }); });
    }, []);
    const openReport = function (f) {
      api("/research/report-file?path=" + encodeURIComponent(f.relPath)).then(function (r) { setView({ name: f.name, content: r.content || "" }); }).catch(function () {});
    };
    if (!data) return h("div", { className: "yti-empty" }, "Loading…");
    const totals = data.totals || [];
    return h("div", null,
      h("div", { className: "yti-stats-row" },
        totals.map(function (t) { return h(StatCard, { key: t.provider, value: formatNumber(t.credits), label: t.provider + " credits (all time, " + t.calls + " calls)" }); }),
        (data.today || []).map(function (t) { return h(StatCard, { key: "t" + t.provider, value: formatNumber(t.credits), label: t.provider + " today" }); }),
        h(StatCard, { value: String(data.quarantine || 0), label: "quarantined records" })),
      h("div", { className: "yti-subtle" }, "Caps: " + JSON.stringify(data.caps || {}) + " · the ledger reads only api_usage; failures are recorded with credits=0."),
      h("div", { className: "yti-rs-row" },
        h("div", { className: "yti-card yti-rs-flex1" }, h("h3", { className: "yti-rs-h3" }, "By endpoint (" + data.days + "d)"),
          h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
            h(Th, { sort: epSort, k: "provider", label: "provider", tip: "provider", first: "asc" }),
            h(Th, { sort: epSort, k: "endpoint", label: "endpoint", tip: "endpoint", first: "asc" }),
            h(Th, { sort: epSort, k: "credits", label: "credits", tip: "credits", cls: "yti-right" }),
            h(Th, { sort: epSort, k: "calls", label: "calls", tip: "calls", cls: "yti-right" }),
            h(Th, { sort: epSort, k: "failures", label: "failures", tip: "failures", cls: "yti-right" }))),
            h("tbody", null, sortRows(data.byEndpoint || [], epSort).map(function (r, i) { return h("tr", { key: i }, h("td", null, r.provider), h("td", { className: "yti-small" }, r.endpoint), h("td", { className: "yti-right" }, r.credits), h("td", { className: "yti-right yti-muted" }, r.calls), h("td", { className: "yti-right yti-muted" }, r.failures)); })))),
        h("div", { className: "yti-card yti-rs-flex1" }, h("h3", { className: "yti-rs-h3" }, "By day"),
          h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
            h(Th, { sort: daySort, k: "day", label: "day", tip: "day" }),
            h(Th, { sort: daySort, k: "provider", label: "provider", tip: "provider", first: "asc" }),
            h(Th, { sort: daySort, k: "credits", label: "credits", tip: "credits", cls: "yti-right" }),
            h(Th, { sort: daySort, k: "calls", label: "calls", tip: "calls", cls: "yti-right" }))),
            h("tbody", null, sortRows(data.byDay || [], daySort).slice(0, 40).map(function (r, i) { return h("tr", { key: i }, h("td", null, r.day), h("td", null, r.provider), h("td", { className: "yti-right" }, r.credits), h("td", { className: "yti-right yti-muted" }, r.calls)); }))))),
      h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Reports (", h(Tip, { k: "d15" }, "D1–D5"), ")"),
        h("div", { className: "yti-subtle" }, "Markdown written to the workspace under research/reports/ — also browsable on the Artifacts tab."),
        !reports || !reports.files.length ? h("div", { className: "yti-empty" }, "No reports yet — run Report on the Run panel.") :
        h("div", { className: "yti-chip-row" }, reports.files.map(function (f) {
          return h("button", { key: f.relPath, className: "yti-filter-chip", onClick: function () { openReport(f); } }, f.date + " · " + f.name);
        })),
        view ? h("div", { className: "yti-rs-report" }, h("div", { className: "yti-rs-head" }, h("b", null, view.name), h(Button, { size: "sm", variant: "outline", onClick: function () { setView(null); } }, "Close")),
          h("div", { className: "yti-md" }, renderMarkdown(view.content))) : null));
  }

  function ResearchView() {
    const [overview, setOverview] = useState(null);
    const [panel, setPanel] = useState("run");
    const [err, setErr] = useState(null);
    const reload = useCallback(function () {
      api("/research/overview").then(function (d) { setOverview(d); setErr(null); }).catch(function (e) { setErr(String((e && e.message) || e)); });
    }, []);
    useEffect(function () { reload(); }, [reload]);
    useEffect(function () {
      if (overview && overview.niches && overview.niches.length === 0 && panel === "run") setPanel("setup");
    }, [overview]);  // eslint-disable-line
    const counts = (overview && overview.counts) || {};
    const body = panel === "setup" ? h(ResearchSetup, { overview: overview, reload: reload, goRun: function () { setPanel("run"); } })
      : panel === "run" ? h(ResearchRun, { overview: overview, reload: reload })
      : panel === "outliers" ? h(ResearchOutliers, { overview: overview })
      : panel === "supply" ? h(SupplyDemandView, { overview: overview })
      : panel === "formats" ? h(ResearchFormats, {})
      : panel === "gaps" ? h(ResearchGaps, {})
      : panel === "teardown" ? h(ResearchTeardown, { overview: overview })
      : h(ResearchBudget, {});
    return h("div", { className: "yti-page yti-rs-page" },
      h("div", { className: "yti-header" },
        h("h1", { className: "yti-title" }, "YouTube Research"),
        h("div", { className: "yti-header-actions" }, h(Button, { size: "sm", variant: "outline", onClick: reload }, "Refresh"))),
      h("div", { className: "yti-subtle" },
        "Niche teardown engine: crawl a niche depth-first, find videos that beat their own channel's baseline, mine the ",
        "packaging formats behind them with their failure rates, and surface formats proven elsewhere but absent from your niche.",
        overview && overview.lastRuns && overview.lastRuns.snapshot ? " · last snapshot " + formatAgo(overview.lastRuns.snapshot) : ""),
      err ? h("div", { className: "yti-notice yti-notice-error" }, err) : null,
      h(ChannelManager, { title: "Followed Channels", defaultOpen: true,
        hint: "Same list as the Trends tab. Every followed channel is snapshotted daily for free (exact views), and its uploads are scored against its own baseline." }),
      h("div", { className: "yti-stats-row" },
        h(StatCard, { value: String(counts.channels_long != null ? counts.channels_long : (counts.channels || 0)), label: "Channels", tip: "stat:channels" }),
        h(StatCard, { value: String(counts.videos_long != null ? counts.videos_long : (counts.videos || 0)), label: "Long-form videos", tip: "stat:videos" }),
        h(StatCard, { value: String(counts.hits_long != null ? counts.hits_long : (counts.hits || 0)), label: "Hits (≥3× baseline)", tip: "stat:hits" }),
        h(StatCard, { value: String(counts.formats || 0), label: "Formats", tip: "stat:formats" }),
        h(StatCard, { value: String(counts.video_snapshots || 0), label: "Snapshot points", tip: "stat:snapshots" })),
      h("div", { className: "yti-subtle", style: { marginTop: -12 } },
        "Long-form only. ",
        counts.videos_short ? formatNumber(counts.videos_short) + " Shorts are kept out of every number, list and format on this tab (they stay in the data for the Short Form page)." : "",
        counts.videos_unknown_form ? " " + counts.videos_unknown_form + " videos of unknown length are held back until their duration is known." : "",
        counts.videos_out_of_niche ? h("span", null, " ", h(Tip, { k: "scope" }, formatNumber(counts.videos_out_of_niche) + " off-topic videos"), " (channels that are not about the niche) are tagged and hidden.") : null,
        counts.channels_unknown_size ? " " + counts.channels_unknown_size + " channels still need a size lookup (Run → Channel sizes)." : ""),
      h("div", { className: "yti-rs-nav" }, RS_PANELS.map(function (p) {
        return h("button", { key: p[0], className: "yti-rs-pill" + (panel === p[0] ? " yti-rs-pill-on" : ""), onClick: function () { setPanel(p[0]); } }, p[1]);
      })),
      overview === null ? h("div", { className: "yti-empty" }, "Loading…") : body);
  }

  // -------------------------------------------------------------------------
  // Root page: Research | Trends | Insights | Artifacts sub-tabs
  // -------------------------------------------------------------------------

  function YouTubeInsightsPage() {
    const [view, setView] = useState("research");
    return h("div", { className: "yti-root" },
      // same gutters as the page content (24px) and the Short Form top
      // margin (28px)
      h("div", { style: { padding: "28px 24px 0", marginBottom: 14 } },
        h("div", { style: { fontSize: 13, letterSpacing: 1.5,
                            color: "var(--color-muted-foreground, #9aa0b4)",
                            textTransform: "uppercase" } },
          "AI Cyber Value Creator™"),
        h("h1", { style: { fontSize: 30, margin: "4px 0 6px",
                           fontWeight: 800 } },
          "🎬 Long Form")),
      h("div", { className: "yti-tabs" },
        h("button", {
          className: "yti-tab" + (view === "research" ? " yti-tab-active" : ""),
          onClick: function () { setView("research"); },
        }, "Research"),
        h("button", {
          className: "yti-tab" + (view === "trends" ? " yti-tab-active" : ""),
          onClick: function () { setView("trends"); },
        }, "Trends"),
        h("button", {
          className: "yti-tab" + (view === "insights" ? " yti-tab-active" : ""),
          onClick: function () { setView("insights"); },
        }, "Insights"),
        h("button", {
          className: "yti-tab" + (view === "artifacts" ? " yti-tab-active" : ""),
          onClick: function () { setView("artifacts"); },
        }, "Artifacts")
      ),
      view === "research" ? h(ResearchView)
        : view === "trends" ? h(TrendsView)
        : view === "insights" ? h(InsightsView)
        : h(ArtifactsView)
    );
  }

  // -------------------------------------------------------------------------
  // Register
  // -------------------------------------------------------------------------

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("youtube-insights", YouTubeInsightsPage);
  }
})();
