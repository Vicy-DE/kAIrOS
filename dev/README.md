# dev/ — everything an outside developer needs to build and test kAIrOS

Until 2026-10-10 the test rig, the test-container recipes and the helper scripts lived next to
the repository on the owner's laptop. This folder is their path-independent, in-repository copy.

| folder | what | guide |
|---|---|---|
| [`docker/`](docker/) | the kernel-test container (Debian trixie + aiohttp + quickjs) and `run-suite.sh`, the one command that runs the whole mock-engine test suite without a GPU; `run-js-checks.sh` for the shell | [docs/how-to/TESTING.md](../docs/how-to/TESTING.md) |
| [`rig/`](rig/) | the QEMU/KVM VM test rig (optional VFIO GPU passthrough), the guest sync, the GPU rebind helpers, the desktop-in-a-window kernel, the stick-log collector | [docs/how-to/VM-TEST-RIG.md](../docs/how-to/VM-TEST-RIG.md) |
| [`issues/`](issues/) | the seed issues (open work items, known gaps) and the script that creates them on GitHub | [issues/README.md](issues/README.md) |

Quick start, no GPU, no models:

```bash
bash dev/docker/build.sh          # once: the kernel-test image (~1.2 GB, needs network)
bash dev/docker/run-suite.sh      # the suite: 9 test files, mock engines, ~10 min
```

Scratch state (`dev/work/`, `dev/rig/work/`, `dev/rig/.env`, the rig's SSH key) is gitignored.
