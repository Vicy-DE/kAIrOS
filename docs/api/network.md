<!-- keywords: wifi, wi-fi, wlan, internet, network, connect, router, hotspot, signal, bluetooth, pair, headphones, airplane, online, offline -->
# kAIrOS API — network (WiFi, Bluetooth, airplane mode)

No radio → `{available:false, reason}` (show it). `ok:false` answers carry `error` (a code) +
`detail` and resolve — render them.

**WiFi**:

```js
await vibe.hw.wifi.status(); // {enabled, connected, ssid, bars:0-4, ip, connectivity:"full"|"portal"|"none"}
await vibe.hw.wifi.scan();   // {networks:[{ssid, bars, band, needs:"none"|"password"|"login", saved, in_use}]}
                             //  instant; scan({fresh:true}) rescans (seconds, spinner)
await vibe.hw.wifi.connect(ssid, pass); // {ok, error, detail} — saved, rejoins by itself
await vibe.hw.wifi.disconnect(); await vibe.hw.wifi.forget(ssid); await vibe.hw.wifi.saved();
await vibe.hw.wifi.on(); await vibe.hw.wifi.off(); await vibe.hw.wifi.signin(); // hotspot login page
```

Flow for a WiFi app: status → scan → tap: `needs:"none"` (open or OWE) connects at once — render
NO password field for it; `"password"` → an INLINE password field (never window.prompt) unless `saved`.
`error:"wrong_password"` → say so; also `not_in_range`, `timeout`. `connectivity:"portal"` → a
"Sign in" button calling `signin()`. Poll status ~5 s.

**Bluetooth**:

```js
await vibe.hw.bluetooth.status(); // {powered, blocked, connected:[names], devices:[{mac, name, kind, trusted, connected, battery}]}
await vibe.hw.bluetooth.scan();   // {devices:[{mac, name, kind}]} — new devices nearby, ~10 s
await vibe.hw.bluetooth.pair(mac);// {ok, error, code} — pairs, trusts, connects
await vibe.hw.bluetooth.connect(mac); // also disconnect(mac), remove(mac), on(), off()
```

`devices` = the paired ones (Connect/Disconnect); list what `scan()` returns, each with Pair. `kind`:
audio|mouse|keyboard|gamepad|phone|other → an icon. `error:"enter_code"` → show "type {code} on the
keyboard + Enter" in the page, poll status. **Airplane**: `vibe.hw.airplane.get()` →
`{airplane}`; `vibe.hw.airplane.set(true|false)`.
