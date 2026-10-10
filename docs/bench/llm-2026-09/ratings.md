# Blind ratings — kAIrOS generated apps and pages (llm-2026-09)

Rater: blind (screenshots only, `blind/` folder + README). Each id rated on its own; A/B randomised per id.

## Criteria and judgement boundaries (applied to all 30 ids)

Scores 0–3 per image, format `works/prompt/look`.

**Works** (render and layout integrity of the initial state)
- 0 = blank, white, error, or nothing recognisable of the app.
- 1 = renders, but visibly broken: overlapping elements that obscure content, raw/unstyled browser-default controls in core UI (white native textareas/inputs/buttons on a dark app), or a core UI region that is empty/unpopulated (e.g. unit selectors with no options, placeholder images not loaded).
- 2 = usable: core UI renders and is legible; minor cosmetic flaws allowed (a small badge touching a button, off-centre layout, a stray "Loading…").
- 3 = polished, no visible defects, clearly functional-looking.
- An empty list/canvas in its initial state is NOT counted as broken (nothing has been added yet) as long as the controls to add are visible.

**Fulfils the prompt** (what is visibly present)
- 0 = wrong thing or nothing. 1 = a fragment (one of the named features, or mostly off-topic).
- 2 = the core features named in the prompt are visible (a colour picker counts as "colour palette"; a type dropdown counts as the three categories).
- 3 = all named features plus sensible extras (history, undo, save, etc.).
- Off-topic additions (AI "generate" bars, "Upgrade System") are not extras and do not add points.

**Look**
- 0 = raw/unstyled (browser defaults dominate). 1 = plain: minimal styling, default-ish controls, low contrast or large dead areas with no design intent.
- 2 = clean and consistent deliberate styling (cards, spacing, accent colours).
- 3 = modern and futuristic: glow/gradients/neon accents, strong hierarchy, attractive.

**Preference** — overall "which would a user rather get": prompt fulfilment and working layout weigh more than look; look decides only when the other two are close. "Both broken" = both images have Works ≤ 1.

## Ratings

| id | A works/prompt/look | B works/prompt/look | preference | confidence | reason |
|---|---|---|---|---|---|
| app-todo-1 | 2/2/1 | 0/0/0 | A | high | B is an empty gradient with no UI at all; A has header, task input and category buttons. |
| app-todo-2 | 2/2/1 | 1/2/2 | A | low | B has input, filters and a sleeker look but its icon sidebar overlaps the title and panel; A is bare but intact. |
| app-pomodoro-1 | 2/2/1 | 2/2/2 | B | medium | B shows a styled ring with WORK label and session-history area; A's buttons have dark-on-dark text. |
| app-pomodoro-2 | 2/2/3 | 1/2/1 | A | high | A is a polished neon ring timer with cycle mode; B has raw native buttons with off-topic actions (time zone, upgrade system). |
| app-notes-1 | 1/1/0 | 1/2/2 | B | high | A is a tiny raw white textarea and a purple block; B has a notes sidebar, editor, Edit/Preview toggle and status bar (with a slight button/placeholder overlap). |
| app-notes-2 | 1/1/0 | 2/3/2 | B | high | B shows list, editor and preview panels plus save; A is a raw white textarea with native Save/Delete. |
| app-converter-1 | 1/1/2 | 1/2/2 | B | medium | Both have empty unit selectors, but B shows a Length type selector, value, result and history; A shows no categories and a raw white result field. |
| app-converter-2 | 1/2/1 | 2/2/3 | B | high | B is a styled card with length/weight/temperature rows and a gradient Convert button; A's inputs/selects are raw white native controls. |
| app-canvas-1 | 1/1/2 | 1/2/0 | B | low | B's unstyled toolbar at least shows sizes, Color Palette and Eraser; A is styled but only has a size slider plus an off-topic AI generate bar, palette pill empty. |
| app-canvas-2 | 1/3/1 | 3/3/3 | B | high | B is a polished neon toolbar (size, colour, eraser/brush, save/load) with a framed canvas; A mixes raw native buttons with a plain bar. |
| app-weather-1 | 1/1/3 | 1/0/1 | A | medium | A has a futuristic dashboard with a (stuck "INITIALIZING", --°C) weather panel but off-topic system stats and no forecast; B is only three buttons with no weather content. |
| app-weather-2 | 1/1/1 | 2/1/3 | B | high | B shows a styled 5-day forecast strip with temperatures (next to an off-topic system monitor, no today panel); A has bare labels with no values and two broken forecast tiles. |
| app-memory-1 | 2/2/2 | 1/1/1 | A | high | A shows a grid of emoji pair cards (face-up, with off-topic system stat tiles); B shows only score/time and buttons, no cards. |
| app-memory-2 | 1/0/2 | 2/2/2 | B | high | B shows a card grid, difficulty, score and leaderboard; A shows only system stats (negative percentages) and no game. |
| app-habits-1 | 2/2/3 | 1/1/1 | A | high | A has a neon streak counter, category filters and add-habit input; B is an empty page with a raw white input. |
| app-habits-2 | 1/2/1 | 2/3/3 | B | high | B has today's goals, a day-streak pill, weekly chart and New Habit button in a polished layout; A has headings only with raw native buttons and no way to add a habit. |
| app-wifi-1 | 2/2/1 | 2/2/2 | B | low | Both show an empty list while loading/scanning; B's status pill and styled buttons look better, A has a plain Scan/Disconnect/Forget row. |
| app-wifi-2 | 2/2/2 | 0/0/1 | A | high | A shows a WiFi Manager with a hardware-not-available notice and Rescan; B is an empty gradient with only a clock and battery. |
| app-music-1 | 2/2/1 | 1/3/2 | B | low | B has a mood tile grid, lyrics box, Compose & Play and a status bar, though several mood tiles are clipped/overlapped; A is intact but only three low-contrast mood buttons and a hint. |
| app-music-2 | 2/2/3 | 2/2/1 | A | medium | A is a polished composer card with mood and language selects and a glowing Compose & Play; B is a plain mood text field with playback buttons and a large empty area. |
| app-gallery-1 | 0/0/0 | 0/0/0 | tie | medium | Neither shows any pictures: A is an almost invisible dark-on-dark title, B only status text. BOTH BROKEN. |
| app-gallery-2 | 0/0/1 | 0/0/1 | tie | medium | Neither shows any pictures: A is an empty gradient with "Generating media assets...", B only a header title. BOTH BROKEN. |
| app-calculator-1 | 1/2/2 | 2/2/1 | B | medium | B has a complete (if low-contrast) digit/operator keypad with a History button; A's neon card shows a history log but its keypad is giant sin/cos keys overflowing the card with no digits visible. |
| app-calculator-2 | 2/2/1 | 1/1/2 | A | medium | A shows a full keypad with memory keys and a side panel; B has a styled display and History panel but no keypad at all. |
| web-news | 3/3/3 | 1/1/1 | A | high | A is a full neon news front page (nav, hero story, news grid, trending list); B is a header over a solid purple fill with no articles. |
| web-recipes | 2/2/1 | 2/3/2 | B | medium | B is a characterful blog layout (serif, sidebar nav, search, post with caption) though the footer sits as a side column; A is a plain welcome card with a tiny placeholder and no recipes. |
| web-shop | 3/2/3 | 1/2/1 | A | medium | A is a polished neon store hero with nav and inventory heading; B shows products but in misaligned, staggered cards with only one image. |
| web-wiki | 3/3/3 | 2/2/1 | A | high | A is a styled wiki with nav, search, article and Quick Facts box; B is a plain unstyled-looking text page with an unbalanced equation and a dark empty block. |
| web-subpage | 3/3/1 | 2/2/2 | A | medium | A shows the actual lemon cake ingredients and steps above the fold; B is the prettier blog post but only a placeholder image and intro text are visible. |
| web-form | 1/1/2 | 3/2/1 | B | high | B shows a complete signup form (username, email, password, confirm, Sign Up); A's hero sits above an empty card with no form fields. |

## Both broken (Works ≤ 1 for both images)

- Apps: `app-notes-1`, `app-converter-1`, `app-canvas-1`, `app-weather-1`, `app-gallery-1`, `app-gallery-2` (the two gallery ids are Works 0 on both sides: no pictures at all).
- Web pages: none.

## Totals

Sums of per-image scores (works / prompt / look; max per cell = 3 × number of ids).

| set | letter | works | prompt | look | sum |
|---|---|---|---|---|---|
| apps (24) | A | 33 | 37 | 35 | 105 |
| apps (24) | B | 31 | 38 | 38 | 107 |
| pages (6) | A | 15 | 14 | 13 | 42 |
| pages (6) | B | 11 | 12 | 8 | 31 |
| all (30) | A | 48 | 51 | 48 | 147 |
| all (30) | B | 42 | 50 | 46 | 138 |

Preferences:

| set | A | B | tie |
|---|---|---|---|
| apps (24) | 9 | 13 | 2 |
| pages (6) | 4 | 2 | 0 |
| all (30) | 13 | 15 | 2 |

Letters are randomised per id, so letter totals say nothing about which system is better; they are only reported as requested.

## Observations (per pair)

Within most pairs the two images differ in a recurring way: one is a dark neon "futuristic OS" design, the other a plainer generic layout.
- The neon side usually wins on Look, but it often adds off-topic OS widgets (CPU/RAM/GPU tiles, "AI job queue", "Forge new app", AI generate/vision bars) and sometimes leaves out the core UI. Examples: memory-2 A (stats, no game), calculator-2 B (no keypad), weather-1 A (no forecast), canvas-1 A (no palette/eraser), web-form A (empty form card). This happens in weather-1 A, weather-2 B, memory-1 A, memory-2 A, canvas-1 A, canvas-2 B, music-1 B, calculator-1 A, calculator-2 B.
- The plain side more often shows raw browser-default controls: white native textareas/inputs/buttons (notes-1 A, notes-2 A, converter-2 A, canvas-1 B, canvas-2 A, pomodoro-2 B, habits-1 B, habits-2 A). It also often has dark-on-dark button labels (pomodoro-1 A, weather-1 B, music-1 A, calculator-1 B). On the other hand, it more often shows the plain core functions the prompt names (canvas-1 B, calculator-2 A, web-form B, web-subpage A).
- Web pages: in news, shop, wiki and form one side is a polished neon dark site (A in all four) and the other a light generic template (B). For the two grandma's-kitchen ids the split is different. One side is a plain coral-header template (A in both recipes and subpage). The other is a warmer serif blog with sidebar nav whose footer renders as a side column (B in both). Each side keeps its style consistent across the home page and the sub-page.
- Failures are shared: both galleries show no images on either side, and both sides frequently leave the main content area empty on first mount ("Loading…", "Initializing…", "Scanning…").
- No screenshot reveals which system produced it. "kAIrOS/KAIROS" branding appears on both styles and only names the OS.
