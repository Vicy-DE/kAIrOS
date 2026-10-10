<!-- keywords: display, screen, monitor, multiscreen, multi-monitor, resolution, rotate, rotation, mirror, extend, projector, brightness, backlight -->
# kAIrOS API — displays & multiscreen

kAIrOS runs one fullscreen desktop per connected monitor (extended, left-to-right).
Changing the layout makes the OS relaunch its screens automatically.

```js
const d = await vibe.hw.display.list();
// {available, count, multiscreen, displays:[{output,connected,primary,
//   resolution:"1920x1080", position:[x,y], rotation, modes:[…]}]}

await vibe.hw.display.set({output:"HDMI-1", resolution:"2560x1440"});
await vibe.hw.display.set({output:"eDP-1", primary:true});
await vibe.hw.display.set({output:"HDMI-1", rotation:"left"});   // normal|left|right|inverted
await vibe.hw.display.set({output:"HDMI-1", position:[1920,0]});
await vibe.hw.display.set({output:"HDMI-1", brightness:70});     // software dim per display
await vibe.hw.display.set({output:"HDMI-1", off:true});
await vibe.hw.display.arrange("extend");   // or "mirror" — all connected displays
```

- Results are `{ok}` or `{ok:false, error}`; `{available:false, reason}` = headless.
- After any change the kiosks relaunch (a short black flicker is normal).
- **Panel backlight** (the laptop's real brightness) is separate:
  `vibe.hw.brightness.get()/.set(percent)` → power-battery.md.
- Offer the user the `modes` list of each display as a resolution picker.
