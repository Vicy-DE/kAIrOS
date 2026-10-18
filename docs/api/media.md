<!-- keywords: image, picture, photo, genimg, gallery, video, clip, music, audio, song, sound, generate, art, draw, vision, see, look, describe, read text, ocr, screenshot, screen, camera, webcam, recognize, retouch, enhance -->
# kAIrOS API — media generation

**Pictures — the tool (simplest, preferred):** just write an URL, the OS paints it:

```html
<img src="/genimg/a red fox on a snowy cliff at dawn" style="width:100%">
```
The page shows instantly; the picture appears when rendered (cached afterwards —
same description = instant). Optional size: `/genimg/desc?w=768&h=512`.

**Programmatic media** (when you need the URL in JS):

```js
const png = await vibe.image("prompt");             // URL — seconds to minutes
const set = await vibe.images(["p1", "p2", "p3"]);  // [URL, URL, URL] — ONE model load for the list (null = failed)
const var4 = await vibe.images("prompt", 4);        // 4 variations, fresh seeds; opts: {count,width,height,model,adult,expand}
const mp4 = await vibe.video("prompt");             // URL — MINUTES; explicit user click only
const wav = await vibe.music("prompt", lyrics, 60); // URL — ~20 s per 15 s of audio; empty lyrics = instrumental
const de  = await vibe.music("Schlager, Akkordeon", "[Verse]\nIch tanze durch die Nacht", 30, "de"); // sung language
```

- `vibe.music(prompt, lyrics, seconds, language)`: `language` = ISO 639-1 code of the
  lyrics (`"de"`, `"ja"`, …). **Default `"en"`** — set it whenever the lyrics are not
  English; `"auto"` lets the model guess; anything else rejects. Music is an add-on: try/catch.

**Improving a picture — `vibe.image.improve(picture, instruction)`** (✨, the OS picture editor):

```js
const v = await vibe.image.improve(img, "make it a snowy night"); // img, or its URL
img.src = v;           // v reads as the new version's URL; also v.url, v.version
const v2 = await vibe.image.improve(v, "add a full moon");        // continues from v
const all = await vibe.image.versions(img.src); // {current, versions: [{v, url, instruction}]}
```
Only pictures the OS painted: `vibe.image()` / `vibe.images()` URLs, `/genimg/…` URLs
or the `<img>` showing one — never a canvas, a data: URL or an upload. ~1 min per
edit (more the first time); a `/genimg/` picture still painting is waited for.
Every version is kept. The editor is an add-on (`image_edit`): try/catch, offer
`vibe.models.download("image_edit")`; a refused instruction rejects with the reason.

**Seeing — `vibe.vision(image, prompt)` → string** (the OS looks at a picture):

```js
const what = await vibe.vision(canvas.toDataURL("image/png"), "What is drawn here?"); // any data:image/… URL
const text = await vibe.vision("/files/images/x.png", "Read all the text, verbatim.");  // any /files or /genimg URL
const desk = await vibe.vision("screen", "Which windows are open?");                  // the display; "screen:1" = monitor 1
```
Seconds (model swap + one look); pictures are fitted to 1280 px. Vision is an add-on
(`vibe.models()` → `extras[vision]`): try/catch it.

- A gallery / several pictures: ONE `vibe.images([...])` call (or `<img src="/genimg/…">`
  tags, batched by the OS) — never `vibe.image()` in a loop (each call reloads the model).
  Prompts for the default model are expanded by the OS into a full description (setting
  `image_expand`); write what should be in the picture, not keyword soup.
- `adult: true` (18+ pictures): every person is a fictional adult aged 25+. An under-18
  age or word anywhere in such a prompt blocks the picture, even a thing's age ("an old
  scotch", not "a 12-year-old scotch"); "teen" / "barely legal" become "adult … aged 30".
- Wrap in try/catch; show elapsed time; disable the trigger button while running.
- Video (and some image models) are downloadable add-ons: check
  `vibe.models()` → `extras[]` / `models[]` for `{present, download_gb}` and offer
  `vibe.models.download(key)` when absent.
- Model choice / steps / schedulers → system-settings.md.
- Result URLs are plain `/files/...` paths: valid in `<img>/<video>/<audio>` and
  they survive reboots. Saving files → files-storage.md.
