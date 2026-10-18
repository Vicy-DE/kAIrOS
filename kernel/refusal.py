"""[R86] Refusal detection and the narrow minors block (round 4, 2026-10-10).

kAIrOS is an 18+ system without content filters. Its default LLM used to refuse adult requests, and until
R86 the kernel shipped the refusal as the result: a cached "website" that read "I cannot fulfill this
request. I am programmed to be a helpful and harmless AI assistant. My safety guidelines prohibit me from
generating sexually explicit content or pornography." (the owner's test 2026-10-09, desk-test
cache/web/porn-xxx-…-387761c2.html), and a picture whose prompt was the expander's refusal text (job
1d491e7b6c58). server.llm_generate / llm_stream call is_refusal() on every user-facing generation and climb
the fallback ladder when it says yes; when every rung is used up the request is declined (never cached,
never rendered, never a 502).

is_refusal(text, kind) -> (bool, reason). The three kinds are judged asymmetrically (review round 3, §3):
  "prompt" — an image-prompt expansion. RECALL first: a description never says "I can't", so any refusal,
             apology or policy sentence anywhere in the (short) answer is a refusal. The one exception is a
             refusal-shaped phrase inside quotation marks that a sign/slogan/caption word introduces ("a
             sign that reads \"I can't help you\""): that is the picture's text, not the model's.
  "text"   — a prose answer, a spec, game code, a wallpaper scene. PRECISION first: a refusal is either an
             answer that is essentially only a refusal (a bare "I can't do that." / "Das kann ich leider
             nicht machen."), or a refusal opening followed by content-policy vocabulary (explicit, sexual,
             adult content, guidelines, policy, appropriate, harmful, comfortable …). A limitation followed
             by help is NOT a refusal: "I can't generate a QR code image directly, but …", "I can't do that
             calculation without the interest rate. What rate …", "I don't support that file format yet.
             Try PNG." — the sentence names a concrete object and no policy word follows.
  "html"   — a page or an app. A real page (structure AND substantial visible text) is never a refusal; a
             short, structure-poor document whose visible text is a refusal IS one, even with a button in it.

Ground truth: the real refusals of the refusal screen (results/r86-refusal-fallback/screen/, stock Gemma 4,
the stock Qwen3-VL encoder, Qwen2.5-Coder) and the owner's two. The phrases are first-person refusals WITH
their object ("I can't help WITH / fulfil / create / generate …", never a bare "I can't"); a match led by a
quote or by "say:" / "e.g." (a story's dialogue, an answer explaining refusals) does not count for text and
html; the idioms "I can't help thinking / but" are excluded; "guidelines / policies" count only as the
model's OWN ("my …"); "as an AI" / "I'm designed to" count only with a refusal verb. Languages: EN, DE (the
OS languages the owner uses) plus FR, ES, IT, JA, RU.

minor_block(text) is the ONE minors check of the kernel (the section at the end): a floor, not a filter.

Round 4b (2026-10-11, the independent review of round 4): the text rule's policy vocabulary is CONTENT-POLICY
forms only (my / our / content policy or guidelines, inappropriate / not appropriate, the explicit / sexual /
porn / nude words) — an everyday answer that mentions a privacy policy, an appropriate date, a restricted
folder or safety guidelines is not a refusal (S1); the plain refusals "that's not something I can help with",
"I'm not going to write that", "I won't be generating that", "Das mache ich nicht", "Ich werde keine …
erstellen", "Ich lehne … ab", "Leider darf ich keine …" are (S2); the expander's own check
is_expansion_refusal() adds any first-person "I can't …" outside a quoted sign text (R5); strip_thinking /
visible_text / the tag count are find()-based and linear on pathological model output (S10).
"""
import bisect
import re
import unicodedata
from html import unescape

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)     # the rule strip_thinking implements
# [round 4b, S10] the linear scanners' pieces: every opening / closing found once (finditer of a literal is linear),
# paired by bisect — the regexes THINK_RE, <(script|style)\b.*?</\1\s*>, <!--.*?--> and <[^>]+> are quadratic on model
# output such as 8 000 unclosed <think> tags (2.9 s), 8 000 unclosed <script> tags (1.3 s) or 30 000 "<a" (1.1 s)
_THINK_OPEN, _THINK_CLOSE = re.compile(r"<think>", re.IGNORECASE), re.compile(r"</think>", re.IGNORECASE)
_BLOCK_OPEN = re.compile(r"<(script|style)\b", re.IGNORECASE)
_BLOCK_CLOSE = {"script": re.compile(r"</script\s*>", re.IGNORECASE), "style": re.compile(r"</style\s*>", re.IGNORECASE)}
_COMMENT_OPEN, _COMMENT_CLOSE = re.compile(r"<!--"), re.compile(r"-->")

# how far into the text a refusal sentence is looked for, and how short a "short" answer is
SCAN = 700
SHORT = 700            # chars: a STRONG pattern a sentence or two in counts only in an answer this short
START_TEXT = 48        # chars after the lead-in within which the sentence must start (text)
START_HTML = 90        # the same for a page's visible text (a <title> may come first)
HTML_SHORT = 600       # visible chars: a refusal page has little to say
HTML_RICH = 12         # opening tags: more than this AND more than HTML_SHORT visible chars = a real page
PIVOT_CONTENT = 40     # chars of real content after "…, but / so / however" that make it partial help
CONTINUATION_MIN = 60  # chars of plain content after the refusal sentence that make it partial help
PROMPT_MAX = 2000      # an expansion is one paragraph; longer texts are not expansions

# lead-ins a refusal may open with before the phrase itself (markdown marks, apologies, "but", labels)
_LEAD = re.compile(
    r"^(?:[\s*#>_\-•·]+"
    r"|(?:i'?m\s+|i am\s+)?(?:really\s+|so\s+|very\s+|terribly\s+)?sorry[,.!]*\s*"
    r"|unfortunately[,]?\s*|i apologi[sz]e[,.]*\s*|my apologies[,.]*\s*|(?:oh|hmm|well|ok|okay)[,.]*\s*"
    r"|(?:error|note|warning|notice|important|request declined|declined|response|answer|reply|ai|assistant)[:.]?\s*"
    r"|sadly[,]?\s*|but\s+|however[,]?\s*"
    r"|es tut mir (?:sehr |wirklich )?leid[,.]*\s*|leider[,]?\s*|entschuldigung[,.]*\s*|tut mir leid[,.]*\s*|aber\s+|nein[,.!]*\s*"
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
# ([round 4b] NOT make / include / show / add / display: RE_ANY is kind "prompt" too, i.e. the MT check (Y5) — "I can't show
# the preview", "I can't add the file" are UI translations there; the expander's own check catches them, is_expansion_refusal)
# [round 4b, S2] "-ing" forms after "won't be / am not": "I won't be generating that", "I'm not writing that"
_EN_VERBS_ING = (r"(?:help|assist|fulfil|fulfill|comply|complet|creat|writ|generat|produc|provid|do|continu|proceed|describ|depict"
                 r"|engag|participat|draw|design|build|cod|compos|render|expand|rewrit|answer|respond|support|accommodat|honou?r"
                 r")ing(?!\s+(?:but|it|myself)\b)")
_EN_CANT = (r"(?:cannot|can'?t|can not|am unable to|'m unable to|am not able to|'m not able to|won'?t|will not"
            r"|must not|may not|do not|don'?t|refuse to|have to decline to|must decline to"
            r"|am not (?:allowed|permitted|going|willing) to|'m not (?:allowed|permitted|going|willing) to)")
# [round 4b, S2] en_inappropriate's pieces: the negated copula, and "something I can help with / I'm comfortable writing"
_EN_NOT_BE = r"(?:'s not| is not| isn'?t| would not be| wouldn'?t be| won'?t be| will not be| was not| wasn'?t)"
_EN_SOMETHING = (r"something (?:I|we)(?: can| could|'m able to| am able to|'m willing to| am willing to|'m going to| am going to"
                 r"|'d be willing to| would| will|'m allowed to| am allowed to) (?:help|do|create|write|assist|generate|produce|make"
                 r"|provide|support|describe|draw|continue|answer|engage)|something I(?:'m| am) (?:comfortable|willing|able|going to"
                 r"|allowed to|permitted to)(?:\s+\w+)?")
_DE_VERBS = (r"(?:helfen|weiterhelfen|erfüllen|erstellen|schreiben|generieren|nachkommen|entsprechen|unterstützen"
             r"|fortfahren|beschreiben|darstellen|tun|machen|anbieten|liefern|verfassen|umsetzen|erzeugen|bearbeiten"
             r"|ausführen|bauen|programmieren|produzieren|anfertigen|zeichnen|malen)")

# every pattern is a named group; the STRONG ones name the model's own rules/programming — they
# count a sentence or two in too, the others only at the start
_PATTERNS = [
    # ---- English
    # [round 4b, S2] "I" + a space OR an apostrophe: "I'm not going to / not allowed to / not willing to …" were dead
    # behind "\bI " (review R4); "I won't be generating that" (an -ing form after "be")
    ("en_cant", r"\bI(?:\s+|(?='))" + _EN_CANT + r"\s+(?:be able to\s+|be\s+)?(?:" + _EN_VERBS + r"|" + _EN_VERBS_ING + r")\b"),
    # [round 4b, S2] the whole answer is "Sorry, I can't." — nothing follows
    ("en_bare_cant", r"\bI (?:cannot|can'?t|won'?t|will not|must not|refuse)(?=[.!…]+\s*$)"),
    ("en_not_ing", r"\bI(?:'m| am) not " + _EN_VERBS_ING + r"\b"),
    ("en_unable", r"\bI'?m (?:unable|not able) to\s+" + _EN_VERBS + r"\b"),
    # only the NEGATED form: "that is not appropriate / isn't something I can help with" — never "yes, that is allowed" [S1]
    # [round 4b, S2] + "that's not something I can help with / I'm able to help with / I'm going to do" (the review's
    # "that's" forms); the negation stays mandatory
    # "it" only with the refusal-specific predicates: "No, it's not allowed." / "it's not appropriate" answer a question
    ("en_inappropriate", r"\b(?:(?:that|this)(?: request| content| kind of content| sort of thing)?" + _EN_NOT_BE
                         + r" (?:appropriate|allowed|permitted|possible for me|" + _EN_SOMETHING + r")"
                         + r"|it" + _EN_NOT_BE + r" (?:possible for me|" + _EN_SOMETHING + r"))\b"),
    # round 4 (review-r3 I2): "I'd rather not write that", "that's not something I'm comfortable writing"
    ("en_rather", r"\bI(?:'d| would) (?:rather not|prefer not to)(?:\s+\w+)?\b"),
    ("en_comfortable", r"\b(?:(?:that|this|it)(?:'s| is) not something I(?:'m| am) (?:comfortable|willing|able|going to)(?:\s+\w+)?"
                       r"|I(?:'m| am) not comfortable (?:with|doing|writing|creating|generating|producing|describing))\b"),
    # "I'm programmed/designed to …" only with a refusal object — not "designed to be a friendly assistant" [S1]
    ("en_programmed", r"\bI(?:'m| am) (?:programmed|designed|built|trained) to (?:avoid|refuse|decline|not\b"
                      r"|be (?:a )?(?:helpful|safe)(?:,| and) harmless)"),
    # the model's OWN guidelines: "my safety guidelines", "my policies", "my programming" — never "my rules for sourdough" [S1]
    ("en_guidelines", r"\bmy (?:(?:safety|content|usage|ethical|own) (?:guidelines|policies|programming|principles|rules)"
                      r"|guidelines|policies|programming)\b"),
    ("en_against", r"\b(?:against|violates?|goes against|conflicts? with) my (?:content |usage |safety |ethical )?"
                   r"(?:policy|policies|guidelines|programming|principles|rules|values|terms)\b"),
    # "violates ethical and safety guidelines" (the stock encoder's refusal, screen 2026-10-09) [round 4]
    ("en_violates", r"\b(?:violates?|goes against|conflicts? with) (?:the |our |these |its )?(?:ethical|safety|content|usage|community)"
                    r"(?: and (?:ethical|safety|content|usage))? (?:guidelines|policies|policy|principles|rules|standards)\b"),
    # "as an AI, I …" only with a refusal verb — never "as an AI, I don't have feelings" [S1]
    ("en_as_ai", r"\bas an AI(?: language model| assistant| model| system)?,? I(?:\s+|(?='))" + _EN_CANT + r"\s+(?:be able to\s+|be\s+)?(?:"
                 + _EN_VERBS + r"|" + _EN_VERBS_ING + r")\b"),
    ("en_decline", r"\bI (?:must|have to|need to|will|'ll|respectfully|politely) (?:respectfully |politely )?(?:decline|refuse)\b"),
    ("en_prohibited", r"\bI(?:'m| am) (?:prohibited|forbidden|restricted|not permitted|not allowed) (?:from|to)\b"),
    ("en_explicit", r"\b(?:sexually explicit|pornographic|explicit sexual|adult|explicit) (?:content|material)"
                    r"[^.!?\n]{0,40}?\b(?:is|are|goes|go) (?:not (?:allowed|permitted|something I)|against|prohibited|outside)\b"),
    # ---- German  (round 3, Y2: every "ich kann … nicht" needs a refusal VERB — "ich kann dir nicht sagen, wer
    # gewinnt" is information)
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
    # round 4 (review-r3 I2): "Das kann ich leider nicht machen.", "Ich werde dabei nicht helfen."
    ("de_das_kann", r"\b(?:das|dies|so etwas|sowas|solche inhalte) kann ich (?:dir |ihnen |euch )?(?:leider )?nicht\b"),
    ("de_werde", r"\bich werde (?:(?:dir|ihnen|euch|dabei|damit|hierbei|das|dies|leider)\s+)*nicht\s+" + _DE_VERBS + r"\b"),
    ("de_dabei", r"\b(?:dabei|damit|hierbei) kann ich (?:dir |ihnen |euch )?(?:leider )?nicht (?:helfen|weiterhelfen)\b"),
    ("de_ablehnen", r"\bich (?:muss|werde) (?:diese anfrage |das |dies |diese bitte )?(?:leider )?ablehnen\b"),
    # [round 4b, S2] the review's plain German forms: "Das mache ich nicht.", "Solche Inhalte erstelle ich nicht.", "Das werde /
    # darf ich nicht (schreiben).", "Ich werde keine expliziten Inhalte erstellen.", "Ich lehne diese Anfrage ab.", "Leider darf
    # ich keine pornografischen Texte verfassen." (inverted order), "Ich möchte das nicht schreiben.", "Darauf kann ich nicht
    # eingehen.", "Das ist nicht angemessen."
    ("de_das_mache", r"\b(?:das|dies|so etwas|sowas|solche (?:inhalte|texte|geschichten|bilder|anfragen|sachen)|solches|derartiges|diese anfrage) "
                     r"(?:mache|tue|schreibe|erstelle|beantworte|generiere|produziere|erzeuge|liefere|beschreibe|zeichne|male|übernehme|unterstütze|erfülle) ich "
                     r"(?:(?:dir|ihnen|euch|leider|hier|jetzt|definitiv|grundsätzlich|auf keinen fall)\s+)*nicht\b"),
    ("de_das_werde", r"\b(?:das|dies|so etwas|sowas|solche inhalte|diese anfrage) (?:werde|will|möchte|darf) ich (?:(?:dir|ihnen|euch|leider|hier|jetzt)\s+)*nicht\b"),
    ("de_werde_keine", r"\bich werde (?:(?:dir|ihnen|euch|dabei|damit|hierbei|leider|hier|jetzt)\s+)*keine[nrs]?(?:\s+[\wäöüß,-]+){0,5}?\s+" + _DE_VERBS + r"\b"),
    ("de_lehne", r"\bich lehne (?:(?:das|dies|es|diese anfrage|diese bitte|den auftrag|diesen auftrag|solche anfragen|hiermit|leider|höflich|entschieden)\s+)*ab\b"),
    ("de_darf_inv", r"\bdarf ich (?:(?:dir|ihnen|euch|leider|dabei|damit|hier|das|dies|so etwas)\s+)*(?:nicht|keine[nrs]?)(?:\s+[\wäöüß,-]+){0,5}?\s+" + _DE_VERBS + r"\b"),
    ("de_moechte", r"\bich (?:möchte|moechte|will) (?:(?:das|dies|so etwas|sowas|dir|ihnen|euch|dabei|damit|hier|leider|dazu)\s+)*nicht\s+" + _DE_VERBS + r"\b"),
    ("de_darauf", r"\bdarauf (?:kann|werde|möchte|will|darf) ich (?:leider )?nicht eingehen\b"),
    ("de_nicht_angemessen", r"\b(?:das|dies|so etwas|sowas|diese anfrage|dieser inhalt|solche inhalte) (?:ist|wäre|sind|wären) (?:leider |einfach |wirklich )?nicht angemessen\b"),
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
# [round 4b, S1] the CONTENT-POLICY forms — the only policy vocabulary that counts, in _POLICY, in _REFUSAL_VOCAB's veto
# and in _REFUSAL_OBJ: the model's OWN policy / guidelines ("my content policy", "our guidelines", "meine Richtlinien"),
# "content policy", "inappropriate / not appropriate", and the explicit / sexual / porn / nude words (EN, DE, FR, ES, IT,
# RU, JA). The bare words policy / guidelines / appropriate / safety / restricted / Richtlinien do NOT count any more: an
# everyday answer that opens with a limitation and mentions a privacy policy, an appropriate date, a restricted folder
# or the safety guidelines on a package is not a refusal (review R4: 10 of 14 such answers were judged refusals).
_POLICY_FORMS = (
    r"(?:my|our|its)\s+(?:(?:own|safety|content|usage|ethical|community)\s+)?(?:polic(?:y|ies)|guidelines|programming)"
    r"|content\s+(?:polic(?:y|ies)|guidelines)|(?:not|n't)\s+appropriate|inappropriate|unangemessen|nicht\s+angemessen"
    r"|meine[nrs]?\s+(?:(?:sicherheits|nutzungs|inhalts)-?)?richtlinien|inhaltsrichtlinien"
    r"|explicit\w*|sexual\w*|sexually|pornograph\w*|porn\b|porno\w*|nudity|nudes?\b|naked|erotic\w*|nsfw"
    r"|adult\s+(?:content|material|themes?|websites?|images?|stories|videos?|scenes?)"
    r"|explizit\w*|pornograf\w*|sexuell\w*|nackt\w*|erotisch\w*|erotik\w*|explícit\w*|explicite\w*|esplicit\w*|sessual\w*"
    r"|sexuel\w*|erótic\w*|érotique\w*|erotic[oaei]|mes\s+(?:directives|principes|règles)|mis\s+(?:directrices|políticas|normas)"
    r"|le mie\s+(?:linee guida|politiche|regole)|мо(?:и|им|их)\s+(?:правил\w*|принцип\w*|политик\w*)|ポリシー|ガイドライン")
_REFUSAL_OBJ = re.compile(
    r"(?:" + _POLICY_FORMS + r")|(?:that|this|your|the|such an?) (?:request|kind|type|sort)"
    r"|(?:this|that|such) content|such material|harmful|illegal|unethical|offensive|hateful|violent|dangerous|against (?:my|the|our)"
    r"|comply|fulfil|generat(?:e|ing) (?:this|that|such)|creat(?:e|ing) (?:this|that|such)|assist with (?:that|this)|help with (?:that|this)"
    r"|(?:diese|deine|ihre) anfrage|schädlich", re.IGNORECASE)
# after a refusal sentence, a continuation that speaks the refusal's vocabulary is still a refusal; one that
# does not (and repeats no refusal pattern) is the real answer — partial help [Y2]. [round 4b, S1] the vocabulary =
# the content-policy forms, the refusal verbs, the reference forms and the polite closers — no bare policy words
# (policy, guidelines, appropriate, safety, restricted, Richtlinien, rules); the model's self-descriptions stay ("I am
# programmed …", "my purpose is …", "ethical", "respectful", "boundaries": "…, so this is not a refusal" read them wrong)
_REFUSAL_VOCAB = re.compile(
    r"(?:" + _POLICY_FORMS + r")|harmless|declin|comply|prohibit|violat|refus|unable|can't|cannot|won't|not able"
    r"|programm|ethic|purpose|respectful|boundar|assist(?:ance)? with"
    r"|(?:this|that|such|the) (?:kind|type|sort) of (?:content|request|material)|such (?:content|material|requests?)"
    r"|(?:this|that) (?:content|material|request)|your request|anything else|something else|happy to help"
    r"|other (?:request|topic|question|queries)|different topics?|let me know|feel free|what I can do"
    r"|gerne (?:helfe|bei)|etwas anderes|stattdessen|andere[nrs]? (?:anfrage|frage|thema)|ablehn\w*|verweiger\w*|kann ich nicht|darf ich nicht",
    re.IGNORECASE)
# [round 4, C] content-policy vocabulary: a refusal opening followed by any of these (outside the matched phrase —
# inside it too for a VIOLATION pattern, round 4b S2: "Writing this would violate my content policy, so I won't")
# is a refusal whatever its object — "I can't create pornographic content", "I cannot write that, it would be
# inappropriate", "Ich kann keine expliziten Inhalte produzieren"
_POLICY = re.compile(r"\b(?:" + _POLICY_FORMS + r")", re.IGNORECASE)
# [round 4, C] the tail of a BARE refusal sentence: the verb's object is only a reference to the request
# ("… with that request", "… that", "… this kind of content", "… das", nothing at all)
_BARE_TAIL = re.compile(
    r"^[\s,]*(?:(?:with|to|on|in|for|bei|mit|dabei|damit|hierbei)\b\s*)?"
    r"(?:that|this|it|them|those|these|so|das|dies|es|so etwas|sowas|here|your request|the request|this request|that request|me(?: to \w+)?"
    r"|(?:your|this|that|the) (?:task|ask|question|one|content|material|story|prompt|scene|text|image|picture|website|site|page|webpage"
    r"|web page|document|app|game|code|requests?)|(?:content|material) of (?:that|this|such a|this particular) (?:kind|type|sort|nature)"
    r"|(?:that|this|such|the) (?:kind|type|sort) of (?:content|request|material|thing|task|stuff)|(?:an? |the )?\w+ like (?:that|this)"
    r"|ce contenu|cette demande|ce genre de (?:contenu|demande)|este contenido|esta solicitud|ese tipo de contenido|questo contenuto"
    r"|questa richiesta|questo tipo di contenuto|такой контент|такой запрос|этот запрос|это|такое"
    r"|(?:machen|tun|helfen|weiterhelfen|erstellen|schreiben|erfüllen|beantworten|umsetzen|generieren|produzieren|erzeugen|anbieten|liefern))?"
    r"(?: (?:in|into) (?:that|this|such an?|the same) (?:direction|way|manner|form|style))?"
    r"(?:,? (?:either|sorry|though|here|right now|at all|unfortunately|leider|hier|jetzt|i'?m afraid|für dich|for you|for anyone|as requested"
    r"|as written|as asked))*[.!?…\s]*$",
    re.IGNORECASE)
# [round 4b, S2] the patterns that state a VIOLATION of the model's own rules: the policy word sits INSIDE the matched
# phrase ("Writing this would violate my content policy, so I won't", "This goes against my guidelines, so I can't write
# it") — _judge searches _POLICY in the phrase too for these (and only these: "My guidelines say to greet you warmly"
# must stay an answer)
_OWN_RULES = re.compile(r"\b(?:my|our|meine[nrsm]?|mes|mis|le mie)\b", re.IGNORECASE)   # [re-review K3]
VIOLATION = {"en_against", "en_violates", "de_verstoesst", "fr_contraire", "es_contra", "it_contro"}
# the STRONG patterns name the model's own rules / programming / role — they count a sentence or two in
STRONG = {"en_programmed", "en_guidelines", "en_against", "en_violates", "en_as_ai", "en_prohibited", "en_decline",
          "de_programmiert", "de_als_ki", "de_richtlinien", "de_verstoesst", "de_ablehnen",
          "fr_en_tant", "fr_contraire", "fr_directives", "fr_refuser",
          "es_como_ia", "es_contra", "es_directrices", "es_rechazar",
          "it_come_ia", "it_contro", "it_linee", "it_rifiutare"}
RE_ANY = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _PATTERNS), re.IGNORECASE)

# what precedes a match that is NOT the model speaking (text / html): a quote, "say:", "e.g.", "message:" …
_QUOTED = re.compile(r"(?:[\"“„'‘«(\[]|:|\b(?:say|says|said|saying|like|e\.?g\.?|example|message|phrase|wrote|writes"
                     r"|replied|replies|answered|answers|responds?|responded|reads?|reading|text|sagt|sagte|schrieb|antwortete"
                     r"|dit|dice|disse|slogan|sign|caption|poster|banner|motto|tattoo|lettering|title|headline|quote|words"
                     r"|shirt|t-shirt|label|sticker|graffiti|neon|billboard|placard))\W*$", re.IGNORECASE)      # [Y2] quoted slogans
# [round 4] for a PROMPT the only exception: the phrase stands inside quotation marks that a sign word introduces
_SIGN_LEAD = re.compile(r"\b(?:reads?|reading|says?|saying|signs?|slogan|caption(?:ed)?|poster|banner|text|lettering|letters"
                        r"|shirt|t-shirt|tattoo|label|sticker|graffiti|neon|billboard|placard|title|headline|motto|words|written"
                        r"|inscription|quote|message|note|speech bubble|bubble|schild|aufschrift|spruch|slogan|text)\W*$", re.IGNORECASE)
_OPEN_Q, _CLOSE_Q = "\"“„'‘«", "\"”“'’»"
# "I can't X, but / so / however / instead …": a pivot after the refusal sentence introduces partial help [S1]
_PIVOT = re.compile(r"[,;:—–-]\s*(?:but|so|however|instead|yet|although|though|here(?:'s| is| are)|what I can|aber|jedoch"
                    r"|doch|stattdessen|hier ist|dafür|mais|pero|sin embargo|ma|però)\b|\.\s+(?:however|instead|but|here(?:'s| is| are)"
                    r"|what I can|jedoch|stattdessen|dafür|hier ist|allerdings)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?\n。]")
# [round 4, C] for a PROMPT: an apology or a policy sentence is a refusal too (descriptions never apologise)
_APOLOGY = re.compile(r"\b(?:I(?:'m| am) (?:(?:really|so|very|terribly) )?sorry|I apologi[sz]e|my apologies|es tut mir leid|entschuldigung"
                      r"|je suis désolée?|lo siento|mi dispiace)\b|申し訳(?:ありません|ございません)|извините|простите", re.IGNORECASE)
_POLICY_SENTENCE = re.compile(
    r"\b(?:(?:my|our|these|the|its|ethical|safety|content|community) (?:\w+ )?(?:guidelines|policies)"
    r"|(?:violates?|against|conflicts? with) (?:ethical|safety|content|my|our|the) (?:and \w+ )?(?:guidelines|policies|policy|principles|rules)"
    r"|not (?:appropriate|allowed|permitted|possible) (?:for me )?to (?:create|generate|provide|describe|depict|produce|write)"
    r"|cannot be (?:fulfilled|generated|created|provided|accommodated)|(?:this|that|your) request (?:cannot|can'?t|can not|will not|won'?t) be"
    r"|(?:I|we) (?:must|have to|will) (?:refrain|decline|refuse)|(?:sexually )?explicit (?:content|material|imagery|images?) (?:is|are) not"
    r"|(?:unable|not able) to (?:fulfil|fulfill|comply|generate|create|produce|assist)|inappropriate content"
    r"|meine richtlinien|verst(?:ö|oe)(?:ß|ss)t gegen|kann nicht erfüllt werden)\b", re.IGNORECASE)
# [R88 fix round 4, 2026-10-18] a refusal that OFFERS SOMETHING ELSE is a refusal. "I can't write sexually explicit content, even
# with fictional adult characters.\n\nWhat I can offer instead: - A romantic short story …" (Ornith-1.5-9B, the fast-models
# benchmark's A5) passed as "a limitation followed by help": the pivot ". What I can" made it partial help, the refusal reached
# the user and no ladder rung ran — on an 18+ OS a watered-down answer. An OFFER is a first-person proposal of something other
# than what was asked ("What I can offer instead", "Instead, I can", "I'd be happy to … instead", "If you'd like, I can …",
# "Here's a tamer / non-explicit version", "… fades to black"; DE "Stattdessen kann ich", "Was ich anbieten kann", "Ich kann
# dir aber … anbieten"). It turns partial help into a refusal ONLY when the refusal sentence itself refused content (_offered):
# the sentence speaks the content-policy vocabulary (_POLICY: "sexually explicit content"), or it is bare ("I can't write
# that.") and the offer is a watered-down version (_SOFT or _POLICY inside it: "a romantic story that fades to black", "a
# non-explicit version"). A limitation followed by real help keeps passing: "I can't browse the web, but here is what I know:
# …", "I can't do that from here. Instead, I can walk you through it: open Settings …" (bare, but nothing watered down).
OFFER_WINDOW = 400     # chars after the refusal sentence in which the offer is looked for
_I_CAN = r"I (?:can|could)(?!['’]t|\s+not\b)"
_NOT_OF = r"(?!\s+of\b)"                       # "… instead of …" is a preposition, no offer
_OFFER_I = (r"\bwhat I (?:can|could) (?:offer|do|write|suggest|provide|create|give you)\b"
            r"|\binstead,?\s+I(?:'d| would| can| could| will|'ll|'m happy| am happy)\b|\bI (?:can|could) instead\b"
            r"|\b" + _I_CAN + r" (?:offer|suggest)\b|\b" + _I_CAN + r"\b[^.!?\n]{0,120}?\binstead\b" + _NOT_OF
            + r"|\bI(?:'d| would)(?: be)? (?:happy|glad|more than happy) to\b[^.!?\n]{0,160}?\binstead\b" + _NOT_OF
            + r"|\bif you(?:'d| would)? (?:like|prefer|want)(?: to| me to)?,? " + _I_CAN + r"\b"
            r"|\b(?:alternatively|as an alternative),? " + _I_CAN + r"\b"
            r"|\bstattdessen (?:kann|könnte|würde|biete|schreibe|erstelle) ich\b"
            r"|\bich (?:kann|könnte|würde) (?:dir |ihnen |euch )?(?:aber |gerne |gern )?stattdessen\b"
            r"|\bwas ich (?:dir |ihnen |euch )?(?:stattdessen |aber |gerne |gern )?(?:anbieten|tun|machen|schreiben) kann\b"
            r"|\bich (?:kann|könnte) (?:dir|ihnen|euch)\b[^.!?\n]{0,120}?\banbieten\b|\bich biete (?:dir|ihnen|euch)\b"
            r"|\b(?:als alternative|alternativ) (?:kann|könnte|biete|würde) ich\b"
            r"|\bwenn du (?:magst|möchtest|willst|lust hast),? (?:kann|könnte) ich\b")
# a watered-down version: a softening word up to two words before a version noun ("a tamer version", "a romantic short
# story", "eine entschärfte Fassung"); the STRONG words alone also mark a prompt's sanitised rewrite
_SOFT_STRONG = (r"tamer|toned[\s-]down|watered[\s-]down|non[\s-]?explicit|less explicit|sfw|safe[\s-]for[\s-]work|pg(?:-13)?"
                r"|family[\s-]friendly|censored|saniti[sz]\w+|non[\s-]?sexual|non[\s-]?nude|clothed|fade[\s-]to[\s-]black"
                r"|entschärft\w*|zahmer\w*|jugendfrei\w*|nicht[\s-]explizit\w*|weniger explizit\w*")
_SOFT_MILD = (r"tasteful|romantic|sensual|suggestive|implied|closed[\s-]door|softer|romantisch\w*|sinnlich\w*|angedeutet\w*"
              r"|dezent\w*|harmloser\w*")
_VERSION = (r"versions?|takes?|alternatives?|variants?|rewrites?|story|stories|scenes?|descriptions?|prompts?|depictions?"
            r"|fassung\w*|variante\w*|geschichte\w*|szene\w*")
_SOFT_VERSION = (r"\b(?:" + _SOFT_STRONG + r"|" + _SOFT_MILD + r")(?:[\s,-]+[\w'-]+){0,2}?[\s-]+(?:" + _VERSION + r")\b")
_FADE = r"\bfad(?:es?|ed|ing) to black\b|\bfade-to-black\b|\bbehind closed doors\b|\boff[\s-](?:page|screen)\b"
_OFFER = re.compile(_OFFER_I + r"|\bhere (?:are|is) (?:some|a few|an?) (?:\w+ )?alternatives?\b"
                    r"|\b(?:how|what) about\b[^.!?\n]{0,80}?\binstead\b" + _NOT_OF
                    + r"|\b(?:here(?:'s| is| are)|hier (?:ist|sind))\b[^.!?\n:]{0,30}?" + _SOFT_VERSION
                    + r"|\b(?:" + _VERSION + r")\s+instead\b" + _NOT_OF + r"|" + _FADE, re.IGNORECASE)
_SOFT = re.compile(_SOFT_VERSION + r"|" + _FADE + r"|\bwithout (?:any |the )?(?:explicit|graphic)\b|\b(?:not|non|less)[\s-]graphic\b"
                   r"|\bnicht (?:grafisch|explizit)\w*|\bohne (?:explizite|grafische)\w*", re.IGNORECASE)
# for a PROMPT (recall first; an expansion never offers anything): the first-person offers and a sanitised rewrite — never
# "fades to black" (a video's last shot) or "alternatives" (an expander may list prompt variants)
_OFFER_PROMPT = re.compile(_OFFER_I + r"|\b(?:here(?:'s| is)|this is|hier ist)\b[^.!?\n:]{0,30}?\b(?:" + _SOFT_STRONG
                           + r")(?:[\s,-]+[\w'-]+){0,2}?[\s-]+(?:" + _VERSION + r")\b|\b(?:" + _VERSION + r")\s+instead\b" + _NOT_OF,
                           re.IGNORECASE)

KINDS = ("html", "text", "prompt")


def _cut_spans(t: str, opens, closes: dict, repl: str = "", eat_space: bool = False) -> str:
    """[round 4b, S10] the linear form of `re.sub(OPEN.*?CLOSE, repl)`: `opens` = [(start, end, name)] of every
    opening in order, `closes[name]` = sorted [(start, end)] of every closing of that name; a span runs from an
    opening to the first closing that starts at or after the opening's end (an opening inside a cut span is gone
    with it, an opening with no closing after it stays — exactly what the lazy regex does); `eat_space` also
    swallows the whitespace after the closing (THINK_RE's trailing \\s*)"""
    out, pos, n = [], 0, len(t)
    for s, e, name in opens:
        if s < pos:
            continue
        lst = closes[name]
        k = bisect.bisect_left(lst, (e, -1))
        if k >= len(lst):
            continue
        j = lst[k][1]
        if eat_space:
            while j < n and t[j].isspace():
                j += 1
        out.append(t[pos:s])
        out.append(repl)
        pos = j
    out.append(t[pos:])
    return "".join(out)


def strip_thinking(text: str) -> str:
    """server.strip_thinking's rule, without importing the kernel: <think> blocks go, an unterminated
    opening tag at the start too. [round 4b, S10] linear (find + bisect), the same result as THINK_RE.sub"""
    t = text or ""
    opens = [(m.start(), m.end(), "think") for m in _THINK_OPEN.finditer(t)]
    if opens:
        t = _cut_spans(t, opens, {"think": [(m.start(), m.end()) for m in _THINK_CLOSE.finditer(t)]}, eat_space=True)
    if t.lstrip().lower().startswith("<think>"):
        t = t.lstrip()[len("<think>"):]
    return t


def _strip_tags(t: str) -> str:
    """[round 4b, S10] `re.sub(r"<[^>]+>", " ", t)`, linear: a tag runs from a "<" to the first ">" after it with at
    least one character between ("<>" is no tag and the scan goes on at the next character)"""
    gts = [m.start() for m in re.finditer(">", t)]
    if not gts or "<" not in t:
        return t
    out, pos = [], 0
    for m in re.finditer("<", t):
        s = m.start()
        if s < pos:
            continue
        k = bisect.bisect_left(gts, s + 1)
        if k >= len(gts):
            break
        if gts[k] == s + 1:
            continue                              # "<>": no tag here, the scan goes on
        out.append(t[pos:s])
        out.append(" ")
        pos = gts[k] + 1
    out.append(t[pos:])
    return "".join(out)


def _count_tags(t: str) -> int:
    """[round 4b, S10] `len(re.findall(r"<[a-zA-Z][^>]*>", t))`, linear"""
    gts = [m.start() for m in re.finditer(">", t)]
    n, pos = 0, 0
    for m in re.finditer(r"<[a-zA-Z]", t):
        s = m.start()
        if s < pos:
            continue
        k = bisect.bisect_left(gts, s + 2)
        if k >= len(gts):
            break
        n += 1
        pos = gts[k] + 1
    return n


def visible_text(html: str) -> str:
    """what a page shows: scripts/styles/comments and tags removed, entities decoded, whitespace
    collapsed. [round 4b, S10] linear on any input (the regex form was quadratic on unclosed tags)"""
    t = html or ""
    opens = [(m.start(), m.end(), m.group(1).lower()) for m in _BLOCK_OPEN.finditer(t)]
    if opens:
        t = _cut_spans(t, opens, {k: [(m.start(), m.end()) for m in rx.finditer(t)] for k, rx in _BLOCK_CLOSE.items()}, " ")
    opens = [(m.start(), m.end(), "c") for m in _COMMENT_OPEN.finditer(t)]
    if opens:
        t = _cut_spans(t, opens, {"c": [(m.start(), m.end()) for m in _COMMENT_CLOSE.finditer(t)]}, " ")
    t = _strip_tags(t)
    return unescape(" ".join(t.split()))


def _as_text(value) -> str:
    """a non-string request (content parts, a dict, bytes) as text (X3)"""
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


def _sign_quoted(text: str, start: int, end: int) -> bool:
    """[round 4] the phrase is the text ON something in the picture: inside quotation marks that a sign /
    slogan / caption word introduces ('a sign that reads "I can\'t help you"')"""
    pre = text[max(0, start - 80):start]
    q = max(pre.rfind(c) for c in _OPEN_Q)
    if q < 0 or not _SIGN_LEAD.search(pre[:q][-60:]):
        return False
    return any(c in text[end:end + 160] for c in _CLOSE_Q)


def _sentence(text: str, m) -> tuple:
    """(the refusal sentence's tail after the matched phrase, the text after that sentence)"""
    end = _SENTENCE_END.search(text, m.end())
    stop = end.start() if end else len(text)
    return text[m.end():stop], (text[stop + 1:].strip() if end else "")


def _offered(text: str, m) -> bool:
    """[R88 fix round 4] the refusal sentence refused CONTENT and an offer of something else follows within OFFER_WINDOW
    chars: the sentence (with up to 200 chars before it) speaks the content-policy vocabulary, or it is bare and the offer
    is a watered-down version (see _OFFER)"""
    end = _SENTENCE_END.search(text, m.end())
    stop = end.start() if end else len(text)
    window = text[m.end():stop + OFFER_WINDOW]
    if not _OFFER.search(window):
        return False
    if _POLICY.search(text[max(0, m.start() - 200):stop]):
        return True
    return bool(_BARE_TAIL.match(text[m.end():stop])) and bool(_SOFT.search(window) or _POLICY.search(window))


def _partial_help(text: str, m, raw: str = None) -> bool:
    """after the refusal sentence a pivot ("…, but here is …", "…, so this app simulates it:") introduces
    real content: PIVOT_CONTENT chars of it, or (html) the document itself — partial help, not a refusal [S1].
    [R88 fix round 4] never when the content is an offer of something else after a content refusal (_offered)"""
    if _offered(text, m):
        return False
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


def _judge(t: str, start_window: int, raw: str = None) -> tuple:
    """[round 4, C] the text / html rule, precision first: a refusal sentence of the model's own at the start
    (a STRONG one a sentence or two in, in a short answer) that is not a limit statement and not followed by
    partial help — and then EITHER bare (its object only refers to the request) OR followed by content-policy
    vocabulary. "I don't support that file format yet. Try PNG." is neither."""
    t = t.strip()
    if not t:
        return False, ""
    lead = _lead_end(t)
    hit = _find(t, lead_end=lead)
    if not hit:
        return False, ""
    m, name = hit
    at_start = m.start() - lead <= start_window
    if not at_start and not (name in STRONG and len(t) <= SHORT):
        return False, ""
    if _partial_help(t, m, raw=raw):
        return False, ""
    tail, rest = _sentence(t, m)
    if _BARE_TAIL.match(tail):
        return True, _reason(name, m)
    inside = m.group(0) if name in VIOLATION and _OWN_RULES.search(m.group(0)) else ""   # [round 4b, S2; re-review K3]
    around = t[max(0, m.start() - 200):m.start()] + " " + inside + tail + " " + rest[:SCAN]
    if _POLICY.search(around):
        return True, _reason(name, m)
    return False, ""


def _text(t: str) -> tuple:
    return _judge(t, START_TEXT)


def _prompt(t: str) -> tuple:
    """[round 4, C] an expansion is one paragraph that never says "I can't": a refusal phrase anywhere (not a
    quoted sign text), an apology or a policy sentence is a refusal — recall first"""
    t = t.strip()
    if not t or len(t) > PROMPT_MAX:
        return False, ""
    for m in RE_ANY.finditer(t):
        if _sign_quoted(t, m.start(), m.end()):
            continue
        return True, _reason(m.lastgroup, m)
    m = _APOLOGY.search(t)
    if m and not _sign_quoted(t, m.start(), m.end()):
        return True, f"apology: '{m.group(0)[:70]}'"
    m = _POLICY_SENTENCE.search(t)
    if m and not _sign_quoted(t, m.start(), m.end()):
        return True, f"policy: '{m.group(0)[:70]}'"
    m = _OFFER_PROMPT.search(t)                    # [R88 fix round 4] "Here's a tamer version instead: …"
    if m and not _sign_quoted(t, m.start(), m.end()):
        return True, f"offer: '{m.group(0)[:70]}'"
    return False, ""


def _html(t: str) -> tuple:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    visible = visible_text(t)
    if not visible:
        return False, ""                      # an empty page is validate_html's business, not a refusal
    tags = _count_tags(t)                     # [round 4b, S10] linear
    if tags > HTML_RICH and len(visible) > HTML_SHORT:
        # [round 4] a real page — structure AND substantial text — is never a refusal; [R88 fix round 4] unless its visible
        # text OPENS with a content refusal that offers something else ("I can't create this website because it would
        # contain sexually explicit content … What I can do instead: …" with a list of tame suggestions)
        lead = _lead_end(visible)
        hit = _find(visible, lead_end=lead)
        if hit and hit[0].start() - lead <= START_HTML and _offered(visible, hit[0]):
            return True, _reason(hit[1], hit[0]) + f" + an offer (visible {len(visible)} chars, {tags} tags)"
        return False, ""
    hit, why = _judge(visible, START_HTML, raw=t)
    if not hit:
        return False, ""
    return True, why + f" (visible {len(visible)} chars, {tags} tags)"


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


_FIRST_PERSON_NO = re.compile(r"\bI(?:'m| am)?\s*(?:cannot|can'?t|can not|won'?t|will not|unable to|not able to|must not|refuse to"
                              r"|have to decline|must decline)\b(?!\s+(?:believe|wait|stress|imagine|help\s+(?:but|thinking|it|myself)"
                              r"|get over|stop thinking|tell you how|thank you))", re.IGNORECASE)


def is_expansion_refusal(text) -> tuple:
    """[review r4] the image-prompt EXPANDER's check: is_refusal(kind="prompt") plus ANY first-person refusal ("I can't add
    nudity to this image, but …") outside a quoted sign text — a description never says "I can't". The MT check (Y5)
    keeps kind "prompt": UI strings such as "I can't open the file" are translations there."""
    hit, why = is_refusal(text, "prompt")
    if hit or not isinstance(text, str):
        return hit, why
    t = strip_thinking(text).strip()
    if not t or len(t) > PROMPT_MAX:
        return False, ""
    for m in _FIRST_PERSON_NO.finditer(t):
        if not _sign_quoted(t, m.start(), m.end()):
            return True, f"first_person: '{m.group(0)[:70]}'"
    return False, ""


def refusal_snippet(text: str, n: int = 160) -> str:
    """the start of a refusal for the journal — visible text, one line"""
    return " ".join(visible_text(strip_thinking(text or "")).split())[:n]


# ---- the narrow minors block (round 4, 2026-10-10: the owner's redirect, integrator Option B) ----------------
# A floor, not a filter. Three rounds of a lexical minors GUARD (soft terms, clause splitting, obfuscation
# folding, adult-age adjacency, a model-side net) never converged: each round passed its own suite and a fresh
# probe set broke it, while it blocked ordinary parenting, education and art requests. The owner: "gemma
# shouldn't block the prompt; if it drops it → other llm or uncensored gemma". What stays is ONE narrow,
# high-precision block that fires only on unambiguous indicators:
#   1. an explicit under-18 AGE — a number 0–17 or its number word with an age unit ("15-year-old", "15 yo",
#      "15 y.o.", "aged 15", "age: 15", "15 Jahre alt", "15-jährige", "fifteen-year-old"; never an age DIFFERENCE
#      "10 years older" [R1]) — or an explicit under-18 WORD (underage, under 18 / eighteen, under the age of 18,
#      younger than / below / not yet 18, under 16 (12–17), under-18s, unter 18 / 16, jünger als 18, nicht
#      volljährig, minderjährig, preteen, prepubescent [S3]; [R88 fix] an under-18 DECADE: "early teens", "mid teens" /
#      "mid-teens", "early-teen", "early teenage years", "frühe(n) Teenagerjahre(n)", "im frühen Teenageralter", "Mitte /
#      Anfang der Teenagerjahre" — never "the early teens" / "the mid-teens" (an era, a temperature) or a unit after it,
#      and never the ambiguous ones that can mean 18–19: "teens", "in her teens", "late teens", "teen", "barely legal",
#      "Ende der Teenagerjahre") TOGETHER WITH a sexual term (porn, nude/naked/nackt,
#      sex and the German sex / nackt / erotik compounds — Sexvideo, Oralsex, Nacktvideo, Erotikfilm [R3] —, sexual,
#      hentai, nsfw, erotic, lewd, genitals, …) anywhere in the same text;
#   2. known illegal-content terms: csam, pthc, child porn(ography), kiddie porn, kinderporno(grafie) and jailbait
#      fire on their own; loli/lolicon/shota/shotacon only together with a sexual term.
# It never fires on: ages 18 and up (compound number words included: [R88 fix] "twenty-two-year-old", "forty five years
# old", "a hundred and two-year-old" are no "two-year-old" — a ones word right after a tens word or "hundred" is never
# read; German compounds are one word, "zweiundzwanzigjährige", and never matched); numbers that are measurements
# (16 GB, 15 cm, 1080p, 17 inch, 8 steps, 12:30, 2 girls — no age unit; "under 18 dollars / degrees / Euro / Grad / %"
# [R2]); "minor/minors" in any sense; negated disclaimers ("no minors", "nobody under 18", "never anyone who looks
# underage", "all characters 18+", "keine Minderjährigen") and age-gate disclaimers ("if you are under 18, leave",
# "visitors under 18 are not allowed", "für Minderjährige verboten" [S4]) — the kernel's own prompts contain such
# sentences and the expander may echo them; "child / kid / teen / girl / boy / schoolgirl" and every other soft
# indicator (they are not indicators here). Obfuscation (leet, homoglyphs, letter spacing, split sentences) is
# deliberately out of scope. Known, accepted cost: a text that pairs an explicit under-18
# age with a sexual term is blocked even when its intent is protective ("my 14-year-old found porn, how do I
# talk to him") — no context or intent rules rescue it; the message suggests rephrasing without the age.
# Normalisation: NFKC + casefold + zero-width / combining marks stripped, and the length is capped AFTER
# normalising (review round 3, Y1: NFKC expansion of 400 000 chars stalled the event loop for 10.5 s); the scan
# is linear and takes milliseconds on 50 KB. server.py runs it at exactly four points: every media render's
# entry (image, video, music — on the user's prompt before anything, the expander included), the final image /
# video prompt right before sd renders it, once on a refused text request before any rung, and at the entry
# when the primary LLM is uncensored (the manifest flag, or the llm_model setting). A hit: BLOCK_MESSAGE as a
# 400, no model call, nothing cached, nothing rendered. [R88 fix round 2] Where the caller KNOWS the request is adult
# (minor_block(text, adult=True): an image job's `adult` flag at the sd floor and at render_images' entry, a video
# job's), that flag counts as the sexual term — the under-18 age or word fires on its own, as in a picture edit.
BLOCK_MESSAGE = ("kAIrOS does not create sexual content involving minors (an under-18 age or term together with a "
                 "sexual term) — if this is about adults, rephrase it without the age")
RAW_MAX = 100_000            # chars of the raw request read before normalising (every prompt budget is far below)
BLOCK_MAX = 100_000          # chars kept AFTER normalising (NFKC can expand a character up to 18-fold)
_STRIP_CATS = frozenset({"Cf", "Mn", "Me", "Cc", "Co", "Cs"})      # format, combining marks, control, private use

_AGE_NUM = r"(?<![\d:/])(?<!\d[.,])(?:1[0-7]|[1-9])(?![\d:/%])(?![.,]\d)"
_AGE_UNIT = (r"(?:\s*-?\s*(?:years?|yrs?|jahre?|jahren)\s*-?\s*(?:old|alt(?:e[nrsm]?)?|of age|young)\b|\s*-?\s*(?:year|yr)\s*-?\s*olds?\b"
             r"|\s*(?:yo|y\.o\.|y\.o|y/o)\b|\s*-?\s*jährig\w*)")
# [R88 fix] a ones word is never the last part of a compound number: "twenty-two-year-old" (22) read "two-year-old"
# (fixed-width lookbehinds — the text is normalised, so one space or one hyphen / dash joins the parts); "ten" to
# "seventeen" form no compound ("twenty fifteen-year-olds" = 20 people aged 15 still fires)
_NOT_COMPOUND = "".join(f"(?<!{w}{sep})" for w in ("twenty", "thirty", "forty", "fourty", "fifty", "sixty", "seventy", "eighty",
                                                   "ninety", "hundred") for sep in ("-", " ", "‐", "–", "—")) \
    + "(?<!hundred and )"
_AGE_WORDS = (r"(?:" + _NOT_COMPOUND + r"(?:one|two|three|four|five|six|seven|eight|nine)"
              r"|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
              r"|eins?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwoelf|dreizehn|vierzehn|fünfzehn|fuenfzehn"
              r"|sechzehn|siebzehn)")
_UNDER_AGE = re.compile(
    r"\b(?:" + _AGE_NUM + _AGE_UNIT
    + r"|" + _AGE_WORDS + r"(?:\s*-?\s*(?:years?|yrs?|jahre?)\s*-?\s*(?:old|alt(?:e[nrsm]?)?|of age)\b|\s*-?\s*(?:year|yr)\s*-?\s*olds?\b|\s*-?\s*jährig\w*)"
    + r"|(?:aged?|ages|alter|im alter von|age of|at the age of)\s*[:=]?\s*" + _AGE_NUM + r"\b"
    + r"|(?:aged?|im alter von)\s+" + _AGE_WORDS + r"\b)")
_UNITS_EN = (r"cm|mm|m|km|kg|g|min|mins|minutes?|seconds?|sec|secs|hours?|hrs?|days?|weeks?|months?|percent|inch|inches|px|mb|gb|k"
             r"|dollars?|bucks|euros?|eur|usd|cents?|pounds?|lbs?|degrees?|celsius|fahrenheit|feet|foot|ft|mph|kph|kmh"
             r"|meters?|metres?|miles?|liters?|litres?|ml|kcal|calories")
_UNITS_DE = (r"cm|mm|m|km|kg|g|min|minuten|sekunden|sek|stunden|std|tagen?|wochen|monaten?|prozent|zoll|px|mb|gb|euro|eur|cent"
             r"|franken|chf|grad|celsius|uhr|meter|kilometer|liter|kalorien|kcal")
# [R2] a measurement after the number is not an age: a unit word, or %, €, $, £, ° (a decimal, a time, a fraction too)
_NOT_MEASURE_EN = r"(?![\d:/%])(?![.,]\d)(?!\s*(?:%|€|\$|£|°))(?!\s*(?:" + _UNITS_EN + r")\b)"
_NOT_MEASURE_DE = r"(?![\d:/%])(?![.,]\d)(?!\s*(?:%|€|\$|£|°))(?!\s*(?:" + _UNITS_DE + r")\b)"
# [round 4b, S3] "under 16" / "unter 16" (12–17; the review simulated 1–17 FP-free) and "below / younger than / not yet 18",
# "under-18s", "jünger als 18", "nicht volljährig". The 12–17 forms also skip the THING counts a small number is followed
# by ("under 15 photos", "unter 12 Bilder"); never person nouns ("under 16 players / models" is an age) and never for the
# 18-forms, whose exclusion stays the review's R2 (units and symbols only: "nude under 18 models" fires as in round 4)
_COUNTS_EN = (r"items|photos?|pictures?|pics|images?|words|lines|pages|steps|chapters|episodes|levels|points|rounds|songs|tracks|files"
              r"|outfits|poses|frames|fps|hz|ghz|mhz|x|times|options|categories|slides"
              r"|people|persons|guests|members|participants|attendees|visitors|couples|pairs|women|men|adults|ladies|gentlemen|characters"
              r"|dancers|crew|staff|employees|workers|customers|clients|viewers|subscribers|followers|fans|spectators|seats|tickets|spots|places")
_COUNTS_DE = (r"fotos?|bilder|wörter|zeilen|seiten|schritte|kapitel|folgen|punkte|songs|dateien|outfits|posen|mal|optionen|kategorien"
              r"|stück|teile|minuten|personen|leute|gäste|mitglieder|teilnehmer\w*|besucher\w*|paare?n?|frauen|männer|erwachsene\w*"
              r"|zuschauer\w*|kunden|mitarbeiter\w*|tänzer\w*|plätze|tickets|karten|follower")
_NOT_COUNT_EN = _NOT_MEASURE_EN + r"(?!\s*(?:" + _COUNTS_EN + r")\b)"
_NOT_COUNT_DE = _NOT_MEASURE_DE + r"(?!\s*(?:" + _COUNTS_DE + r")\b)"
# [R88 fix, the integrator's decision 2026-10-17] the unambiguous under-18 DECADES: early / mid teens (13–16) — never late
# teens, "in her teens" or "teen" (they can mean 18–19). The plural "teens" after "the" is an era or a number ("the early
# teens" = 2013–15, "temperatures in the mid-teens") and a unit after it a measurement — both never read; the forms with
# "teen-age" / "teenager" / "Teenagerjahre" / "Teenageralter" are always a person's age. German "früher" is skipped (it is
# also the adverb "formerly": "früher Teenager-Star")
_TEEN_EARLY = (r"(?<!\bthe )(?:early|mid)[\s-]?teens" + _NOT_MEASURE_EN
               + r"|(?:early|mid)[\s-]?teen(?:age[ds]?|agers?)?"
               + r"|fr(?:ü|ue)h(?:e|en|es|em)[\s-]+teen(?:ager|ie)[\s-]?(?:jahren?|alters?|zeit)"
               + r"|fr(?:ü|ue)h(?:e|en)[\s-]+(?:teens|teenager(?:in|innen|n)?|teenies?)(?![\w-])"
               + r"|(?:mitte|anfang)[\s-]+(?:(?:der|des|ihrer|seiner|ihres|seines|meiner|deiner|unserer|eurer)\s+)?"
               + r"(?:teen(?:ager|ie)[\s-]?(?:jahren?|alters?|zeit)|teens)")
_UNDER18_WORD = (r"(?:" + _TEEN_EARLY + r"|underage[ds]?|under[\s-]+age[ds]?|under[\s-]+(?:18|eighteen)" + _NOT_MEASURE_EN + r"s?"
                 + r"|under the age of (?:18|eighteen)|(?:younger than|below|not yet|not even) (?:18|eighteen)" + _NOT_MEASURE_EN
                 + r"|under[\s-]+1[2-7]" + _NOT_COUNT_EN + r"s?"
                 + r"|unter[\s-]+(?:18|achtzehn)" + _NOT_MEASURE_DE + r"|unter[\s-]+1[2-7]" + _NOT_COUNT_DE
                 + r"|j(?:ü|ue)nger als (?:18|achtzehn)" + _NOT_MEASURE_DE + r"|noch nicht (?:18|achtzehn)" + _NOT_MEASURE_DE
                 + r"|nicht volljährig\w*|minderjährig\w*|preteens?|pre[\s-]+teens?|prepubescent|vorpubertär\w*)")
_UNDER18 = re.compile(r"\b" + _UNDER18_WORD + r"\b")
# a negated disclaimer: a negation and only function words / audience words before the under-18 word ("nobody
# under 18", "never anyone who looks underage", "not for minors or anyone under 18", "keine Minderjährigen"; [round 4b,
# S5] the kernel's own limits line prompts.UNCENSORED_LIMITS and its echoes: "never write or depict sexual content
# involving minors or anyone who looks underage", "no explicit content involving anyone under 18")
_NEG_HEAD = (r"\b(?:no|nobody|no[\s-]one|none|never|not|without|non|niemand|niemals|nie|kein|keine|keinen|keiner|keinem|keines"
             r"|nicht|ohne|don'?t|do not|doesn'?t|does not|avoid\w*|exclud\w*"
             r"|neither|weder)")                                  # [R88 fix round 3] "neither … nor", "weder … noch"
# [R88 fix round 4] every word ONCE: "anybody" was listed twice (since 2026-10-15) and the duplicate alternative made the
# bridge exponential on a run of it ("nude, no " + 22 × "anybody " took 7.6 s in minor_block, 20 in adult_ages 3.1 s)
_NEG_WORDS = (
    r"anyone|anybody|someone|somebody|one|person|persons|people|a|an|the|for|of|is|are|was|were|who|that|or|and|looks?"
    r"|appears?|seems?|be|to|minors?|children|kids|teens?|them|him|her|she|he|they|it|with|involving|depict\w*|show\w*|für|jemand"
    r"|their|his|its|ihre[nrsm]?|seine[nrsm]?"                # [R88 fix] "nobody in their early teens", "niemand in seinen …"
    r"|writ\w*|sexual\w*|sexuell\w*|explicit|explizit\w*"
    r"|jemanden|eine[nrsm]?|ein|der|die|das|den|dem|wer|ist|sind|aussieht|aussehen|oder|und|mit|von|minderjährige[nrsm]?|kinder"
    r"|jugendliche[nrsm]?|personen|menschen|zutritt|zugang|eintritt|verkauf|abgabe|entry|access|admission|admittance|sale|sales"
    r"|content|inhalte?|place|room|service|dienst|any|single|in|image|picture|photo|scene")
# [R88 fix round 3, the integrator's decision 2026-10-17] the negation's scope carries across a COORDINATED LIST of under-18
# words — an expander's disclaimer "nobody is underage or under 18" exempted only "underage" and blocked the adult picture:
# "nobody under 18 or underage", "niemand ist minderjährig oder unter 18", "neither underage nor under 18", "weder
# minderjährig noch unter 18", "no one underage, under 18 or pre-teen", "underage and/or under 18". A join is "or", "and",
# "nor", ",", "/" (DE "oder", "und", "noch", ","), optionally followed by a closed list of words that start no statement
# of their own ("… or anyone who looks underage", "… oder wer unter 18 ist"). The scope ends at everything else: a clause
# break (". ; : ! ?" are no joins: "nobody is underage. she is under 18" reads the second), any other word ("…, she is
# under 18" reads), an under-18 AGE (a number is never negated, in a list neither: "nobody underage or 15 years old"
# reads the 15), and a list item a person noun follows within two words ("nobody under 18, underage girl": a positive
# noun phrase, in a comma-separated prompt a tag of its own; the FIRST item keeps the old rule: "no underage models"
# passes). A repeated negation ("no one underage, no one under 18") is two matches, as before.
_NEG_JOIN = (r"(?:\s*[,/]\s*(?:(?:or|and|nor|oder|und|noch)\s+)?"
             r"|\s+(?:or|and|nor|oder|und|noch)(?:\s*/\s*(?:or|and|oder|und))?\s+)")
_NEG_FILL = (r"(?:(?:anyone|anybody|someone|somebody|who|that|looks?|appears?|seems?|is|are|be|jemand|jemanden|wer|ist|sind"
             r"|aussieht|aussehen)\s+)*")
_NEG_LIST_PERSON = (r"girls?|boys?|kids?|child|children|teens?|teenagers?|models?|woman|women|man|men|lady|ladies|guys?"
                    r"|students?|schoolgirls?|schoolboys?|daughters?|sons?|sisters?|brothers?|babes?|cheerleaders?|gymnasts?"
                    r"|actress(?:es)?|actors?|dancers?|blondes?|brunettes?|redheads?|brides?|wife|wives|girlfriends?|boyfriends?"
                    r"|mädchen|jungen?|jungs|frau|frauen|mann|männer|kind|kinder|schülerin(?:nen)?|schüler|tochter|töchter"
                    r"|sohn|söhne|teenager\w*|tänzerin\w*|freundin\w*|schwester\w*")
_NEG_ITEM = (_NEG_JOIN + _NEG_FILL + _UNDER18_WORD
             + r"\b(?!(?:[\s-]+[\w']+)?[\s-]+(?:" + _NEG_LIST_PERSON + r")\b)")
# [R88 fix round 4] "neither … nor" / "weder … noch" lists of bridge words and under-18 words: "neither minors nor anyone
# under 18", "neither minors, nor anyone under 18", "neither underage nor minors nor under 18", "weder Kinder noch
# Jugendliche unter 18", "weder minderjährig noch Kinder noch unter 18". "nor" is a bridge word after any negation (it only
# ever continues one), also after a comma; German "noch" only after "weder" ("ist sie nicht noch minderjährig?" is no
# negation of "minderjährig"). After an under-18 word a "nor" / "noch" item may carry bridge words before its under-18
# word ("… nor minors nor under 18"); round 3's person-noun rule holds for it ("nobody under 18 nor underage girls" reads).
# The 25+ check's teen rewrite (_TEEN_NEGATED) keeps its bridge without "nor": "neither minors nor teen girls" in an adult
# picture prompt is still rewritten — a teen word never reaches the image model
_NEG_BRIDGE = r"(?:\s+(?:" + _NEG_WORDS + r"|nor\b)|\s*,\s*nor\b)"
_NOR_ITEM = (r"(?:\s*,\s*|\s+)(?:nor|noch)\b(?:\s+(?:" + _NEG_WORDS + r"|nor\b|noch\b))*\s+" + _UNDER18_WORD
             + r"\b(?!(?:[\s-]+[\w']+)?[\s-]+(?:" + _NEG_LIST_PERSON + r")\b)")
_NEGATED = re.compile(r"(?:" + _NEG_HEAD + _NEG_BRIDGE + r"*|\bweder(?:" + _NEG_BRIDGE + r"|(?:\s+|\s*,\s*)noch\b)*)\s+"
                      + _UNDER18_WORD + r"\b(?:" + _NEG_ITEM + r"|" + _NOR_ITEM + r")*", re.IGNORECASE)
# [round 4b, S4] an age-gate disclaimer (the integrator: an adult site's age gate is a normal 18+ request) — a CLOSED
# list of function / audience / venue words, like the negation bridge above, never an intent rule: the conditional
# "if you are under 18 …" / "wenn du unter 18 bist", and the prohibitive forms where a PROHIBITION follows the word
# ("visitors under 18 are not allowed", "underage users will be banned", "für Minderjährige verboten", "Minderjährigen
# ist der Zutritt zum Pornokino untersagt", "Under 18? Leave now."); "you must be 18 or older", "18+ only", "nur ab 18",
# "Zutritt erst ab 18" never fired (18 is not an under-18 age)
_GATE_BRIDGE = (r"(?:\s+(?:[\wäöüß-]*(?:kino|club|shop|seite|site|page|forum|store|laden|bar|party|show|film|video|portal|bereich|area"
                r"|section|content|inhalte?|galerie|gallery|chat|room|website|webseite|plattform|platform|dienst|service|angebot|material)"
                r"|ist|sind|der|die|das|den|dem|des|dieser|diese|dieses|diesem|diesen|zutritt|zugang|eintritt|zum|zur|zu|auf|in|im|von|vom"
                r"|für|an|hier|dort|this|that|the|to|on|of|at|into|from|here|there|access|entry|admission|admittance|entrance|visitors?"
                r"|users?|persons?|people|viewers?|members?|guests?|readers?|customers?|nutzer|besucher|personen|leser|mitglieder|gäste"
                r"|kunden|jeder|everyone|anyone|alle|all|strictly|absolutely|streng|strengstens|ausdrücklich|grundsätzlich|is|are|will"
                r"|would|shall|may|can|must|should|be|get|have|has|werden|wird|sein|haben|hat|dürfen|darf|müssen|muss|sollen|soll|können"
                r"|kann|bitte|please|now|jetzt|sofort|immediately|dann|then|you|du|sie|ihr|bist|seid|do|does|darfst|musst|sollst|kannst)[,;:]?)*")
# the prohibition itself: a closed list of PROHIBITION predicates — never a bare negation, which would read plain
# statements as age gates ("under 18 is not a problem", "minderjährig ist kein Problem" fire)
_GATE_NEG = (r"(?:(?:not|never|cannot|can'?t|don'?t|nicht|nie|niemals)\s+(?:be\s+|get\s+|sein\s+|werden\s+)?(?:allowed|permitted|admitted|welcome"
             r"|accepted|let in|enter|access|view|visit|use|watch|join|buy|order|register|sign up|erlaubt|gestattet|zugelassen|willkommen"
             r"|erwünscht|betreten|besuchen|nutzen|benutzen|ansehen|anschauen|aufrufen|kaufen|bestellen|teilnehmen|eintreten|hinein|rein)"
             r"|(?:no|kein|keine|keinen)\s+(?:entry|access|admission|admittance|entrance|zutritt|zugang|eintritt|einlass)"
             r"|verboten|untersagt|unzulässig|gesperrt|ausgeschlossen|banned|prohibited|forbidden|barred|excluded|denied|unwelcome"
             r"|leave (?:now|immediately|this|the|here)|leave(?=\s*(?:[.!,;]|$))|go away|verlass\w*|raus|close (?:this|the)|go back"
             r"|turn back|zurück)")
# [re-review 4b, K1] the conditional "if you are under 18" is a gate only with its PROHIBITION ("…, leave / do not enter /
# verlasse …"): on its own it exempted plain requests ("if anyone is underage, that's fine"); a prohibition followed by "?"
# is rhetorical ("… verboten? Schreib trotzdem …"), no gate
_GATED = re.compile(
    r"\b(?:(?:if|wenn|falls|sofern|unless)\s+(?:you|u|du|sie|ihr|someone|anyone|anybody|jemand|a visitor|a user|the visitor|the user"
    r"|der besucher|der nutzer|visitors|users)(?:\s*(?:are|'re|is|bist|sind|seid|ist))?\s+)?" + _UNDER18_WORD
    + r"(?:\s+(?:bist|sind|seid|ist))?[,;:?!.]?" + _GATE_BRIDGE + r"\s+" + _GATE_NEG + r"\b(?!\s*\?)", re.IGNORECASE)
_SEX = re.compile(
    r"\b(?:porn\w*|pr0n|nudes?|nudity|naked|nackt(?:e[nrsm]?|heit|foto\w*|bild\w*|aufnahme\w*|szene\w*)?|sex|sexy|sexual\w*|sexuell\w*"
    r"|hentai|nsfw|erotic\w*|erotik|erotisch\w*|lewd|genitals?|genitalia|genitalien|penis|vagina|vulva|pussy|tits|titten|boobs"
    r"|topless|oben ohne|intercourse|geschlechtsverkehr|fuck(?:ed|ing|s)?|fick(?:en|t|st|te|ten|erei|er)?|blowjobs?|handjobs?"
    r"|masturbat\w*|masturbier\w*|orgasm\w*|cumshots?|bdsm|bondage|fetish\w*|fetisch\w*|xxx|lingerie|dessous|striptease|undress\w*"
    r"|unclothed|bare[\s-]*breasted|rape|raping|raped|vergewaltig\w*|molest\w*|incest\w*|inzest\w*|deepthroat|gangbang|threesome"
    r"|creampie|bukkake|(?:oral|anal|gruppen|telefon|cyber|hardcore)sex\w*|sex(?:video|film|bild|foto|szene|geschichte|spielzeug|chat"
    r"|cam|party|puppe|treffen|seite|portal|shop|kino|club|tape)\w*|nackt(?:video|film|selfie)\w*"
    r"|erotik(?:film|video|bild|foto|geschichte|seite|portal|shop|kino|club|massage)\w*|sexting|sexted)\b")
_ILLEGAL_ALONE = re.compile(r"\b(?:csam|pthc|child[\s-]*porn\w*|kiddie[\s-]*porn\w*|kinderporno\w*|kinderpornogra\w*|jail[\s-]?bait)\b")
_ILLEGAL_WITH_SEX = re.compile(r"\b(?:lolis?|lolicon|shotas?|shotacon)\b")


def _norm(text) -> str:
    """the block's view of a request: NFKC + casefold, format / combining / control characters gone,
    whitespace collapsed — read from at most RAW_MAX raw chars, kept to BLOCK_MAX after normalising"""
    t = _as_text(text)[:RAW_MAX]
    if not t:
        return ""
    t = unicodedata.normalize("NFKC", t).casefold()[:BLOCK_MAX + 1024]
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch) not in _STRIP_CATS)
    return " ".join(t[:BLOCK_MAX].split())


def minor_block(text, adult: bool = False) -> tuple:
    """(True, reason) when `text` — a user's request, an image / video prompt, a page address, a list of
    content parts — pairs an explicit under-18 age or word with a sexual term, or names illegal content (see
    the section comment); (False, "") otherwise. Pure, linear, milliseconds; no model call.
    [R88 fix round 2, the integrator's decision 2026-10-17] `adult`: the caller knows the request is adult (an image
    job's `adult` flag — the request's {"adult"} / {"uncensored"} flag or a sexual term in the request itself). The
    flag then counts as the sexual term: the under-18 age or word fires on its own ("explicit photo, a woman in her
    early teens", "… a 15-year-old …"), and loli / shota too; negated disclaimers, age gates, measurements and counts
    stay exempt exactly as above. Accepted cost: "a 12-year-old scotch on the bar" in an ADULT picture is blocked."""
    t = _norm(text)
    if not t:
        return False, ""
    m = _ILLEGAL_ALONE.search(t)
    if m:
        return True, f"'{m.group(0)}'"
    sex = _SEX.search(t)
    if not sex and not adult:
        return False, ""
    term = f"'{sex.group(0)}'" if sex else "the adult flag"
    m = _ILLEGAL_WITH_SEX.search(t)
    if m:
        return True, f"'{m.group(0)}' + {term}"
    tn = _GATED.sub(" ", _NEGATED.sub(" ", t))    # negated disclaimers and age gates are not indicators [S4]
    m = _UNDER_AGE.search(tn) or _UNDER18.search(tn)
    if m:
        return True, f"age '{m.group(0).strip()}' + {term}"
    return False, ""


def minor_age(text) -> tuple:
    """[review v7.5-2] minor_block for a PICTURE EDIT (✨ improve a picture): the edit's words are only half of the
    request — the picture is the other half, which no text check can read. An explicit under-18 age or word fires on
    its own here (negated disclaimers and age gates stay exempt, exactly as in minor_block); everything minor_block
    fires on fires too."""
    hit, why = minor_block(text)
    if hit:
        return hit, why
    t = _norm(text)
    if not t:
        return False, ""
    tn = _GATED.sub(" ", _NEGATED.sub(" ", t))
    m = _UNDER_AGE.search(tn) or _UNDER18.search(tn)
    return (True, f"age '{m.group(0).strip()}' (a picture edit)") if m else (False, "")


# ============================================================================= [R88] the 25+ check
# The prompt writers' adult rule (prompts.ADULT_LIMITS, the expander's rule 8) asks for every person "aged 25 or older —
# say so explicitly"; in one run a picture description still said "in their 20s". adult_ages() is the cheap,
# deterministic post-check on the FINAL image prompt of an adult request (server: MM.sd_batch, right AFTER the minors
# block): a stated age from 18 to 24 ("a 22-year-old", "aged 19", "21 years old", "twenty-two-year-old", "23yo") becomes
# 30, a decade that reaches below 25 ("in her twenties", "in their early 20s", "in his mid-twenties", "in her late
# teens") becomes "in her thirties", a bare one ("early twenties", "mid-20s", "late teens") "aged 30", and
# "twenty-something" "thirty-something". Never touched: an age of 25 or more ("in her late twenties" = 27–29 stays), a
# number that is no person's age (a 21-year-old whisky, a cask "aged 20 years in oak", the 1920s, "the early twenties",
# "early-20s fashion", 20 candles, "18+"), a negated phrase ("no longer in her twenties"), a prompt that is not adult
# (the caller decides: the request's adult flag, or a sexual term — sexual() — in the request or the final prompt), and
# every age UNDER 18: those are the minors block's, which runs first on the same text — a text it refuses comes back
# unchanged, and no rewrite here removes a token it reads (its numbers stop at 17, its words at "seventeen"; [R88 fix]
# its under-18 decades "early teens" / "mid-teens" / "early teenage years" are never aged up, with or without a sexual
# term — only the ranges that reach 18–19 are: "in her teens", "late teens", "mid- to late teens").
# [R88 fix round 2, the integrator's decision 2026-10-17] the person words "teen", "teens", "teenage(d)", "teen-age",
# "teenager(s)" and "barely legal" become adult wording, consistent with the rewrite to 30: "teen girl" -> "adult woman
# aged 30" (girl(s) -> woman / women, boy(s) -> man / men; another person noun is kept: "teenage couple" -> "adult couple
# aged 30"; one word may stand between: "teen blonde girl" -> "adult blonde woman aged 30"), the noun "a teen" -> "an adult
# aged 30" ("teens" -> "adults aged 30") where the noun phrase ends (punctuation, a preposition, a verb), "barely legal"
# alone -> "adult, aged 30", and every other use -> "adult(s)" ("teen porn" -> "adult porn", "teenage" -> "adult"); "aged
# 30" is left out where an age follows already. Never: a title ([R88 fix round 3] only a capitalised "Teen" / "Teenage"
# that starts one of the few real titles of _TEEN_TITLES, with no person noun within two words: "Teen Titans", "Teen
# Vogue", "Teenage Mutant Ninja Turtles"; a lowercase teen word is never a title — "nude teen Latina" -> "nude adult
# Latina", "teen Anna" -> "adult Anna" — and an unknown title is rewritten: cosmetic in an adult prompt, while a teen word
# reaching the image model is not), a negated phrase ("no teens",
# "nobody who looks like a teen" — the minors block's negation bridge + "like"), "pre-teen" / "early teens" / "mid-teens" /
# "late teens" (the block's, or the decade rule's), a possessive decade the decade rule left ("no longer in her teens"),
# and every text the block refuses: adult_ages() reads it with the adult flag (an under-18 age or word ALONE refuses it),
# so a text the floor blocks always comes back unchanged. English only (the final prompt is English: the expander writes
# it, the MT hop translates it); a bare number ("she is 22") is not read — too ambiguous to rewrite blind.
ADULT_AGE_MIN = 25            # the system's floor for a person in adult content (prompts.ADULT_LIMITS)
ADULT_AGE_TO = 30             # what a younger stated age becomes
_YOUNG_NUM = r"(?:1[89]|2[0-4])"
_YOUNG_WORD = r"(?:eighteen|nineteen|twenty(?:[\s-]?(?:one|two|three|four))?)"
_AGE_PRON = r"(?:his|her|their|my|your|our)"
_UNIT_YO = r"(?:\s*-\s*|\s+)(?:years?|yrs?)(?:\s*-\s*|\s+)olds?\b"
# "an age range": aged 20-35, ages 18 to 22, 19–23 years old (the number pair is read only with "aged" or "years old")
_AGE_RANGE = re.compile(r"(?<![\w.,])(?P<pre>(?:aged?|ages)\s+)?(?P<a>\d{2})\s*(?:-|–|to)\s*(?P<b>\d{2})(?!\w|[.,]\d)"
                        r"(?P<post>" + _UNIT_YO + r")?", re.IGNORECASE)
_AGE_YO = re.compile(r"(?<![\w.,-])(?P<n>" + _YOUNG_NUM + "|" + _YOUNG_WORD + r")(?P<rest>" + _UNIT_YO + r")"
                     r"|(?<![\w.,-])(?P<n2>" + _YOUNG_NUM + r")(?P<rest2>\s*(?:yo|y\.o\.?|y/o))(?![\w])", re.IGNORECASE)
_AGE_AGED = re.compile(r"\b(?P<pre>aged?|ages)(?P<sep>\s*[:=]?\s*)(?P<n>\d{2}|" + _YOUNG_WORD + r")(?!\w|[.,]\d)"
                       r"(?!\s*(?:-|–|to)\s*\d)(?P<more>(?:\s*(?:,|and|or|&)\s*\d{2}(?!\w|[.,]\d))*)", re.IGNORECASE)
_AGE_SOMETHING = re.compile(r"\b(?:20|twenty)[\s-]?(?P<s>somethings?)\b", re.IGNORECASE)
_DECADE_PRON = re.compile(r"\bin\s+(?P<p>" + _AGE_PRON + r")\s+(?:(?P<q>early|mid|late)[\s-]*(?:to[\s-]+(?P<q2>mid|late)[\s-]*)?)?"
                          r"(?P<d>20s|20's|'20s|twenties|teens|teenage years|teen years)\b", re.IGNORECASE)
# [R88 fix] bare teens: only the late ones (17–19, a range that reaches 18–19); early / mid teens are the minors block's
_DECADE_BARE = re.compile(r"(?<![\w'])(?P<q>early|mid)[\s-]*(?:to[\s-]+(?:mid|late)[\s-]*)?(?:20s|20's|twenties)\b"
                          r"|(?<![\w'])(?:(?:early|mid)[\s-]*to[\s-]+)?late[\s-]*teens\b", re.IGNORECASE)
_AGE_NEGATED = re.compile(r"\b(?:never|not|no longer|no more)\s+(?:(?:a|an|the)\s+)?[\"'“‘„]?$", re.IGNORECASE)
_AGE_ERA = re.compile(r"\b(?:the|in the|of the|since the|from the)\s*$", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[.,;:!?()\[\]\n]")
# the nouns that decide whose age a number is (the nearest one after it, else before it, in the same clause); neither
# found = a person (an adult picture prompt's ages are people's)
_AGE_THING = frozenset("""car cars vehicle vehicles truck van bike bicycle motorcycle motorbike boat ship yacht plane aircraft
train house houses home building buildings barn church castle bridge tower ruin ruins tree trees oak vine vines forest
garden whisky whiskey scotch bourbon rum cognac brandy wine wines port malt beer bottle bottles cask casks barrel barrels
cheese dog dogs cat cats horse horses pony pet pets parrot turtle tortoise guitar piano violin camera photo photos
photograph photographs picture pictures painting paintings film films movie movies record records album albums song
songs book books letter letters map sofa couch chair table rug carpet furniture jacket coat dress shoes boots watch clock
company business tradition recipe game games computer laptop phone tv television mansion villa hotel bar pub restaurant
cabin cottage apartment flat mine tunnel wall poster sign era eras decade decades century fashion style styles music jazz
decor design aesthetic architecture glamour flapper outfit outfits wardrobe clothing hairstyle hairstyles makeup trend
trends bed beds mattress blanket pillow lamp desk door window floor tattoo scar""".split())
_AGE_PERSON = frozenset("""woman women man men girl girls guy guys lady ladies gentleman gentlemen boy boys model models couple
couples wife wives husband husbands girlfriend girlfriends boyfriend boyfriends student students blonde blondes brunette
brunettes redhead redheads mother mothers mom moms mum mums milf milfs father fathers dad dads female females male males
person persons people actress actresses actor actors dancer dancers nurse nurses teacher teachers secretary secretaries
she he they her him them bride brides groom grooms partner partners lover lovers stranger strangers neighbor neighbors
neighbour neighbours twins sister sisters brother brothers friend friends adult adults human humans pornstar pornstars
stripper strippers escort escorts performer performers athlete athletes cowboy cowgirl""".split())


# [R88 fix round 2] "teen" / "barely legal" -> adult wording (see the section comment)
_TEEN_PHRASE = re.compile(r"(?<!\w)(?:(?P<bl>barely[\s-]+legal)(?:[\s-]+(?P<t1>teen-?age[ds]?|teen-?agers?|teens?))?"
                          r"|(?P<t2>teen-?age[ds]?|teen-?agers?|teens?))(?!\w)", re.IGNORECASE)
_TEEN_NOUNS = {"teen": "adult", "teens": "adults", "teenager": "adult", "teenagers": "adults"}   # (hyphen dropped)
_TEEN_PERSON = (_AGE_PERSON - {"she", "he", "they", "her", "him", "them"}) | frozenset(
    "cheerleader cheerleaders babe babes lesbian lesbians gymnast gymnasts daughter daughters".split())
_TEEN_ADULT_NOUN = {"girl": "woman", "girls": "women", "boy": "man", "boys": "men"}
_TEEN_FN = frozenset("""a an the and or with without in on at of for to by from is are was were who that as into onto near under
over next beside behind wearing her his their its my your our this these those""".split())
_TEEN_NEXT = re.compile(r"[\s-]+([A-Za-z][\w']*)(?:[\s-]+([A-Za-z][\w']*))?")
_TEEN_NP_END = re.compile(r"\s*(?:[.,;:!?()\[\]\n\"]|$)|[\s-]+(?:on|in|at|with|without|and|or|by|from|under|near|next|beside"
                          r"|behind|inside|outside|against|who|that|is|are|was|were|has|have|wearing|\w+ing)\b", re.IGNORECASE)
_TEEN_PUNCT_END = re.compile(r"\s*(?:[.,;:!?()\[\]\n\"]|$)")
_TEEN_AGE_FOLLOWS = re.compile(r"\s*,?\s*(?:aged?\s*\d|ages\s*\d|\d{2}(?!\d)|in\s+(?:his|her|their)\s+(?:thirties|forties|fifties"
                               r"|sixties))", re.IGNORECASE)
_TEEN_AGE_BEFORE = re.compile(r"(?:\b(?:\d{2}|thirty)[\s-]*(?:years?|yrs?)[\s-]*olds?|\b\d{2}\s*(?:yo|y\.o\.?)|\baged?\s*\d{2})"
                              r"[\s,]*$", re.IGNORECASE)
_TEEN_HEAD_KEEP = re.compile(r"\b(?:early|mid|pre)[\s-]*$", re.IGNORECASE)               # the minors block's forms
_TEEN_HEAD_LATE = re.compile(r"\blate[\s-]*$", re.IGNORECASE)          # "late teens": the decade rule's; "late teen girl": ours
_TEEN_HEAD_DECADE = re.compile(r"\b(?:his|her|their|my|your|our|its)\s+$", re.IGNORECASE)  # "(no longer in) her teens"
_TEEN_NEGATED = re.compile(_NEG_HEAD + r"(?:\s+(?:" + _NEG_WORDS + r"|like))*\s*$", re.IGNORECASE)
# [R88 fix round 3, the integrator's decision 2026-10-17] the ONLY titles a teen word may start, and only capitalised ("Teen" /
# "Teenage" as written): the shows, films, magazine, awards, songs and the synth maker an adult picture may show on a
# poster, a cover or a screen — Teen Titans, Teen Wolf, Teen Vogue, Teen Choice (Awards), (Smells Like) Teen Spirit, Teenage
# Mutant Ninja (Hero) Turtles, Teenage Dream, Teenage Kicks, Teenage Engineering. Kept short on purpose: an unknown title
# rewritten in an ADULT prompt is cosmetic ("Teen Beach Movie" -> "Adult Beach Movie"), a teen word that reaches the image
# model is not. "Teen Mom" is no title here (a person noun)
_TEEN_TITLES = {
    "teen": re.compile(r"[\s-]+(?:titans|wolf|vogue|choice|spirit)\b", re.IGNORECASE),
    "teenage": re.compile(r"[\s-]+(?:mutant[\s-]+(?:ninja|hero)[\s-]+turtles|dream|kicks|engineering)\b", re.IGNORECASE),
}


def _teen_rewrite(t: str, m) -> tuple:
    """one "teen" / "barely legal" phrase of an adult prompt -> (new text, start, end of the span it replaces), or
    (None, 0, 0) to keep it (one of the few titles, a negation, an under-18 or decade form)"""
    clause = _CLAUSE_END.split(t[max(0, m.start() - 160):m.start()])[-1]
    w = (m.group("t1") or m.group("t2") or "").lower().replace("-", "")
    late = _TEEN_HEAD_LATE.search(clause)
    if _TEEN_HEAD_KEEP.search(clause) or w == "teens" and (late or _TEEN_HEAD_DECADE.search(clause)) \
            or _TEEN_NEGATED.search(clause[:late.start()] if late else clause):
        return None, 0, 0
    start = m.start() - (len(clause) - late.start() if late else 0)     # "a late teen girl" -> "an adult woman aged 30"
    if late:
        clause = clause[:late.start()]
    nx = _TEEN_NEXT.match(t, m.end())
    words = [g for g in (nx.groups() if nx else ()) if g]
    base = [x.lower()[:-2] if x.lower().endswith("'s") else x.lower() for x in words]     # "girl's" -> girl
    tw = m.group("t2") or ""                     # [R88 fix round 3] a title: capitalised + _TEEN_TITLES, no person noun next
    title = _TEEN_TITLES.get(tw.lower())
    if tw[:1].isupper() and title and title.match(t, m.end()) and not any(x in _TEEN_PERSON for x in base[:2]):
        return None, 0, 0                        # "Teen Titans", "Teenage Mutant Ninja Turtles"; never "teen Latina"
    adj = noun = None
    if len(words) > 1 and base[1] in _TEEN_PERSON and base[0] not in _TEEN_FN and not base[0].endswith("ing") \
            and not words[0].lower().endswith("'s"):
        adj, noun, end = words[0], words[1], nx.end(2)                # "teen blonde girl", "teen Asian girls"
    elif words and base[0] in _TEEN_PERSON:
        noun, end = words[0], nx.end(1)                               # "teen girl"
    else:
        end = m.end()
    near = bool(_TEEN_AGE_BEFORE.search(clause) or _TEEN_AGE_FOLLOWS.match(t, end))    # an age is stated already
    if noun:                                     # "teen girl" -> "adult woman aged 30"
        poss = noun.lower().endswith("'s")
        nb = noun[:-2] if poss else noun
        n = _TEEN_ADULT_NOUN.get(nb.lower(), nb)
        n = (n[0].upper() + n[1:] if nb[0].isupper() else n) + ("'s" if poss else "")
        if adj and adj.lower() in _TEEN_ADULT_NOUN:                    # "teen girl model" -> "adult woman model"
            adj = _TEEN_ADULT_NOUN[adj.lower()]
        new = "adult " + (adj + " " if adj else "") + n + ("" if near or poss else f" aged {ADULT_AGE_TO}")
    elif w in _TEEN_NOUNS and _TEEN_NP_END.match(t, end):         # the noun: "a teen" -> "an adult aged 30"
        new = _TEEN_NOUNS[w] + ("" if near else f" aged {ADULT_AGE_TO}")
    elif not w and _TEEN_PUNCT_END.match(t, end):                  # "barely legal" alone -> "adult, aged 30"
        new = "adult" + ("" if near else f", aged {ADULT_AGE_TO}")
    else:                                        # an attribute or a noun before more words: "teen porn" -> "adult porn"
        new = _TEEN_NOUNS.get(w, "adult")
    return (new[0].upper() + new[1:] if t[start].isupper() else new), start, end


def _teen_words(t: str, changes: list) -> str:
    """the "teen" / "barely legal" rewrite of adult_ages over the whole prompt (a phrase may take the person noun after
    it, so the scan is a loop, not re.sub); a/A before the new "adult" becomes an/An"""
    out, pos, n0 = [], 0, len(changes)
    for m in _TEEN_PHRASE.finditer(t):
        if m.start() < pos:
            continue
        new, start, end = _teen_rewrite(t, m)
        if new is None or start < pos:
            continue
        out += [t[pos:start], new]
        changes.append((t[start:end], new))
        pos = end
    if len(changes) == n0:
        return t
    return re.sub(r"\b([Aa]) (?=[\"'“‘]?[Aa]dult\b)", lambda x: x.group(1) + "n ", "".join(out) + t[pos:])


def sexual(text) -> bool:
    """[R88] the request is adult: the minors block's own sexual-term list (EN + DE) finds a term in `text`"""
    t = _norm(text)
    return bool(t) and _SEX.search(t) is not None


def _age_words(s: str) -> list:
    return [w[:-2] if w.endswith("'s") else w for w in re.findall(r"[a-z]+(?:'[a-z]+)?", s.lower())]


def _age_of_person(t: str, start: int, end: int) -> bool:
    """is the age at t[start:end] a person's? the nearest decisive noun after it (≤ 3 words, same clause), else before it
    (≤ 4 words, same clause); none = a person"""
    for w in _age_words(_CLAUSE_END.split(t[end:end + 80], 1)[0])[:3]:
        if w in _AGE_PERSON:
            return True
        if w in _AGE_THING:
            return False
    for w in reversed(_age_words(_CLAUSE_END.split(t[max(0, start - 80):start])[-1])[-4:]):
        if w in _AGE_PERSON:
            return True
        if w in _AGE_THING:
            return False
    return True


def _age_num(s: str) -> int:
    s = s.lower().replace("-", " ").split()
    words = {"eighteen": 18, "nineteen": 19, "twenty": 20, "one": 1, "two": 2, "three": 3, "four": 4}
    return int(s[0]) if s[0].isdigit() else sum(words.get(w, 0) for w in s)


def adult_ages(text) -> tuple:
    """[R88] (text, changes): the 25+ check on an adult image prompt (see the section comment) — every stated person age
    or decade below ADULT_AGE_MIN rewritten to ADULT_AGE_TO / "thirties", changes = [(old, new), …]; a text the minors
    block refuses, or one without such an age, comes back unchanged with changes = []. Pure, regex-only."""
    if not isinstance(text, str) or not text.strip() or minor_block(text, adult=True)[0]:
        return text, []                          # [R88 fix round 2] read as the adult request it is: the age alone refuses
    changes = []

    def keep(t, m, era=False):  # a negated phrase ("not a 22-year-old") or, for a decade, an era ("the early twenties")
        head = t[max(0, m.start() - 24):m.start()]
        return bool(_AGE_NEGATED.search(head) or (era and _AGE_ERA.search(head)))

    def rng(m):
        if not (m.group("pre") or m.group("post")):
            return m.group(0)
        a, b = int(m.group("a")), int(m.group("b"))
        if not 18 <= a < ADULT_AGE_MIN or keep(m.string, m) or not _age_of_person(m.string, m.start(), m.end()):
            return m.group(0)
        b = max(b, ADULT_AGE_TO)
        new = (m.group("pre") or "") + (str(ADULT_AGE_TO) if b == ADULT_AGE_TO else f"{ADULT_AGE_TO}-{b}") + (m.group("post") or "")
        changes.append((m.group(0), new))
        return new

    def num(m, n_key, rest_key):
        n = m.group(n_key)
        if keep(m.string, m) or not _age_of_person(m.string, m.start(), m.end()):
            return m.group(0)
        to = str(ADULT_AGE_TO) if n[0].isdigit() else "thirty"
        new = to + m.group(rest_key)
        changes.append((m.group(0), new))
        return new

    def yo(m):
        return num(m, "n", "rest") if m.group("n") else num(m, "n2", "rest2")

    def aged(m):                 # "aged 22", "age: 19", "aged 19 and 23" (a list: every number of it is read)
        ns = [m.group("n")] + re.findall(r"\d{2}", m.group("more") or "")
        vals = [_age_num(n) for n in ns]
        if any(v < 18 for v in vals) or all(v >= ADULT_AGE_MIN for v in vals) or keep(m.string, m) \
                or not _age_of_person(m.string, m.start(), m.end()):
            return m.group(0)                    # an under-18 number is the minors block's, never rewritten here
        to = lambda n: n if _age_num(n) >= ADULT_AGE_MIN else (str(ADULT_AGE_TO) if n[0].isdigit() else "thirty")
        more = re.sub(r"\d{2}", lambda x: to(x.group(0)), m.group("more") or "")
        new = m.group("pre") + m.group("sep") + to(m.group("n")) + more
        changes.append((m.group(0), new))
        return new

    def something(m):
        if keep(m.string, m) or not _age_of_person(m.string, m.start(), m.end()):
            return m.group(0)
        new = "thirty-" + m.group("s")
        changes.append((m.group(0), new))
        return new

    def decade_pron(m):
        top = (m.group("q2") or m.group("q") or "").lower()          # the range's upper end ("early to mid" -> mid)
        if (m.group("q") or "").lower() == "late" and m.group("d").lower() in ("20s", "20's", "'20s", "twenties") \
                or m.group("d").lower() in ("teens", "teenage years", "teen years") and top in ("early", "mid") \
                or _AGE_NEGATED.search(m.string[max(0, m.start() - 24):m.start()]):
            return m.group(0)                    # late twenties = 27-29: 25+ already; early / mid teens: under 18, never aged up
        new = f"in {m.group('p')} thirties"
        changes.append((m.group(0), new))
        return new

    def decade_bare(m):
        if keep(m.string, m, era=True) or not _age_of_person(m.string, m.start(), m.end()) \
                or re.search(r"\bin\s+" + _AGE_PRON + r"\s+$", m.string[max(0, m.start() - 12):m.start()], re.IGNORECASE):
            return m.group(0)    # [R88 fix round 2] "in her <decade>" is decade_pron's: left there only when negated
        new = "their thirties" if re.search(r"\bin\s+$", m.string[max(0, m.start() - 8):m.start()], re.IGNORECASE) \
            else f"aged {ADULT_AGE_TO}"
        changes.append((m.group(0), new))
        return new

    t = _AGE_RANGE.sub(rng, text)
    t = _AGE_YO.sub(yo, t)
    t = _AGE_AGED.sub(aged, t)
    t = _AGE_SOMETHING.sub(something, t)
    t = _DECADE_PRON.sub(decade_pron, t)
    t = _DECADE_BARE.sub(decade_bare, t)
    t = _teen_words(t, changes)                  # [R88 fix round 2] "teen" / "barely legal" (after the decades)
    if not changes:
        return text, []
    return re.sub(r"\b([Aa])n (?=(?:30|thirty)\b)", r"\1 ", t), changes     # "an 18-year-old" -> "a 30-year-old"
