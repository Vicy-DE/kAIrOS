# Pull request

## What and why

<!-- One paragraph. Name the requirement IDs (R…) or the issue this closes. -->

## Verification

- [ ] `bash dev/docker/run-suite.sh` passes (paste the per-file totals below)
- [ ] `bash dev/docker/run-js-checks.sh` passes (if `shell/` changed)
- [ ] GPU smoke / VM test run, when the change touches the kernel's engine handling, the manifests, `iso/` or `scripts/` (say which level of `docs/how-to/TESTING.md`, on which hardware)
- [ ] a decision with alternatives is backed by a measurement (link the report under `docs/`)

```
<suite totals>
```

## Docs

- [ ] `docs/REQUIREMENTS.md` status note updated, if a requirement's state changed
- [ ] a dated report under `docs/` for non-trivial work, listed in `docs/README.md`
- [ ] `docs/API.md` / `docs/api/` / `docs/wiki/` updated, if the app-facing API changed (the coder LLM reads them)

## Hygiene

- [ ] no secrets, keys, tokens, WiFi credentials, model weights, images (`*.img`, `*.qcow2`), generated adult material or `*.bak*` files in the diff
- [ ] no personal absolute paths; new scripts take paths from env vars with defaults
- [ ] commits carry **no AI/assistant attribution** — no `Co-Authored-By`, no "Generated with …" trailer or line; the committing account is the only author named (project rule, `CONTRIBUTING.md`)
