# Implementation plan: state, quality, and portability

**Design:** `2026-10-01-state-quality-portability-design.md`  
**Execution target:** local global skill, then `kert25/lab-works-skill/main`

## 1. Establish a synchronized baseline

1. Inspect the local global skill's changed files and test layout.
2. Clone `https://github.com/kert25/lab-works-skill` into a disposable ASCII-only
   workspace.
3. Compare `SKILL.md`, runner, CDP harness, and tests between local and remote.
4. Do not overwrite remote changes. If baseline differs, apply the design to the
   remote version and reconcile only relevant overlapping edits.

**Validation:** record the compared revisions and confirm no unrelated file is
included in the implementation change set.

## 2. Extend `lab_runner.py` state validation

1. Add helpers for normalized relative-path validation, duplicate detection,
   command scanning, logical bundled-tool resolution, and verification object
   validation.
2. Keep current runner actions compatible with legacy state.
3. Add `validate-state` to the CLI choices and make it validate only root state
   and the selected lab; it must not require report outputs.
4. Make failure messages identify the field and concrete invalid value.
5. Preserve the existing report-building and report-reference checks.

**Validation:** legacy fixture passes existing actions; `validate-state` accepts
valid state and rejects invalid types, absolute paths, duplicates, malformed
logical references, and invalid verification data.

## 3. Emit an atomic quality-gate record

1. Create a helper that forms project-relative final-document paths.
2. Add atomic UTF-8 JSON writing through a temporary file in the lab directory.
3. After all current quality checks pass, write `quality_gate.json` using schema
   version 1 and print its path.
4. Ensure an exception in any gate check occurs before the write, leaving an
   existing record unchanged.

**Validation:** a fixture gate produces parseable JSON with UTC timestamp,
expected check names, and only relative final-document paths; an intentionally
failed gate preserves a sentinel prior record.

## 4. Improve CDP profile management

1. Thread an optional `--profile-dir` value through `launch`, `quit`, and
   `reset`, retaining the current temporary directory as default.
2. Extract profile-path selection and deletion retry logic into focused helpers.
3. Change reset cleanup to retry deletion three times with bounded pauses.
4. Make output distinguish removed, already absent, and blocked profiles.
5. Keep reset non-destructive outside its selected profile path.

**Validation:** Node syntax check; argument/profile selection tests; filesystem
stub tests for success after retry, absent profile, and persistent failure.
No test depends on installed Edge.

## 5. Update skill documentation

1. Extend `_lab_state.json` documentation with `tooling` and `verification`.
2. Document the `quality_gate.json` schema, lifecycle, and backward
   compatibility.
3. Add `validate-state` to the state-update and quality-gate workflows.
4. Replace any user-specific tool command examples with relative paths,
   `bundled:` references, or command templates.
5. Document `cdp.js --profile-dir` and reset retry behaviour.

**Validation:** cross-check every new documented field and command against the
runner and CDP implementation.

## 6. Add and run tests locally

1. Add focused Python tests next to existing runner tests.
2. Add focused Node tests or testable helper coverage for CDP.
3. Run the complete Python test suite.
4. Run `node --check scripts/cdp.js`.
5. Run a fixture `quality-gate`, inspect its generated JSON, and run the
   existing report-related tests.

**Validation:** all tests pass. Any pre-existing unrelated failure is recorded
separately and does not get hidden or changed.

## 7. Synchronize GitHub repository

1. Apply the completed local changes and the two documents to the fresh remote
   clone.
2. Run the same test commands in the clone.
3. Inspect the final diff and status, ensuring only the planned skill files are
   included.
4. Commit using the repository's existing style; otherwise use:
   `Add portable quality-gate validation`.
5. Push to `main` and verify the resulting remote revision is available.

**Validation:** push exits successfully; `git status --short` is clean after
commit; the remote repository shows the new commit.

## Execution boundaries

- No student project files are modified by this implementation.
- No absolute personal paths are added to code, examples, state, or tests.
- Existing laboratory state remains usable; portability enforcement is opt-in
  through `validate-state` until labs are migrated.
- A failed quality gate never replaces a previous audit record.
