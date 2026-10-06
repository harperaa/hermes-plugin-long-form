"""Depth-first niche crawler (spec §7).

Explicit LIFO stack; follows one branch to exhaustion; prunes a parent
branch after ``prune_after_barren_nodes`` consecutive zero-yield children,
where yield = NEWLY recorded provisional outliers (not total results).

Tier 1 only: approximate views ("3.4M views") and fuzzy dates ("2 years
ago"). Provisional scores live in ``videos.provisional_multiple`` and are
never written to ``scores`` — the §8 pass does that after enrichment.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

try:
    from . import yti_rs_db, yti_rs_normalize, yti_rs_relevance
    from .yti_rs_budget import BudgetExceeded
    from .yti_rs_clients import ClientError, CreditsExhausted
except ImportError:  # pragma: no cover
    import yti_rs_db  # type: ignore
    import yti_rs_normalize  # type: ignore
    import yti_rs_relevance  # type: ignore
    from yti_rs_budget import BudgetExceeded  # type: ignore
    from yti_rs_clients import ClientError, CreditsExhausted  # type: ignore


class Node:
    __slots__ = ("id", "type", "key", "niche", "depth", "parent_id", "rank")

    def __init__(self, type_: str, key: str, niche: str, depth: int,
                 parent_id: Optional[int] = None, rank: float = 0.0, id_: Optional[int] = None) -> None:
        self.id, self.type, self.key, self.niche = id_, type_, key, niche
        self.depth, self.parent_id, self.rank = depth, parent_id, rank

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "key": self.key, "niche": self.niche,
                "depth": self.depth, "parent_id": self.parent_id, "rank": self.rank}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Node":
        return cls(d["type"], d["key"], d["niche"], d["depth"], d.get("parent_id"),
                   d.get("rank", 0.0), d.get("id"))


def provisional_multiple(views: Optional[int], others: list[int], min_n: int = 3) -> Optional[float]:
    """views / median(views of this channel's other known results)."""
    if views is None:
        return None
    vals = [v for v in others if v is not None and v > 0]
    if len(vals) < min_n:
        return None
    med = statistics.median(vals)
    return views / med if med > 0 else None


def estimate_catalog_dates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill ``published_at`` for undated catalogue rows from the channel's
    median upload interval among dated rows (coarse granularity → excluded
    from age projection, but usable for ordering and decay)."""
    dated = [(r["catalog_index"], datetime.fromisoformat(r["published_at"]))
             for r in rows if r.get("published_at") and r.get("catalog_index") is not None]
    if len(dated) < 2:
        return rows
    dated.sort()
    gaps = []
    for (i1, d1), (i2, d2) in zip(dated, dated[1:]):
        if i2 > i1:
            gaps.append((d1 - d2).total_seconds() / 86400 / (i2 - i1))
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return rows
    interval = statistics.median(gaps)
    last_i, last_d = dated[-1]
    for r in rows:
        if r.get("published_at") or r.get("catalog_index") is None or r["catalog_index"] <= last_i:
            continue
        steps = r["catalog_index"] - last_i
        r["published_at"] = (last_d - timedelta(days=interval * steps)).isoformat()
        r["published_approx"] = 1
        r["published_granularity_days"] = max(7.0, interval * steps * 0.5)
    return rows


class Crawler:
    def __init__(self, conn: sqlite3.Connection, cfg: dict[str, Any], tapi, budget, *,
                 run_id: Optional[str] = None, now: Optional[datetime] = None,
                 log: Callable[[str], None] = lambda m: None) -> None:
        self.conn, self.cfg, self.tapi, self.budget = conn, cfg, tapi, budget
        self.run_id = run_id or "crawl-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") \
            + "-" + uuid.uuid4().hex[:6]
        self.now = now or datetime.now(timezone.utc)
        self.log = log
        self.c = cfg.get("crawl", {})
        self.s = cfg.get("scoring", {})
        self.stack: list[Node] = []
        self.visited: set[tuple[str, str]] = set()
        self.barren: dict[Optional[int], int] = {}
        self.stats: dict[str, Any] = {"nodes": 0, "videos_new": 0, "outliers_new": 0,
                                      "channels_new": 0, "pruned": 0, "errors": 0,
                                      "quarantined": 0, "skipped_offtopic": 0, "skipped_size": 0}
        self.vocabs = {n["name"]: yti_rs_relevance.niche_vocab(n) for n in cfg.get("niches") or []}
        self.stop_reason: Optional[str] = None

    # -- persistence -------------------------------------------------------------
    def _insert_node(self, node: Node, status: str = "pending") -> None:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO crawl_nodes(run_id, node_type, node_key, niche, depth, parent_id,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.run_id, node.type, node.key, node.niche, node.depth, node.parent_id, status,
             yti_rs_db.now_iso()))
        if cur.lastrowid and cur.rowcount:
            node.id = cur.lastrowid
        else:
            row = self.conn.execute("SELECT id FROM crawl_nodes WHERE run_id=? AND node_type=? AND node_key=?",
                                    (self.run_id, node.type, node.key)).fetchone()
            node.id = row["id"] if row else None

    def _set_status(self, node_id: Optional[int], status: str, yield_count: int = 0) -> None:
        if node_id is None:
            return
        self.conn.execute("UPDATE crawl_nodes SET status=?, yield_count=? WHERE id=?",
                          (status, yield_count, node_id))

    def _save_run(self, status: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO crawl_runs(run_id, started_at, finished_at, status, niches_json,"
            " stack_json, stats_json) VALUES (?, COALESCE((SELECT started_at FROM crawl_runs WHERE run_id=?), ?),"
            " ?, ?, ?, ?, ?)",
            (self.run_id, self.run_id, yti_rs_db.now_iso(),
             yti_rs_db.now_iso() if status in ("done", "aborted", "paused", "error") else None,
             status, json.dumps(self._niche_names), json.dumps([n.to_json() for n in self.stack]),
             json.dumps({**self.stats, "stop_reason": self.stop_reason,
                         "credits": self.budget.spent("transcriptapi") if self.budget else None})))
        self.conn.commit()

    # -- public ----------------------------------------------------------------
    def seed(self, niche_names: Optional[list[str]] = None) -> None:
        niches = self.cfg.get("niches") or []
        if niche_names:
            niches = [n for n in niches if n["name"] in niche_names]
        # target first, then adjacents; reversed so the target pops first
        ordered = sorted(niches, key=lambda n: (0 if n.get("is_target") else 1))
        self._niche_names = [n["name"] for n in ordered]
        seeds: list[Node] = []
        for n in ordered:
            for term in n.get("seed_terms") or []:
                seeds.append(Node("seed_term", term.strip(), n["name"], 0))
        for node in reversed(seeds):
            self._insert_node(node)
            self.stack.append(node)

    def seed_channels(self, handles: list[str], *, depth: int = 1) -> int:
        """Seed the dashboard's followed channels as ``channel`` nodes in the
        target niche so their back-catalogue is pulled (durations + baselines)
        and their outliers feed the format engine. Handles already known to
        research.db are used directly; unknown ones are resolved for free."""
        tgt = next((n for n in self.cfg.get("niches") or [] if n.get("is_target")), None)
        niche = (tgt or {}).get("name") or "followed"
        n = 0
        nodes: list[Node] = []
        seen: set[str] = set()
        for h in handles:
            h = yti_rs_normalize.channel_handle_clean(h)
            if not h:
                continue
            row = yti_rs_db.one(self.conn, "SELECT channel_id FROM channels WHERE lower(handle) = lower(?)", (h,))
            cid = row["channel_id"] if row else None
            if not cid:
                try:
                    cid = (self.tapi.resolve(h) or {}).get("channel_id")
                except ClientError as exc:
                    self.log(f"resolve {h} failed: {exc}")
                    continue
                if not cid:
                    continue
                yti_rs_db.upsert_channel(self.conn, {"channel_id": cid, "handle": h, "niche": niche, "is_tracked": 1})
            if ("channel", cid) in self.visited or cid in seen:
                continue
            seen.add(cid)
            nodes.append(Node("channel", cid, niche, depth))
            n += 1
        # followed channels sit UNDER the seed terms on the LIFO stack, so the
        # niche search still runs first; they expand once the search branch
        # is exhausted or pruned.
        for node in reversed(nodes):
            self._insert_node(node)
            self.stack.insert(0, node)
        self.conn.commit()
        return n

    def resume(self, run_id: str) -> bool:
        row = yti_rs_db.one(self.conn, "SELECT * FROM crawl_runs WHERE run_id = ?", (run_id,))
        if not row:
            return False
        self.run_id = run_id
        self._niche_names = json.loads(row["niches_json"] or "[]")
        self.stack = [Node.from_json(d) for d in json.loads(row["stack_json"] or "[]")]
        for r in yti_rs_db.rows(self.conn, "SELECT node_type, node_key FROM crawl_nodes "
                                           "WHERE run_id = ? AND status != 'pending'", (run_id,)):
            self.visited.add((r["node_type"], r["node_key"]))
        try:
            self.stats.update({k: v for k, v in json.loads(row["stats_json"] or "{}").items()
                               if k in self.stats})
        except json.JSONDecodeError:
            pass
        return True

    def run(self) -> dict[str, Any]:
        self._save_run("running")
        max_depth = int(self.c.get("max_depth", 3))
        prune_after = int(self.c.get("prune_after_barren_nodes", 2))
        try:
            while self.stack:
                if self.budget is not None and not self.budget.ok("transcriptapi", 1):
                    raise BudgetExceeded("transcriptapi", self.budget.spent("transcriptapi"),
                                         self.budget.caps.get("transcriptapi", 0))
                node = self.stack.pop()
                if (node.type, node.key) in self.visited:
                    self._set_status(node.id, "done")
                    continue
                self.visited.add((node.type, node.key))
                if node.id is None:
                    self._insert_node(node)
                try:
                    children, yield_count = self.expand(node)
                    self._set_status(node.id, "done", yield_count)
                except CreditsExhausted:
                    raise
                except BudgetExceeded:
                    self.stack.append(node)
                    self.visited.discard((node.type, node.key))
                    raise
                except ClientError as exc:
                    self.stats["errors"] += 1
                    self.log(f"node {node.type}:{node.key} failed: {exc}")
                    self._set_status(node.id, "error")
                    children, yield_count = [], 0
                self.stats["nodes"] += 1
                self.stats["outliers_new"] += yield_count
                self.log(f"[{node.depth}] {node.type} {node.key!r} → {len(children)} children, "
                         f"yield {yield_count}")
                # barren-branch pruning (§7.2): count consecutive zero-yield
                # children per parent; on the threshold, drop the parent's
                # remaining children from the stack.
                if node.parent_id is not None:
                    if yield_count == 0:
                        self.barren[node.parent_id] = self.barren.get(node.parent_id, 0) + 1
                        if self.barren[node.parent_id] >= prune_after:
                            before = len(self.stack)
                            self.stack = [n for n in self.stack if n.parent_id != node.parent_id]
                            dropped = before - len(self.stack)
                            if dropped:
                                self.stats["pruned"] += dropped
                                self._set_status(node.parent_id, "pruned")
                                self.log(f"pruned branch of node {node.parent_id}: {dropped} nodes dropped")
                    else:
                        self.barren[node.parent_id] = 0
                if node.depth >= max_depth:
                    continue
                for child in children:
                    child.parent_id = node.id
                    child.depth = node.depth + 1
                    self._insert_node(child)
                for child in reversed(children):   # preserve rank order under LIFO
                    self.stack.append(child)
                self.conn.commit()
                if self.stats["nodes"] % 10 == 0:
                    self._save_run("running")
            self.stop_reason = "exhausted"
            self._save_run("done")
        except BudgetExceeded as exc:
            self.stop_reason = str(exc)
            self.log(self.stop_reason)
            self._save_run("paused")
        except CreditsExhausted as exc:
            self.stop_reason = f"aborted: {exc}"
            self.log(self.stop_reason)
            self._save_run("aborted")
        return {"run_id": self.run_id, "stop_reason": self.stop_reason, **self.stats,
                "credits": self.budget.spent("transcriptapi") if self.budget else None,
                "remaining_stack": len(self.stack)}

    # -- expansion -----------------------------------------------------------------
    def expand(self, node: Node) -> tuple[list[Node], int]:
        if node.type == "seed_term":
            return self._expand_seed(node)
        if node.type == "search_term":
            return self._expand_search(node)
        if node.type == "video":
            return self._expand_video(node)
        if node.type == "channel":
            return self._expand_channel(node)
        return [], 0

    def _expand_seed(self, node: Node) -> tuple[list[Node], int]:
        variants = self.c.get("term_variants") or ["{term}"]
        children = []
        seen = set()
        for tpl in variants:
            term = tpl.format(term=node.key).strip()
            if term.lower() in seen:
                continue
            seen.add(term.lower())
            children.append(Node("search_term", term, node.niche, node.depth + 1))
        return children, 0

    def _expand_search(self, node: Node) -> tuple[list[Node], int]:
        pages = int(self.c.get("search_pages_per_term", 2))
        results: list[dict[str, Any]] = []
        continuation = None
        for page in range(pages):
            try:
                data = self.tapi.search(node.key, "video", continuation)
            except ClientError as exc:
                if page == 0:
                    raise
                self.log(f"search {node.key!r} page {page + 1} failed: {exc}")
                self.stats["errors"] += 1
                break
            results.extend(r for r in (data.get("results") or []) if r.get("type", "video") == "video")
            continuation = data.get("continuation_token")
            if not data.get("has_more") or not continuation:
                break
        return self._absorb_results(results, node, f"search_term:{node.key}")

    def _expand_video(self, node: Node) -> tuple[list[Node], int]:
        video = yti_rs_db.one(self.conn, "SELECT * FROM videos WHERE video_id = ?", (node.key,))
        if not video:
            return [], 0
        children: list[Node] = []
        if ("channel", video["channel_id"]) not in self.visited:
            children.append(Node("channel", video["channel_id"], node.niche, node.depth + 1,
                                 rank=float(video.get("views") or 0)))
        # Recommendation substitute (§7.2): keyword search seeded with the
        # outlier's title — a proxy for what YouTube associates with it.
        n_rec = int(self.c.get("recommendations_per_video", 3))
        yield_count = 0
        if n_rec > 0:
            data = self.tapi.search(video["title"][:120], "video")
            recs = [r for r in (data.get("results") or [])
                    if r.get("type", "video") == "video" and r.get("videoId") != node.key
                    and r.get("channelId") != video["channel_id"]]
            rec_children, yield_count = self._absorb_results(recs, node, "recommendation",
                                                             top_n=n_rec, push_channels=False)
            children.extend(rec_children)
        return children, yield_count

    def _channel_gate(self, node: Node, ch: dict[str, Any], sample_titles: list[str]) -> Optional[str]:
        """Why this channel must NOT be expanded, or None when it may be.

        Followed/tracked channels always pass. Otherwise (1) relevance, free:
        too few of the channel's recent titles mention the niche's vocabulary
        -> it is a general-interest channel that happened to publish one
        relevant video; (2) size, 1 credit when unknown: outside the configured
        subscriber band -> not a comparable channel. Both verdicts need
        evidence; with none the channel is given the benefit of the doubt."""
        if ch.get("is_tracked"):
            return None
        vocab = self.vocabs.get(node.niche)
        if vocab:
            known = [r["title"] for r in yti_rs_db.rows(
                self.conn, "SELECT title FROM videos WHERE channel_id = ? AND (is_short IS NULL OR is_short = 0)",
                (node.key,))]
            titles = list(dict.fromkeys(sample_titles + known))
            verdict = yti_rs_relevance.channel_on_topic(
                titles, vocab, min_share=float(self.c.get("channel_relevance_min", 0.20)),
                min_titles=int(self.c.get("relevance_min_titles", 5)))
            if verdict is False:
                share, n = yti_rs_relevance.channel_share(titles, vocab)
                return f"off-topic ({share:.0%} of {n} recent titles mention the niche's terms)"
        subs = ch.get("subscriber_count")
        if subs is None:
            try:
                from .yti_rs_enrich import lookup_channel_size
            except ImportError:  # pragma: no cover
                from yti_rs_enrich import lookup_channel_size  # type: ignore
            try:
                subs = lookup_channel_size(self.conn, self.tapi, {**ch, "channel_id": node.key})
            except (CreditsExhausted, BudgetExceeded):
                raise
            except ClientError as exc:
                self.log(f"size lookup {node.key} failed: {exc}")
        if subs is not None:
            lo, hi = int(self.c.get("min_subscribers", 0) or 0), int(self.c.get("max_subscribers", 0) or 0)
            if subs < lo or (hi and subs > hi):
                return f"outside the subscriber band (~{subs:,}; band {lo:,}-{hi:,})"
        return None

    def _expand_channel(self, node: Node) -> tuple[list[Node], int]:
        ch = yti_rs_db.one(self.conn, "SELECT * FROM channels WHERE channel_id = ?", (node.key,))
        ident = (ch or {}).get("handle") or node.key
        # free, exact: newest ~15 uploads with exact dates + integer views
        try:
            latest = self.tapi.latest(ident)
        except ClientError as exc:
            self.log(f"latest {ident} failed: {exc}")
            latest = {}
        sample = [str(r.get("title") or "") for r in (latest.get("results") or [])
                  if "/shorts/" not in str(r.get("link") or "")] if isinstance(latest, dict) else []
        reason = self._channel_gate(node, ch or {"channel_id": node.key}, sample)
        if reason:
            self.stats["skipped_size" if reason.startswith("outside") else "skipped_offtopic"] += 1
            self.log(f"channel {ident} not expanded: {reason}")
            return [], 0
        exact: dict[str, dict[str, Any]] = {}
        if isinstance(latest, dict) and latest.get("results"):
            chinfo = latest.get("channel") or {}
            yti_rs_db.upsert_channel(self.conn, {
                "channel_id": node.key, "title": chinfo.get("title") or (ch or {}).get("title"),
                "niche": (ch or {}).get("niche") or node.niche, "raw_json": chinfo})
            for r in latest["results"]:
                exact[str(r.get("videoId"))] = r
        pages = int(self.c.get("channel_pages_per_channel", 2))
        rows: list[dict[str, Any]] = []
        continuation = None
        idx = 0
        for page in range(pages):
            try:
                data = self.tapi.channel_videos(ident, continuation)
            except ClientError as exc:
                if page == 0:
                    raise
                self.log(f"channel {ident} page {page + 1} failed: {exc}")
                self.stats["errors"] += 1
                break
            for r in data.get("results") or []:
                vid = str(r.get("videoId") or "")
                if not vid:
                    continue
                views, approx = yti_rs_normalize.parse_views(r.get("viewCountText"))
                rec = {
                    "video_id": vid, "channel_id": node.key,
                    "title": str(r.get("title") or "untitled"),
                    "duration_seconds": yti_rs_normalize.parse_duration(r.get("lengthText")),
                    "catalog_index": int(r.get("index")) if str(r.get("index", "")).isdigit() else idx,
                    "views": views, "views_approx": int(approx),
                    "thumbnail_url": _thumb(r), "niche": node.niche,
                    "discovered_via": "channel", "discovery_depth": node.depth, "raw_json": r,
                }
                if vid in exact:
                    e = exact[vid]
                    ev, _ = yti_rs_normalize.parse_views(e.get("viewCount"))
                    pub, _, _ = yti_rs_normalize.parse_published(e.get("published"))
                    if ev is not None:
                        rec["views"], rec["views_approx"] = ev, 0
                    rec["published_at"], rec["published_approx"] = pub, 0
                    rec["published_granularity_days"] = 0.0
                    rec["description"] = e.get("description")
                if rec["views"] is None:
                    yti_rs_db.quarantine(self.conn, "transcriptapi", "channel/videos: unparseable views", r)
                    self.stats["quarantined"] += 1
                    continue
                rec["is_short"] = _short_int(rec["duration_seconds"])
                rows.append(rec)
                idx += 1
            continuation = data.get("continuation_token")
            if not data.get("has_more") or not continuation:
                break
        # any exact rows not in the paged catalogue (e.g. a brand-new upload)
        known = {r["video_id"] for r in rows}
        for vid, e in exact.items():
            if vid in known:
                continue
            ev, _ = yti_rs_normalize.parse_views(e.get("viewCount"))
            pub, _, _ = yti_rs_normalize.parse_published(e.get("published"))
            if ev is None:
                continue
            rows.insert(0, {"video_id": vid, "channel_id": node.key, "title": str(e.get("title") or "untitled"),
                            "published_at": pub, "published_approx": 0, "published_granularity_days": 0.0,
                            "views": ev, "views_approx": 0, "catalog_index": -1,
                            "is_short": _form_int(yti_rs_normalize.is_short_rss(None, str(e.get("link") or ""))),
                            "description": e.get("description"), "niche": node.niche,
                            "discovered_via": "channel", "discovery_depth": node.depth, "raw_json": e})
        rows = estimate_catalog_dates(rows)
        # provisional scoring: trailing median over the 20 OLDER catalogue rows,
        # long-form only (Shorts have their own distribution — §8.1)
        ordered = sorted(rows, key=lambda r: (r.get("catalog_index") if r.get("catalog_index") is not None else 0))
        longform = [r for r in ordered if r.get("is_short") != 1]
        window = int(self.s.get("baseline_window", 20))
        hit = float(self.s.get("hit_multiple", 3.0))
        outliers: list[dict[str, Any]] = []
        yield_count = 0
        for i, r in enumerate(longform):
            older = [x["views"] for x in longform[i + 1:i + 1 + window]]
            r["provisional_multiple"] = provisional_multiple(r["views"], older)
        for r in rows:
            new = yti_rs_db.upsert_video(self.conn, r)
            self.stats["videos_new"] += int(new)
            pm = r.get("provisional_multiple")
            if pm is not None and pm >= hit:
                outliers.append(r)
                if new:
                    yield_count += 1
        self.conn.commit()
        n = int(self.c.get("outliers_to_expand_per_node", 4))
        outliers.sort(key=lambda r: r["provisional_multiple"], reverse=True)
        children = [Node("video", r["video_id"], node.niche, node.depth + 1, rank=r["provisional_multiple"])
                    for r in outliers[:n] if ("video", r["video_id"]) not in self.visited]
        return children, yield_count

    def _absorb_results(self, results: list[dict[str, Any]], node: Node, via: str, *,
                        top_n: Optional[int] = None, push_channels: bool = True) -> tuple[list[Node], int]:
        """Record search-shaped rows (approximate metrics), score provisionally
        against what the DB already knows about each channel, and return
        children: outlier video nodes + (optionally) channel nodes."""
        n = top_n or int(self.c.get("outliers_to_expand_per_node", 4))
        hit = float(self.s.get("hit_multiple", 3.0))
        window = int(self.s.get("baseline_window", 20))
        yield_count = 0
        scored: list[tuple[float, dict[str, Any]]] = []
        by_views: list[tuple[int, dict[str, Any]]] = []
        for r in results:
            vid = str(r.get("videoId") or "")
            cid = str(r.get("channelId") or "")
            if not vid or not cid:
                continue
            views, approx = yti_rs_normalize.parse_views(r.get("viewCountText"))
            if views is None:
                yti_rs_db.quarantine(self.conn, "transcriptapi", f"{via}: unparseable views", r)
                self.stats["quarantined"] += 1
                continue
            pub, pub_approx, gran = yti_rs_normalize.parse_published(r.get("publishedTimeText"), self.now)
            dur = yti_rs_normalize.parse_duration(r.get("lengthText"))
            handle = yti_rs_normalize.channel_handle_clean(r.get("channelHandle"))
            existed = self.conn.execute("SELECT 1 FROM channels WHERE channel_id = ?", (cid,)).fetchone()
            yti_rs_db.upsert_channel(self.conn, {
                "channel_id": cid, "handle": handle, "title": r.get("channelTitle"),
                "niche": node.niche if not existed else None})
            if not existed:
                self.stats["channels_new"] += 1
            rec = {
                "video_id": vid, "channel_id": cid, "title": str(r.get("title") or "untitled"),
                "published_at": pub, "published_approx": int(pub_approx),
                "published_granularity_days": gran, "duration_seconds": dur,
                "is_short": _short_int(dur), "views": views, "views_approx": int(approx),
                "thumbnail_url": _thumb(r), "niche": node.niche, "discovered_via": via,
                "discovery_depth": node.depth, "raw_json": r,
            }
            others = [x["views"] for x in yti_rs_db.rows(
                self.conn, "SELECT views FROM videos WHERE channel_id = ? AND video_id != ? "
                           "AND views IS NOT NULL AND (is_short IS NULL OR is_short = 0) "
                           "ORDER BY COALESCE(published_at,'') DESC LIMIT ?", (cid, vid, window))]
            # Shorts are recorded (the Short Form page will use them) but never
            # scored against a long-form baseline, never counted as yield and
            # never expanded: this crawl researches long-form.
            is_short_row = rec["is_short"] == 1
            # "similar video" searches drift: a news outlier's title finds more
            # news. A recommendation is only followed when it is about the niche.
            vocab = self.vocabs.get(node.niche)
            drifted = (via == "recommendation" and bool(vocab)
                       and not yti_rs_relevance.title_matches(rec["title"], vocab))
            is_short_row = is_short_row or drifted
            pm = None if is_short_row else provisional_multiple(views, others)
            rec["provisional_multiple"] = pm
            new = yti_rs_db.upsert_video(self.conn, rec)
            self.stats["videos_new"] += int(new)
            if pm is not None and pm >= hit:
                scored.append((pm, rec))
                if new:
                    yield_count += 1
            if not is_short_row:
                by_views.append((views, rec))
        self.conn.commit()
        scored.sort(key=lambda t: t[0], reverse=True)
        children = [Node("video", rec["video_id"], node.niche, node.depth + 1, rank=pm)
                    for pm, rec in scored[:n] if ("video", rec["video_id"]) not in self.visited]
        if push_channels:
            # unknown channels can't be scored yet — expand the biggest ones so
            # their back-catalogue gives us a baseline (§7.1 video→channel)
            by_views.sort(key=lambda t: t[0], reverse=True)
            seen: set[str] = set()
            for views, rec in by_views:
                cid = rec["channel_id"]
                if cid in seen or ("channel", cid) in self.visited:
                    continue
                seen.add(cid)
                children.append(Node("channel", cid, node.niche, node.depth + 1, rank=float(views)))
                if len(seen) >= n:
                    break
        return children, yield_count


def _thumb(r: dict[str, Any]) -> Optional[str]:
    th = r.get("thumbnails")
    if isinstance(th, list) and th:
        best = max(th, key=lambda t: t.get("width") or 0)
        return best.get("url")
    return None


def _form_int(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(value)


def _short_int(duration: Optional[int]) -> Optional[int]:
    s = yti_rs_normalize.is_short(duration)
    return None if s is None else int(s)


def plan(cfg: dict[str, Any], niche_names: Optional[list[str]] = None) -> dict[str, Any]:
    """``--dry-run``: the planned Tier-1 calls and projected credit cost."""
    c = cfg.get("crawl", {})
    niches = [n for n in cfg.get("niches") or [] if not niche_names or n["name"] in niche_names]
    variants = len(c.get("term_variants") or ["{term}"])
    pages = int(c.get("search_pages_per_term", 2))
    per_node = int(c.get("outliers_to_expand_per_node", 4))
    ch_pages = int(c.get("channel_pages_per_channel", 2))
    recs = 1 if int(c.get("recommendations_per_video", 3)) > 0 else 0
    lines = []
    total = 0
    for n in niches:
        terms = len(n.get("seed_terms") or [])
        search_calls = terms * variants * pages
        # worst case: every search term expands `per_node` channels, each
        # channel `ch_pages` paid pages, each channel yields `per_node` video
        # nodes costing one recommendation search each (depth cap 3 applies)
        channel_calls = terms * variants * per_node * ch_pages
        video_calls = terms * variants * per_node * per_node * recs
        est = search_calls + channel_calls + video_calls
        total += est
        lines.append({"niche": n["name"], "seed_terms": terms, "search_calls": search_calls,
                      "channel_calls_worst": channel_calls, "recommendation_calls_worst": video_calls,
                      "credits_worst_case": est})
    return {"niches": lines, "credits_worst_case": total,
            "note": "Worst case before barren-branch pruning and the credit cap; the free "
                    "channel/latest calls are not counted. Typical runs land at 30-50% of this."}
