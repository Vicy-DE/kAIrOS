<!-- keywords: display, screen, monitor, multiscreen, multi-monitor, resolution, rotate, rotation, mirror, extend, projector, brightness, backlight -->
# kAIrOS API — displays & multiscreen

kAIrOS is ONE desktop over every connected monitor: windows move between monitors like on any
OS, the menu bar and the dock live on the primary monitor. A layout change applies live — no
restart, no window lost (a monitor that goes away hands its windows to the primary).

```js
const d = await vibe.hw.display.list();
// {available, count, multiscreen, displays:[{output,connected,primary,
//   resolution:"1920x1080", position:[x,y], rotation, modes:[…]}],
//  monitors:[{id:"eDP", x:0, y:0, w:1920, h:1080, primary:true, outputs:["eDP"]}, …],
//  desktop:{x:0, y:0, w:4480, h:1440},          // the area the one desktop spans
//  providers:[{name:"NVIDIA-G0", caps:["Sink Output"], associated:1, …}, …],   // RandR 1.4 GPUs
//  hdmi:{state:"active"|"absent"|"off"|"unsupported"|"failed"|"pending"|"n/a", reason, connectors}}

await vibe.hw.display.set({output:"HDMI-1", resolution:"2560x1440"});
await vibe.hw.display.set({output:"eDP-1", primary:true});
await vibe.hw.display.set({output:"HDMI-1", rotation:"left"});   // normal|left|right|inverted
await vibe.hw.display.set({output:"HDMI-1", position:[1920,0]});
await vibe.hw.display.set({output:"HDMI-1", brightness:70});     // software dim per display
await vibe.hw.display.set({output:"HDMI-1", off:true});
await vibe.hw.display.arrange("extend");   // or "mirror": one picture, at the primary's native mode
```

- Results are `{ok, relayout:"live"}` or `{ok:false, error}`; `{available:false, reason}` = headless.
- `monitors` = what the user sees as screens: mirrored outputs are one, primary first (the
  numbering of `vibe.vision("screen:N")`). Draw a layout preview from `monitors`, label it
  with `displays[].output`.
- Switching the primary off makes the next monitor the primary.
- **The HDMI port on a hybrid laptop** (wired to the NVIDIA GPU while the AMD/Intel iGPU draws the
  desktop) joins the desktop through reverse PRIME: `hdmi.state` says `active` (its outputs are
  in `displays`/`monitors`, named after the GPU screen, e.g. `HDMI-1-0`), `absent` (no monitor on
  it), `off` (the user switched it off: `vibe.settings.set("hdmi", "off")`), `failed` (the desktop
  runs without it — `reason` says why), `unsupported` / `n/a` (no such port here), or **`consent`** /
  **`declined`**: a monitor was plugged in while the desktop was up — using it needs ONE desktop
  restart (a few seconds, open windows close), which never happens without the user: the OS shows
  a notice with Restart now / Not now. A display-settings app offers the same choice with
  `vibe.hw.display.set({op:"hdmi", action:"restart"})` (restart the desktop with the HDMI
  monitor now) or `action:"later"` (not now, remembered until the next boot). At boot, a monitor
  already plugged in is attached automatically (before any window exists). Tell the user when
  `state` is `failed`. A `providers` entry with `associated > 0` is a GPU screen that is bound.
- **Panel backlight** (the laptop's real brightness) is separate:
  `vibe.hw.brightness.get()/.set(percent)` → power-battery.md.
- Offer the user the `modes` list of each display as a resolution picker.
