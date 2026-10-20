/* kAIrOS — one desktop across all monitors [R90] (docs/ONE-DESKTOP-2026-10-19.md): the monitor math of the shell.

   The kiosk is ONE page over every monitor (.xinitrc: an --app window at 0,0 with the X screen's size). Windows are DOM
   nodes of that page, so they move between monitors like on any OS; what the page needs to know is where the monitors
   are. A monitor is {id, x, y, w, h, primary} in this page's CSS pixels. Pure functions, no DOM — loaded before
   shell.js, and by the tests (tests/test_one_desktop.py runs the shared cases through this file and kernel/monitors.py).
   * normalize: a mirror (two outputs at the same origin) is ONE monitor, the area both show; exactly one is primary
     (X's primary, else the top-left one); order primary first, then left-to-right, top-to-bottom.
   * the dead areas: the parts of the bounding box no monitor shows (1920x360 under a 1080p panel next to a 1440p monitor).
   * a window's monitor: the one under its title bar's centre, else the one it overlaps most, else the nearest.
   * a new window: on the target monitor's work area (the primary's minus the menu bar and the dock), cascaded.
   * a dropped window: its title bar must lie on monitors entirely — never in a dead area, never under the menu bar or
     in the dock's band at the primary's bottom — else it moves the shortest way until it does (clampTitle).
   * snap: a drop with the pointer at a monitor's OUTER edge (no monitor beyond it) — left / right half, top = maximise;
     the edge between two monitors is a doorway, not a snap zone.
   * a layout change (hot-plug, the display API): a window keeps its place on a monitor that still exists (moved with
     it), one on a monitor that is gone goes to the primary (relocate), then clampTitle. */
"use strict";

const KMon = (() => {
  const BAR = 34;          // a window's title bar (.win-bar)
  const MENU = 30;         // the menu bar, on the primary only
  const MIN_W = 280, MIN_H = 180;

  const num = (v, d = 0) => { const n = Number(v); return Number.isFinite(n) ? Math.round(n) : d; };

  /* [{id, x, y, w, h, primary}] -> the monitor list (see the header) */
  function normalize(list) {
    const groups = new Map(), out = [];
    for (const r of list || []) {
      const x = num(r.x), y = num(r.y), w = num(r.w), h = num(r.h);
      if (w <= 0 || h <= 0) continue;
      const k = x + "," + y, g = groups.get(k);
      if (!g) {
        const m = { id: String(r.id == null ? "monitor-" + out.length : r.id), x, y, w, h, primary: !!r.primary };
        groups.set(k, m); out.push(m);
      } else {                               // a mirror: the area all of them show
        g.w = Math.min(g.w, w); g.h = Math.min(g.h, h);
        if (r.primary) { g.primary = true; g.id = String(r.id); }
      }
    }
    if (!out.length) return [];
    let keep = out.find((m) => m.primary);
    if (!keep) keep = out.reduce((a, b) => (b.y < a.y || (b.y === a.y && b.x < a.x) ? b : a));
    for (const m of out) m.primary = m === keep;
    out.sort((a, b) => (b.primary - a.primary) || (a.x - b.x) || (a.y - b.y));
    return out;
  }

  /* the ?mon= start hint .xinitrc writes (kernel/monitors.py hint()): "id:x,y,w,h,p;…" */
  function parseHint(s) {
    const list = [];
    for (const part of String(s || "").split(";")) {
      if (!part) continue;
      const i = part.lastIndexOf(":");
      const id = i >= 0 ? part.slice(0, i) : "";
      const v = (i >= 0 ? part.slice(i + 1) : part).split(",").map(Number);
      if (v.length < 4 || v.slice(0, 4).some((n) => !Number.isFinite(n))) continue;
      list.push({ id: id || "monitor-" + list.length, x: v[0], y: v[1], w: v[2], h: v[3], primary: v[4] === 1 });
    }
    return normalize(list);
  }

  function union(mons) {
    if (!mons || !mons.length) return null;
    const x0 = Math.min(...mons.map((m) => m.x)), y0 = Math.min(...mons.map((m) => m.y));
    const x1 = Math.max(...mons.map((m) => m.x + m.w)), y1 = Math.max(...mons.map((m) => m.y + m.h));
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  function at(mons, x, y) {
    for (let i = 0; i < mons.length; i++) {
      const m = mons[i];
      if (m.x <= x && x < m.x + m.w && m.y <= y && y < m.y + m.h) return i;
    }
    return -1;
  }

  function nearest(mons, x, y) {
    let best = Infinity, bi = 0;
    mons.forEach((m, i) => {
      const dx = Math.max(m.x - x, 0, x - (m.x + m.w - 1)), dy = Math.max(m.y - y, 0, y - (m.y + m.h - 1));
      const d = dx * dx + dy * dy;
      if (d < best) { best = d; bi = i; }
    });
    return bi;
  }

  const uniq = (a) => [...new Set(a)].sort((p, q) => p - q);

  /* same algorithm as kernel/monitors.py dead_areas(): grid over every edge, runs along rows, merged downwards */
  function deadAreas(mons) {
    const u = union(mons);
    if (!u) return [];
    const xs = uniq([u.x, u.x + u.w, ...mons.map((m) => m.x), ...mons.map((m) => m.x + m.w)]);
    const ys = uniq([u.y, u.y + u.h, ...mons.map((m) => m.y), ...mons.map((m) => m.y + m.h)]);
    const out = [];
    for (let j = 0; j < ys.length - 1; j++) {
      let run = null;
      for (let i = 0; i < xs.length - 1; i++) {
        const dead = at(mons, (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2) < 0;
        if (dead && run) run.w = xs[i + 1] - run.x;
        else if (dead) { run = { x: xs[i], y: ys[j], w: xs[i + 1] - xs[i], h: ys[j + 1] - ys[j] }; out.push(run); }
        else run = null;
      }
    }
    const merged = [];
    for (const r of out) {
      const prev = merged.find((p) => p.x === r.x && p.w === r.w && p.y + p.h === r.y);
      if (prev) prev.h += r.h; else merged.push(Object.assign({}, r));
    }
    return merged;
  }

  const area = (r) => Math.max(0, r.w) * Math.max(0, r.h);
  function inter(a, b) {
    const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
    const w = Math.min(a.x + a.w, b.x + b.w) - x, h = Math.min(a.y + a.h, b.y + b.h) - y;
    return w > 0 && h > 0 ? { x, y, w, h } : null;
  }

  /* where windows may have their title bar: the monitors, the primary's menu bar strip and dock band taken off */
  function usable(mons, dockRoom = 0) {
    return mons.map((m) => (m.primary ? { x: m.x, y: m.y + MENU, w: m.w, h: Math.max(BAR, m.h - MENU - Math.max(0, dockRoom)) }
                                      : { x: m.x, y: m.y, w: m.w, h: m.h }));
  }

  /* is rect r entirely on the given rects? (a grid over every edge inside r: overlapping rects counted once) */
  function covered(rects, r) {
    if (r.w <= 0 || r.h <= 0) return true;
    const xs = uniq([r.x, r.x + r.w, ...rects.flatMap((q) => [q.x, q.x + q.w]).filter((v) => v > r.x && v < r.x + r.w)]);
    const ys = uniq([r.y, r.y + r.h, ...rects.flatMap((q) => [q.y, q.y + q.h]).filter((v) => v > r.y && v < r.y + r.h)]);
    for (let j = 0; j < ys.length - 1; j++)
      for (let i = 0; i < xs.length - 1; i++)
        if (at(rects, (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2) < 0) return false;
    return true;
  }

  /* the monitor a window (rect) belongs to */
  function ofRect(mons, r) {
    if (!mons.length) return -1;
    const i = at(mons, r.x + r.w / 2, r.y + BAR / 2);
    if (i >= 0) return i;
    let best = 0, bi = -1;
    mons.forEach((m, k) => { const o = inter(m, r); if (o && area(o) > best) { best = area(o); bi = k; } });
    return bi >= 0 ? bi : nearest(mons, r.x + r.w / 2, r.y + BAR / 2);
  }

  /* a monitor's work area: the primary's without the menu bar and the dock's room (dockRoom px at its bottom) */
  function workArea(m, dockRoom = 0) {
    if (!m) return null;
    if (!m.primary) return { x: m.x, y: m.y, w: m.w, h: m.h };
    return { x: m.x, y: m.y + MENU, w: m.w, h: Math.max(MIN_H, m.h - MENU - Math.max(0, dockRoom)) };
  }

  /* a new window of (w x h) on monitor i, the n-th window: cascaded like the single-screen desktop always did
     (90 + n%7 x 36, 40 + n%5 x 32 into the work area), kept inside the work area */
  function placeNew(mons, i, w, h, n, dockRoom = 0) {
    const m = mons[i] || mons[0];
    const wa = workArea(m, dockRoom);
    w = Math.max(MIN_W, Math.min(num(w, 720), wa.w - 60));
    h = Math.max(MIN_H, Math.min(num(h, 520), wa.h - 40));
    let x = wa.x + 90 + (n % 7) * 36, y = wa.y + 40 + (n % 5) * 32;
    x = Math.max(wa.x, Math.min(x, wa.x + wa.w - w));
    y = Math.max(wa.y, Math.min(y, wa.y + wa.h - h));
    return { x, y, w, h };
  }

  /* a window dropped at r ({x, y, w}): its title bar ({x, y, w, BAR}) must lie on the usable monitor area entirely
     (dockRoom: the band the dock takes at the primary's bottom). -> {x, y, moved}: r itself when it does, else the
     nearest position (|dx| + |dy|) where it does — a move of only y, of only x, or into one monitor. */
  function clampTitle(mons, r, dockRoom = 0) {
    if (!mons.length) return { x: r.x, y: r.y, moved: false };
    const us = usable(mons, dockRoom);
    const bar = (x, y) => ({ x, y, w: r.w, h: BAR });
    if (covered(us, bar(r.x, r.y))) return { x: r.x, y: r.y, moved: false };
    const cands = [];
    for (const u of us) {
      const ty = Math.max(u.y, Math.min(r.y, u.y + u.h - BAR));
      const tx = r.w <= u.w ? Math.max(u.x, Math.min(r.x, u.x + u.w - r.w)) : u.x;
      cands.push([r.x, ty], [tx, r.y], [tx, ty]);
    }
    let best = null;
    for (const [x, y] of cands) {
      if (!covered(us, bar(x, y))) continue;
      const cost = Math.abs(x - r.x) + Math.abs(y - r.y);
      if (!best || cost < best.cost) best = { x, y, cost };
    }
    if (!best) {                                      // wider than every monitor: the top-left of the nearest one
      const u = us[nearest(mons, r.x + r.w / 2, r.y)];
      best = { x: u.x, y: u.y };
    }
    return { x: best.x, y: best.y, moved: true };
  }

  /* the pointer at (x, y) at the end of a drag: a snap zone? {i, zone: "left" | "right" | "top"} or null */
  function snapZone(mons, x, y, edge = 3) {
    const i = at(mons, x, y);
    if (i < 0) return null;
    const m = mons[i];
    if (y - m.y < edge && at(mons, x, m.y - 1) < 0) return { i, zone: "top" };
    if (x - m.x < edge && at(mons, m.x - 1, y) < 0) return { i, zone: "left" };
    if (m.x + m.w - 1 - x < edge && at(mons, m.x + m.w, y) < 0) return { i, zone: "right" };
    return null;
  }

  /* the rect a snapped (or maximised: zone "top") window gets on monitor i */
  function snapRect(mons, i, zone, dockRoom = 0) {
    const wa = workArea(mons[i] || mons[0], dockRoom);
    const half = Math.floor(wa.w / 2);
    if (zone === "left") return { x: wa.x, y: wa.y, w: half, h: wa.h };
    if (zone === "right") return { x: wa.x + half, y: wa.y, w: wa.w - half, h: wa.h };
    return { x: wa.x, y: wa.y, w: wa.w, h: wa.h };
  }

  /* a window rect r after the monitors changed from `before` to `after`: on a monitor that still exists (same id)
     it moves with it; on one that is gone it goes to the primary at the same offset, made to fit. -> {x, y, w, h,
     from, to, gone} (from / to: monitor ids). clampTitle runs after this. */
  function relocate(before, after, r) {
    const i = ofRect(before, r);
    const old = before[i];
    let to = old ? after.find((m) => m.id === old.id) : null;
    const gone = !to;
    if (!to) to = after.find((m) => m.primary) || after[0];
    if (!to) return Object.assign({}, r, { from: old && old.id, to: null, gone });
    let { x, y, w, h } = r;
    if (old) { x = to.x + (r.x - old.x); y = to.y + (r.y - old.y); }
    const wa = workArea(to);
    w = Math.min(w, wa.w); h = Math.min(h, wa.h);
    if (gone) {                                       // all of it on the primary
      x = Math.max(wa.x, Math.min(x, wa.x + wa.w - w));
      y = Math.max(wa.y, Math.min(y, wa.y + wa.h - h));
    }
    return { x, y, w, h, from: old && old.id, to: to.id, gone };
  }

  /* where this page's viewport sits on the X screen: {x, y} CSS px + the device pixel ratio s. A page whose viewport
     IS the X screen (the kiosk once the X side has spanned it) sits at the origin — by the X side's contract, not by
     window.screenX, which a freshly navigated page was seen to report stale (the VM: 1920 for ~1 s after the window
     had moved to 0); anything else (the moment before the span, a plain window) at its window position + the
     browser's own chrome around the viewport */
  function pageOrigin(rootMons, w) {
    const s = w.devicePixelRatio > 0 ? w.devicePixelRatio : 1;
    const u = union(normalize(rootMons || []));
    if (u && Math.abs(w.innerWidth * s - (u.x + u.w)) <= 1 && Math.abs(w.innerHeight * s - (u.y + u.h)) <= 1)
      return { x: 0, y: 0, s };
    const cx = Math.max(0, Math.min(100, (w.outerWidth - w.innerWidth) / 2));
    const cy = Math.max(0, Math.min(200, w.outerHeight - w.innerHeight - cx));
    return { x: (w.screenX || 0) + cx, y: (w.screenY || 0) + cy, s };
  }

  /* root pixels -> this page's CSS pixels: the page's window sits at (ox, oy) CSS px, scale = devicePixelRatio */
  function toPage(mons, ox = 0, oy = 0, scale = 1) {
    const s = scale > 0 ? scale : 1;
    return normalize((mons || []).map((m) => ({ id: m.id, primary: m.primary,
      x: Math.round(m.x / s - ox), y: Math.round(m.y / s - oy), w: Math.round(m.w / s), h: Math.round(m.h / s) })));
  }

  return { BAR, MENU, normalize, parseHint, union, at, nearest, deadAreas, covered, usable, ofRect, workArea,
           placeNew, clampTitle, snapZone, snapRect, relocate, pageOrigin, toPage };
})();

if (typeof module !== "undefined" && module.exports) module.exports = KMon;
