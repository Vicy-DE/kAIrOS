<!-- keywords: bluetooth, pair, pairing, headphones, headset, earbuds, airpods, speaker, mouse, keyboard, gamepad -->
# Recipe: Bluetooth manager

Everything goes through `vibe.hw.bluetooth` (bluez). No adapter → `{available:false, reason}`.

```js
const st = await vibe.hw.bluetooth.status();
// {available, powered, blocked, connected:["MX Master 2S"],      ← names (strings)
//  devices:[{mac, name, kind, paired, trusted, connected, battery}], pairing?:{mac, state, code}}
const sc = await vibe.hw.bluetooth.scan();    // {devices:[{mac, name, kind, rssi}]} — ~10 s, new devices only
const r = await vibe.hw.bluetooth.pair(mac);  // {ok, error, code, name, kind, connected, sound}
```

- Switch: `blocked` or `!powered` → "Bluetooth off" + `vibe.hw.bluetooth.on()`; `off()` stays
  off after a reboot. `scan()` needs it on.
- "My devices": `st.devices`, an icon per `kind` (🎧 audio, 🖱 mouse, ⌨ keyboard, 🎮 gamepad,
  📱 phone), `battery` %, Connect / Disconnect (`connect(mac)`, `disconnect(mac)`), Remove (`remove(mac)`).
- "Add device": tell the user to put the device in pairing mode, Scan (spinner ~10 s), a Pair
  button per found device.
- `pair()`: `ok` → paired, trusted (it reconnects by itself after a reboot) and connected;
  headphones become the sound output (`r.sound.sink`). `error:"enter_code"` → show "Type
  {r.code} on the keyboard, then Enter" and poll `status()` until `st.pairing.state` is
  "paired". A phone: "Confirm {r.code} on the phone". `auth_failed`, `not_in_range`, `timeout`
  → show it + Retry.
- Render fields, never whole objects (`st.connected` is a list of names).
