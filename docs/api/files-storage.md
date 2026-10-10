<!-- keywords: file, files, folder, save, load, storage, persist, autosave, document, notes, data, format, reset -->
# kAIrOS API — files & storage

Two levels. **Small app state** (JSON, per app — use this first):

```js
vibe.store("state", {list:[…]});  const s = await vibe.load("state");
```

**Real files** — Android-style partitions: `/system` read-only, `/data` yours:

```js
await vibe.fs.list("/data/my-app");            // [{name,type,size,modified}]; "/" = partitions
await vibe.fs.read(path);                      // text (≤2 MB)
await vibe.fs.write("/data/my-app/notes/a.txt", text);   // parents auto-created
await vibe.fs.mkdir(path);  await vibe.fs.delete(path);
await vibe.fs.format();                        // DANGER: wipes ALL of /data (factory reset)
```

- Keep app files under `/data/<your-app-name>/…`; writes to `/system` fail (read-only).
- Autosave pattern: debounce ~500 ms after the last keystroke + a tiny "saved" hint.
- `format()` needs a two-step confirm in YOUR UI — it erases every app's data
  and resets settings (system-settings.md).
- Generated media lives at `/files/...` URLs and needs no fs calls → media.md.
