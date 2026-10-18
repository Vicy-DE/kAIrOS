#!/usr/bin/env node
/* Unit test of the app bridge (shell.js BRIDGE_SRC) outside a browser [R12/R45/R66]:
   the storage shims, the vibe.* aliases and the unknown-member guard, the localStorage
   seed, the hw.get() result contract and the sandbox attribute. Run with the node image:
     docker run --rm --network none -v <repo>:/w:ro node:24-alpine node /w/tests/bridge-test.js
   (no node on the appliance or in the testrig container — see kairos-testrig-workflow). */
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const repo = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(repo, "shell", "shell.js"), "utf8");
const host = fs.readFileSync(path.join(repo, "shell", "bench", "host.js"), "utf8");
const m = js.match(/const BRIDGE_SRC = `([\s\S]*?)`;\n/);
assert(m, "BRIDGE_SRC literal not found");
// the kernel (bridge_src) and host.js copy the RAW literal and only undo <\/script>, so the
// bridge must not rely on any other template-literal escape: no backslash at all
assert(!/\\(?!\/script>)/.test(m[1]), "BRIDGE_SRC contains a backslash escape other than <\\/script>");
const bridge = m[1].replace(/<\\\/script>/g, "</script>");
const body = bridge.match(/<script>([\s\S]*?)<\/script>/)[1];
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/* ---- static: the sandbox is untouched, the theme rides along ---- */
test("sandbox attribute unchanged in every mount path (no allow-same-origin, no token)", () => {
  for (const [file, src] of [["shell.js", js], ["host.js", host]]) {
    const mount = src.match(/async function mountApp\([^)]*\) \{[\s\S]*?\n\}/);
    assert(mount, file + ": mountApp not found");
    const attrs = [...mount[0].matchAll(/iframe\.sandbox = "([^"]*)"/g)].map((x) => x[1]);
    assert.deepStrictEqual(attrs, ["allow-scripts allow-pointer-lock"], file + ": " + attrs);
    assert(!/allow-same-origin/.test(src.replace(/\/\*[\s\S]*?\*\//g, "")), file + " grants allow-same-origin");
  }
  assert(!/X-Kairos-Token|\/api\//.test(body), "the bridge must not talk to /api directly");
});
test("theme stylesheet present, zero-specificity selectors only", () => {
  const style = bridge.match(/<style data-kairos-theme>([\s\S]*?)<\/style>/);
  assert(style, "no theme <style>");
  const rules = style[1].split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("<"));
  for (const r of rules) {
    const sel = r.slice(0, r.indexOf("{")).trim();
    // one :where(...) group, optionally followed by a pseudo-ELEMENT (which the app's own
    // ::placeholder rule still beats by source order) — never a bare type/class/pseudo-class
    assert(/^:where\((?:[^()]|\([^()]*\))*\)(?:::[\w-]+)?$/.test(sel), "selector not zero-specificity: " + sel);
  }
  assert(/color-scheme:dark/.test(style[1]) && /:where\(button/.test(style[1]));
});
test("bridge literal is template-safe (no ${ or backtick inside)", () => {
  assert(!/\$\{/.test(m[1]) && !/`/.test(m[1]));
});

/* ---- a fake window to run the bridge script in ---- */
function boot(seed) {
  const listeners = {};
  const posted = [];
  const window = {
    __kairosSeed: seed,
    addEventListener: (t, f) => (listeners[t] = listeners[t] || []).push(f),
  };
  const document = { addEventListener: () => {} };
  const parent = { postMessage: (msg) => posted.push(msg) };
  const fn = new Function("window", "document", "parent", body);
  fn(window, document, parent);
  const reply = (id, result, error) => {
    for (const f of listeners.message) f({ data: { vibeReply: true, id, result, error } });
  };
  return { window, document, posted, reply, listeners };
}

test("bridge script evaluates and installs window.vibe", () => {
  const { window } = boot();
  assert.strictEqual(typeof window.vibe, "object");
  assert.strictEqual(typeof window.vibe.ask, "function");
});

test("localStorage shim: synchronous Storage API + property access + JSON round trip", () => {
  const { window } = boot();
  const ls = window.localStorage;
  assert.strictEqual(ls.getItem("x"), null);
  ls.setItem("todos", JSON.stringify([{ t: "a" }]));
  assert.deepStrictEqual(JSON.parse(ls.getItem("todos")), [{ t: "a" }]);
  assert.strictEqual(ls.length, 1);
  assert.strictEqual(ls.key(0), "todos");
  ls.count = 5;                                         // property style, stringified like the real thing
  assert.strictEqual(ls.getItem("count"), "5");
  assert.strictEqual(ls.count, "5");
  assert.deepStrictEqual(Object.keys(ls).sort(), ["count", "todos"]);
  assert.strictEqual("todos" in ls, true);
  delete ls.count;
  assert.strictEqual(ls.getItem("count"), null);
  ls.removeItem("todos");
  ls.clear();
  assert.strictEqual(ls.length, 0);
  assert.strictEqual(typeof window.sessionStorage.setItem, "function");
});

test("localStorage is seeded from the OS store and persisted back through vibe.store (debounced)", async () => {
  const { window, posted } = boot({ localStorage: { habits: "[1,2]" } });
  assert.strictEqual(window.localStorage.getItem("habits"), "[1,2]");
  window.localStorage.setItem("habits", "[1,2,3]");
  window.localStorage.setItem("streak", "4");
  assert.strictEqual(posted.length, 0, "persist must be asynchronous");
  await new Promise((r) => setTimeout(r, 300));
  const stores = posted.filter((p) => p.method === "store");
  assert.strictEqual(stores.length, 1, "one debounced store call");
  assert.deepStrictEqual(stores[0].args, ["__localStorage", { habits: "[1,2,3]", streak: "4" }]);
  // sessionStorage is in-memory only: never persisted
  window.sessionStorage.setItem("s", "1");
  await new Promise((r) => setTimeout(r, 300));
  assert.strictEqual(posted.filter((p) => p.method === "store").length, 1);
});

test("indexedDB and document.cookie do not throw", async () => {
  const { window, document } = boot();
  let errored = false;
  const req = window.indexedDB.open("db", 1);
  req.onerror = () => { errored = true; };
  await new Promise((r) => setTimeout(r, 10));
  assert(errored, "open() must fail asynchronously through onerror");
  assert(/localStorage|vibe\.store/.test(req.error.message));
  document.cookie = "theme=dark; path=/";
  document.cookie = "lang=de";
  assert.strictEqual(document.cookie, "theme=dark; lang=de");
  document.cookie = "lang=; max-age=0";
  assert.strictEqual(document.cookie, "theme=dark");
});

test("aliases the models invent map onto the real calls", () => {
  const { window, posted } = boot();
  const v = window.vibe;
  v.store.get("k"); v.store.set("k", 1); v.store.load("k"); v.store.store("k", 2); v.store.remove("k");
  v.fs.get("/data/a"); v.fs.set("/data/a", "x"); v.hw.battery.get(); v.hw.wifi.get();
  v.store("k", 3); v.load("k");
  const calls = posted.map((p) => [p.method, ...p.args]);
  assert.deepStrictEqual(calls, [
    ["load", "k"], ["store", "k", 1], ["load", "k"], ["store", "k", 2], ["store", "k", null],
    ["fs", "read", "/data/a"], ["fs", "write", "/data/a", "x"],
    ["hw", { device: "battery" }], ["hw", { device: "wifi", op: "status" }],
    ["store", "k", 3], ["load", "k"]]);
});

test("unknown vibe.* members are undefined (feature detection is honest) + a console warning naming the real API [D4]", () => {
  const warned = [];
  const origWarn = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { window, listeners, posted } = boot();
    const v = window.vibe;
    assert.strictEqual(typeof v.clipboard, "undefined");
    assert.strictEqual(typeof v.hw.haptics, "undefined");
    assert.strictEqual(v.hw.haptics?.trigger, undefined, "optional chaining short-circuits on the missing member");
    assert.strictEqual(typeof v.fs.exportData, "undefined");
    assert.strictEqual("haptics" in v.hw, false);
    assert.strictEqual("sensors" in v.hw, false);
    assert.strictEqual(typeof v.hw.battery, "function", "real members untouched");
    assert.throws(() => v.hw.vibrate(), TypeError);                   // a call = the plain TypeError
    assert.strictEqual(v.then, undefined, "vibe must not look like a thenable");
    assert.strictEqual(v.hw.then, undefined);
    assert(warned.some((w) => /vibe\.clipboard is not part of the kAIrOS API — vibe has: ask, search/.test(w)), warned);
    assert(warned.some((w) => /vibe\.hw\.haptics is not part of the kAIrOS API — vibe\.hw has: get, wifi, battery/.test(w)), warned);
    assert(warned.some((w) => /vibe\.fs\.exportData is not part of the kAIrOS API — vibe\.fs has: list, read, write/.test(w)), warned);
    v.clipboard; v.clipboard;                                          // repeated access warns once
    assert.strictEqual(warned.filter((w) => /vibe\.clipboard is not/.test(w)).length, 1);
    // the bug report the shell files carries the list, so the self-repair learns what does not exist
    for (const f of listeners.error) f({ message: "Cannot read properties of undefined (reading 'trigger')", lineno: 9, error: { name: "TypeError" } });
    const rep = posted.filter((p) => p.method === "jsError")[0].args[0];
    assert(Array.isArray(rep.unknown_api) && rep.unknown_api.some((u) => /vibe\.hw\.haptics is not part/.test(u)), rep);
  } finally { console.warn = origWarn; }
});

test("window.alert/confirm/prompt are shimmed: non-blocking notice, sandbox return values kept [D5]", () => {
  const warned = [];
  const origWarn = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { window } = boot();
    assert.strictEqual(typeof window.alert, "function");
    assert.strictEqual(window.alert("saved!"), undefined);
    assert.strictEqual(window.confirm("delete all?"), false);
    assert.strictEqual(window.prompt("file name?"), null);
    assert(warned.some((w) => /saved!/.test(w)), warned);
    assert(warned.some((w) => /confirm\(\) is blocked.*delete all\?/.test(w)), warned);
    assert(warned.some((w) => /prompt\(\) is blocked.*file name\?/.test(w)), warned);
  } finally { console.warn = origWarn; }
});

/* ---- the transports: every method the bridge can post has a handler in BOTH host pages [D3] ---- */
test("shell.js and host.js handle every bridge method (parity), incl. images and vision", () => {
  const methods = new Set([...body.matchAll(/call\("([a-zA-Z]+)"/g)].map((m) => m[1]));
  methods.add("jsError");
  assert(methods.has("images") && methods.has("vision") && methods.size >= 20, [...methods]);
  for (const [file, src] of [["shell.js", js], ["host.js", host]]) {
    const handler = src.match(/async function handleBridge\([^)]*\) \{[\s\S]*?\n\}/);
    assert(handler, file + ": handleBridge not found");
    const handled = new Set([...handler[0].matchAll(/method === "([a-zA-Z]+)"/g)].map((m) => m[1]));
    const missing = [...methods].filter((m) => !handled.has(m));
    assert.deepStrictEqual(missing, [], file + " lacks handlers for: " + missing.join(", "));
  }
});

test("destructive calls are confirmed by the host page, outside the sandbox [D9]; heal rounds capped [D1]; maximize clears the dock [D2]", () => {
  for (const [file, src] of [["shell.js", js], ["host.js", host]]) {
    const h = src.match(/async function handleBridge\([^)]*\) \{[\s\S]*?\n\}/)[0];
    const fsBranch = h.slice(h.indexOf('method === "fs"'), h.indexOf('method === "settingsGet"'));
    assert(/op === "format"[\s\S]*?await uiConfirm\(/.test(fsBranch) && fsBranch.indexOf("await uiConfirm(") < fsBranch.indexOf('fetch("/api/fs"'),
           file + ": format must be confirmed before /api/fs is called");
    const up = h.slice(h.indexOf('method === "upgrade"'), h.indexOf('method === "search"'));
    assert(up.indexOf("await uiConfirm(") !== -1 && up.indexOf("await uiConfirm(") < up.indexOf('fetch("/api/setup/upgrade"'), file + ": upgrade unconfirmed");
    const hw = h.slice(h.indexOf('method === "hw"'), h.indexOf('method === "wiki"'));
    assert(/device === "power"[\s\S]*?await uiConfirm\(/.test(hw) && hw.indexOf("await uiConfirm(") < hw.indexOf('fetch("/api/hw"'), file + ": power unconfirmed");
    assert(/function uiConfirm\(/.test(src), file + ": no uiConfirm");
    assert(/const HEAL_MAX_ROUNDS = 3;/.test(src) && /win\.healRounds > HEAL_MAX_ROUNDS\) \{ showHealStop\(win, rep\); return true; \}/.test(h), file + ": heal cap");
    assert(/win\.healRounds = 0;/.test(src), file + ": a new version must reset the heal budget");
  }
  const max = js.slice(js.indexOf('querySelector(".win-max").onclick'), js.indexOf('querySelector(".win-improve").onclick'));
  assert(/calc\(100% - 30px - \$\{dockRoom\(\)\}px\)/.test(max), "maximized height must subtract the dock's room");
  assert(!/height: "calc\(100% - 30px\)"/.test(max));
  assert(/function dockRoom\(\)[\s\S]*?getBoundingClientRect/.test(js), "dockRoom measures the real dock box");
});

/* ---- [N4] the OS confirmation cannot be consented to during its first CONFIRM_ARM_MS: uiConfirm run
   against a minimal fake DOM — the OK button is disabled with a countdown, a click during arming is
   ignored, the click after arming resolves true; Cancel/Escape work at once (both host pages) ---- */
function fakeDom() {
  const body = { children: [], appendChild(n) { this.children.push(n); n.parent = body; } };
  const mk = (cls) => ({ className: cls, disabled: false, textContent: "", onclick: null, focus() {},
                         classList: { add() {}, remove() {} } });
  const document = {
    body,
    createElement() {
      const ov = { className: "", dataset: {}, onclick: null, _q: mk("ui-prompt-q"), _ok: mk("ui-prompt-ok"), _cancel: mk("ui-prompt-cancel"),
                   set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
                   querySelector(sel) { return { ".ui-prompt-q": this._q, ".ui-prompt-ok": this._ok, ".ui-prompt-cancel": this._cancel }[sel]; },
                   remove() { body.children = body.children.filter((c) => c !== this); } };
      return ov;
    },
  };
  const keys = [];
  const win = { addEventListener: (t, f) => keys.push(f), removeEventListener: () => {},
                setTimeout, setInterval, clearInterval, performance: { now: () => Date.now() } };
  return { document, win, keys };
}
for (const [file, src] of [["shell.js", js], ["host.js", host]]) {
  test(`[N4] ${file}: uiConfirm arms its OK button for CONFIRM_ARM_MS (disabled + countdown), then accepts`, async () => {
    const arm = src.match(/const CONFIRM_ARM_MS = (\d+);/);
    assert(arm && +arm[1] >= 500 && +arm[1] <= 1500, file + ": CONFIRM_ARM_MS must be 0.5–1.5 s");
    const fn = src.match(/function uiConfirm\([\s\S]*?\n\}/)[0];
    const { document, win, keys } = fakeDom();
    const make = new Function("document", "addEventListener", "removeEventListener", "setTimeout", "setInterval", "clearInterval", "performance",
                              `const CONFIRM_ARM_MS = ${arm[1]}; ${fn}; return uiConfirm;`);
    const uiConfirm = make(document, win.addEventListener, win.removeEventListener, setTimeout, setInterval, clearInterval, win.performance);
    let settled = null;
    const p = uiConfirm("erase everything?", "Erase").then((v) => (settled = v));
    const ov = document.body.children[0];
    assert(ov && ov.className === "ui-prompt-overlay", "overlay not appended");
    const ok = ov.querySelector(".ui-prompt-ok");
    assert.strictEqual(ok.disabled, true, "OK must start disabled");
    assert.strictEqual(ov.dataset.armed, "0");
    assert(/^Erase \(\d\.\d s\)$/.test(ok.textContent), "countdown label: " + ok.textContent);
    ok.onclick();                                                    // the 60 ms click of the re-verification
    await new Promise((r) => setTimeout(r, 120));
    assert.strictEqual(settled, null, "a click during arming must not resolve");
    assert(document.body.children.includes(ov), "overlay must stay open");
    await new Promise((r) => setTimeout(r, +arm[1] + 250));
    assert.strictEqual(ok.disabled, false, "OK enabled after arming");
    assert.strictEqual(ov.dataset.armed, "1");
    assert.strictEqual(ok.textContent, "Erase");
    ok.onclick();
    await p;
    assert.strictEqual(settled, true, "the click after arming consents");
    assert(!document.body.children.includes(ov), "overlay removed");
    // cancel paths work immediately: Escape, the Cancel button, the backdrop
    let c1 = null;
    uiConfirm("q", "OK").then((v) => (c1 = v));
    for (const f of keys) f({ key: "Escape", preventDefault() {} });
    await new Promise((r) => setTimeout(r, 10));
    assert.strictEqual(c1, false, "Escape cancels at once");
    let c2 = null;
    uiConfirm("q", "OK").then((v) => (c2 = v));
    document.body.children[document.body.children.length - 1].querySelector(".ui-prompt-cancel").onclick();
    await new Promise((r) => setTimeout(r, 10));
    assert.strictEqual(c2, false, "Cancel cancels at once");
  });
}

/* ---- [N5] a version the kernel delivered with a failing JS gate is shown, never self-healed ---- */
test("[N5] both host pages apply the kernel's gate verdict after every mount (done/cached/fallback + repair reply) and spend the heal budget", () => {
  for (const [file, src] of [["shell.js", js], ["host.js", host]]) {
    assert(/function applyGateVerdict\(win, b\) \{[\s\S]*?win\.healRounds = HEAL_MAX_ROUNDS;[\s\S]*?showHealStop\(win, brokenReport\(b\)\)/.test(src), file + ": applyGateVerdict");
    assert(/mountApp\((?:win, )?data\.html\);[\s\S]{0,700}applyGateVerdict\(win, data\.broken\)/.test(src), file + ": verdict applied after the stream mount");
    const h = src.match(/async function handleBridge\([^)]*\) \{[\s\S]*?\n\}/)[0];
    const jsErr = h.slice(h.indexOf('method === "jsError"'));
    assert(jsErr.indexOf("mountApp(") !== -1 && jsErr.indexOf("mountApp(") < jsErr.indexOf("applyGateVerdict(win, r.broken)"), file + ": repair reply verdict");
    assert(/delivered with a syntax error/.test(src) && /win\.gateBroken/.test(src), file + ": gate banner");
  }
  assert(/document\.body\.dataset\.broken/.test(host), "host.js applies the page's data-broken at load");
  // the banner helper itself: brokenReport keeps the parser message and the document line
  const br = new Function(js.match(/function brokenReport\(b\) \{[\s\S]*?\n\}/)[0] + "; return brokenReport;")();
  assert.deepStrictEqual(br({ message: "JavaScript does not parse — inline <script> #1, document line 12: SyntaxError: x", line: 12, rounds: 2 }),
                         { type: "SyntaxError", message: "inline <script> #1, document line 12: SyntaxError: x", line: 12, gate: true });
});

test("vibe.hw.get(): unknown devices answer {available:false, reason} (the documented contract)", async () => {
  const { window, posted, reply } = boot();
  const p = window.vibe.hw.get();
  reply(posted[0].id, { wifi: { available: true }, battery: { available: false, reason: "no battery" } });
  const hw = await p;
  assert.strictEqual(hw.wifi.available, true);
  assert.strictEqual(hw.battery.reason, "no battery");
  assert.strictEqual(hw.sensors.available, false);
  assert(/sensors/.test(hw.sensors.reason));
  assert.deepStrictEqual(Object.keys(hw), ["wifi", "battery"]);
  assert.strictEqual(JSON.stringify(hw), '{"wifi":{"available":true},"battery":{"available":false,"reason":"no battery"}}');
});

test("vibe.image(): Array.map's (prompt, index, array) extras are dropped, real sizes kept", () => {
  const { window, posted } = boot();
  ["a", "b"].map(window.vibe.image);
  window.vibe.image("c", 768, 512);
  window.vibe.image("d", "1024", "576");
  assert.deepStrictEqual(posted.map((p) => p.args), [["a", undefined, undefined], ["b", undefined, undefined],
                                                     ["c", 768, 512], ["d", 1024, 576]]);
});

test("[R88] vibe.image.improve / .edit / .versions: posted to the host page, the new version reads as its URL, errors reach the app", async () => {
  const warned = [];
  const origWarn = console.warn;
  console.warn = (m) => warned.push(String(m));
  try {
    const { window, posted, reply } = boot();
    const v = window.vibe;
    assert.strictEqual(typeof v.image, "function", "vibe.image() itself is unchanged");
    assert.strictEqual(typeof v.image.improve, "function");
    assert.strictEqual(v.image.edit, v.image.improve, "edit = the name the models reach for");
    const p = v.image.improve("/files/images/a.png", "make it a snowy night");
    v.image.improve({ currentSrc: "http://127.0.0.1:1337/genimg/a%20fox?w=512&h=512", src: "x" }, null);   // an <img>
    v.image.improve({ url: "/files/images/versions/pabc0123456789ab/v1.png" });                          // a version
    v.image.versions("/files/images/a.png");
    assert.deepStrictEqual(posted.map((x) => [x.method, ...x.args]), [
      ["imageImprove", "/files/images/a.png", "make it a snowy night"],
      ["imageImprove", "http://127.0.0.1:1337/genimg/a%20fox?w=512&h=512", ""],
      ["imageImprove", "/files/images/versions/pabc0123456789ab/v1.png", ""],
      ["imageVersions", "/files/images/a.png"]]);
    const rec = { url: "/files/images/versions/pabc0123456789ab/v1.png", version: 1, root: "pabc0123456789ab", from: 0,
                  versions: [{ v: 0 }, { v: 1 }], model: "Qwen-Image-2.1 Turbo edit", ms: 66000 };
    reply(posted[0].id, rec);
    const r = await p;
    assert.strictEqual(String(r), rec.url, "img.src = await vibe.image.improve(…) gets the URL");
    assert.strictEqual("" + r, rec.url);
    assert.strictEqual(`${r}`, rec.url);
    assert.strictEqual(r.url, rec.url);
    assert.strictEqual(r.version, 1);
    assert.deepStrictEqual(Object.keys(r), ["url", "version", "root", "from", "versions", "model", "ms"], "toString stays hidden");
    assert.strictEqual(JSON.stringify(r), JSON.stringify(rec));
    reply(posted[3].id, { root: null, current: 0, url: "/files/images/a.png", versions: [{ v: 0, url: "/files/images/a.png" }] });
    const q = v.image.improve("/files/images/b.png", "x");
    reply(posted[4].id, undefined, "kAIrOS does not create sexual content involving minors");
    await assert.rejects(q, /does not create/);
    assert.strictEqual(typeof v.image.filter, "undefined", "an invented member is undefined");
    assert(warned.some((w) => /vibe\.image\.filter is not part of the kAIrOS API — vibe\.image has: improve, edit, versions/.test(w)), warned);
    ["c"].map(v.image);                                              // the guarded function still maps
    assert.deepStrictEqual(posted[5].args, ["c", undefined, undefined]);
  } finally { console.warn = origWarn; }
});

test("vibe.images(): a list or prompt+count, options normalised, sizes validated like vibe.image", () => {
  const { window, posted } = boot();
  window.vibe.images(["a", "b"]);
  window.vibe.images("x", 3);
  window.vibe.images("y", { count: 2, width: "1024", height: 576, model: "classic", adult: true, expand: "off" });
  const none = { count: undefined, model: undefined, adult: undefined, expand: undefined, width: undefined, height: undefined };
  assert.deepStrictEqual(posted.map((p) => [p.method, p.args]), [
    ["images", [["a", "b"], none]],
    ["images", ["x", { ...none, count: 3 }]],
    ["images", ["y", { count: 2, model: "classic", adult: true, expand: "off", width: 1024, height: 576 }]],
  ]);
});

test("error reporter: the first uncaught error becomes one jsError bug report", () => {
  const { listeners, posted } = boot();
  for (const f of listeners.error) f({ message: "boom", lineno: 3, colno: 4, error: { name: "TypeError", stack: "s" } });
  for (const f of listeners.error) f({ message: "again", lineno: 5 });
  const reps = posted.filter((p) => p.method === "jsError");
  assert.strictEqual(reps.length, 1);
  assert.deepStrictEqual(reps[0].args[0], { type: "TypeError", message: "boom", line: 3, col: 4, stack: "s" });
});

test("seedScript escapes </script> and line separators", () => {
  const fn = js.match(/function seedScript\(seed\) \{[\s\S]*?\n\}/)[0];
  const seedScript = new Function(fn + "; return seedScript;")();
  const out = seedScript({ a: "</script><b> x" });
  assert(!/<\/script><b>/.test(out.slice(8, -9)), out);
  assert(out.startsWith("<script>window.__kairosSeed=") && out.endsWith("</script>"), out);
  const literal = out.slice("<script>window.__kairosSeed=".length, -"</script>".length);
  assert.deepStrictEqual(JSON.parse(literal), { localStorage: { a: "</script><b> x" } });
  assert.strictEqual(seedScript(null), "");
});

(async () => {
  let passed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); passed++; console.log("ok   " + name); }
    catch (e) { console.log("FAIL " + name + "\n     " + String(e.message).split("\n")[0]); }
  }
  console.log(`\n${passed} of ${tests.length} bridge tests passed`);
  process.exit(passed === tests.length ? 0 : 1);
})();
