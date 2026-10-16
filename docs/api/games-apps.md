<!-- keywords: game, games, pygame, arcade, play, level, forge, launcher, create app, new app -->
# kAIrOS API — games & spawning apps

```js
const r = await vibe.game("asteroids but the asteroids are pizzas");
// {slug, running, attempt, version, gate, window, pid} — the game opens in its own
// desktop window. A NEW game is written first (pygame, checked, self-repaired; ~minutes);
// an existing one plays its current version at once. running:true means its window has
// opened (gate:"mapped"). A crash is recorded (vibe.apps() → games[].last_crash);
// ✨ improve in the game's window forges the next (repaired) version.

await vibe.forge("a flashcard trainer for Japanese kana");
// true — starts a full development cycle in a NEW window: the OS researches the
// topic, writes a spec, codes the app and generates its resources (images/music).
```

- Both are slow, deliberate actions: trigger only on an explicit user click and
  say what is happening ("forging your game — a window will open…").
- Launching an existing game runs its current version; the user evolves it with ✨ in its window.
- In-page graphics for YOUR app (canvas, sprites) need no API at all — plain
  JS/canvas; AI pictures → media.md (`/genimg/`).
