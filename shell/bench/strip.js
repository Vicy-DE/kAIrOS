/* shared by the strip windows: screen hint, real-window opener, toasts, the WM driver
   (bench stand-in for kernel/wm.py: GET /windows as an event stream, POST /op/…) */
"use strict";
const $ = (s) => document.querySelector(s);
const WMCTL = "http://127.0.0.1:1339";
const SCREEN = (() => {
  const q = new URLSearchParams(location.search);
  const num = (k, d) => { const v = parseInt(q.get(k), 10); return Number.isFinite(v) ? v : d; };
  return { index: Math.max(0, num("screen", 0)), x: num("x", 0), y: num("y", 0), w: num("w", 1280), h: num("h", 800) };
})();
document.title += ":" + SCREEN.index;
function toast(msg, isErr = false) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 8000 : 4000);
}
const openReal = (url, w, h) =>
  window.open(url, "_blank", `popup,width=${w},height=${h},left=${SCREEN.x + 90},top=${SCREEN.y + 70}`);
const wmOp = (ref, op, body) => fetch(`${WMCTL}/op/${encodeURIComponent(ref)}/${op}`,
  { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).catch(() => {});
