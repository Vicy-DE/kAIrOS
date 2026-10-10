<!-- keywords: image, picture, photo, video, music, audio, song, gallery, player, files, storage, save, persist, notes, editor, vision, see, describe, ocr, screenshot -->
# Recipe: media generation & persistent storage

Media (all local GPU — show loading states, generation is not instant):

```js
const url = await vibe.image("a chrome koi fish, cinematic");   // PNG URL, ~seconds-minutes
const urls = await vibe.images(["koi at dawn", "koi at dusk", "koi at night"]); // [URL,URL,URL] — one model load for the whole list
const mp4 = await vibe.video("neon rain over a glass city");    // MP4 URL, minutes! explicit user action only
const wav = await vibe.music("dreamy synthwave", "optional lyrics", 60); // audio URL, ~20 s per 15 s; sung in English by default
const de  = await vibe.music("Schlager, Akkordeon", "[Verse]\nIch tanze durch die Nacht", 30, "de"); // 4th arg: ISO 639-1 language of the lyrics
const seen = await vibe.vision(canvas.toDataURL(), "describe this drawing");   // string; also "/files/…" URLs or "screen" (the display) [R70]
```

- **Video is a downloadable add-on** (it is slow on 8 GB VRAM and large, so it is not
  baked into the small image). If it is not installed, `vibe.video()` rejects with a
  hint; check `vibe.models()` → `extras` for `{key:"video", present, download_gb}` and
  offer `vibe.models.download("video")` before using it.
- **Music is a downloadable add-on** (ACE-Step, `extras[music]`): `vibe.music()` rejects
  when it is not installed — try/catch it, show "music unavailable", never block the app
  on it. Lyrics default to English; pass `language` (`"de"`, `"ja"`, …) for other
  languages, `"auto"` to let the model guess.
- `vibe.image()` works immediately (Krea 2 is baked: `default` = uncensored, `safe` = the stock,
  safety-tuned one); the other image models (`qwen` = Qwen-Image-2.1, `classic` = Z-Image, `chroma`) are downloadable via
  `vibe.models.download(key)`. A gallery or "N variations" = ONE `vibe.images(...)` call,
  never `vibe.image()` in a loop (every call would reload the model).

- Wrap each call in try/catch; on error show the message and a retry button.
- Show elapsed time while waiting; disable the trigger button during generation.
- Generated URLs are plain `/files/...` paths — they work in `<img src>`, `<video src>`,
  `<audio src>` directly and survive reboots.

Storage — two levels:

```js
vibe.store("state", {list: [...]}); const s = await vibe.load("state");
// per-app JSON key-value: perfect for app state, settings, small lists

await vibe.fs.write("/data/my-app/notes/first.txt", "text");
await vibe.fs.read("/data/my-app/notes/first.txt");
await vibe.fs.list("/data/my-app/notes");   // [{name,type,size,modified}]
// real files: use for documents the user should find in the Files app
```

- Keep files under `/data/<your-app-name>/…`; `/system` is read-only.
- Autosave pattern: debounce writes ~500 ms after the last keystroke, show a tiny
  "saved" indicator.
