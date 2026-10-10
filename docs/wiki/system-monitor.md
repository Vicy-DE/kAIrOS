<!-- keywords: monitor, task manager, system info, cpu, ram, memory, disk, storage, gpu, dashboard, stats -->
# Recipe: system monitor / dashboard

```js
const hw = await vibe.hw.get();
// { wifi:{connected,ssid}, battery:{percent,charging}, brightness:{percent},
//   volume:{percent,muted}, memory_mb:{total,available}, disk_gb:{total,free} }
const sys = await vibe.system.setup();   // {vram_mb, current_tier, tiers:[…]} — the GPU brain
```

Patterns:
- Poll `vibe.hw.get()` every 5-10 s; render tiles/gauges (memory used =
  total-available, disk used = total-free). Animate value changes.
- GPU: `sys.vram_mb` + `sys.current_tier` make a nice "AI brain" tile; list `sys.tiers`
  with their `present` flag.
- The AI job queue is visible via `fetch("/api/status")` → `{state, jobs}` — `state`
  shows what the GPU is doing right now (llm/image/video/music). Great for an
  "AI activity" LED.
- Everything that can be `available:false` (battery on a desktop, wifi in a VM) gets a
  muted "not present" tile, never an error.
