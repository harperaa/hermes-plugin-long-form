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
      h("div", { className: "yti-stat-label" }, props.label)
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
      ? "/kanban#task=" + encodeURIComponent(g.taskId) : null;

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

    const sorted = videos.slice().sort(function (a, b) {
      const mul = sortAsc ? 1 : -1;
      if (sortField === "vph") return (a.vph - b.vph) * mul;
      if (sortField === "views") return (a.views - b.views) * mul;
      return (new Date(a.published).getTime() - new Date(b.published).getTime()) * mul;
    });

    // clamp the page if the list shrank or the page size grew
    const maxPage = Math.max(0, Math.ceil(sorted.length / pageSize) - 1);
    const curPage = Math.min(page, maxPage);
    const paged = sorted.slice(curPage * pageSize, (curPage + 1) * pageSize);

    const topVph = videos.length
      ? Math.max.apply(null, videos.map(function (v) { return v.vph; }))
      : 0;

    const sortGlyph = function (field) {
      if (sortField !== field) return field === "vph" ? " ↓" : "";
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
                  h("th", null, "Title"),
                  h("th", null, "Channel"),
                  h("th", {
                    className: "yti-sortable",
                    onClick: function () { toggleSort("published"); },
                  }, "Published" + sortGlyph("published")),
                  h("th", { className: "yti-right" }, "Duration"),
                  h("th", {
                    className: "yti-right yti-sortable",
                    onClick: function () { toggleSort("views"); },
                  }, "Views" + sortGlyph("views")),
                  h("th", {
                    className: "yti-right yti-sortable yti-strong",
                    onClick: function () { toggleSort("vph"); },
                  }, "VPH" + sortGlyph("vph")),
                  h("th", { className: "yti-center" }, "Trend"),
                  h("th", { className: "yti-center" }, "Status"),
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
      ? "/kanban#task=" + encodeURIComponent(prod.taskId) : null;
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
  const RS_PANELS = [["setup", "Setup"], ["run", "Run"], ["outliers", "Outliers"], ["formats", "Formats"],
    ["gaps", "Gap Report"], ["teardown", "Teardown"], ["budget", "Budget & Reports"]];

  function fmtMult(x) { return x == null ? "—" : Number(x).toFixed(2) + "×"; }
  function fmtPct(x) { return x == null ? "—" : Math.round(x) + ""; }
  function fmtDate(iso) { return iso ? String(iso).slice(0, 10) : "—"; }
  function post(path, body) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function ClassBadge(props) {
    const c = props.cls || "immature";
    return h("span", { className: "yti-rs-badge", style: { borderColor: CLASS_COLOR[c] || "#6b7280", color: CLASS_COLOR[c] || "#6b7280" } }, c);
  }
  function KV(props) {
    return h("div", { className: "yti-rs-kv" }, h("div", { className: "yti-rs-kv-k" }, props.k), h("div", { className: "yti-rs-kv-v" }, props.v));
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

  // ---- Setup ---------------------------------------------------------------
  function ResearchSetup(props) {
    const ov = props.overview;
    const [cfg, setCfg] = useState(null);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const [doctor, setDoctor] = useState(null);
    const [doctoring, setDoctoring] = useState(false);
    const [advanced, setAdvanced] = useState(false);
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
      h("div", { className: "yti-rs-row" },
        h("div", { className: "yti-card yti-rs-flex1" },
          h("h3", { className: "yti-rs-h3" }, "Providers"),
          h("div", { className: "yti-rs-secrets" },
            h("span", { className: "yti-rs-dot " + (sec.transcriptapi ? "on" : "off") }), "TRANSCRIPT_API_KEY ",
            h("span", { className: "yti-muted" }, sec.transcriptapi ? "set — Tier 0/1/3 (discovery, transcripts, free daily snapshots)" : "missing — required"),
            h("br"),
            h("span", { className: "yti-rs-dot " + (sec.apify ? "on" : "off") }), "APIFY_API_TOKEN ",
            h("span", { className: "yti-muted" }, sec.apify ? "set — Tier 2/3 (exact metrics, comments)" : "missing — precision + comments stages are skipped")),
          h("div", { className: "yti-subtle", style: { marginTop: 8 } }, "Keys live only in the environment / ",
            h("a", { href: "/env" }, "Keys page"), ". They are never written to config, logs, or URLs."),
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
        h("div", { className: "yti-rs-head" },
          h("h3", { className: "yti-rs-h3" }, "Niches"),
          h("div", { className: "yti-actions" },
            h(Button, { size: "sm", variant: "outline", onClick: addNiche }, "+ Niche"),
            h(Button, { size: "sm", disabled: saving, onClick: save }, saving ? "Saving…" : "Save config"))),
        h("div", { className: "yti-subtle" },
          "One target niche plus adjacent niches that share viewer INTENT (not subject matter). Seed terms in the ",
          "market's own problem language (\"lose visceral fat\", not \"visceral adiposity reduction\"). ",
          "Outcome terms = what they already want; mechanism terms = what you actually do. The D4 gap report only ",
          "exists when ≥2 niches are crawled."),
        msg ? h("div", { className: "yti-notice " + (msg.tone === "error" ? "yti-notice-error" : "yti-notice-ok") }, msg.text) : null,
        (ov && ov.configProblems && ov.configProblems.length) ? h("div", { className: "yti-notice yti-notice-error" }, ov.configProblems.join(" · ")) : null,
        niches.length === 0 ? h("div", { className: "yti-empty" }, "No niches yet — add your target niche to begin.") : null,
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
                onChange: function (v) { setNiche(i, { mechanism_terms: v.split("\n") }); } })));
        }),
        h("div", { className: "yti-channels-toggle", style: { marginTop: 10 }, onClick: function () { setAdvanced(!advanced); } },
          (advanced ? "▼" : "▶") + " Advanced: crawl / scoring / formats / budget"),
        advanced ? h("div", { className: "yti-rs-grid yti-rs-grid-4" },
          [["crawl", "max_depth"], ["crawl", "search_pages_per_term"], ["crawl", "channel_pages_per_channel"], ["crawl", "outliers_to_expand_per_node"],
           ["crawl", "prune_after_barren_nodes"], ["crawl", "recommendations_per_video"], ["crawl", "min_subscribers"], ["crawl", "max_subscribers"],
           ["scoring", "baseline_window"], ["scoring", "min_baseline_videos"], ["scoring", "hit_multiple"], ["scoring", "strong_multiple"],
           ["scoring", "underperformer_multiple"], ["scoring", "breakout_watch_max_age_days"], ["scoring", "breakout_watch_comment_pct"],
           ["formats", "min_support"], ["formats", "min_distinct_channels"], ["formats", "min_distinct_niches"], ["formats", "gap_min_wilson_lb"],
           ["formats", "min_actionable_n"], ["budget", "transcriptapi_credits"], ["budget", "crawl_default_credits"], ["budget", "apify_max_results"],
           ["apify", "max_comments_per_video"]].map(function (pair) {
            const s = pair[0], k = pair[1];
            return h(Field, { key: s + k, label: s + "." + k, type: "number", step: "any", value: cfg[s][k] == null ? "" : cfg[s][k], onChange: setNum(s, k) });
          })) : null));
  }

  // ---- Run -------------------------------------------------------------------
  function ResearchRun(props) {
    const ov = props.overview;
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
          h("div", { className: "yti-rs-step" }, h("b", null, "Tier 0 · History (free)"),
            h("div", { className: "yti-muted yti-small" }, "Exact daily views for every followed + tracked channel. Cron this daily; it is the moat."),
            btn("Snapshot", "snapshot", {}, "Free RSS snapshot of the latest ~15 uploads per tracked channel")),
          h("div", { className: "yti-rs-step" }, h("b", null, "Tier 1 · Discovery"),
            h("div", { className: "yti-muted yti-small" }, "Depth-first crawl on approximate data (1 credit per ~20-100 rows). Barren branches are pruned."),
            h("div", { className: "yti-rs-inline" },
              h("input", { className: "yti-rs-mini", placeholder: "max credits (" + (ov && ov.config ? ov.config.budget.crawl_default_credits : 150) + ")", value: maxCredits,
                onChange: function (e) { setMaxCredits(e.target.value); } }),
              btn("Dry run", "crawl", { dry_run: true }, "Plan the calls and projected credits without spending anything"),
              btn("Crawl", "crawl", { max_credits: cp(maxCredits) }, "Run the DFS crawl", "default"))),
          h("div", { className: "yti-rs-step" }, h("b", null, "Tier 2 · Precision"),
            h("div", { className: "yti-muted yti-small" }, "Apify exact views / likes / comments / duration / subscribers for the shortlist only."),
            btn("Enrich", "enrich", {}, sec.apify ? "Enrich hits with exact numbers" : "APIFY_API_TOKEN missing")),
          h("div", { className: "yti-rs-step" }, h("b", null, "Tier 3 · Depth"),
            h("div", { className: "yti-muted yti-small" }, "Transcripts (packaging + structure) and comments (satisfaction + sentiment) for the shortlist."),
            h("div", { className: "yti-rs-inline" },
              btn("Transcripts", "transcripts", {}, "Free caption check first; one credit per transcript"),
              btn("Comments", "comments", {}, sec.apify ? "Apify comment bodies" : "APIFY_API_TOKEN missing"))),
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
        h("h3", { className: "yti-rs-h3" }, "Dry run — projected Tier-1 credits"),
        h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null,
          h("th", null, "niche"), h("th", { className: "yti-right" }, "seed terms"), h("th", { className: "yti-right" }, "search calls"),
          h("th", { className: "yti-right" }, "channel calls (worst)"), h("th", { className: "yti-right" }, "rec calls (worst)"), h("th", { className: "yti-right" }, "credits (worst)"))),
          h("tbody", null, (plan.niches || []).map(function (n) {
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
          h("th", null, "run"), h("th", null, "status"), h("th", { className: "yti-right" }, "nodes"), h("th", { className: "yti-right" }, "new videos"),
          h("th", { className: "yti-right" }, "new outliers"), h("th", { className: "yti-right" }, "credits"), h("th", null, "stop reason"), h("th", null, ""))),
          h("tbody", null, ov.crawlRuns.map(function (r) {
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
    const [sort, setSort] = useState("projected_multiple");
    const [data, setData] = useState(null);
    const [demand, setDemand] = useState(null);
    const [showDemand, setShowDemand] = useState(false);
    useEffect(function () {
      const p = new URLSearchParams({ niche: niche, classes: classes.join(","), sort: sort, limit: "150" });
      api("/research/outliers?" + p.toString()).then(setData).catch(function () { setData({ rows: [], total: 0, counts: {} }); });
    }, [niche, classes, sort]);
    useEffect(function () { if (showDemand && !demand) api("/research/demand").then(setDemand).catch(function () {}); }, [showDemand]);  // eslint-disable-line
    const toggle = function (c) { setClasses(classes.indexOf(c) >= 0 ? classes.filter(function (x) { return x !== c; }) : classes.concat([c])); };
    const rows = (data && data.rows) || [];
    const counts = (data && data.counts) || {};
    return h("div", null,
      h("div", { className: "yti-rs-filter" },
        h("select", { className: "yti-select", value: niche, onChange: function (e) { setNiche(e.target.value); } },
          h("option", { value: "" }, "All niches"),
          ((ov && ov.niches) || []).concat(["followed"]).map(function (n) { return h("option", { key: n, value: n }, n); })),
        ["strong_hit", "hit", "normal", "under", "immature"].map(function (c) {
          return h("button", { key: c, className: "yti-filter-chip" + (classes.indexOf(c) >= 0 ? " yti-filter-chip-on" : ""),
            onClick: function () { toggle(c); } }, c + (counts[c] != null ? " " + counts[c] : ""));
        }),
        h("select", { className: "yti-select", value: sort, onChange: function (e) { setSort(e.target.value); } },
          [["projected_multiple", "× projected"], ["z", "robust z"], ["views", "views"], ["age", "age"], ["vs", "satisfaction"], ["weight", "signal weight"]].map(function (o) {
            return h("option", { key: o[0], value: o[0] }, "sort: " + o[1]); })),
        h("button", { className: "yti-filter-chip" + (showDemand ? " yti-filter-chip-on" : ""), onClick: function () { setShowDemand(!showDemand); } }, "D1 Demand map")),
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
        h("div", { className: "yti-subtle" }, data.total + " videos · ≈ marks approximate Tier-1 numbers · raw = date too coarse to project"),
        h("table", { className: "yti-table yti-rs-table" },
          h("thead", null, h("tr", null, h("th", null, ""), h("th", null, "Title"), h("th", null, "Channel"), h("th", null, "Niche"), h("th", null, "Class"),
            h("th", { className: "yti-right yti-strong" }, "× proj"), h("th", { className: "yti-right" }, "× raw"), h("th", { className: "yti-right" }, "z"),
            h("th", { className: "yti-right" }, "Views"), h("th", { className: "yti-right" }, "Age d"), h("th", { className: "yti-right" }, "Weight"),
            h("th", { className: "yti-right" }, "VS %"), h("th", null, "Flags"))),
          h("tbody", null, rows.map(function (r) {
            const flags = [];
            if (r.views_approx) flags.push("views≈");
            if (r.published_approx) flags.push("date≈");
            if (r.raw_only) flags.push("raw");
            if (r.organic_flag === "suspect_paid") flags.push("⚠ paid?");
            if (r.breakout_watch) flags.push("🚀 breakout");
            if (r.fade_watch) flags.push("fade");
            if (r.precision_tier >= 2) flags.push("exact");
            return h("tr", { key: r.video_id },
              h("td", null, r.thumbnail_url ? h("img", { className: "yti-thumb yti-rs-thumb", src: r.thumbnail_url, alt: "" }) : null),
              h("td", null, h("a", { className: "yti-video-link", href: "https://www.youtube.com/watch?v=" + r.video_id, target: "_blank", rel: "noopener" }, r.title)),
              h("td", { className: "yti-muted yti-small" }, r.handle || r.channel_title || r.channel_id),
              h("td", { className: "yti-muted yti-small" }, r.niche),
              h("td", null, h(ClassBadge, { cls: r["class"] })),
              h("td", { className: "yti-right yti-strong" }, fmtMult(r.projected_multiple)),
              h("td", { className: "yti-right yti-muted" }, fmtMult(r.multiple)),
              h("td", { className: "yti-right yti-muted" }, r.log_mad_z == null ? "—" : Number(r.log_mad_z).toFixed(1)),
              h("td", { className: "yti-right" }, formatNumber(r.views)),
              h("td", { className: "yti-right yti-muted" }, r.age_days == null ? "—" : Math.round(r.age_days)),
              h("td", { className: "yti-right yti-muted" }, r.signal_weight == null ? "—" : Number(r.signal_weight).toFixed(2)),
              h("td", { className: "yti-right yti-muted" }, fmtPct(r.vs_percentile)),
              h("td", { className: "yti-small yti-muted" }, flags.join(" ")));
          })))));
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
    useEffect(function () { api("/research/formats").then(setData).catch(function () { setData({ formats: [] }); }); }, []);
    if (!data) return h("div", { className: "yti-empty" }, "Loading…");
    const rows = data.formats.filter(function (f) { return !kind || f.kind === kind; });
    return h("div", null,
      h("div", { className: "yti-rs-filter" },
        ["", "seeded", "mined"].map(function (k) { return h("button", { key: k, className: "yti-filter-chip" + (kind === k ? " yti-filter-chip-on" : ""), onClick: function () { setKind(k); } }, k || "all"); }),
        h("span", { className: "yti-muted yti-small" }, "Ranked by Wilson 95% lower bound of hit rate (P2) — a 2-for-2 format cannot outrank a 40-for-60 one. n < " + data.minActionableN + " is not actionable.")),
      rows.length === 0 ? h("div", { className: "yti-empty" }, "No formats yet — run Formats on the Run panel (after Score).") :
      h("div", { className: "yti-table-wrap" }, h("table", { className: "yti-table yti-rs-table" },
        h("thead", null, h("tr", null, h("th", null, "Format"), h("th", null, "Kind"), h("th", { className: "yti-right yti-strong" }, "Wilson LB"),
          h("th", { className: "yti-right" }, "Hits / n"), h("th", { className: "yti-right" }, "Under"), h("th", { className: "yti-right" }, "Median ×"),
          h("th", { className: "yti-right" }, "Weighted hit"), h("th", { className: "yti-right" }, "Channels"), h("th", null, "Niches"), h("th", { className: "yti-right" }, "Target uses"))),
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
        h(KV, { k: "Wilson LB", v: Number(r.wilson_lb).toFixed(3) }), h(KV, { k: "hits / n", v: r.n_hits + " / " + r.n_total }),
        h(KV, { k: "under", v: r.n_under }), h(KV, { k: "median ×", v: fmtMult(r.median_multiple) }),
        h(KV, { k: "proven in", v: (r.niches || []).join(", ") }), h(KV, { k: "target uses", v: r.target_niche_uses })),
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
              h(KV, { k: "uploads", v: p.n_videos }),
              h(KV, { k: "changepoint", v: p.changepoint_date ? fmtDate(p.changepoint_date) + " (p=" + p.changepoint_p + ")" : "none (p > 0.05)" }),
              h(KV, { k: "lift ratio", v: p.lift_ratio == null ? "—" : Number(p.lift_ratio).toFixed(2) + "×" }),
              h(KV, { k: "coherence", v: p.coherence_score == null ? "—" : Number(p.coherence_score).toFixed(3) }),
              h(KV, { k: "computed", v: formatAgo(p.computed_at) })),
            (p.off_topic_hits || []).length ? h("div", { className: "yti-notice yti-notice-error" }, "Off-topic hits (poor model to double down on): " + p.off_topic_hits.map(function (o) { return o.title; }).join(" · ")) : null,
            (p.cohort_diff || []).length ? h("div", null, h("h4", { className: "yti-rs-h4" }, "Cohort diff (before → after, by effect size)"),
              h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null, h("th", null, "feature"), h("th", null, "before"), h("th", null, "after"), h("th", { className: "yti-right" }, "n"), h("th", { className: "yti-right" }, "effect"))),
                h("tbody", null, p.cohort_diff.slice(0, 30).map(function (d, i) {
                  const fmt = function (v) { return typeof v === "object" ? JSON.stringify(v) : String(v); };
                  return h("tr", { key: i }, h("td", null, d.feature), h("td", { className: "yti-small" }, fmt(d.before)), h("td", { className: "yti-small" }, fmt(d.after)),
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
          h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null, h("th", null, "provider"), h("th", null, "endpoint"), h("th", { className: "yti-right" }, "credits"), h("th", { className: "yti-right" }, "calls"), h("th", { className: "yti-right" }, "failures"))),
            h("tbody", null, (data.byEndpoint || []).map(function (r, i) { return h("tr", { key: i }, h("td", null, r.provider), h("td", { className: "yti-small" }, r.endpoint), h("td", { className: "yti-right" }, r.credits), h("td", { className: "yti-right yti-muted" }, r.calls), h("td", { className: "yti-right yti-muted" }, r.failures)); })))),
        h("div", { className: "yti-card yti-rs-flex1" }, h("h3", { className: "yti-rs-h3" }, "By day"),
          h("table", { className: "yti-table yti-rs-table" }, h("thead", null, h("tr", null, h("th", null, "day"), h("th", null, "provider"), h("th", { className: "yti-right" }, "credits"), h("th", { className: "yti-right" }, "calls"))),
            h("tbody", null, (data.byDay || []).slice(0, 40).map(function (r, i) { return h("tr", { key: i }, h("td", null, r.day), h("td", null, r.provider), h("td", { className: "yti-right" }, r.credits), h("td", { className: "yti-right yti-muted" }, r.calls)); }))))),
      h("div", { className: "yti-card", style: { marginTop: 12 } },
        h("h3", { className: "yti-rs-h3" }, "Reports (D1–D5)"),
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
    const body = panel === "setup" ? h(ResearchSetup, { overview: overview, reload: reload })
      : panel === "run" ? h(ResearchRun, { overview: overview, reload: reload })
      : panel === "outliers" ? h(ResearchOutliers, { overview: overview })
      : panel === "formats" ? h(ResearchFormats, {})
      : panel === "gaps" ? h(ResearchGaps, {})
      : panel === "teardown" ? h(ResearchTeardown, { overview: overview })
      : h(ResearchBudget, {});
    return h("div", { className: "yti-page" },
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
        h(StatCard, { value: String(counts.channels || 0), label: "Channels seen" }),
        h(StatCard, { value: String(counts.videos || 0), label: "Videos" }),
        h(StatCard, { value: String(counts.hits || 0), label: "Hits (≥3× baseline)" }),
        h(StatCard, { value: String(counts.formats || 0), label: "Formats" }),
        h(StatCard, { value: String(counts.video_snapshots || 0), label: "Snapshot points" })),
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
