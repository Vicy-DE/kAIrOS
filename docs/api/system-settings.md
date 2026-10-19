<!-- keywords: settings, preferences, config, theme, wallpaper, accent, os name, time, date, timezone, clock, ntp, volume, sound, speaker, output, model, models, install, installer, tier, upgrade, setup, llm, fast mode, coder model, vram, language, translation, translate, multilingual, dictionary, dictionaries, wiktionary, add-on -->
# kAIrOS API — settings & system

**Exact result shapes — render FIELDS, never whole objects** (an object in text prints `[object Object]`):

```js
const info = await vibe.system.setup();
// {vram_mb: 8188, current_tier: "8gb", recommended_tier: "8gb", first_boot: false,
//  tiers: [{tier:"8gb", present:true, total_gb:23.6, have_gb:23.6, llm:"Gemma 4 26B-A4B-it (…)"}, {tier:"16gb", present:false, total_gb:24.1, have_gb:9.7, llm:"…"}, …],
//  upgrade: null | {tier, status:"downloading"|"switching"|"done"|"error", error}}
vramEl.textContent = `${(info.vram_mb / 1024).toFixed(0)} GB`;                       // NOT info.gpu_vram / info.vram
for (const t of info.tiers) row(`${t.tier} — ${t.llm}`, t.present ? "installed" : `⬇ ${(t.total_gb - t.have_gb).toFixed(1)} GB`);   // NOT t.name / t.id / t.download_gb
const hw = await vibe.hw.get();   // ONE call: {wifi, battery, brightness, volume, display, bluetooth, datetime, memory_mb, disk_gb}
// battery:{available, percent, status, charging, ac_plugged, health_percent}  brightness:{percent}  volume:{percent, muted}
// memory_mb:{total, available}  disk_gb:{total, free}   — missing hardware: {available:false, reason} → render the reason
memEl.textContent = `${Math.round(hw.memory_mb.total / 1024)} GB RAM`;  battEl.textContent = hw.battery.available ? `${hw.battery.percent}%` : hw.battery.reason;
```

**The coder LLM** (an "AI" section): `const m = await vibe.models();` → `m.llm` =
`[{key, name, present, addon, download_gb, download_key, active, selected}]`, `m.llm_setting` = `{setting, effective,
fallback, modes}`. Render FROM `m.llm` (fetch `vibe.models()` first): on 8/16 GB it holds ONE entry, `default` = the
uncensored heretic Gemma 4 (the best apps, minutes per app, answers adult requests itself) — show its name, no picker.
There is no separate fast model [R89]: `llm_model` "fast" serves the same model. A picker only when `m.llm` lists more
than one entry (`vibe.settings.set("llm_model", entry.key)`); an entry with `present:false` → "⬇ Download (N GB)" =
`vibe.models.download(entry.download_key)` (never the bare "uncensored" — that is the image model). Show
`m.llm_setting.fallback` when set. `m.refusal_fallback.last`
(`{at, from, to, kind, prompt}`) = the last refusal fallback — show it as one line in this section.

**Language & translation** (a "Language" section) [R71]: `m.translation` = `{enabled, language,
languages: ["en","de","es","fr","it","ja","ru"], names: {de: "German", …}, quality: "fast"|"quality", effective,
fallback, quality_llm, model: {fast, quality}, present: {fast, quality}, running, cache}`. A `<select>` over
`m.translation.languages` (label `names[code]`, selected = `language`) → `vibe.settings.set("language", code)`; a
switch → `vibe.settings.set("translation", "on"|"off")`; fast/quality → `vibe.settings.set("translation_quality", …)`
— label them "Fast (small CPU model)" and "Quality (" + `model.quality` + ")": `quality_llm` true (8 GB) = the loaded
LLM translates itself, no download. Off = nothing is translated. The fast model is built in; `m.extras` lists
`translation` with `present` (true = works — never "not downloaded" then), `present_modes: {fast, quality}` and
`missing_gb` (what the download still fetches): `missing_gb` > 0 → a "⬇ Download (missing_gb GB)" button calling
`vibe.models.download("translation")`. Any app: `await vibe.translate
("text")` → string in the OS language, `vibe.translate(["a", "b"], "en")` → array into a named language; it
rejects while translation is off. Two more add-ons in `m.extras` (render a download button each while
`present` is false): `dictionaries` (kind `dictionary`, 0.12 GB, `pairs`) powers `vibe.dict(word, from?, to?)` →
`{entries: [{headword, translations, source}]}` — works with translation off; `wiktionary_<lang>` (kind
`knowledge`, one per language, `language`, `download_gb` 0.4–9.2) joins the offline `vibe.search()` — offer the
OS language's + `wiktionary_en`.

**OS look & feel** (applied live within seconds; persisted on /data):

```js
const s = await vibe.settings.get();            // {os_name, accent, accent2, wallpaper, image_model, image_expand, llm_model, …}
await vibe.settings.set("accent", "#ff5cf0");
```

**Validation**: known keys are checked — `accent`/`accent2` must be a CSS colour, `os_name` 1–40 characters,
`image_model`/`image_expand`/`llm_model`/`translation`/`language`/`translation_quality` one of their listed values, `image_steps` 1–150 or null; a bad value
rejects the `vibe.settings.set` promise with the rule and stores nothing. Always pass the value.

**AI image models**: `m.models` → `[{key,name,present,addon,download_gb,active,defaults,expander,licence}]` (+ `m.expand`,
`m.sessions`); switch via `vibe.settings.set("image_model", "default|safe|qwen|classic|chroma")` (default = Krea 2
uncensored, safe = stock Krea 2 faster, qwen = Qwen-Image-2.1 best text, classic = Z-Image fastest, chroma = Chroma1-HD
slow; "uncensored" = default);
prompt expansion `vibe.settings.set("image_expand", "auto|on|off")`; tune `image_steps/image_sampler/image_scheduler/
image_cfg` (null = tuned default); download add-ons with `vibe.models.download(key)`. Many pictures: `vibe.images([...])` → media.md.

**Bigger brain / danger zone**: `vibe.system.upgrade(tier)` downloads a bigger tier — the OS asks the user to confirm
in its own dialog (declined → rejects with `cancelled: …`); factory reset `vibe.fs.format()` (also OS-confirmed) →
files-storage.md. Displays → display.md.

**Date & time**: `vibe.hw.datetime.get()` → `{timezone, ntp, ntp_synced}`; `.set({timezone:"Europe/Berlin"})`,
`.set({ntp:true})`, `.set({time:"2026-07-10 21:30:00"})`; `.timezones()` → the full picker list.
**Volume & audio output**: `vibe.hw.volume.get()/.set(percent, mute?)`; `vibe.hw.sound.list()` → `{sinks:[{name,default}]}`,
`vibe.hw.sound.set(sinkName)` (e.g. after Bluetooth headphones connect → network.md).

Questions and text entry: in-page inputs and buttons only — `window.prompt/confirm/alert` are blocked;
`vibe.ask()` asks the language model, not the user.
