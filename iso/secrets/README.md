# iso/secrets — operator inputs the image build reads, never shipped in this tree [R66]

`iso/build-iso.sh` looks here (or at `KAIROS_SSH_PUBKEY` / `KAIROS_WIFI_ENV`) and
renders the result into the live-build work tree only. Everything but this README,
`.gitignore` and `wifi.env.example` is ignored.

| file | purpose | without it |
|---|---|---|
| `authorized_keys` | public key(s) root accepts over SSH — sshd is `PermitRootLogin prohibit-password` + `PasswordAuthentication no` | the image has **no remote shell** (safe consumer build; not monitorable) |
| `wifi.env` | `KAIROS_WIFI_SSID` / `KAIROS_WIFI_PSK` the image auto-joins (hardware-test monitoring) | the image joins no network by itself; the user connects from the kiosk's WiFi app |

Testrig: `dev/rig/make-key.sh` (or `dev/rig/host-setup.sh`) generates `dev/rig/testrig/id_kairos{,.pub}`
and stages the `.pub` here as `authorized_keys` when this file does not exist yet; otherwise
append it yourself before building. All `dev/rig/testrig/*.sh` use that key and fall
back to the old password for images built before this change.

Network exposure of the API (:1337) is a third, separate opt-in: `KAIROS_EXPOSE_UI=1`
at build time, or `dev/rig/testrig/expose-ui.sh` in a booted guest.
