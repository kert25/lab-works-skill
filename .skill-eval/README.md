# Local evaluation pipeline for `lab-works`

This directory makes the skill-improvement loop reproducible inside this repository.
It vendors a pinned local copy of Anthropic's `skill-creator` only for execution;
the copy and all generated workspaces are ignored by Git. The source repository remains
linked through the project remote:

```text
origin  https://github.com/kert25/lab-works-skill.git
```

## One-time setup

Install the Python dependencies and retrieve the exact upstream revision
recorded in `.skill-eval/upstream.json`:

```sh
python -m pip install --user -r .skill-eval/requirements.txt
git clone https://github.com/anthropics/skills.git .skill-eval/anthropic-skills
git -C .skill-eval/anthropic-skills checkout --detach 683bc88e56f3e09ba94f7055977f3d3aa499f202
python .skill-eval/pipeline.py bootstrap
```

The commands use `python`, which is available on this workstation. If the
Python launcher is installed, `py` is an equivalent Windows alternative.

`bootstrap` verifies that local checkout and copies its `skill-creator` into
`.skill-eval/skill-creator/`, so the validator, benchmark, and review scripts
are local and pinned. The checkout and copy are ignored by Git.

## Deterministic checks

Run these before and after a skill change:

```sh
python .skill-eval/pipeline.py check
python .skill-eval/pipeline.py test
```

`check` validates the frontmatter, referenced bundled scripts, and every eval
case. `test` runs the existing Python and Node tests for the deterministic
report/screenshot tooling.

## Iterative agent evaluation

1. Snapshot the current version and create matching with-skill/baseline tasks:

   ```sh
   python .skill-eval/pipeline.py prepare --iteration iteration-001
   ```

2. For each `<eval>/with_skill/run-1/task.json`, run an isolated Zed agent with
   the snapshot skill path. For the matching `without_skill` task, run the same
   prompt without the skill. Put the final agent response in
   `outputs/response.md` and its artifacts in the same `outputs/` directory.
   Never let an eval agent write into the repository root.

3. Create reviewer-ready grading files, review each expectation independently,
   and update the `passed`/`evidence` fields in `grading.json`:

   ```sh
   python .skill-eval/pipeline.py grade --iteration iteration-001
   python .skill-eval/pipeline.py benchmark --iteration iteration-001
   python .skill-eval/pipeline.py review --iteration iteration-001
   ```

   The last command writes a self-contained
   `.skill-eval/workspace/iteration-001/review.html`, using the upstream
   `eval-viewer/generate_review.py`. Open it locally to compare outputs and
   inspect the benchmark.

4. Improve `SKILL.md` or bundled scripts only from concrete failures, then
   repeat with a new iteration. To compare a new iteration to the old viewer:

   ```sh
   python .skill-eval/pipeline.py review --iteration iteration-002 --previous iteration-001
   ```

## Scope and limitation

The repository can automatically validate the skill, create immutable snapshots,
prepare paired tasks, aggregate completed grades, render review UI, and test
bundled code. Zed does not expose a local non-interactive API for this pipeline
to launch independent agent sessions with and without a skill, so those runs and
their semantic grading remain deliberately explicit. This prevents false claims
that a baseline was executed when it was not.
