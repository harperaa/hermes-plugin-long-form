/**
 * youtube-insights — Hermes Dashboard Plugin
 *
 * Trends + Insights pages, ported from the original paperclip plugin with the
 * same layout, panels, and functions, restyled onto the hermes design system.
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
    const [newChannel, setNewChannel] = useState("");
    const [showChannels, setShowChannels] = useState(false);
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

    const handleAddChannel = function (e) {
      e.preventDefault();
      const handle = newChannel.trim();
      if (!handle) return;
      api("/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: handle }),
      }).then(function (d) {
        setChannels((d && d.channels) || []);
        setNewChannel("");
      }).catch(function (e2) { setNotice({ tone: "error", text: String(e2) }); });
    };

    const handleRemoveChannel = function (handle) {
      api("/channels/" + encodeURIComponent(handle), { method: "DELETE" })
        .then(function (d) { setChannels((d && d.channels) || []); })
        .catch(function () {});
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

      // Channel management
      h("div", { className: "yti-card yti-channels-card" },
        h("div", {
          className: "yti-channels-toggle",
          onClick: function () { setShowChannels(!showChannels); },
        }, (showChannels ? "▼" : "▶") + " Tracked Channels (" + channels.length + ")"),
        showChannels ? h("div", { className: "yti-channels-body" },
          h("div", { className: "yti-chip-row" },
            channels.map(function (ch) {
              return h("span", { key: ch, className: "yti-chip yti-chip-channel" },
                ch,
                h("span", {
                  className: "yti-chip-remove",
                  title: "Stop tracking " + ch,
                  onClick: function () { handleRemoveChannel(ch); },
                }, "✕")
              );
            })
          ),
          h("form", { className: "yti-add-channel", onSubmit: handleAddChannel },
            h(Input, {
              value: newChannel,
              placeholder: "@ChannelHandle",
              onChange: function (e) { setNewChannel(e.target.value); },
            }),
            h(Button, { size: "sm", type: "submit" }, "Add")
          )
        ) : null
      ),

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
            : "Generate all beat images, 3 thumbnails, and the production PDF into this script's assets folder",
          onClick: produceScript,
          className: "yti-produce-btn",
        }, prodOpen ? "Producing…" : "Produce 🎥") : null,
        isScript ? h(Button, {
          size: "sm",
          variant: "outline",
          disabled: iterOpen,
          title: iterOpen
            ? "Iterating — the script is being rewritten from your steering"
            : "Rewrite this script from its concept doc, steered by your input",
          onClick: function () { setSteering(""); setIterModal(true); },
        }, iterOpen ? "Iterating…" : "Iterate ↻") : null,
        isAssetImage ? h(Button, {
          size: "sm",
          disabled: regenOpen,
          title: regenOpen
            ? "Regenerating — a fixed version of this image is being generated"
            : "Regenerate JUST this image with your feedback (e.g. a misspelling), then rebuild the deck PDF — no other image is touched",
          onClick: function () { setRegenFeedback(""); setRegenModal(true); },
        }, regenOpen ? "Regenerating…" : "Regenerate ↻") : null,
        isDeckPdf ? h(Button, {
          size: "sm",
          variant: "outline",
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
                   margin: "0 16px", borderRadius: 8,
                   border: "1px solid color-mix(in srgb, currentColor 25%, transparent)" },
        }) : null,
        h("div", { className: "yti-actions" },
          pipeline.available ? h(Button, {
            size: "sm",
            disabled: pipelineRunning,
            title: pipelineRunning
              ? "Running — the content pipeline is finding gaps and writing 3 new topic script sets"
              : "Run the twice-daily content pipeline now: find fresh gaps across tracked videos and write 3 new topics x 3 scripts into today's recommended folder",
            onClick: runPipeline,
          }, pipelineRunning ? "Running…" : "3 More 🔁") : null,
          h(Button, {
            size: "sm",
            disabled: topicOpen,
            title: topicOpen
              ? "Generating — a topic script set is being written into today's recommended folder"
              : "Generate a new 3-script set (standard, hot take, contrarian) on a topic of your choice, grounded in the insights database",
            onClick: function () { setTopicModal(true); },
          }, topicOpen ? "Generating…" : "Generate ✨"),
          h(Button, {
            size: "sm",
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
          style: { display: "block", maxWidth: "100%", maxHeight: "82vh",
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
  // Root page: Trends | Insights | Artifacts sub-tabs
  // -------------------------------------------------------------------------

  function YouTubeInsightsPage() {
    const [view, setView] = useState("trends");
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
      view === "trends" ? h(TrendsView)
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
