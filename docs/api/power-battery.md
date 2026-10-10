<!-- keywords: battery, charge, charging, power, energy, shutdown, poweroff, reboot, restart, suspend, sleep, backlight -->
# kAIrOS API — power & battery

```js
await vibe.hw.battery();
// {available, percent, status:"Charging|Discharging|Full", charging,
//  ac_plugged, health_percent}   — available:false on desktops/VMs: show it gracefully

await vibe.hw.brightness.get();          // {available, percent} — the panel backlight
await vibe.hw.brightness.set(70);

await vibe.hw.power.off();               // } all three interrupt the whole OS:
await vibe.hw.power.reboot();            // } ONLY behind an explicit user
await vibe.hw.power.suspend();           // } confirmation step in YOUR UI
```

- Poll battery every ~10 s; color the gauge (green >40, amber 15-40, red <15),
  pulse while charging; `health_percent` (may be null) = design-capacity health.
- Power-saver pattern: one toggle that dims brightness + reduces your app's timers.
- Per-display software dimming (external monitors) → display.md.
- System volume / audio output → system-settings.md.
