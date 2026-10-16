# Contributing to kAIrOS

kAIrOS is a pre-release appliance OS built by one developer, verified on one laptop. Outside
contributions are welcome — the most useful ones right now are test reports from other
hardware, fixes with a test, and the seed issues labelled `help wanted` / `good first issue`.

Read first: [README.md](README.md) (what it is, status, requirements),
[docs/how-to/GETTING-STARTED.md](docs/how-to/GETTING-STARTED.md) (pick a path),
[docs/how-to/TESTING.md](docs/how-to/TESTING.md) (what "verified" means here).

## Setup

```bash
git clone https://github.com/Vicy-DE/kAIrOS.git && cd kAIrOS
bash dev/docker/build.sh          # the kernel-test image (Debian trixie + aiohttp + quickjs), once
bash dev/docker/run-suite.sh      # the mock-engine suite, no GPU, no network, ~10 min
```

That is enough for kernel, shell, manifest, docs and test work. Engines, models, the image and
the VM rig are optional layers described in [docs/how-to/BUILDING.md](docs/how-to/BUILDING.md)
and [docs/how-to/VM-TEST-RIG.md](docs/how-to/VM-TEST-RIG.md).

## Branches and pull requests

- Work on a branch; open a pull request against `main`. One topic per PR.
- Fill in the PR template — it is the checklist the maintainer uses.
- The maintainer merges; there is no team and no SLA. Small, well-tested PRs go first.
- Before a large change (new subsystem, model swap, window architecture) open an issue and get a
  nod — several of those are already queued with a decision pending (`dev/issues/seed-issues.json`).

## Tests required

Every PR runs level 0 (`bash dev/docker/run-suite.sh`) and, for `shell/` changes,
`bash dev/docker/run-js-checks.sh`. Paste the per-file totals into the PR.

| change touches | also required |
|---|---|
| `kernel/` engine handling, `manifests/`, `scripts/install-engines.sh` | a GPU smoke on real hardware (level 1) or a clear statement that you could not run it |
| `iso/`, `scripts/splash/`, the boot chain | a VM test with the boot-log verdict (level 2), ideally a stick boot (level 3) |
| a decision between alternatives (model, engine flag, UI tech) | a measurement in a dated report under `docs/` |

The culture, in one line: the mock suite proves the logic, the real VM/stick run proves the
machine, and decisions are backed by benchmarks — see
[docs/how-to/TESTING.md](docs/how-to/TESTING.md) for the levels and
[docs/README.md](docs/README.md) for the reports that set the precedent.

A failing test is fixed in the product unless the test itself is wrong; never silence it.

## Requirement IDs

Owner requests are numbered requirements in `docs/REQUIREMENTS.md` (R1 …). Tests name the IDs
they cover (`[R66, R5]` in their result line) and `docs/PROGRESS.md` is generated from that.
When your change implements or alters a requirement, name the ID in the PR and in the test's
`reqs`; when it changes a requirement's state, add a status note under that requirement
(do not rewrite the original request — it is the owner's wording).

## Docs

- Non-trivial work gets a dated report `docs/<TOPIC>-<YYYY-MM-DD>.md` (what was done, how it
  was verified, the numbers) and a line in `docs/README.md`.
- The app-facing API (`docs/API.md`, `docs/api/*.md`, `docs/wiki/*.md`) is read by the coder LLM
  at generation time — keep it exact; a wrong sentence there produces broken apps.
- Scripts and recipes: paths come from documented env vars with defaults, never from a personal
  home directory. The in-repo rig (`dev/rig/.env.example`) shows the pattern.

## What must never be committed

- private keys, tokens, passwords, WiFi credentials (`iso/secrets/` is gitignored for a reason),
  the rig's `dev/rig/testrig/id_kairos`
- model weights, ZIMs, appliance images (`*.img`, `*.img.gz`), VM overlays (`*.qcow2`)
- generated adult material of any kind, and nothing that sexualises minors — ever
  (`CODE_OF_CONDUCT.md`)
- `*.bak*` edit backups (the owner's agents make them; gitignored), `__pycache__`, generated apps
  and caches from a running kernel
- another person's absolute paths or hostnames

If you spot any of these in the history, report it privately (`SECURITY.md`).

## The commit rule

Commits, pull requests, templates and documents carry **no AI or assistant attribution**:
no `Co-Authored-By:` trailer naming a model or tool, no "Generated with …" line, no "written by
Claude/ChatGPT/…" note. The committing account is the only author named. This is the owner's
rule and it is absolute; a PR that violates it is squashed or rejected. Describe *what* changed
and *why*; the tools you used are not part of the record.

## Licence

No licence has been chosen yet (tracked as a seed issue). By contributing you agree that your
contribution may be released under the licence the owner picks for the repository. If that is
not acceptable to you, wait for the decision before contributing code.
