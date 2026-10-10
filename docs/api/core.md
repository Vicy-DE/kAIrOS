<!-- keywords: ask, ai, llm, text, search, facts, web, browse, site, page, notify, toast, docs, translate, translation, language, multilingual, dictionary, dict, lookup, vocabulary, wiktionary, encyclopedia -->
# kAIrOS API — core

Every app gets `window.vibe` injected. All calls return Promises.

| call | returns | notes |
|---|---|---|
| `vibe.ask(prompt)` | string | the OS language model — text/JSON on demand |
| `vibe.search(query)` | `{online, results:[{title,snippet,url,source}]}` | real facts: live web when online, offline encyclopedia otherwise (every installed ZIM; `source` = "Wikipedia (en)", "Wiktionary (de)", …) |
| `vibe.dict(word, from?, to?)` | `{entries:[{headword, translations:[…], source, from, to, match}], count}` | dictionary lookup [R71 P3] over en/de/es/fr/it/ja/ru (the `dictionaries` add-on, system-settings.md); `to` defaults to the OS language, `from` optional; exact → folded → prefix; rejects while the add-on is missing |
| `vibe.browse(address)` | `{html, cached, grounded_by}` | one page of the imaginary web as HTML |
| `vibe.store(key, value)` / `vibe.load(key)` | true / value | persist small JSON state per app (`localStorage` works too — in-memory, persisted by the OS) |
| `vibe.notify(text)` | true | OS toast notification |
| `vibe.translate(text, to?)` | string (an array for an array) | the translation layer [R71]: into the OS language (settings `language`) or `to` — en/de/es/fr/it/ja/ru; cached; rejects while `translation` is off (system-settings.md) |
| `vibe.docs(category?)` | index / one category (markdown) | this documentation |

**The imaginary web is a real URL space**: `<iframe src="/web/any address or plain
words">` shows a full AI-written site (a loading page appears until it is built —
no code needed). Every link inside is a prompt for its next page.

**Pictures the easy way**: `<img src="/genimg/short vivid description?w=512&h=384">` → see
media.md — for galleries too (each picture paints in the background; never `vibe.image` in a loop).
Only the calls listed in these pages exist; an unknown `vibe.*` member is **`undefined`** (the console
names the real one) — feature-detect with `typeof vibe.x === "function"` and skip what is missing.
`window.alert/confirm/prompt` are blocked in apps (shimmed to a notice / `false` / `null`): every
question, confirmation and text entry is an in-page element. `vibe.ask()` asks the language model,
never the user.
Hardware (WiFi, display, battery…) → hardware pages: network.md, display.md,
power-battery.md. Files & saving → files-storage.md. Settings/models → system-settings.md.
