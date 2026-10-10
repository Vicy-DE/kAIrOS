/* kAIrOS bench (candidates 2/3a): the unchanged shell page as the desktop (bottom)
   layer. Everything in shell.js that would create a DOM window opens a real
   top-level window instead — a window.open() into the same browser process/profile;
   the WM driver places it (cascade, or the geometry the harness asked for). */
"use strict";
document.title = "kairos-desk:" + SCREEN.index;
const openReal = (url, w, h) =>
  window.open(url, "_blank", `popup,width=${w},height=${h},left=${SCREEN.x + 90},top=${SCREEN.y + 70}`);
openApp = async (prompt, appId) => {
  if (!appId) return toast("summoning a NEW app is not wired in this prototype (host page serves cached apps)", true);
  openReal("/app/" + encodeURIComponent(appId), 720, 520);
};
openPage = async (address) =>
  openReal("/shell/bench/page.html?address=" + encodeURIComponent(String(address).replace(/^\/?(web\/)?/, "")), 820, 600);
openStudio = (name) => toast("studio app “" + name + "” is not wired in this prototype", true);
