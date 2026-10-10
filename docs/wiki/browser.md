<!-- keywords: browser, web, website, surf, address bar, url, imaginary web, tabs, bookmarks, back, forward, navigate -->
# Recipe: web browser

The imaginary web is a REAL server: `GET /web/<any address or words>` returns a full
site (a loading page shows until it is built, then it swaps in by itself). So a
browser is tiny — an address bar + one full-size `<iframe>`. Do NOT over-engineer it.

**The whole engine is one line** — point the iframe at /web/:

```html
<iframe id="view" src="/web/home"
        sandbox="allow-scripts allow-forms" style="flex:1;width:100%;border:0"></iframe>
```

**Rules that keep it bug-free (a fragile browser is the #1 mistake here):**
- Give EVERY control a unique `id` and select with `getElementById`. NEVER use
  positional selectors like `:nth-child(2)` — they return null and crash on
  `addEventListener`. Guard: `const b=document.getElementById('back'); if(b) b.onclick=…`.
- Navigating = set the src: `view.src = "/web/" + encodeURI(input.value.trim())`.
  `encodeURI` keeps the `/` in sub-paths; the server accepts any text.
- **Links inside a page navigate the iframe on their own.** Do NOT add `message`
  listeners, click interceptors, or postMessage plumbing — that is obsolete and
  breaks things. The page just works.
- Reload/refresh a page: `view.src = view.src.split('?')[0] + "?_fresh=1"`.
- Back/forward: keep your OWN `const hist=[]; let i=-1;` of addresses YOU navigate to
  from the bar/bookmarks; on Enter do `hist.splice(i+1); hist.push(a); i=hist.length-1`.
  Back = `if(i>0){ i--; view.src="/web/"+encodeURI(hist[i]); }`. Bound-check every index.
  (You cannot read the iframe's own URL — it is cross-origin — so history only tracks
  bar/bookmark navigations; that is expected and fine.)
- Bookmarks: `vibe.store("bookmarks", array)` / `await vibe.load("bookmarks")`.
- Layout: a slim top bar (flex row: back, forward, reload, address input flex:1, star)
  above the iframe (flex:1). Address input handles `keydown` Enter.
- No error handling gymnastics — with the pattern above there is nothing to catch.
