# State, quality, and portability improvements for `lab-works`

**Date:** 2026-10-01  
**Status:** Proposed

## 1. Purpose

Improve the universal reliability of the `lab-works` skill without tying it to
a course, technology stack, report type, operating-system user profile, or
specific student project.

The changes address three general problems:

1. A state file currently lists planned and created artifacts but does not
   preserve an auditable fact of which checks succeeded.
2. Commands saved in state can accidentally contain user-specific absolute
   paths, making a laboratory non-reproducible on another machine.
3. CDP cleanup on Windows can fail transiently because Edge releases its
   temporary profile asynchronously.

The design intentionally excludes web-server orchestration, form automation,
and other runtime-specific workflows.

## 2. Goals

- Preserve compatibility with existing `_context.json` and `_lab_state.json`.
- Make state validation available as a fast, standalone runner operation.
- Make a successful quality gate produce an inspectable, project-local record.
- Keep tool references portable across users and machines.
- Make CDP profile cleanup retry transient Windows locking failures.
- Document the contract precisely enough for future agents and users to
  diagnose failures without inspecting implementation code.

## 3. Non-goals

- Replacing `artifacts` with a new incompatible format.
- Running arbitrary commands stored in `_lab_state.json`.
- Treating an old quality record as proof that current source files are valid.
- Building an application server, browser test framework, or course-specific
  submission workflow.
- Changing report layout, title-page generation, or the report content format.

## 4. Compatibility and migration

All existing state files remain usable without modification by the existing
`build-report`, `verify-report`, and `quality-gate` actions. The new
`validate-state` action intentionally reports portability defects in legacy
state (such as absolute paths in commands) so they can be migrated explicitly.

- `artifacts` remains a list of project-relative file or directory paths.
- Existing actions `build-report`, `verify-report`, and `quality-gate` retain
  their present behaviours before the new record is written.
- New fields are optional; no existing laboratory needs to be migrated before
  it can be checked.
- A new `quality_gate.json` is produced only after a successful quality gate.
  A failed gate never overwrites an earlier successful record.

For a new or updated lab, the workflow is:

1. Add each artifact to `artifacts` as it is created.
2. Run `validate-state` before report work or whenever state is edited.
3. Run the usual verification sequence.
4. Run `quality-gate`; on success it writes `quality_gate.json`.
5. Add `quality_gate.json` to `artifacts` when a durable audit trail is wanted.

## 5. State contract

### 5.1 `commands`

For newly maintained state, `commands` must be an object whose values are
non-empty strings and must not contain absolute filesystem paths. The
`validate-state` action rejects paths such as `C:\\Users\\name\\...` or
`/home/name/...`, preventing them from silently becoming part of portable
project state. Legacy runner actions retain their present validation boundary
until a project is deliberately checked with `validate-state`.

`commands` is descriptive and reproducible documentation. The runner does not
execute its values.

Commands requiring substitutions remain in `command_templates`, as already
defined by the skill.

### 5.2 `tooling`

A lab may contain a `tooling` object mapping descriptive names to logical tool
references:

```json
"tooling": {
  "report_inspector": "bundled:inspect_docx",
  "pdf_text_extractor": "bundled:pdf_text"
}
```

Initially, the only valid reference syntax is `bundled:<tool-name>`. Supported
tool names map to scripts shipped in the skill `scripts/` directory. The
runner validates each reference and reports its resolved bundled script. It
does not run arbitrary state-provided commands.

This gives project state a portable way to name shared tools while retaining
human-readable commands as optional documentation.

### 5.3 `verification`

A lab may store a summary of the most recently recorded verification:

```json
"verification": {
  "status": "passed",
  "checked_at": "2026-10-01T12:34:56Z",
  "checks": ["content_references", "artifacts", "report_docx", "report_pdf", "text_docx"],
  "warnings": []
}
```

The runner validates this object's structure when it exists. It does not
consider it current proof of validity: the quality gate always checks files
again.

## 6. `quality_gate.json` contract

A successful quality gate writes a UTF-8 JSON file to the selected laboratory
directory, named `quality_gate.json`:

```json
{
  "schema_version": 1,
  "lab": 7,
  "report_number": 15,
  "status": "passed",
  "checked_at": "2026-10-01T12:34:56Z",
  "checks": [
    "content_references",
    "artifacts",
    "report_docx",
    "report_pdf",
    "text_docx"
  ],
  "files": [
    "ЛР7/Отчет_ЛР7.docx",
    "ЛР7/Отчет_ЛР7.pdf",
    "ЛР7/text.docx"
  ]
}
```

Rules:

- `schema_version` is `1` for this design.
- `checked_at` is UTC in ISO 8601 format ending in `Z`.
- `files` contains paths relative to the project root.
- The record is written atomically through a temporary file in the same
  directory followed by replacement.
- The final documents, not every source artifact, are listed in `files`.
- The record is written only after all gate checks pass.

## 7. Runner design

### 7.1 `validate-state`

Add the action:

```text
py lab_runner.py --root . --lab N --action validate-state
```

It validates the complete root state and selected lab without reading report
content or requiring output files. Checks include:

- root is an object with `labs` as an object;
- selected lab exists, has a valid status and non-empty theme;
- `directory`, `methodical_guide`, artifact paths, optional output paths and
  tool references are valid relative paths or valid logical references;
- `artifacts` is a non-empty list when provided;
- artifact entries have no duplicates after path normalization;
- `commands`, `command_templates`, `tooling`, and `verification` have valid
  types and values;
- commands contain no absolute paths.

The current report-build path keeps its existing required-file checks.

### 7.2 Quality gate

`quality-gate` keeps content-reference, artifact, DOCX, PDF-signature, and
`text.docx` checks. On success it creates the record described in section 6
and prints its path. On failure it writes no record.

The existing `verify-report` remains useful before final documents exist;
missing artifact errors should list every missing path and state that artifacts
must be added as they are created.

### 7.3 Tool resolution

The runner locates bundled scripts relative to `lab_runner.py`, not the user's
home directory. It validates a `bundled:` name against an explicit allow-list:
`inspect_docx`, `pdf_text`, `docx2pdf`, `cdp`, and `shot`.

Resolution is informational in this release. Execution is deliberately not
added, avoiding a security boundary change.

## 8. CDP cleanup design

`cdp.js` gains:

```text
--profile-dir <path>
```

for `launch`, `quit`, and `reset`. Omission preserves the current temporary
profile location. The selected path is passed to Edge on launch and targeted by
cleanup operations.

`reset` will:

1. attempt to close the CDP browser;
2. attempt recursive profile deletion up to three times;
3. wait for a short bounded delay between failed attempts;
4. print whether cleanup succeeded, was unnecessary, or remains blocked;
5. return non-zero only if the profile still cannot be removed after all
   attempts.

The retry is intentionally narrow: it handles transient process/file-handle
release, without deleting unrelated profiles or terminating non-CDP Edge
instances.

## 9. Documentation updates

`SKILL.md` will document:

- `validate-state` before and after state edits;
- the distinction between planned state and actual `quality_gate.json`;
- `verification`, `tooling`, command portability, and backwards compatibility;
- adding a quality-gate record to artifacts for a durable lab audit trail;
- CDP's `--profile-dir` and bounded reset retries.

Examples use only relative paths and logical `bundled:` references.

## 10. Test strategy

Python tests for `lab_runner.py` cover:

- legacy minimal state remains accepted;
- valid and invalid `commands`, `tooling`, and `verification` structures;
- rejection of absolute paths and duplicate artifacts;
- `validate-state` requires no report outputs;
- a passing quality gate writes valid `quality_gate.json` with project-relative
  paths;
- a failing quality gate leaves an existing record untouched.

Node tests or focused testable helpers for `cdp.js` cover argument parsing and
profile-directory selection. Profile deletion retry logic is separated into a
small helper where practical and tested using injected filesystem operations;
Edge itself is not required in unit tests.

Final validation runs the Python test suite, Node syntax check, and a fixture
quality gate. The same tests run after synchronizing the remote repository.

## 11. Synchronization strategy

The local global skill is the implementation workspace. After all local tests
pass, the same files are applied to a fresh clone of
`https://github.com/kert25/lab-works-skill`.

The remote clone is tested before commit. The commit includes the implementation,
documentation, tests, and this specification. It is pushed to `main` only after
the remote working tree contains no unrelated changes and the push succeeds.
