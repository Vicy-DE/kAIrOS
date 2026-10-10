"""vibeOS knowledge organ: web search when online, offline encyclopedia otherwise.

- Online:  DuckDuckGo HTML endpoints (no API key), 4s budget.
- Offline: kiwix-serve over EVERY local ZIM archive (Wikipedia + the Wiktionary add-ons
  [R71 P3]), managed as a child process. libkiwix refuses a search over books of different
  languages, so the ZIMs are searched one language group at a time: the English books get
  the (English) query, the books in the user's language get the text as typed (`native`), a
  Wiktionary answers the page whose title IS the query first. Every hit says where it came
  from: `source` ("Wikipedia (en)", "Wiktionary (de)"), `book`, `lang`.
Both return the same shape: [{"title", "snippet", "url", "source"?, "book"?, "lang"?}].
"""
import asyncio
import html as htmllib
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp

KIWIX_PORT = 8090
KINDS = {"wikipedia": "Wikipedia", "wiktionary": "Wiktionary", "wikibooks": "Wikibooks",
         "wikivoyage": "Wikivoyage", "wikiquote": "Wikiquote"}


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return htmllib.unescape(re.sub(r"\s+", " ", s)).strip()


def book_info(name: str) -> dict:
    """{name, kind, lang, label} from a ZIM's file stem: `wiktionary_de_all_nopic_2026-07` →
    kind wiktionary, lang de, label "Wiktionary (de)"; an unknown stem keeps itself as the label."""
    m = re.match(r"^([a-z]+)_([a-z]{2,3})(?:_|$)", name)
    kind, lang = (m.group(1), m.group(2)) if m else (name, "")
    title = KINDS.get(kind, kind.capitalize() if m else name)
    return {"name": name, "kind": kind, "lang": lang, "label": f"{title} ({lang})" if lang else title}


def _zim_order(z: Path):
    """Wikipedia first (the encyclopedia stays books[0]: suggestions, the default search), then
    the rest by name."""
    return (0 if z.stem.startswith("wikipedia") else 1, z.name)


class Knowledge:
    def __init__(self, vibeos_dir: Path):
        self.bin = vibeos_dir / "bin" / "kiwix-serve"
        self.zim_dir = vibeos_dir / "knowledge"
        self.proc = None
        self.books = []
        self.info = {}                 # book name → book_info()
        self._net_ok = None
        self._net_checked = 0.0
        # [R78] async () -> "full"|"portal"|"limited"|"none"|"unknown"|None: NetworkManager's
        # connectivity (set by the kernel: hw.nm_connectivity); None = no such source
        self.connectivity = None

    # ---------------- kiwix (offline encyclopedia) ----------------

    @property
    def has_local(self) -> bool:
        return bool(self.books) or bool(self.bin.exists() and list(self.zim_dir.glob("*.zim")))

    async def start_kiwix(self):
        zims = sorted(self.zim_dir.glob("*.zim"), key=_zim_order)
        if not zims or not self.bin.exists():
            print("[knowledge] no ZIM archives / kiwix-serve — offline search disabled", flush=True)
            return
        self.books = [z.stem for z in zims]
        self.info = {z.stem: book_info(z.stem) for z in zims}
        self.proc = await asyncio.create_subprocess_exec(
            str(self.bin), "--port", str(KIWIX_PORT), "--nodatealiases",
            *[str(z) for z in zims],
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        print(f"[knowledge] kiwix-serve up on :{KIWIX_PORT} with {len(zims)} archive(s): "
              f"{', '.join(self.info[b]['label'] for b in self.books)}", flush=True)

    async def stop_kiwix(self, grace: float = 5.0):
        p, self.proc = self.proc, None
        self.books, self.info = [], {}
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), timeout=grace)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()

    async def restart_kiwix(self):
        """After a knowledge add-on download [R71 P3]: kiwix-serve lists its archives at start,
        so a new ZIM means a restart (a search in flight fails once and retries)."""
        await self.stop_kiwix()
        await self.start_kiwix()

    def source_of(self, book: str) -> dict:
        info = self.info.get(book) or book_info(book)
        return {"source": info["label"], "book": book, "lang": info["lang"]}

    def groups(self, query: str, native=None) -> list:
        """[(query, [books])] — one kiwix query per language AND kind (libkiwix refuses a
        mixed-language search; an encyclopedia and a dictionary of the same language are kept
        apart so Xapian's short dictionary pages never crowd the encyclopedia out). `native` =
        {"lang", "query"}: the books in that language get the text as typed [R71 P3];
        everything else gets `query`. Order: the native-language groups, then the
        encyclopedias, then the other dictionaries."""
        out = {}
        for b in self.books:
            info = self.info.get(b) or book_info(b)
            is_native = bool(native) and info["lang"] == native.get("lang") and info["lang"] != "en"
            q = native["query"] if is_native else query
            out.setdefault((info["lang"], info["kind"] == "wikipedia", q, is_native), []).append(b)
        items = list(out.items())
        items.sort(key=lambda kv: 0 if kv[0][3] else (1 if kv[0][1] else 2))
        return [(q, books) for (lang, _enc, q, is_native), books in items]

    async def _page_text(self, s, path: str) -> str:
        try:
            async with s.get(f"http://127.0.0.1:{KIWIX_PORT}/content/{path}",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status != 200:
                    return ""
                body = await r.text()
        except Exception:
            return ""
        return self.page_text(body)

    @staticmethod
    def page_text(body: str) -> str:
        """The readable text of a ZIM page: its paragraphs, then — a Wiktionary keeps its
        senses in lists under one-word section labels ("Bedeutungen:") — its <dd>/<li> items,
        labels and stubs dropped, 900 chars."""
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.DOTALL)
        keep = lambda t: len(t) >= 12 and not t.endswith(":")
        paras = [t for t in (_strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.DOTALL)) if keep(t)]
        text = " ".join(paras)
        if len(text) < 400:
            items = [t for t in (_strip_tags(i) for i in re.findall(r"<(?:li|dd)[^>]*>(.*?)</(?:li|dd)>", body, re.DOTALL)) if keep(t)]
            text = " ".join([text] + items).strip()
        return text[:900]

    @staticmethod
    def parse_search_links(page: str) -> list:
        """[(path, title)] of a kiwix-serve search page: result links look like
        <a href="/content/<book>/Article_Title">Title</a> (older kiwix: /viewer#<book>/A/Title);
        the first path segment is the book = the source."""
        out, seen = [], set()
        for href, title in re.findall(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', page, re.DOTALL):
            m = re.search(r"(?:viewer#|content/)([^\"]+)", href)
            if not m or "search" in href:
                continue
            path = m.group(1).replace("viewer#", "")
            title = _strip_tags(title)
            if title and path not in seen:
                seen.add(path)
                out.append((path, title))
        return out

    async def title_hit(self, s, book: str, title: str):
        """The page whose title IS the query — a dictionary's first answer (Wiktionary books)."""
        for t in dict.fromkeys([title, title.lower(), title.capitalize()]):
            for variant in dict.fromkeys([t, t.replace(" ", "_")]):
                path = f"{book}/{quote(variant)}"
                text = await self._page_text(s, path)
                if text:
                    return {"title": t, "snippet": text, "url": f"kiwix://{path}", **self.source_of(book)}
        return None

    async def kiwix_search(self, query: str, limit: int = 3, books=None, title_first: bool = True):
        """Full-text search over `books` (default: books[0], Wikipedia; all of ONE language) →
        [{title, snippet, url, source, book, lang}]. The Wiktionaries among them also answer the
        page whose title IS the query — first (a dictionary's best answer; the encyclopedia is
        its own group and comes before the dictionaries anyway), or last with title_first=False."""
        books = [b for b in (books if books is not None else self.books[:1]) if b in self.books]
        if not books or not query.strip():
            return []
        results, titles, seen = [], [], set()
        async with aiohttp.ClientSession() as s:
            if len(query.split()) <= 3:
                for b in books:
                    if (self.info.get(b) or book_info(b))["kind"] == "wiktionary":
                        hit = await self.title_hit(s, b, query.strip())
                        if hit and hit["url"][8:] not in seen:
                            seen.add(hit["url"][8:])
                            titles.append(hit)
            try:
                params = [("pattern", query), ("pageLength", str(limit))] + [("books.name", b) for b in books]
                async with s.get(f"http://127.0.0.1:{KIWIX_PORT}/search", params=params,
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    page = await r.text()
            except Exception:
                page = ""
            room = max(0, limit - len(titles))          # the title hits keep their place either way
            for path, title in self.parse_search_links(page):
                if len(results) >= room:
                    break
                if path in seen:
                    continue
                seen.add(path)
                book = path.split("/", 1)[0]
                text = await self._page_text(s, path)
                results.append({"title": title, "snippet": text, "url": f"kiwix://{path}", **self.source_of(book)})
        merged = titles + results if title_first else results + titles
        return merged[:limit]

    async def zim_search(self, query: str, limit: int = 4, native=None):
        """Every ZIM, one kiwix query per language-and-kind group (the native-language group
        first, then the encyclopedias, then the other dictionaries; a Wiktionary's title hit
        leads its group), merged round-robin and capped at `limit`."""
        if not self.books:
            return []
        groups = self.groups(query, native)
        lists = await asyncio.gather(*(self.kiwix_search(q, limit, books) for q, books in groups),
                                     return_exceptions=True)
        lists = [x for x in lists if isinstance(x, list)]
        out, seen = [], set()
        for i in range(limit):
            for lst in lists:
                if i < len(lst) and lst[i]["url"] not in seen:
                    seen.add(lst[i]["url"])
                    out.append(lst[i])
        return out[:limit]

    async def suggest(self, term: str, limit: int = 5):
        """Fast title suggestions from the offline encyclopedia (for live search)."""
        if not self.books or not term:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{KIWIX_PORT}/suggest",
                                 params={"content": self.books[0], "term": term},
                                 timeout=aiohttp.ClientTimeout(total=1.5)) as r:
                    data = await r.json(content_type=None)
        except Exception:
            return []
        out = []
        for item in data if isinstance(data, list) else []:
            label = _strip_tags(str(item.get("label", "")))
            path = item.get("path") or item.get("value", "")
            if label and item.get("kind") != "pattern":
                out.append({"title": label, "path": str(path)})
            if len(out) >= limit:
                break
        return out

    # ---------------- web (DuckDuckGo, keyless) ----------------

    def reset_online(self):
        """forget the cached probe — the kernel calls this after every WiFi connect/disconnect/radio
        change, so web search follows the network at once instead of up to 30 s later [R78]"""
        self._net_checked = 0.0

    async def online(self) -> bool:
        """web reachable? NetworkManager's own check decides when it is sure ("full" -> yes,
        "none" -> no); a portal, limited, unknown or no NetworkManager -> the DuckDuckGo probe.
        Either answer is cached 30 s (reset_online() clears it)."""
        if os.environ.get("VIBEOS_FORCE_OFFLINE"):
            return False
        if time.time() - self._net_checked < 30 and self._net_ok is not None:
            return self._net_ok
        conn = None
        if self.connectivity is not None:
            try:
                conn = await self.connectivity()
            except Exception:
                conn = None
        if conn in ("full", "none"):
            self._net_ok, self._net_checked = conn == "full", time.time()
            return self._net_ok
        try:
            async with aiohttp.ClientSession() as s:
                async with s.head("https://duckduckgo.com",
                                  timeout=aiohttp.ClientTimeout(total=2.5)) as r:
                    self._net_ok = r.status < 500
        except Exception:
            self._net_ok = False
        self._net_checked = time.time()
        return self._net_ok

    async def web_search(self, query: str, limit: int = 4):
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) vibeOS-local/1.0"}
        async with aiohttp.ClientSession(headers=headers) as s:
            for url, kind in (("https://html.duckduckgo.com/html/", "html"),
                              ("https://lite.duckduckgo.com/lite/", "lite")):
                try:
                    async with s.post(url, data={"q": query},
                                      timeout=aiohttp.ClientTimeout(total=4)) as r:
                        page = await r.text()
                except Exception:
                    continue
                results = self._parse_ddg(page, kind, limit)
                if results:
                    return results
        return []

    @staticmethod
    def _parse_ddg(page: str, kind: str, limit: int):
        out = []
        if kind == "html":
            blocks = re.findall(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)
            for href, title, snippet in blocks[:limit]:
                out.append({"title": _strip_tags(title), "snippet": _strip_tags(snippet)[:500],
                            "url": htmllib.unescape(href)})
        else:
            rows = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class=["\']result-link["\'][^>]*>(.*?)</a>'
                              r'.*?class=["\']result-snippet["\'][^>]*>(.*?)</td>', page, re.DOTALL)
            for href, title, snippet in rows[:limit]:
                out.append({"title": _strip_tags(title), "snippet": _strip_tags(snippet)[:500],
                            "url": htmllib.unescape(href)})
        return out

    # ---------------- unified ----------------

    async def search(self, query: str, limit: int = 4, native=None):
        """Web first when online; offline encyclopedia otherwise (or as fallback): every ZIM,
        hits labelled by source; `native` = {"lang", "query"} = the text as typed for the
        books in the user's language [R71 P3]. `sources` lists the books that answered."""
        if await self.online():
            results = await self.web_search(query, limit)
            if results:
                return {"online": True, "source": "web search", "results": results}
        results = await self.zim_search(query, limit, native)
        sources = list(dict.fromkeys(x["source"] for x in results if x.get("source")))
        return {"online": False, "source": "offline encyclopedia", "sources": sources, "results": results}

    async def context_block(self, query: str, limit: int = 3, native=None) -> tuple[str, str]:
        """(facts_text, source) for prompt grounding — best-effort, never raises. Each fact names
        its book; the source string lists the books that answered."""
        try:
            r = await asyncio.wait_for(self.search(query, limit, native), timeout=8)
        except Exception:
            return "", ""
        if not r["results"]:
            return "", ""
        facts = "\n".join(f"- {x['title']}{' (' + x['source'] + ')' if x.get('source') else ''}: {x['snippet'][:350]}"
                          for x in r["results"] if x["snippet"])
        source = r["source"] + (f" ({', '.join(r['sources'])})" if r.get("sources") else "")
        return facts[:1600], source
