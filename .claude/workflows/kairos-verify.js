export const meta = {
  name: 'kairos-verify',
  description: 'Run the kAIrOS test suite (mock engines), regenerate PROGRESS.md, repair failures',
  whenToUse: 'After implementing or changing kAIrOS features — verifies every requirement and refreshes the progress doc.',
  phases: [
    { title: 'Test', detail: 'run tests/run-tests.sh in the testrig container' },
    { title: 'Repair', detail: 'one agent per failing test' },
    { title: 'Docs', detail: 'verify PROGRESS/REQUIREMENTS consistency' },
  ],
}

// the suite needs python3-aiohttp + trixie's quickjs: run it in a container. RUN is the owner's
// out-of-repo rig (pod/ lives next to repo/ on the reference laptop, cwd = pod/); RUN_INREPO is the
// equivalent for a plain checkout (dev/docker/run-suite.sh: kernel-test image, --network none, the
// data mounts from dev/rig/.env — docs/how-to/TESTING.md). Swap the two when pod/ is not there.
const RUN = "docker compose run --rm --entrypoint bash dev -lc 'cd /images/repo/vibeOS-local && bash tests/run-tests.sh'"
const RUN_INREPO = "bash dev/docker/run-suite.sh"   // eslint-disable-line no-unused-vars

const RESULTS_SCHEMA = {
  type: 'object',
  properties: {
    passed: { type: 'number' },
    failed: { type: 'number' },
    skipped: { type: 'number' },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          test: { type: 'string' },
          reqs: { type: 'array', items: { type: 'string' } },
          detail: { type: 'string' },
        },
        required: ['test', 'detail'],
      },
    },
  },
  required: ['passed', 'failed', 'skipped', 'failures'],
}

const FIX_SCHEMA = {
  type: 'object',
  properties: {
    fixed: { type: 'boolean' },
    summary: { type: 'string' },
    filesChanged: { type: 'array', items: { type: 'string' } },
  },
  required: ['fixed', 'summary'],
}

phase('Test')
const run1 = await agent(
  `Run the kAIrOS verification suite: \`${RUN}\`. ` +
  `It boots the kernel against mock engines and tests every requirement (the R-numbered map in ` +
  `docs/REQUIREMENTS.md of this repository). After it finishes, read ` +
  `tests/results.json (and the other tests/results-*.json files) and report the counts and every failing ` +
  `test with its requirement ids and failure detail. If the suite cannot even start ` +
  `(e.g. python3-aiohttp missing), report that as a single failure named "suite-boot".`,
  { label: 'run-suite', schema: RESULTS_SCHEMA }
)

let repairs = []
if (run1 && run1.failures.length > 0) {
  phase('Repair')
  repairs = await parallel(run1.failures.map((f) => () =>
    agent(
      `kAIrOS test "${f.test}" (requirements ${f.reqs ? f.reqs.join(', ') : '?'}) failed: ${f.detail}. ` +
      `Project: this repository (kernel/, shell/, tests/). Diagnose and FIX the ` +
      `product code (or the test, only if the test itself is wrong). Then re-run just the suite ` +
      `(\`${RUN}\`) to confirm your test passes. Keep changes minimal and idiomatic.`,
      { label: `fix:${f.test}`, phase: 'Repair', schema: FIX_SCHEMA }
    )
  ))
}

phase('Docs')
const docs = await agent(
  `In the repository root: run \`python3 scripts/update-progress.py\` so docs/PROGRESS.md reflects ` +
  `the latest tests/results.json. Then read docs/REQUIREMENTS.md and docs/PROGRESS.md and verify every ` +
  `requirement id R1–R26 appears in the progress table. Report a one-paragraph status summary suitable ` +
  `for the project owner.`,
  { label: 'refresh-progress' }
)

return {
  firstRun: run1,
  repairs: repairs.filter(Boolean),
  progressSummary: docs,
}
