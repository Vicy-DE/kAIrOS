# dev/issues — seed issues

`seed-issues.json` lists the open work items and known gaps of the project as GitHub issues
(title, body, labels), collected 2026-10-10 from the owner's work queue and from making the
repository buildable for outsiders, brought up to date on 2026-10-14 (24 issues, 14 labels); one VM-rig issue added 2026-10-15 (25); the licence and Muse-redistribution issues removed 2026-10-17 once decided (23). `create-issues.sh` creates the labels and the issues through
the GitHub REST API; it skips titles that already exist, so it can be re-run after the file grows.

```bash
bash dev/issues/create-issues.sh --dry-run             # preview, no token
GITHUB_TOKEN=… bash dev/issues/create-issues.sh         # create on Vicy-DE/kAIrOS (GITHUB_REPO overrides)
```

Adding an item: append to `issues` (keep the body plain Markdown; name the requirement IDs and
the documents that hold the evidence; never paste secrets, personal paths or generated 18+
material) and, if it needs a new label, to `labels`.
