<!-- keywords: settings, preferences, config, configuration, theme, wallpaper, accent, setup, brightness, volume, sound, display, image model, expansion, llm, fast mode, coder model, factory reset, vram, tier, language, translation, translate, multilingual, dictionary, dictionaries, wiktionary, add-on -->
# Recipe: settings / control-center app

Two stores: OS look-and-feel via `vibe.settings`, hardware via `vibe.hw`; the machine via `vibe.system`.

## Exact result shapes — render FIELDS, never whole objects

`vibe.hw.get()` and `vibe.system.setup()` return nested objects. Writing an object into text
(`"VRAM: " + info` or `${hw.battery}%`) prints **`[object Object]`** — pick the field. These are the
ONLY field names; `info.gpu_vram`, `info.vram`, `tier.name`, `tier.id`, `tier.download_gb` do not exist.

```js
const info = await vibe.system.setup();   // {first_boot, vram_mb, current_tier, recommended_tier, tiers, upgrade} (the API sheet's shape)
// tiers: [{tier:"8gb", present:true, total_gb:23.6, have_gb:23.6, llm:"Gemma 4 26B-A4B-it ultra-uncensored heretic"}, …]
// upgrade: null | {tier:"16gb", status:"downloading"|"switching"|"done"|"error", current:"llm/…gguf", error:null}
tierEl.textContent = info.current_tier;
const hw = await vibe.hw.get();           // wifi+battery+brightness+volume+display+bluetooth+datetime+memory+disk in ONE call
// display:   {available, count, multiscreen, displays:[{name:"eDP-1", connected, primary, mode:"2560x1600", …}]}
// datetime:  {available, timezone:"Europe/Berlin", ntp, ntp_synced, rtc_utc}
// memory_mb: {total:31967, available:17233}, disk_gb: {total:1024.2, free:611.4} — objects, NOT numbers
// battery: {…, health_percent:80}; missing hardware: {available:false, reason:"no battery (desktop or VM)"} — render the reason
batteryEl.textContent = hw.battery.available ? `${hw.battery.percent}% ${hw.battery.charging ? "⚡" : ""}` : hw.battery.reason;
memEl.textContent     = `${Math.round((hw.memory_mb.total - hw.memory_mb.available) / 1024)} / ${Math.round(hw.memory_mb.total / 1024)} GB`;
diskEl.textContent    = `${hw.disk_gb.free} GB free of ${hw.disk_gb.total} GB`;
```

## The coder LLM (an "AI" section)

```js
const m = await vibe.models();   // m.llm on 8/16 GB: ONE entry {key:"default", name:"Gemma 4 … heretic (…)", present:true, active:true, selected:true}
llmEl.textContent = m.llm.find((l) => l.active).name;        // show the model; no fast model [R89]
if (m.llm.length > 1) {                                      // a picker only where the tier offers a choice
  sel.innerHTML = m.llm.map((l) => `<option value="${l.key}" ${l.selected ? "selected" : ""}>${l.name}${l.present ? "" : ` — download ${l.download_gb} GB first`}</option>`).join("");
  sel.onchange = () => vibe.settings.set("llm_model", sel.value);
}
```
- Fill it FROM `m.llm` after `await vibe.models()` resolves — never a static list, never offer "fast" yourself
  (`llm_model` "fast" serves the same model). Show `m.llm_setting.fallback` when set.
- `present:false` → a "⬇ Download (N GB)" button (`vibe.models.download(entry.download_key)`), then poll
  `vibe.models()` until `present`.

## Settings store

```js
const s = await vibe.settings.get();          // read s.image_expand — never (await vibe.settings.get().image_expand)
await vibe.settings.set("accent", "#ff5cf0"); // applied live; a bad value rejects with the rule (API sheet) — show it
await vibe.hw.brightness.set(70);             // percent
await vibe.hw.volume.set(40);                 // percent; vibe.hw.volume.set(40, true) also mutes
```
Always send the value — `vibe.settings.set("accent")` without one is an error, not "reset".

## Language & translation (a "Language" section) [R71]

```js
// m.translation: {enabled:false, language:"en", languages:["en","de",…], names:{en:"English", …}, quality:"fast", fallback:null,
//                 quality_llm:true, model:{fast:"Index-Translate …", quality:"Gemma 4 … (the loaded LLM)"}, present:{fast:true, quality:true}}
sel.innerHTML = m.translation.languages.map((c) => `<option value="${c}" ${c === m.translation.language ? "selected" : ""}>${m.translation.names[c]}</option>`).join("");
sel.onchange = () => vibe.settings.set("language", sel.value);
toggle.checked = m.translation.enabled;
toggle.onchange = () => vibe.settings.set("translation", toggle.checked ? "on" : "off");
qual.innerHTML = `<option value="fast">Fast (small CPU model)</option><option value="quality">Quality (${m.translation.model.quality})</option>`;
qual.value = m.translation.quality;
qual.onchange = () => vibe.settings.set("translation_quality", qual.value);
const tr = m.extras.find((e) => e.key === "translation");    // {present, present_modes:{fast, quality}, missing_gb, download_gb}
if (tr.missing_gb > 0) dlBtn.onclick = () => vibe.models.download("translation");   // label it with tr.missing_gb GB
```
- Off (the default) = nothing is translated; on + a language other than English = the whole OS, every app
  window and the generated content follow it. Show `fallback` when set.
- `tr.present` is true on the shipped OS (the fast model is built in): never show translation as "not
  downloaded"; `missing_gb` is 0 on 8 GB (quality needs no download).
- An app's own text: `await vibe.translate("Save")` → the OS language; it rejects while translation is off — catch it.
- Dictionaries & Wiktionary (same section): `m.extras` entries `dictionaries` (`vibe.dict("Haus", "de", "en")`)
  and `wiktionary_<lang>` (one ZIM per language, for `vibe.search()`); each with `present` false → "⬇ Download
  (N GB)" → `vibe.models.download(key)`; suggest the OS language's Wiktionary + `wiktionary_en`.

## Image models (the same "AI" section)
- A picker from `m.models` (keys and settings: the API sheet): badge the `active` one, show `licence` when set.
  The OS bakes in Krea 2 (`default` + `safe`); add-ons (`addon:true`, `present:false`) get "⬇ Download (N GB)"
  → `vibe.models.download(key)`, then poll `vibe.models()` until `present`.
- Prompt expansion `image_expand` (shown in `m.expand.setting`): a three-way switch auto / on / off.
- Each model's `defaults` (steps/sampler/scheduler/cfg) as placeholders; "reset to defaults" sets null.

## Bigger model tiers via `vibe.system`
- List `info.tiers`; for any not `present`, offer "⬇ Download the {tier} models" with its size
  (`total_gb - have_gb`) → `vibe.system.upgrade(tier)`. The OS stays usable while it downloads.
- `vibe.system.upgrade()` and `vibe.fs.format()` are confirmed **by the OS itself**; declined → the promise
  rejects with `cancelled: …` — catch it, show "cancelled". Call them only from their own button, never on load.

## Patterns
- Sections: Appearance (accent color pickers + live preview, os_name text field, wallpaper), Display
  (brightness slider), Sound (volume slider + mute), Network (WiFi, Bluetooth, airplane), AI (the coder LLM's
  name + image model picker + expansion switch), Language (picker + switch + fast/quality + add-on downloads),
  System (VRAM, current tier, tiers + upgrade), Danger zone (factory reset behind an in-page two-step confirm:
  "Reset…" reveals a red "Yes, erase everything" button).
- Sliders: update state optimistically, call the API on `change` (not every `input` event).
- Any `available:false` device: render the row disabled with its `reason`.
- Text the user types (a new OS name, a WiFi password) comes from an `<input>` — never `window.prompt()` or `vibe.ask()`.
