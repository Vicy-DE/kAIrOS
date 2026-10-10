<!-- keywords: game, games, pygame, arcade, play, level, forge, launcher, create app, new app -->
# kAIrOS API — games & spawning apps

```js
const r = await vibe.game("asteroids but the asteroids are pizzas");
// {slug, running, attempt, version, gate, window, pid} — the OS writes a real pygame
// game, test-launches it, self-repairs crashes; running:true means its window has
// opened on the desktop (gate:"mapped"; takes ~minutes). A game that crashes later is
// recorded (vibe.apps() → games[].last_crash) — relaunching it forges a fixed version.

await vibe.forge("a flashcard trainer for Japanese kana");
// true — starts a full development cycle in a NEW window: the OS researches the
// topic, writes a spec, codes the app and generates its resources (images/music).
```

- Both are slow, deliberate actions: trigger only on an explicit user click and
  say what is happening ("forging your game — a window will open…").
- Relaunching an existing game forges its NEXT version (living software).
- In-page graphics for YOUR app (canvas, sprites) need no API at all — plain
  JS/canvas; AI pictures → media.md (`/genimg/`).
