<!-- keywords: battery, power, charge, charging, energy, suspend, shutdown, reboot, akku -->
# Recipe: battery & power manager

```js
const b = await vibe.hw.battery();
// {available, percent, status:"Charging|Discharging|Full", charging, ac_plugged, health_percent}
await vibe.hw.power.off();      // shuts the machine down — ONLY on explicit user click
await vibe.hw.power.reboot();
await vibe.hw.power.suspend();
```

Patterns:
- `available:false` means desktop/VM — show "no battery" gracefully, keep power buttons.
- Poll `battery()` every ~10 s; animate a big battery gauge (fill = percent, pulse while
  `charging`), color it green > 40, amber 15-40, red < 15.
- `health_percent` (may be null) = design-capacity health — show as "battery health".
- Power actions must sit behind a confirm step in YOUR UI (a real button press, e.g.
  "hold to power off" or a two-step confirm) — they interrupt the whole OS.
- Combine with `vibe.hw.brightness.set(p)` for a power-saver toggle (e.g. dim to 30%).
