<!-- keywords: settings, preferences, config, configuration, theme, wallpaper, accent, setup, brightness, volume, sound, display, image model, expansion, llm, fast mode, coder model, factory reset, vram, tier, language, translation, translate, multilingual, dictionary, dictionaries, wiktionary, add-on -->
# Recipe: settings / control-center app

Two stores: OS look-and-feel via `vibe.settings`, hardware via `vibe.hw`; the machine via `vibe.system`.

## Exact result shapes — render FIELDS, never whole objects

`vibe.hw.get()` and `vibe.system.setup()` return nested objects. Writing an object into text
(`"VRAM: " + info` or `${hw.battery}%`) prints **`[object Object]`** — pick the field. These are the
ONLY field names; `info.gpu_vram`, `info.vram`, `tier.name`, `tier.id`, `tier.download_gb` do not exist.

```js
const info = await vibe.system.setup();       // GPU VRAM, model tiers, download sizes
// {
//   first_boot: false, vram_mb: 8188,                   // GPU memory in MB → `${(info.vram_mb / 1024).toFixed(0)} GB VRAM`
//   current_tier: "8gb", recommended_tier: "8gb",
//   tiers: [ {tier:"8gb",  present:true,  total_gb:23.6, have_gb:23.6, llm:"Gemma 4 26B-A4B-it ultra-uncensored heretic"},
//            {tier:"16gb", present:false, total_gb:24.1, have_gb:9.7,  llm:"Gemma 4 26B-A4B-it (…)"},
//            {tier:"24gb", present:false, total_gb:41.3, have_gb:0,    llm:"Qwen3-Coder-30B-A3B-Instruct (Q4_K_M)"}, … ],
//   upgrade: null          // or {tier:"16gb", status:"downloading"|"switching"|"done"|"error", current:"llm/…gguf", error:null}
// }
vramEl.textContent = `${(info.vram_mb / 1024).toFixed(0)} GB`;
tierEl.textContent = info.current_tier;
for (const t of info.tiers) row(`${t.tier} — ${t.llm}`, t.present ? "installed" : `⬇ ${(t.total_gb - t.have_gb).toFixed(1)} GB`);

const hw = await vibe.hw.get();               // wifi+battery+brightness+volume+display+bluetooth+datetime+memory+disk in ONE call
// {
//   wifi:       {available:true, connected:true, ssid:"HomeNet", devices:[{device:"wlan0", state:"connected", connection:"HomeNet"}]},
//   battery:    {available:true, percent:73, status:"Charging", charging:true, ac_plugged:true, health_percent:80},
//   brightness: {available:true, percent:50, device:"amdgpu_bl1"},
//   volume:     {available:true, percent:40, muted:false},
//   display:    {available:true, count:1, multiscreen:false, displays:[{name:"eDP-1", connected:true, primary:true, mode:"2560x1600", …}]},
//   bluetooth:  {available:true, powered:true, connected:["WH-1000XM5"]},
//   datetime:   {available:true, timezone:"Europe/Berlin", ntp:true, ntp_synced:true, rtc_utc:true},
//   memory_mb:  {total:31967, available:17233},        // megabytes — NOT a number itself
//   disk_gb:    {total:1024.2, free:611.4}             // gigabytes — NOT a number itself
// }
// missing hardware: {available:false, reason:"no battery (desktop or VM)"} — render the reason
batteryEl.textContent = hw.battery.available ? `${hw.battery.percent}% ${hw.battery.charging ? "⚡" : ""}` : hw.battery.reason;
memEl.textContent     = `${Math.round((hw.memory_mb.total - hw.memory_mb.available) / 1024)} / ${Math.round(hw.memory_mb.total / 1024)} GB`;
diskEl.textContent    = `${hw.disk_gb.free} GB free of ${hw.disk_gb.total} GB`;
```

## The coder LLM (an "AI" section)

```js
const m = await vibe.models();
// m.llm on 8/16 GB: ONE entry — [{key:"default", name:"Gemma 4 26B-A4B-it ultra-uncensored heretic (…)", present:true, addon:false, active:true, selected:true, download_gb:0}]
// m.llm_setting: {setting:"default", effective:"default", fallback:null, modes:["default"]}
llmEl.textContent = m.llm.find((l) => l.active).name;        // show the model; no separate fast model [R89]
if (m.llm.length > 1) {                                      // a picker only where the tier offers a choice
  sel.innerHTML = m.llm.map((l) => `<option value="${l.key}" ${l.selected ? "selected" : ""}>${l.name}${l.present ? "" : ` — download ${l.download_gb} GB first`}</option>`).join("");
  sel.onchange = () => vibe.settings.set("llm_model", sel.value);
}
```
- Fill it FROM `m.llm` after `await vibe.models()` resolves — never from a static list. There is no fast
  model any more (2026-10-18): `llm_model` "fast" serves the same model, so never offer "fast" yourself.
  When `m.llm_setting.fallback` is set show it.
- An entry that is `present:false` gets a "⬇ Download (N GB)" button (`vibe.models.download(entry.download_key)`),
  then poll `vibe.models()` until `present` (or watch `downloading`).

## Settings store and validation

```js
const s = await vibe.settings.get();          // {os_name, accent, accent2, wallpaper, image_model, image_expand, llm_model, …}
await vibe.settings.set("accent", "#ff5cf0"); // applied live by the OS within seconds
await vibe.hw.brightness.set(70);             // percent
await vibe.hw.volume.set(40);                 // percent; vibe.hw.volume.set(40, true) also mutes
await vibe.system.upgrade("16gb");            // start a bigger-brain download — the OS asks the user first
await vibe.fs.format();                       // DANGER: factory reset of /data — the OS asks the user first
```
**Values are validated**: a bad value is rejected (the promise rejects with the rule, e.g. "accent needs a
CSS colour"). `accent`/`accent2` = a CSS colour (`#ff5cf0`, `rgb(…)`, a colour name), `os_name` = 1–40
characters, `wallpaper` = an image URL or `null`, `image_model` ∈ default|safe|qwen|classic|uncensored|chroma,
`image_expand` ∈ auto|on|off, `llm_model` ∈ default (fast = the same model), `image_steps` = 1–150 or null, `translation` ∈ off|on,
`language` ∈ en|de|es|fr|it|ja|ru, `translation_quality` ∈ fast|quality. Always send the
value — `vibe.settings.set("accent")` without one is an error, not "reset". Reading a setting:
`const s = await vibe.settings.get(); s.image_expand` — never `(await vibe.settings.get().image_expand)`.

## Language & translation (a "Language" section) [R71]

```js
const m = await vibe.models();
// m.translation: {enabled:false, language:"en", languages:["en","de","es","fr","it","ja","ru"], names:{en:"English", de:"German", …},
//                 quality:"fast", effective:"fast", fallback:null, quality_llm:true, model:{fast:"Index-Translate …", quality:"Gemma 4 … (the loaded LLM)"},
//                 present:{fast:true, quality:true}, running:{fast:false, quality:false}, cache:{}}
const sel = document.getElementById("lang-select");          // the picker FROM m.translation.languages, labels from names
sel.innerHTML = m.translation.languages.map((c) => `<option value="${c}" ${c === m.translation.language ? "selected" : ""}>${m.translation.names[c]}</option>`).join("");
sel.onchange = () => vibe.settings.set("language", sel.value);
toggle.checked = m.translation.enabled;                      // the on/off switch
toggle.onchange = () => vibe.settings.set("translation", toggle.checked ? "on" : "off");
qual.innerHTML = `<option value="fast">Fast (small CPU model)</option><option value="quality">Quality (${m.translation.model.quality})</option>`;
qual.value = m.translation.quality;
qual.onchange = () => vibe.settings.set("translation_quality", qual.value);   // quality_llm: the loaded LLM translates itself (8 GB)
const tr = m.extras.find((e) => e.key === "translation");    // {present, present_modes:{fast, quality}, missing_gb, download_gb}
if (tr.missing_gb > 0) dlBtn.onclick = () => vibe.models.download("translation");   // only what is missing (tr.missing_gb GB)
```
- Off (the default) = nothing is translated, no model runs; on + a language other than English = the
  whole OS, every app window and the generated content follow it. Show `fallback` when set.
- `tr.present` is true on the shipped OS (the fast model is built in): never show translation as "not downloaded";
  `missing_gb` = what the download still fetches — label the button with it (0 on 8 GB: quality needs no download).
- An app's own text: `await vibe.translate("Save")` → the OS language; `vibe.translate(["a", "b"], "en")` →
  an array into a named language. It rejects while translation is off — catch it.
- Dictionaries & Wiktionary (the same section): `m.extras` carries `dictionaries` (kind `dictionary`, 0.12 GB —
  `vibe.dict("Haus", "de", "en")` → `{entries: [{headword, translations, source}]}`, works with translation off)
  and `wiktionary_<lang>` (kind `knowledge`, one ZIM per language, `language`, `download_gb`) for the offline
  `vibe.search()`; for each one with `present` false render "⬇ Download (N GB)" → `vibe.models.download(key)`;
  suggest the OS language's Wiktionary + `wiktionary_en`.

## Image model configuration (the same "AI" section)

```js
// m.models: [{key,name,present,active,addon,download_gb,downloading,defaults,expander,licence}]
// keys: default (Krea 2, uncensored) | safe (stock Krea 2, faster) | qwen (Qwen-Image-2.1, best text)
//       | classic (Z-Image, fastest) | chroma (Chroma1-HD, slow); "uncensored" = default
await vibe.settings.set("image_model", "qwen");  // default | safe | qwen | classic | chroma
await vibe.settings.set("image_expand", "off");  // auto (default) | on | off — Krea's prompt expansion
await vibe.settings.set("image_steps", 12);      // null = the model's tuned default; also image_sampler, image_scheduler, image_cfg
await vibe.models.download("qwen");              // add-on models: fetch before use
```
- Render a model picker from `m.models` (badge the `active` one; show `licence` when set). The shipped OS
  bakes in Krea 2 (`default` + `safe`); the others are add-ons (`addon:true`, `present:false`): a "⬇ Download
  (N GB)" button → `vibe.models.download(key)`, then poll `vibe.models()` until `present`.
- Prompt expansion (`image_expand`, shown in `m.expand.setting`): a three-way switch auto / on / off.
- Show each model's `defaults` (steps/sampler/scheduler/cfg) as placeholders; offer "reset to defaults" (null).
- Several pictures in one run: `await vibe.images(["a", "b", "c"])` → array of URLs with ONE model load.

## Bigger model tiers via `vibe.system`
- List `info.tiers` (shape above); for any not `present`, offer "⬇ Download the {tier} models
  ({total_gb} GB)" → `vibe.system.upgrade(tier)`. The OS stays usable while it downloads.
- `vibe.system.upgrade()` and `vibe.fs.format()` are confirmed **by the OS itself** in a dialog the app
  cannot answer; if the user declines, the promise rejects with `cancelled: …` — catch it and show
  "cancelled". Never call them on page load or from a generic Save button: only from their own button.

## Patterns
- Sections: Appearance (accent color pickers + live preview, os_name text field, wallpaper), Display
  (brightness slider), Sound (volume slider + mute), Network (small WiFi status, wifi recipe), AI (the coder
  LLM's name — a picker only when m.llm lists more than one — + image model picker + expansion switch), Language (language picker + translation switch +
  fast/quality + the add-on download), System (VRAM, current tier, tiers + upgrade),
  Danger zone (factory reset behind an in-page two-step confirm — a "Reset…" button that reveals a red
  "Yes, erase everything" button; **never `window.confirm()`**, it is blocked).
- Sliders: update state optimistically, call the API on `change` (not every `input` event).
- Any `available:false` device: render the row disabled with its `reason`.
- Text the user must type (a new OS name, a WiFi password) comes from an `<input>` in the page — never
  `window.prompt()` (blocked) and never `vibe.ask()` (that asks the language model, which invents an answer).
