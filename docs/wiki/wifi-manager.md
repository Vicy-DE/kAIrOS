<!-- keywords: wifi, wi-fi, wlan, network, internet, hotspot, connect, router, signal -->
# Recipe: WiFi manager

Everything goes through `vibe.hw.wifi` (NetworkManager). All calls are async; a missing radio
answers `{available:false, reason}` — show that state, never crash.

```js
const st = await vibe.hw.wifi.status();          // {available, enabled, connected, ssid, bars, ip, connectivity}
const { networks } = await vibe.hw.wifi.scan();  // [{ssid, bars, band, security, needs, saved, in_use}]
const r = await vibe.hw.wifi.connect(ssid, pw);  // {ok, error, detail}
```

- Header: `enabled:false` → "WiFi off" + a switch calling `vibe.hw.wifi.on()`; connected →
  "Connected to {ssid}" with `bars`; `starting` → "starting…". Poll `status()` every ~5 s.
- One row per network: `bars` (0-4) as bars, 🔒 only when `needs === "password"`, "5 GHz" from
  `band`, badges for `in_use` and `saved`; already sorted strongest first.
- Tap: `needs:"none"` (open, or OWE "Enhanced Open" like Freifunk) → `connect(ssid)` at once, no
  password field; `"password"` → an inline password field + Connect (a `saved` one connects
  without asking); `"login"` → "needs a company login".
- `r.error === "wrong_password"` → red text under the field, keep it open; otherwise show `r.detail`.
- `connectivity === "portal"` (hotel or train hotspot) → a "Sign in" button: `vibe.hw.wifi.signin()`.
- Rescan button: `scan({fresh:true})` + spinner. Saved networks get Forget: `forget(ssid)`.
