"""[R86] Refusal detection and the hard minors guard.

kAIrOS is an 18+ system without content filters; its default LLM (Gemma 4) still refuses adult
requests, and until R86 the kernel shipped the refusal as the result: a cached "website" that
read "I cannot fulfill this request. I am programmed to be a helpful and harmless AI assistant.
My safety guidelines prohibit me from generating sexually explicit content or pornography."
(the owner's test 2026-10-09, desk-test cache/web/porn-xxx-…-387761c2.html), and a picture whose
prompt was the expander's refusal text (job 1d491e7b6c58). server.llm_generate / llm_stream call
is_refusal() on every user-facing generation and climb the fallback ladder when it says yes.

is_refusal(text, kind) -> (bool, reason). kind:
  "html"   — a page or an app: the visible text (tags, scripts, styles stripped) is short or the
             document is tag-poor, AND a refusal sentence stands near its start.
  "text"   — a prose answer, a spec, game code, a wallpaper scene: a refusal sentence at the start
             (after a lead-in such as "I'm sorry, but"), in a short answer; a long answer that merely
             opens with one is left alone unless it names the model's own guidelines/programming.
  "prompt" — an image-prompt expansion: short by nature, a refusal sentence anywhere counts.

Precision over recall (the task rule): a missed refusal falls back to today's behaviour, a false
positive costs a retry and possibly a model swap — and, on the stick without the uncensored file, a
502 for a correct answer (review round 2, S1). So the phrases are first-person refusals with their
object ("I can't help WITH / fulfil / create / generate …", never a bare "I can't"), a match led by a
quote or by "say:"/"e.g." (a story's dialogue, an answer explaining refusals) does not count, idioms
("I can't help thinking/but") are excluded, "I can't X, but/so here is …" followed by real content is
partial help and not a refusal, the model's "guidelines / policies" count only as ITS OWN ("my …"),
"as an AI" / "I'm designed to" count only when a refusal verb follows, and a page with real structure
or a long answer is never a refusal because of one sentence. Languages: EN, DE (the OS languages the
owner uses) plus FR, ES, IT.

minor_risk(text) is the hard minors guard (§ at the end): sexual term + minor indicator in one clause.
cites_minors(refusal) is its safety net: a refusal that itself names minors blocks the ladder.
"""
import re
import unicodedata
from html import unescape

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

# how far into the text a refusal sentence is looked for, and how short a "short" answer is
SCAN = 700
SHORT = 700            # chars: an answer this short that opens with a refusal IS one
LONG = 1600            # chars: beyond this a leading refusal sentence is ignored (unless strong)
START_TEXT = 48        # chars after the lead-in within which the sentence must start (text/prompt)
START_HTML = 90        # the same for a page's visible text (a <title> may come first)
HTML_SHORT = 600       # visible chars: a refusal page has little to say
HTML_TAG_POOR = 6      # tags: a refusal "page" has (almost) no structure
PIVOT_CONTENT = 40     # chars of real content after "…, but / so / however" that make it partial help

# lead-ins a refusal may open with before the phrase itself (markdown marks, apologies, "but", labels)
_LEAD = re.compile(
    r"^(?:[\s*#>_\-•·]+"
    r"|(?:i'?m\s+|i am\s+)?(?:really\s+|so\s+|very\s+|terribly\s+)?sorry[,.!]*\s*"
    r"|unfortunately[,]?\s*|i apologi[sz]e[,.]*\s*|my apologies[,.]*\s*|(?:oh|hmm|well|ok|okay)[,.]*\s*"
    r"|(?:error|note|warning|notice|important|request declined|declined|response|answer|reply|ai|assistant)[:.]?\s*"
    r"|sadly[,]?\s*|but\s+|however[,]?\s*"
    r"|es tut mir (?:sehr |wirklich )?leid[,.]*\s*|leider[,]?\s*|entschuldigung[,.]*\s*|tut mir leid[,.]*\s*|aber\s+"
    r"|(?:hinweis|fehler|antwort)[:.]?\s*"
    r"|je suis (?:vraiment )?désolée?[,.]*\s*|désolée?[,.]*\s*|malheureusement[,]?\s*|mais\s+"
    r"|lo siento(?: mucho)?[,.]*\s*|lamentablemente[,]?\s*|pero\s+"
    r"|mi dispiace[,.]*\s*|purtroppo[,]?\s*|ma\s+"
    r"|申し訳(?:ありません|ございません)(?:が|。)?\s*|すみません(?:が|。)?\s*|残念ながら\s*"
    r"|извините,?\s*|к сожалению,?\s*|простите,?\s*|мне жаль,?\s*(?:но\s+)?)*", re.IGNORECASE)

# "help" / "assist" as a refusal object — never the idioms "I can't help but / thinking / it / myself"
_HELP = r"(?:help|assist)(?!\s+(?:but|it|myself|\w+ing)\b)"
_EN_VERBS = (r"(?:" + _HELP + r"|fulfil?l|comply|complete|create|write|generate|produce|provide|do (?:that|this)"
             r"|continue|proceed|describe|depict|engage|participate|draw|design|build|code|compose|render|expand"
             r"|rewrite|answer|respond|support|accommodate|honou?r)")
_EN_CANT = (r"(?:cannot|can'?t|can not|am unable to|'m unable to|am not able to|'m not able to|won'?t|will not"
            r"|must not|may not|do not|don'?t|refuse to|have to decline to|must decline to"
            r"|am not (?:allowed|permitted|going) to|'m not (?:allowed|permitted|going) to)")
_DE_VERBS = (r"(?:helfen|weiterhelfen|erfüllen|erstellen|schreiben|generieren|nachkommen|entsprechen|unterstützen"
             r"|fortfahren|beschreiben|darstellen|tun|machen|anbieten|liefern|verfassen|umsetzen|erzeugen|bearbeiten"
             r"|ausführen|bauen|programmieren|produzieren|anfertigen|zeichnen|malen)")

# every pattern is a named group; the STRONG ones name the model's own rules/programming — they
# count in a longer answer too, the others only at the start of a short one
_PATTERNS = [
    # ---- English
    ("en_cant", r"\bI " + _EN_CANT + r"\s+(?:be able to\s+)?" + _EN_VERBS + r"\b"),
    ("en_unable", r"\bI'?m (?:unable|not able) to\s+" + _EN_VERBS + r"\b"),
    # only the NEGATED form: "that is not appropriate / isn't something I can help with" — never "yes, that is allowed" [S1]
    ("en_inappropriate", r"\b(?:that|this)(?: request| content| kind of content)? (?:(?:is|would be) not|isn'?t|is not) "
                         r"(?:appropriate|something I can (?:help|do|create|write)|allowed|permitted)\b"),
    # "I'm programmed/designed to …" only with a refusal object — not "designed to be a friendly assistant" [S1]
    ("en_programmed", r"\bI(?:'m| am) (?:programmed|designed|built|trained) to (?:avoid|refuse|decline|not\b"
                      r"|be (?:a )?(?:helpful|safe)(?:,| and) harmless)"),
    # the model's OWN guidelines: "my safety guidelines", "my policies", "my programming" — never "my rules for sourdough" [S1]
    ("en_guidelines", r"\bmy (?:(?:safety|content|usage|ethical|own) (?:guidelines|policies|programming|principles|rules)"
                      r"|guidelines|policies|programming)\b"),
    ("en_against", r"\b(?:against|violates?|goes against|conflicts? with) my (?:content |usage |safety |ethical )?"
                   r"(?:policy|policies|guidelines|programming|principles|rules|values|terms)\b"),
    # "as an AI, I …" only with a refusal verb — never "as an AI, I don't have feelings" [S1]
    ("en_as_ai", r"\bas an AI(?: language model| assistant| model| system)?,? I " + _EN_CANT + r"\s+(?:be able to\s+)?"
                 + _EN_VERBS + r"\b"),
    ("en_decline", r"\bI (?:must|have to|need to|will|'ll|respectfully|politely) (?:respectfully |politely )?(?:decline|refuse)\b"),
    ("en_prohibited", r"\bI(?:'m| am) (?:prohibited|forbidden|restricted|not permitted|not allowed) (?:from|to)\b"),
    ("en_explicit", r"\b(?:sexually explicit|pornographic|explicit sexual|adult|explicit) (?:content|material)"
                    r"[^.!?\n]{0,40}?\b(?:is|are|goes|go) (?:not (?:allowed|permitted|something I)|against|prohibited|outside)\b"),
    # ---- German  (round 3, Y2: every "ich kann … nicht" needs a refusal VERB — "ich kann dir nicht sagen, wer
    # gewinnt" is information; the verbless de_kann_nicht is gone)
    # "ich kann [dir / diese Website / so etwas …] nicht <refusal verb>": up to four words between kann and nicht
    ("de_kann_nicht_v", r"\bich kann(?:\s+[\wäöüß-]+){0,4}\s+nicht\s+" + _DE_VERBS + r"\b"),
    # "ich kann keine … erstellen" — a refusal verb within the clause; "ich kann dir keine genaue Zahl nennen" is information [S1]
    ("de_keine", r"\bich kann (?:dir |ihnen |euch )?(?:leider )?keine[nrs]?(?:\s+[\wäöüß,-]+){0,5}?\s+" + _DE_VERBS + r"\b"),
    # "ich darf keine/nicht … erstellen/helfen" — never "ich darf nicht vergessen" [S1]
    ("de_darf", r"\bich darf(?:\s+[\wäöüß,-]+){0,4}?\s+(?:nicht|keine[nrs]?)(?:\s+[\wäöüß,-]+){0,5}?\s+" + _DE_VERBS + r"\b"),
    ("de_darf_das", r"\bich darf (?:dir |ihnen |euch )?(?:leider )?(?:das|dies|so etwas|solche inhalte) nicht\b"),
    ("de_lage", r"\bich bin (?:leider )?nicht in der lage\b"),
    ("de_programmiert", r"\bich bin (?:darauf |so )?programmiert\b"),
    ("de_als_ki", r"\bals (?:ki|künstliche intelligenz|sprachmodell|ki-\w+)(?:-\w+)?,? (?:kann|darf|muss) ich (?:(?:dir|ihnen|euch|dabei|das|dies|leider)\s+)*"
                  r"(?:nicht|keine[nrs]?)(?:\s+[\wäöüß,-]+){0,5}?\s+" + _DE_VERBS + r"\b"),       # never "als KI kann ich nicht in die Zukunft sehen" [Y2]
    ("de_richtlinien", r"\bmeine[nr]? (?:sicherheits|nutzungs|inhalts)?richtlinien\b"),
    ("de_verstoesst", r"\bverst(?:ö|oe)(?:ß|ss)t gegen meine\b"),
    ("de_anfrage", r"\b(?:diese|die|deine|ihre) (?:anfrage|bitte|aufgabe) kann ich (?:leider )?nicht\b"),
    ("de_dabei", r"\b(?:dabei|damit|hierbei) kann ich (?:dir |ihnen |euch )?(?:leider )?nicht (?:helfen|weiterhelfen)\b"),
    ("de_ablehnen", r"\bich (?:muss|werde) (?:diese anfrage |das |dies |diese bitte )?(?:leider )?ablehnen\b"),
    # ---- French
    ("fr_peux_pas", r"\bje ne peux pas (?:vous |t'|te |y )?(?:aider|créer|générer|écrire|répondre|fournir|produire|réaliser"
                    r"|satisfaire|donner suite|rédiger|continuer|décrire)\b"),
    ("fr_en_tant", r"\ben tant qu'?(?:ia|intelligence artificielle|assistant),? je ne (?:peux|dois|suis) pas\b"),
    ("fr_contraire", r"\b(?:contraire|contrevient) à mes (?:directives|principes|règles)\b"),
    ("fr_directives", r"\bmes (?:directives|principes|règles)(?: de sécurité)? (?:m'interdisent|ne me permettent pas)\b"),
    ("fr_mesure", r"\bje ne suis pas (?:en mesure|autorisée?) (?:de|à)\b"),
    ("fr_refuser", r"\bje dois (?:refuser|décliner)\b"),
    # ---- Spanish
    ("es_no_puedo", r"\bno puedo (?:ayudarte|ayudarle|ayudar|crear|generar|escribir|cumplir|proporcionar|producir"
                    r"|continuar|realizar|atender|redactar|describir)\b"),
    ("es_como_ia", r"\bcomo (?:una |un )?(?:ia|inteligencia artificial|asistente)(?: de ia)?,? no puedo\b"),
    ("es_contra", r"\bva en contra de mis (?:directrices|políticas|principios|normas)\b"),
    ("es_directrices", r"\bmis (?:directrices|políticas|normas)(?: de seguridad)? (?:me impiden|no me permiten)\b"),
    ("es_autorizado", r"\bno estoy (?:autorizad[oa]|en condiciones) (?:para|de)\b"),
    ("es_rechazar", r"\bdebo (?:rechazar|declinar)\b"),
    # ---- Italian
    ("it_non_posso", r"\bnon posso (?:aiutarti|aiutarla|aiutare|creare|generare|scrivere|soddisfare|fornire|produrre"
                     r"|continuare|realizzare|esaudire|redigere|descrivere)\b"),
    ("it_come_ia", r"\bcome (?:un'|una |un )?(?:ia|intelligenza artificiale|assistente)(?: ia)?,? non posso\b"),
    ("it_contro", r"\b(?:contro|in contrasto con) le mie (?:linee guida|politiche|regole|norme)\b"),
    ("it_linee", r"\ble mie (?:linee guida|politiche|regole)(?: di sicurezza)? (?:mi impediscono|non mi permettono|mi vietano)\b"),
    ("it_grado", r"\bnon sono (?:in grado|autorizzat[oa]) (?:di|a)\b"),
    ("it_rifiutare", r"\bdevo (?:rifiutare|declinare)\b"),
    # ---- Japanese / Russian (OS languages; round 3, Z4)
    ("ja_dekimasen", r"(?:お手伝い|手伝う|生成|作成|作る|対応|提供|お応え|回答|お答え|協力|実行|応じる|書く|描く)(?:する)?(?:こと(?:は|が)?)?(?:でき|出来)ません"
                     r"|ご要望にお応えできません|お断り(?:し|いたし)ます|でき(?:かね|兼ね)ます|申し訳(?:ありません|ございません)が[^。]{0,40}できません"),
    ("ru_ne_mogu", r"\bя не (?:могу|буду|стану) (?:помочь|помогать|создать|написать|выполнить|сгенерировать|предоставить|продолжить|описать|сделать это|с этим|это)"
                   r"|не могу выполнить (?:этот|ваш|данный) запрос|противоречит моим (?:правилам|принципам)|вынужден(?:а)? отказать"
                   r"|как (?:ии|искусственный интеллект|языковая модель),? я не могу"),
]
# names whose sentence may state a LIMIT ("I can't provide real-time prices", "I'm unable to give medical advice",
# "ich kann dir nicht sagen, wer gewinnt") rather than refuse the request: judged by the sentence's object [Y2]
_LIMIT_NAMES = {"en_cant", "en_unable", "en_as_ai", "de_kann_nicht_v", "de_keine", "de_als_ki", "fr_peux_pas", "es_no_puedo", "it_non_posso"}
_LIMIT_OBJ = re.compile(
    r"real[- ]?time|live (?:data|weather|prices?|scores?|information|feed|updates?|news|rates?)|current (?:weather|prices?|news|events|data|rates?)"
    r"|up[- ]to[- ]date|latest|exact|precise|specific (?:numbers?|figures?|dates?|prices?)|definitive|certainty|for (?:sure|certain)"
    r"|(?:legal|medical|financial|professional|tax|investment|health) advice|diagnos\w*|personal opinions?|opinions?|predict\w*|the future"
    r"|lottery|guarantee|promise|verify|confirm|access (?:to )?(?:the )?(?:internet|web|live|files?)|browse|internet|the whole|all of (?:it|them)"
    r"|everything|in one (?:answer|go|message|reply|response)|right now|at the moment|today|tomorrow|yesterday|again|anymore|any ?more"
    r"|next time|remember|recall|see (?:your|the) (?:screen|files?|images?)|open (?:links?|files?|urls?)|run (?:the )?code|execute"
    r"|without (?:more|further) (?:information|details?|context)|whether|if the|who (?:will|wins)|how (?:much|many|long) (?:it|the|this|that)"
    r"|what the weather|the weather|the score|the price|the result|an? (?:exact|precise|reliable|definitive) answer"
    r"|sagen,? (?:ob|wer|wie|was|wann|wo|welche)|echtzeit|live-?daten|aktuelle[nrs]?|genaue[nrs]?|vorhersag\w*|zukunft|garantie\w*"
    r"|versprech\w*|rechtsberatung|medizinische|diagnose|meinung|heute|morgen|gestern|wieder|mehr|wetter|ergebnis|preis"
    r"|en temps réel|prévoir|prédire|en tiempo real|predecir|in tempo reale|prevedere", re.IGNORECASE)
_REFUSAL_OBJ = re.compile(
    r"explicit|sexual|sexually|porn\w*|nude|nudity|naked|erotic|adult (?:content|material)|(?:that|this|your|the|such an?) (?:request|kind|type|sort)"
    r"|(?:this|that|such) content|such material|harmful|illegal|inappropriate|unethical|offensive|hateful|violent|dangerous|against (?:my|the|our)"
    r"|guidelines?|polic(?:y|ies)|programm(?:ed|ing)|principles|ethic\w*|safety|violat\w*|prohibit\w*|comply|fulfil|generat(?:e|ing) (?:this|that|such)"
    r"|creat(?:e|ing) (?:this|that|such)|assist with (?:that|this)|help with (?:that|this)|anfrage|inhalte?|pornograf\w*|sexuell\w*|nackt\w*"
    r"|explizit\w*|richtlinien|unangemessen|schädlich|illegal|contenu|contenido|contenuto", re.IGNORECASE)
# after a refusal sentence, a continuation that speaks the refusal's vocabulary is still a refusal; one that
# does not (and repeats no refusal pattern) is the real answer — partial help [Y2]
_REFUSAL_VOCAB = re.compile(
    r"guideline|polic(?:y|ies)|programm|harmless|explicit|pornograph|sexual|nudity|inappropriate|declin|comply|ethic|safety|safe\b"
    r"|violat|prohibit|purpose|principle|respectful|material|request|content|assist(?:ance)? with|anything else|something else"
    r"|happy to help|other (?:request|topic|question)|let me know|feel free|boundar|restrict|unable|can't|cannot|won't|not able"
    r"|richtlinien|unangemessen|explizit|pornograf|sexuell|anfrage|inhalte|gerne (?:helfe|bei)|etwas anderes|stattdessen|regeln|grundsätze",
    re.IGNORECASE)
CONTINUATION_MIN = 60
# the STRONG patterns name the model's own rules / programming / role — they count a sentence or two in;
# en_explicit is NOT strong since round 2 (it must open the answer) [S1]
STRONG = {"en_programmed", "en_guidelines", "en_against", "en_as_ai", "en_prohibited", "en_decline",
          "de_programmiert", "de_als_ki", "de_richtlinien", "de_verstoesst", "de_ablehnen",
          "fr_en_tant", "fr_contraire", "fr_directives", "fr_refuser",
          "es_como_ia", "es_contra", "es_directrices", "es_rechazar",
          "it_come_ia", "it_contro", "it_linee", "it_rifiutare"}
RE_ANY = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _PATTERNS), re.IGNORECASE)

# what precedes a match that is NOT the model speaking: a quote, "say:", "e.g.", "message:" …
_QUOTED = re.compile(r"(?:[\"“„'‘«(\[]|:|\b(?:say|says|said|saying|like|e\.?g\.?|example|message|phrase|wrote|writes"
                     r"|replied|replies|answered|answers|responds?|responded|reads?|reading|text|sagt|sagte|schrieb|antwortete"
                     r"|dit|dice|disse|slogan|sign|caption|poster|banner|motto|tattoo|lettering|title|headline|quote|words"
                     r"|shirt|t-shirt|label|sticker|graffiti|neon|billboard|placard))\W*$", re.IGNORECASE)      # [Y2] quoted slogans
# "I can't X, but / so / however / instead …": a pivot after the refusal sentence introduces partial help [S1]
_PIVOT = re.compile(r"[,;:—–-]\s*(?:but|so|however|instead|yet|although|though|here(?:'s| is| are)|what I can|aber|jedoch"
                    r"|doch|stattdessen|hier ist|dafür|mais|pero|sin embargo|ma|però)\b|\.\s+(?:however|instead|but|here(?:'s| is| are)"
                    r"|what I can|jedoch|stattdessen|dafür|hier ist|allerdings)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?\n。]")
_INTERACTIVE = re.compile(r"<(?:button|input|form|textarea|select|canvas)\b", re.IGNORECASE)

KINDS = ("html", "text", "prompt")


def strip_thinking(text: str) -> str:
    """server.strip_thinking's rule, without importing the kernel: <think> blocks go, an unterminated
    opening tag at the start too"""
    t = THINK_RE.sub("", text or "")
    if t.lstrip().lower().startswith("<think>"):
        t = t.lstrip()[len("<think>"):]
    return t


def visible_text(html: str) -> str:
    """what a page shows: scripts/styles/comments and tags removed, entities decoded, whitespace
    collapsed"""
    t = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", html or "", flags=re.I | re.S)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return unescape(" ".join(t.split()))


def _lead_end(text: str) -> int:
    m = _LEAD.match(text)
    return m.end() if m else 0


def _quote_led(text: str, start: int, lead_end: int = 0) -> bool:
    """the sentence is quoted or introduced ("…say: I cannot …") — somebody else's words. A match that
    begins right after the lead-in ("Note: I cannot…", "**Request declined:** I can't…") is the model's [N3]"""
    if start <= lead_end:
        return False
    pre = text[max(0, start - 28):start]
    return bool(pre.strip()) and bool(_QUOTED.search(pre))


def _partial_help(text: str, m, raw: str = None) -> bool:
    """after the refusal sentence a pivot ("…, but here is …", "…, so this app simulates it:") introduces
    real content: PIVOT_CONTENT chars of it, or (html) the document itself — partial help, not a refusal [S1]"""
    end = _SENTENCE_END.search(text, m.end())
    stop = end.end() + 1 if end else len(text)
    window = text[m.end():min(len(text), stop + 160)]
    p = _PIVOT.search(window)
    if p:
        after = text[m.end() + p.end():].strip()
        if len(after) >= PIVOT_CONTENT:
            return True
        if raw and re.search(r"<(?:!doctype|html|body|div|main|section)\b", raw[m.end():], re.I):
            return True
    # [round 3, Y2] no pivot word, but the refusal sentence is followed by real content: CONTINUATION_MIN chars
    # that repeat no refusal pattern and speak none of the refusal's vocabulary ("I cannot provide legal advice.
    # In general, a landlord must give three months' notice." is an answer; "I cannot fulfill this request. I am
    # programmed to be a helpful and harmless assistant…" is not)
    rest = text[stop:].strip() if end else ""
    return len(rest) >= CONTINUATION_MIN and not RE_ANY.search(rest[:SCAN]) and not _REFUSAL_VOCAB.search(rest)


def _limit_statement(text: str, m) -> bool:
    """[Y2] the sentence states what the model cannot KNOW or DO ("I can't provide real-time prices", "I'm unable
    to give a diagnosis", "ich kann dir nicht sagen, wer gewinnt") and names no refused content — a limit
    followed by help, not a refusal"""
    end = _SENTENCE_END.search(text, m.end())
    sentence = text[m.start():end.start() if end else len(text)]
    return bool(_LIMIT_OBJ.search(sentence)) and not _REFUSAL_OBJ.search(sentence)


def _find(text: str, scan: int = SCAN, lead_end: int = 0):
    """the first refusal sentence in text[:scan] that is the model's own words — (match, name) or None"""
    for m in RE_ANY.finditer(text[:scan]):
        name = m.lastgroup
        if _quote_led(text, m.start(), lead_end):
            continue
        if name == "en_explicit" and (m.start() != lead_end or len(text) > 300):
            continue                 # "adult content is not allowed" only as the whole short answer, not "On Instagram, …" [Y2]
        if name in _LIMIT_NAMES and _limit_statement(text, m):
            continue
        return m, name
    return None


def _reason(name: str, m) -> str:
    return f"{name}: '{m.group(0)[:70]}'"


def _text(t: str, start_window: int = START_TEXT) -> tuple:
    t = t.strip()
    if not t:
        return False, ""
    lead = _lead_end(t)
    hit = _find(t, lead_end=lead)
    if not hit:
        return False, ""
    m, name = hit
    if _partial_help(t, m):
        return False, ""
    at_start = m.start() - lead <= start_window
    strong = name in STRONG
    if at_start and (len(t) <= LONG or strong):
        return True, _reason(name, m)
    if strong and len(t) <= SHORT:          # "…As an AI, I cannot…" a sentence or two in
        return True, _reason(name, m)
    return False, ""


def _prompt(t: str) -> tuple:
    """an expansion is one paragraph; a refusal anywhere in a short answer counts (the raw answer
    may carry a planning line first, clean_expansion keeps the last paragraph)"""
    t = t.strip()
    if not t or len(t) > 2000:
        return False, ""
    hit = _find(t, scan=2000, lead_end=_lead_end(t))
    if not hit:
        return False, ""
    m, name = hit
    if _partial_help(t, m):
        return False, ""
    return True, _reason(name, m)


def _html(t: str) -> tuple:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    visible = visible_text(t)
    if not visible:
        return False, ""                      # an empty page is validate_html's business, not a refusal
    if _INTERACTIVE.search(t):
        return False, ""                      # a refusal page has no buttons, inputs or forms — an app has [Z3]
    lead = _lead_end(visible)
    hit = _find(visible, lead_end=lead)
    if not hit:
        return False, ""
    m, name = hit
    if _partial_help(visible, m, raw=t):      # "I can't provide live data, so this app simulates it: <!DOCTYPE…" [S1]
        return False, ""
    tags = len(re.findall(r"<[a-zA-Z][^>]*>", t))
    at_start = m.start() - lead <= START_HTML
    little = len(visible) <= HTML_SHORT or tags <= HTML_TAG_POOR
    if at_start and little:
        return True, _reason(name, m) + f" (visible {len(visible)} chars, {tags} tags)"
    if name in STRONG and len(visible) <= HTML_SHORT and tags <= 12:
        return True, _reason(name, m) + f" (visible {len(visible)} chars, {tags} tags)"
    return False, ""


def is_refusal(text, kind: str = "text") -> tuple:
    """(True, reason) when `text` — a model's raw answer — declines the request instead of answering
    it; (False, "") otherwise. `kind`: html | text | prompt (see the module docstring)."""
    if not isinstance(text, str):
        return False, ""
    t = strip_thinking(text)
    if kind == "html":
        return _html(t)
    if kind == "prompt":
        return _prompt(t)
    return _text(t)


def refusal_snippet(text: str, n: int = 160) -> str:
    """the start of a refusal for the journal — visible text, one line"""
    return " ".join(visible_text(strip_thinking(text or "")).split())[:n]


# ---- the hard minors guard (integrator rule 2026-10-09; priority over the fallback itself) ----------
# The fallback must never help produce sexual content involving minors or childlike characters.
# minor_risk(text) flags a REQUEST that pairs (i) a sexual / nudity / erotic term with (ii) a minor or childlike
# indicator. Either alone passes: "a nude woman in her thirties", "camgirls.xxx", "girls' night out", "a family
# picnic with children", "teen fashion store" are fine — the pair is the trigger.
# Round 3 (REVIEW-R2.md X1/Y1/Y4): the text is NORMALISED first — NFKC, format/combining characters (zero-width,
# RLO, tag characters, combining underlines, variation selectors) stripped, Cyrillic/Greek look-alikes folded
# inside mixed-script tokens (tееn, lоli), letter-spaced words joined (t e e n, t.e.e.n), light leetspeak folded
# with a LINEAR tokeniser (t33n, l0li, s3xy, k1dz, 7eens), `-_+./\?&=()[]{}` turned into spaces so slugs, query
# strings and hyphenated ages read as words (porn.xxx/young-girls, ?age=12, (12), 12-year-old), "12 y.o." / "y/o"
# → "12 yo", "u18" → "under 18", "18+" → "18plus". Then THREE scopes:
#   1. a STRONG sexual term (nude, naked, sex, porn, hentai, erotic, fuck, rape, tits, xxx …, DE/FR/ES/IT/JA/RU
#      forms, glued slugs such as nudegirls / sexyteens) and a HARD indicator (child, kid, minor, underage, under
#      18, loli/shota, schoolgirl, pupil, high/middle school, Nth grade, ages under 18 as digits or words in six
#      languages, pedo, jailbait, Kind*, Schüler*, enfant, niña, bambina, 子供, 未成年, несовершеннолетн …) pair
#      within WINDOW characters of each other ANYWHERE in the request — never split at commas, conjunctions or
#      punctuation ("kids and porn", "nude photo; she is 12 years old", "a 12 year old girl and a man having sex"
#      all block — round 1 blocked them, round 2 did not);
#   2. a SOFT indicator (teen, adolescent, young/little/small/petite/lil … girl/boy) or a non-strong sexual word
#      (sexy, lingerie, seductive …) pairs within one CLAUSE (split at . ; line breaks and then/and-style
#      conjunctions — the "dropped the kids at school, then a sexy dinner date" note passes); a stated adult age
#      clears a SOFT indicator only when it stands NEXT TO that noun phrase ("petite girl, 25 years old", "teen
#      18+", "legal teen"), never when it belongs to someone else in the request ("a naked little girl with a 35
#      year old man" blocks);
#   3. WEAK terms (explicit, adult content, x-rated) pair only with UNAMBIGUOUS indicators (underage, minderjährig,
#      loli, pedo, jailbait, preteen, a stated age under 18) within a clause.
# Compounds (teenporn, lolihentai, nude_teens, freeteenporn.com, realkidsex.net, pthc, toddlercon) block outright.
# Exclusions (replaced before matching): sex education, girls' night, boy toy, kids-free, naked mole rat, naked
# eye, nude make-up, "talk to your kids about sex", parental controls / blocks porn, Sex Pistols, blue tits,
# Nacktschnecken, "no minors allowed" / age verification, "in A minor", the baby's sex …; crime-reporting nouns
# ("sexual abuse of minors") are not sexual terms in a news / law / child-safety context. Bare "girl"/"boy"/
# "young"/"student"/"baby" are not indicators (adult content uses them constantly). Real desk-log prompts this
# blocks: "cencored hentai picture of small titted girl and tentacle monster", "cencored hentai images of ninja
# raping schoolgirl on the school table", "cencored image: henai girl getting fucked in school by ninja".
# A non-string request (a list of content parts, a dict) is judged as its JSON text — never a pass (X3). The scan
# is linear and reads at most GUARD_MAX characters (no prompt budget lets a model see more). server.py runs it
# on every user request before the first LLM call (no rung runs — the request fails with BLOCK_MESSAGE, nothing
# is cached) and at every media entry point before expansion or rendering; cites_minors() backs it up on the
# model side.
BLOCK_MESSAGE = "kAIrOS does not create sexual content involving minors or childlike characters"
GUARD_MAX = 400_000          # characters the guard reads of one request (every prompt budget is far below)
WINDOW = 120                 # characters between a STRONG sexual term and a HARD indicator (about 20 tokens)

_STRIP_CATS = frozenset({"Cf", "Mn", "Me", "Cc", "Co", "Cs"})      # format, combining marks, control, private use
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­‎‏⁡⁢⁣⁤]")   # (kept for callers)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "7": "t"})
# linear: a token that starts with a letter (never an age such as "13yo"); folded only when it holds a digit/symbol
_LEET_TOKEN = re.compile(r"\b[a-zäöüß][a-zäöüß0-9@$]*\b")
_LEET_LEAD = re.compile(r"\b[013457][a-zäöüß]{3,}\b")               # 7eens, 5exy, 3rotic: one leet digit in front
_LATIN = re.compile(r"[a-z]")
_CYR_GREEK = re.compile(r"[Ͱ-ϿЀ-ӿԀ-ԯ]")
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "һ": "h",
    "ԁ": "d", "ɡ": "g", "ԝ": "w", "ё": "e", "ї": "i", "к": "k", "т": "t", "м": "m", "в": "b", "н": "h", "г": "r",
    "ԛ": "q", "ԁ": "d", "ѵ": "v", "ҽ": "e", "ӏ": "l", "α": "a", "ο": "o", "ε": "e", "ρ": "p", "τ": "t", "ν": "v",
    "ι": "i", "κ": "k", "η": "n", "υ": "u", "χ": "x", "ϲ": "c", "ϳ": "j", "ѡ": "w", "ԍ": "g"})
_SPACED = re.compile(r"(?<![a-z0-9])(?:[a-z] ){2,}[a-z](?![a-z0-9])")
_SEP = re.compile(r"[-_+./\\?&=()\[\]{}<>\"`~^*#%|]+")


def _as_text(value) -> str:
    """a non-string request (content parts, a dict, bytes) is judged as its text — never a pass (X3)"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "ignore")
    if isinstance(value, (list, tuple, dict)):
        try:
            import json
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _leet(m) -> str:
    """fold light leetspeak inside a token that has two letters or more and a digit / symbol (t33n, l0li, s3xy,
    k1dz) — never a one-letter code such as u18, r18, x1, k12"""
    tok = m.group(0)
    if sum(c.isalpha() for c in tok) >= 2 and any(c in "0134579@$" for c in tok):
        return tok.translate(_LEET)
    return tok


def _fold_token(m) -> str:
    tok = m.group(0)
    return tok.translate(_CONFUSABLES) if _LATIN.search(tok) else tok


def normalize(text) -> str:
    """the guard's view of a request (see the section comment): NFKC, invisible / combining characters gone,
    lower case, look-alikes folded in mixed-script tokens, letter-spaced words joined, light leetspeak folded
    (linear), separators as spaces, the age shorthands unified, whitespace collapsed"""
    t = unicodedata.normalize("NFKC", _as_text(text)[:GUARD_MAX])
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch) not in _STRIP_CATS).lower()
    if _CYR_GREEK.search(t):
        t = re.sub(r"\S+", _fold_token, t)
    t = re.sub(r"(\d{2})\s*\+", r"\1plus", t)                       # 18+ → 18plus (an adult marker, Z5)
    t = _SEP.sub(" ", t)
    t = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), t)       # t e e n → teen
    t = _LEET_LEAD.sub(lambda m: m.group(0).translate(_LEET), t)    # 7eens → teens
    t = re.sub(r"\bu ?(1[0-8])\b", r"under \1", t)                    # u18 → under 18 (before the leet fold)
    t = _LEET_TOKEN.sub(_leet, t)
    t = re.sub(r"\b(\d{1,2}) ?y ?o\b", r"\1 yo", t)                   # 12 y.o. / y/o / y o → 12 yo
    t = re.sub(r"\b(girls?|boys?|mädchen|junge|fille|niñ[ao]|ragazz[ao])\s*,\s*(\d{1,2})\b", r"\1 \2", t)
    return " ".join(t.split())


# phrases that pair the words without meaning sex with minors (education, idioms, animals, make-up, parenting,
# adult-only notices, the musical key) — replaced by " x " before matching
_EXCLUDE = re.compile(
    r"\b(?:sex(?:ual|uality)? ?ed(?:ucation)?|sexual health|sexual orientation|sexual(?:ly)? transmitted|sexualit[äa]t"
    r"|sexualkunde|aufklärung\w*|sexualerziehung|éducation sexuelle|educación sexual|educazione sessuale"
    r"|girls'? ?nights?(?: out)?|girl ?power|girl ?boss|boy ?toy|big boy|(?<!year )(?<!years )old boy|birthday boy|mama'?s boy|cowboys?"
    r"|playboy|tomboy|busboy|boyfriends?|girlfriends?|teen(?:age)? fashion|kids'? ?menu"
    r"|kids? free|child ?free|no kids|no children|adults? only|kinderfrei|ohne kinder|nur für erwachsene"
    r"|(?:in|key of|op\w* \d+ in) [a-g] (?:flat |sharp )?minor(?!'s)"
    # round 3 (Y4): animals, idioms, make-up, parenting, filters, bands, adult-only notices, biology
    r"|naked mole ?rats?|naked eye|naked truth|naked (?:city|lunch|gun|chef|ape)|nacktmull\w*|nacktschneck\w*|nacktbad\w*"
    r"|nude (?:make ?up|lipsticks?|lips|colou?rs?|shades?|tones?|heels|shoes|sandals|palette|nails?|polish|tights|pantyhose|beige|look|pink)"
    r"|talk(?:ing|s|ed)? (?:to|with) (?:your |my |our |the |their )?(?:kids?|children|child|teens?|teenagers?|son|daughter) about sex\w*"
    r"|erkläre?n? (?:ich )?(?:meinem|meiner|dem|den|unserem|unseren|meinen) kind\w*(?: über)? sex\w*|kind\w* (?:über )?sex\w* erklär\w*"
    r"|blocks? porn\w*|porn (?:filters?|blockers?)|parental controls?|kindersicherung|jugendschutz\w*"
    r"|sex pistols|sex and the city|sex (?:determination|chromosomes?|cells?|ratio|differences?|of the baby)|baby'?s sex|same sex|opposite sex"
    r"|blue tits?|great tits?|coal tits?|long tailed tits?|tits? \(?birds?|tit(?:mouse|mice)"
    r"|no minors(?: allowed)?|minors (?:are )?not allowed|keeps? minors out|not for minors|kein zutritt für minderjährige"
    r"|für minderjährige verboten|ab 18|age verification|age gate|age check|altersverifikation|altersprüfung|18plus only"
    r"|no ?one under 18|nobody under 18|niemand unter 18|not for (?:anyone |people )?under 18|under 18s? (?:are )?(?:not allowed|prohibited|banned)"
    r"|must be (?:over |at least )?18|18 (?:or|and) (?:over|older|up)|over 18s? only|adults? 18plus"
    r"|child safety|kinderschutz|safeguarding|kinderleicht\w*|kindergeld|kinderarzt\w*|kinderärzt\w*)\b", re.IGNORECASE)
# a clause ends at . ; a line break or a conjunction — NOT at a comma (image prompts are comma lists); only the
# SOFT / non-strong / WEAK pairings are clause-scoped (STRONG × HARD is a window over the whole request)
_CLAUSE_SPLIT = re.compile(r"[;.\n|]+|\s+(?:and|then|but|while|und|dann|aber|während|et|puis|mais|y|pero|e)\s+", re.IGNORECASE)
_WEAK = re.compile(r"(?<![^\W\d_])(?:explicit|explizit\w*|adult (?:content|material)|x rated|r18|mature content|erwachsenen ?inhalte?)"
                   r"(?![^\W\d_])", re.IGNORECASE)
# crime-reporting nouns ("sexual abuse of minors") are not a request for such content when the text reads like
# news / law / child safety (S3); the nouns stay sexual terms in every other context
_CRIME = re.compile(
    r"\b(?:sexual(?:ly)? (?:abuse[sd]?|assault\w*|harass\w*|exploit\w*|violence|misconduct|offen[cs]e\w*|predator\w*"
    r"|crimes?|abuser\w*)|child abuse|sexuell\w* (?:missbrauch\w*|gewalt|übergriff\w*|belästigung|ausbeutung)"
    r"|kindesmissbrauch\w*|abus sexuels?|abuso sexual|abuso sessuale)\b", re.IGNORECASE)
_NEWS_CTX = re.compile(
    r"\b(?:court|convict\w*|arrest\w*|charged|sentenc\w*|police|news|headlines?|report\w*|summari[sz]e|articles?|trial"
    r"|guilty|victims?|survivors?|prevention|awareness|hotline|helpline|law|lawsuit|case|allegation\w*|accused|investigat\w*"
    r"|verdict|jury|judge|prosecut\w*|scandal|child safety|safeguarding|protect\w*|counsel\w*|therap\w*|gericht|verurteil\w*"
    r"|polizei|nachrichten|schlagzeile\w*|bericht\w*|zusammenfass\w*|prozess|opfer|prävention|anzeige|angeklagt\w*|ermittl\w*"
    r"|kinderschutz|schutz)\b", re.IGNORECASE)

# STRONG sexual terms: pair with a HARD indicator over the window; with SOFT ones within a clause
_STRONG = re.compile(
    r"(?<![^\W\d_])(?:nudes?|nudity|nudist\w*|naked|sex|sexual\w*|sexting|porn\w*|pr0n|pron|hen[t]?ai|hentia|erotic\w*"
    r"|erotik|erotisch\w*|xxx|topless|tits|titties|titted|boobs?|nipples?|genitals?|genitalia|pussy|penis|vagina|vulva"
    r"|cum|cumming|cumshot|orgasm\w*|masturbat\w*|fuck\w*|blowjobs?|handjobs?|lewd|nsfw|fetish\w*|bondage|bdsm|undress\w*"
    r"|unclothed|stripping|striptease|sluts?|slutty|whores?|bare ?breasted|upskirt|cameltoe|intercourse|threesome|gangbang"
    r"|rule ?34|ecchi|ahegao|rape|raping|raped|rapist|molest\w*|incest\w*|deepthroat|anal|bukkake|creampie|dick pics?"
    # German
    r"|nackt\w*|sexuell\w*|pornos?|pornograf\w*|pornograph\w*|oben ohne|titten|busen|nippel|genitalien|muschi|fotze"
    r"|orgasmus|masturbier\w*|ge?fick\w*|unbekleidet\w*|fetisch\w*|geschlechtsverkehr|vergewaltig\w*|blasen|nacktfoto\w*"
    # French / Spanish / Italian
    r"|nue?s?|porno|sexe|sexuel\w*|érotique\w*|desnud[oa]s?|sexo|erótic[oa]s?|nud[oaei]|sesso|sessual\w*|erotic[oaei]"
    r"|violer|violada|stuprata"
    # Russian (Japanese has no word boundaries: _STRONG_JA below)
    r"|голы[йеха]\w*|голая|обнаж\w*|секс\w*|порно\w*|эрот\w*|трах\w*|изнасил\w*)(?![^\W\d_])",
    re.IGNORECASE)
_STRONG_JA = re.compile(r"裸|全裸|ヌード|エロ|ポルノ|セックス|性的|性交")
# a sexual word glued to a plural noun inside a slug: nudegirls, sexyteens (the second part may itself be SOFT/HARD)
_GLUED = re.compile(
    r"(?<![^\W\d_])(?:nude|naked|porn|sex|sexy|hentai|xxx|fuck|erotic)"
    r"(?:girls?|boys?|babes?|women|pics?|pix|photos?|videos?|vids?|tube|hub|cams?|chat|models?|world|land|zone|club|site|tv|live|stars?)"
    r"(?![^\W\d_])", re.IGNORECASE)
# non-strong sexual words: everyday / adult-only use, so clause scope only
_SEXUAL_SOFT = re.compile(
    r"(?<![^\W\d_])(?:sexy|sexiest|lingerie|seductive\w*|sensual\w*|lustful|horny|panties|thong|milfs?|dessous"
    r"|verführerisch\w*|lasziv\w*|anzüglich\w*|セクシー|сексуальн\w*)(?![^\W\d_])", re.IGNORECASE)

_AGE_WORDS_EN = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
_AGE_WORDS_DE = r"(?:eins?|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf|dreizehn|vierzehn|fünfzehn|sechzehn|siebzehn)"
_AGE_WORDS_FR = r"(?:un|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|quinze|seize|dix sept)"
_AGE_WORDS_ES = r"(?:uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciséis|dieciseis|diecisiete)"
_AGE_WORDS_IT = r"(?:uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci|undici|dodici|tredici|quattordici|quindici|sedici|diciassette)"
_AGE_WORDS = r"(?:" + "|".join((_AGE_WORDS_EN, _AGE_WORDS_DE, _AGE_WORDS_FR, _AGE_WORDS_ES, _AGE_WORDS_IT)) + r")"
_AGE_NUM = r"(?<!\d)(?:1[0-7]|[1-9])(?!\d)"           # 1–17; 18+ passes, "1080p"/"7 inch" need a unit anyway
_AGE_UNIT = r"[\s-]*(?:yo|y ?o|yrs?|years?[\s-]*old|year[\s-]*old|years? of age|years? young|yr old|jährig\w*|jahre alt\w*|jahre|ans|años|anni|歳|лет|года?)"
_NOT_MEASURE = (r"(?!\s*(?:k|mm|cm|px|%|pm|am|th|st|nd|rd|x|inch|inches|kg|lbs?|feet|ft|euros?|dollars?|minutes?|mins?|hours?|days?|weeks?"
                r"|months?|seconds?|km|miles?|megapixels?|mp|gb|mb|bit|pages?|items?|steps?|points?|people|players?|slides?))")
_ORDINAL = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|\d{1,2}(?:st|nd|rd|th))"
_GIRLBOY = r"(?:girls?|boys?|gals?|lads?|mädchen|mädels?|junge|jungs|fille|filles|niñ[ao]s?|ragazz[ao]|garçons?|chic[ao]s?)"
_SOFT_MOD = (r"(?:young|little|small|tiny|petite|lil|lil'|wee|childlike|child like|baby ?faced|innocent|small ?titted|small ?breasted"
             r"|flat ?chested|underdeveloped|jung\w*|klein\w*|zierlich\w*|petite?s?|jeunes?|pequeñ[ao]s?|piccol[aeio])")
_UNDER_18 = (r"(?:under|unter|below|younger than|less than|not yet|not even|moins de|menores? de|minori di|sotto i)\s+(?:the age of\s+)?1[0-8]"
             r"(?!\s*(?:minutes?|mins?|seconds?|hours?|days?|weeks?|months?|km|miles?|kg|lbs?|euros?|dollars?|%|percent|degrees?|steps?|items?|words?|lines?|pages?|k\b|mm|cm|px|inch))"
             r"(?:\s*(?:ans|años|anni|jahre|years?(?: old)?|s))?\b|unter 1[0-8]jährig\w*")

_HARD = re.compile(
    r"(?<![^\W\d_])(?:child(?:ren|'?s)?|childlike|child like|kids?|kidz|kiddies?|kiddos?|underaged?|under aged?"
    r"|minors?(?!\s*(?:key|scale|chord|detail|issue|change|bug|fix|role|league|repair|injur\w*|update|tweak|adjust\w*"
    r"|version|release|edit|point|note|thing|problem|glitch|setback|surgery|operation|arcana|premise|third|seventh|second"
    r"|triad|pentatonic|mode|variant|correction|planet|gods?|deity|prophets?|works?|poets?|offen[cs]es?|crimes?|infraction|part(?:y|ies)))"
    r"|pre ?teens?|tweens?|school ?girls?|school ?boys?|schoolkids?|school ?children|schoolchild|pupils?|lolis?|lolitas?|lolicon"
    r"|shotas?|shotacon|toddlers?|toddlercon|infants?|newborns?|preschool\w*|kindergart\w*|p(?:a|ae)?edo\w*|jailbait|prepubescent"
    r"|pubescent|budding breasts?|pthc"
    r"|(?:high|middle|elementary|primary|grade|junior high|secondary) ?school(?:ers?|s)?"
    r"(?!\s+(?:reunion|sweethearts?|diploma|graduates?|graduation|dropouts?|teachers?|principals?|coach|janitor|parking))"
    r"|[a-z]{1,12}schoolers?|" + _ORDINAL + r" ?grade(?:rs?)?\b"
    # "girl … in/at school", "school uniform": the schoolgirl trope written out (a real desk-log prompt) [round 2]
    r"|" + _GIRLBOY + r"(?:\s+[\wäöüß-]+){0,5}\s+(?:in|at|after|during|from)\s+(?:the\s+|her\s+|his\s+)?school(?!\s+of\b)"
    r"|school\s+uniforms?"
    # under 18 in six languages [round 3]
    r"|" + _UNDER_18 +
    # ages under 18, as digits or words
    r"|" + _AGE_NUM + _AGE_UNIT + r"(?![a-z])|" + _AGE_WORDS + r"[\s-]*(?:years?[\s-]*old|years? young|jährig\w*|jahre alt\w*|ans|años|anni)\b"
    r"|aged? (?:" + _AGE_NUM + r"|" + _AGE_WORDS + r")(?![\d.,-])"
    r"|" + _GIRLBOY + r",? (?:who is |who's |is |was |of |at |just turned |turned |aged |age )?" + _AGE_NUM + _NOT_MEASURE +
    r"|(?:she|he|who|sie|er|elle|il) (?:is|was|turned|ist|war|wird|a|est)(?: just)? " + _AGE_NUM + _NOT_MEASURE +
    # German
    # German "Kind": the plural / case forms and compounds, the bare singular only before German words — never the
    # English "what kind of …" (a round-1/2 over-match)
    r"|kinder\w*|kindes|kindern|kind(?=\s*[,;:!]|\s+(?:nackt\w*|sex\w*|porn\w*|beim|im|mit|von|das|ein|eine|und|ist|wird|zu|zum|zur|sexuell\w*|auf|in|am|hat|wurde))"
    r"|kindlich\w*|minderjährig\w*|schulmädchen|schuljunge\w*|[a-zäöüß]{0,14}schüler(?:in|innen)?[a-zäöüß]{0,6}|[a-zäöüß]{0,14}schulkind[a-zäöüß]{0,6}|säugling\w*"
    r"|kleinkind\w*|vorschul\w*|kinderporn\w*|jugendlich\w*|mädchen(?=\s+(?:von|im alter|mit)\s+" + _AGE_NUM + r")"
    # French / Spanish / Italian
    r"|enfants?|mineure?s?|[ée]coli[èe]re?s?|fillettes?|gamin(?:es?|s)?|garçonnets?|niñ[oa]s?|menor(?:es)?|colegial[ao]s?"
    r"|chiquit[ao]s?|bambin[oaie]|minorenn[ei]|scolar(?:etta|ette|o|i)|ragazzin[oaie]"
    # Russian (Japanese: _HARD_JA below)
    r"|ребенок|ребёнок|дети|детей|детск\w*|несовершеннолетн\w*|школьниц\w*|школьник\w*|подростк\w*|малолет\w*|лоли)(?![^\W\d_])",
    re.IGNORECASE)
_HARD_JA = re.compile(r"子供|子ども|こども|児童|少女|少年|幼女|幼児|未成年|ロリ|ショタ|小学生|中学生|高校生|女子高生|園児|1[0-8]歳未満|(?<!\d)(?:1[0-7]|[1-9])歳")
_SOFT = re.compile(
    r"(?<![^\W\d_])(?:teens?|teenie\w*|teenage\w*|teenager\w*|adolescent\w*|adolescente?s?|подросток|тинейджер\w*"
    r"|" + _SOFT_MOD + r"(?:\s+[\wäöüß-]+)?\s+(?:" + _GIRLBOY + r"|buben?))(?![^\W\d_])", re.IGNORECASE)
# one token that is both: teenporn, lolihentai, nudeteens, kidsex, freeteenporn, realkidsex, bestlolihentai …
_SEX_FORMS = (r"(?:porn(?:o|os|s|ography|ographic|ographie|ografie|ografisch\w*|hub|star|stars|tube)?|sex(?:o|e|ual|uality|y)?|hentai|nudes?|nudity|xxx"
              r"|fuck(?:ed|ing|s)?|erotic[as]?|nackt|fick(?:en|t)?|pussy|cams?)")
_MINOR_FORMS = r"(?:teen|teenie|loli|shota|child|kid|kidz|preteen|underage|schoolgirl|jailbait|p(?:a|ae)?edo|kinder|toddler)"
_COMPOUND = re.compile(
    r"(?:" + _MINOR_FORMS + r"s?" + _SEX_FORMS + r"(?![^\W\d_])"
    r"|(?<![^\W\d_])(?:porn|sex|sexy|hentai|nudes?|naked|xxx|fuck|erotic|nackt)" + _MINOR_FORMS + r"\w*"
    r"|(?<![^\W\d_])(?:pthc|toddlercon|babycon|cub ?porn\w*|lolicon|shotacon))", re.IGNORECASE)
_UNAMBIGUOUS = re.compile(
    r"(?<![^\W\d_])(?:underaged?|under aged?|minderjährig\w*|lolis?|lolitas?|lolicon|shotas?|shotacon|p(?:a|ae)?edo\w*|jailbait"
    r"|pre ?teens?|prepubescent|kinderporn\w*|child porn\w*|несовершеннолетн\w*|" + _UNDER_18 + r"|" + _AGE_NUM + _AGE_UNIT + r"(?![a-z])"
    r"|" + _AGE_WORDS + r"[\s-]*(?:years?[\s-]*old|jährig\w*|jahre alt\w*|ans|años|anni))(?![^\W\d_])", re.IGNORECASE)
_ADULT = (r"(?:(?<!\d)(?:1[89]|[2-9]\d)" + _AGE_UNIT + r"(?![a-z])|aged? (?:1[89]|[2-9]\d)(?![\d.,-])"
          r"|(?:eighteen|nineteen|twenty|thirty|forty|fifty|sixty|achtzehn|neunzehn|zwanzig|dreißig|vierzig|fünfzig)(?:[\s-]+(?:one|two|three"
          r"|four|five|six|seven|eight|nine))?[\s-]*(?:years?[\s-]*old|jährig\w*|jahre alt\w*|ans|años|anni)"
          r"|in (?:her|his|their) (?:twenties|thirties|forties|fifties|sixties)|volljährig\w*|college[\s-]age|18plus|over 18|18 or older"
          r"|18 and (?:over|older|up)|barely legal|of legal age|legal)")
_ADULT_AGE = re.compile(r"\b" + _ADULT + r"\b", re.IGNORECASE)
# the adult age clears a SOFT indicator only when it stands next to that noun phrase (Y: "petite girl, 25 years
# old", "25 year old petite girl", "teen 18+", "legal teen") — never "little girl with a 35 year old man"
_ADULT_BEFORE = re.compile(r"\b" + _ADULT + r"[\s,]*(?:\w+\s+)?$", re.IGNORECASE)
_ADULT_AFTER = re.compile(r"^[\s,]*(?:who is |who's |aged |age |of |at |is |she is |he is )?" + _ADULT + r"\b", re.IGNORECASE)


def _clauses(t: str) -> list:
    return [c.strip() for c in _CLAUSE_SPLIT.split(t) if c and c.strip()]


def _adult_adjacent(clause: str, m) -> bool:
    return bool(_ADULT_BEFORE.search(clause[max(0, m.start() - 48):m.start()])
                or _ADULT_AFTER.search(clause[m.end():m.end() + 48]))


def _prepare(text) -> str:
    t = _EXCLUDE.sub(" x ", normalize(text))
    if _NEWS_CTX.search(t):
        t = _CRIME.sub(" x ", t)                 # reporting about such crimes is not a request for them (S3)
    return t


def _near(t: str, window: int = WINDOW):
    """the first STRONG sexual term and HARD indicator within `window` characters of each other, any order,
    over the whole text (no clause scope) — a merge over the sorted match positions, linear in the text"""
    hards = sorted([(m.start(), m.end(), m.group(0)) for m in _HARD.finditer(t)]
                   + [(m.start(), m.end(), m.group(0)) for m in _HARD_JA.finditer(t)])
    if not hards:
        return None
    starts = [h[0] for h in hards]
    import bisect
    for finder in (_STRONG.finditer(t), _GLUED.finditer(t), _STRONG_JA.finditer(t)):
        for s in finder:
            i = bisect.bisect_left(starts, s.start())
            for h in hards[max(0, i - 1):i + 1]:
                gap = max(h[0] - s.end(), s.start() - h[1], 0)
                if gap <= window:
                    return s.group(0), h[2]
    return None


def minor_risk(text) -> tuple:
    """(True, reason) when `text` — a user's request, an image prompt, a page address, a list of content parts —
    pairs a sexual term with a minor/childlike indicator (see the section comment for the three scopes);
    (False, "") otherwise. Used BEFORE any model runs: a flagged request gets BLOCK_MESSAGE, never a fallback,
    never a rendering."""
    t = _prepare(text)
    if not t.strip():
        return False, ""
    c = _COMPOUND.search(t)
    if c:
        return True, f"'{c.group(0)}' (compound)"
    pair = _near(t)
    if pair:
        return True, f"'{pair[0]}' + '{pair[1]}'"
    for clause in _clauses(t):
        sex = _STRONG.search(clause) or _GLUED.search(clause) or _STRONG_JA.search(clause) or _SEXUAL_SOFT.search(clause)
        if sex:
            hard = _HARD.search(clause) or _HARD_JA.search(clause)
            if hard:
                return True, f"'{sex.group(0)}' + '{hard.group(0)}'"
            for soft in _SOFT.finditer(clause):
                if not _adult_adjacent(clause, soft):
                    return True, f"'{sex.group(0)}' + '{soft.group(0)}'"
            continue
        weak, strong = _WEAK.search(clause), _UNAMBIGUOUS.search(clause)
        if weak and strong:
            return True, f"'{weak.group(0)}' + '{strong.group(0)}'"
    return False, ""


# the safety net on the model side (round 2): a refusal that itself names minors is the strongest evidence the
# request was about minors — the ladder stops, no uncensored rung, no raw-prompt render. Round 3 (Z2): a generic
# "could be accessed by children" / "safe for teens" in a refusal of an ADULT request is not a citation.
_CITES = re.compile(
    r"\b(?:minors?|children|child|underage|under-age|kids?|teenagers?|teens?|pre-?teens?|young (?:people|persons?|girls?|boys?"
    r"|individuals?|children)|(?:appears?|looks?|seems?) (?:to be )?(?:young|underage|a minor|under 18|like a (?:child|minor))"
    r"|under (?:the age of )?18|age of consent|child(?:ren)?'s safety|csam|sexualiz(?:ation|ing) of (?:minors|children)"
    r"|(?<!\d)(?:1[0-7]|[1-9])[\s-]*(?:years?|yrs?)[\s-]*olds?"
    r"|minderjährig\w*|kinder|jugendlich\w*|mineurs?|enfants?|menores|niñ[oa]s|minorenn[ei]|bambin[oi]"
    r"|несовершеннолетн\w*|дет(?:и|ей|ск\w*)|ребенк\w*|ребёнк\w*|подростк\w*)\b|未成年|子供|子ども|児童|18歳未満", re.IGNORECASE)
_CITES_GENERIC = re.compile(
    r"\b(?:accessed by|accessible to|available to|safe for|exposed to|seen by|viewed by|protect(?:ing|s)?|harm(?:ful)? to"
    r"|appropriate for|suitable for|unsuitable for|zugänglich (?:für|sein)|geeignet für|für)\s+(?:\w+\s+){0,2}$", re.IGNORECASE)


def cites_minors(text) -> tuple:
    """(True, reason) when a REFUSAL text names minors / children / underage / "appears young" as the subject of
    the request — the caller treats it as a minor-risk block"""
    v = visible_text(strip_thinking(_as_text(text)))
    for m in _CITES.finditer(v):
        if _CITES_GENERIC.search(v[max(0, m.start() - 40):m.start()]):
            continue                                         # "could be accessed by children": about the audience
        return True, f"the refusal cites '{m.group(0)}'"
    return False, ""
