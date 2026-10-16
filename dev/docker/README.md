# dev/docker — the kernel-test container

One Dockerfile, four stages, no GPU:

| stage / tag | contents | used for |
|---|---|---|
| `base` | debian:trixie + python3-aiohttp + ffmpeg + curl + git + libgomp1 | the real kernel's runtime (the appliance is trixie too) |
| `feattest` | + python3-pygame + psmisc | the game forge tests (R11) |
| `kernel-test` → `kairos-test:local` (alias `kairos-fix:local`) | + quickjs (the JS syntax gate's parser) + aria2 | **the whole test suite**, the GPU smokes |
| `chromium` → `kairos-test-chromium:local` (alias `kairos-fix-chromium:local`) | + chromium | the desktop-in-a-window kernel (`dev/rig/desk-window.sh`) — runtime checks of generated apps with a real browser |
| `x11` → `kairos-test-x11:local` (alias `kairos-fix-x11:local`) | + Xvfb, openbox, python3-xlib, xdotool, x11 utils | `tests/test_games_windows.py` (R82): a real X server, the kiosk's openbox, real pygame windows in their desktop frames — `KAIROS_TEST_IMAGE=kairos-fix-x11:local bash dev/docker/run-suite.sh` runs it inside the suite |

```bash
bash dev/docker/build.sh                # kernel-test (~1.2 GB; downloads ~350 MB of packages)
bash dev/docker/build.sh chromium       # + chromium (~1.9 GB)
bash dev/docker/run-suite.sh            # the suite — see docs/how-to/TESTING.md for the output
bash dev/docker/run-js-checks.sh        # node --check + BRIDGE_SRC parse + tests/bridge-test.js (node:24-alpine)
```

`run-suite.sh` never gives the container a route to the internet: `--network none` by default
(the R66 remote-peer test skips, three more run only their loopback half), or
`KAIROS_TEST_NET=internal` for a Docker `--internal` network with an address but no DNS and no
route out (the R66 tests run in full). It mounts the standard
data dirs (`KAIROS_MODELS`, `KAIROS_KNOWLEDGE`, `KAIROS_BIN` from `dev/rig/.env`, read-only, only
when they exist). The suite is offline by design; the real kernel under test must never be able
to start a download (it can — a 4.7 GB model once landed in a test container's `/tmp` because the
container had network and no models mounted). Keep both properties when you write your own
`docker run` line.

The `kairos-fix:local` / `kairos-cudarun-next:local` / `kairos-feattest:local` names in the dated
reports under `docs/` are the stages of this file, built by hand on the owner's machine in
September 2026.
