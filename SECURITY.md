# Security policy

kAIrOS is a pre-release, single-developer project. There is no bug bounty and no guaranteed
response time, but reports are read and taken seriously.

## Reporting

Use GitHub's **private vulnerability reporting**: the *Security* tab of the repository →
*Report a vulnerability* (`https://github.com/Vicy-DE/kAIrOS/security/advisories/new`).
Do not open a public issue for something exploitable. Please include the image version or
commit, the hardware, the steps, and what an attacker gains.

## What is in scope

The appliance is designed around one trust boundary (requirement R66, `docs/REQUIREMENTS.md`):
the machine's own kiosk may do anything; the network may only look.

- **Kernel API** (`kernel/server.py`, port 1337): it binds `127.0.0.1` by default; when exposed
  (`VIBEOS_HOST=0.0.0.0`, `KAIROS_EXPOSE_UI=1` or `dev/rig/testrig/expose-ui.sh`), every route
  that changes the machine (install, format, power, settings, model downloads, file-system writes)
  must refuse a non-loopback caller without the per-boot token (`/run/kairos/token`,
  `X-Kairos-Token`). A way around the token, or a machine-changing route that is not guarded, is a
  vulnerability.
- **SSH**: root is key-only (`iso/secrets/authorized_keys` at build time, no password
  authentication). The baked local-console passwords are public by design and must never work
  remotely.
- **Generated-app sandbox**: apps are LLM-written HTML mounted in sandboxed iframes with the
  `vibe.*` bridge (`shell/shell.js`, `BRIDGE_SRC`). An app escaping its sandbox, reaching the
  kernel's guarded routes without the shell's confirmation step (`vibe.fs.format()`,
  `vibe.system.upgrade()`, `vibe.hw.power.*`), or reading another app's storage is in scope.
- **Virtual file system** (`kernel/vfs.py`): `/system` is read-only, `/data` is confined to its
  backing directory — no `..`, no symlink escape.
- **Installer** (`kairos-install`, R48) and the first-boot resize: writing to a disk the user did
  not choose.
- **Generated games** (`/api/game`): they run as the kiosk user on the kiosk display with a syntax
  gate and a launch test; privilege escalation from a generated program is in scope.

## What is out of scope

- The *content* a local model produces (refusals, hallucinated web pages, images). The project
  is an 18+ appliance with no content filter by design; the only hard rule is in
  `CODE_OF_CONDUCT.md`.
- Vulnerabilities in the upstream engines (llama.cpp, stable-diffusion.cpp, acestep.cpp, kiwix)
  — report them upstream; a pin bump here is welcome afterwards.
- Physical access to a booted stick (the root file system is not encrypted; this is a known
  property, not a bug).
- Anything that needs the attacker to already be root on the appliance.

## Hygiene for contributors

Never commit keys, tokens, WiFi credentials (`iso/secrets/`), the rig's SSH key
(`dev/rig/testrig/id_kairos`) or model weights. See `CONTRIBUTING.md`.
