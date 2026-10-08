#!/usr/bin/env python3
"""Local evaluation pipeline for the lab-works skill.

The pipeline keeps agent-result formats compatible with Anthropic's
skill-creator tooling. It deliberately does not invent an agent executor:
Zed agent sessions must be run independently and their transcript/output must
be copied into the run directory created by ``prepare``.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".skill-eval" / "skill-creator"
WORKSPACE = ROOT / ".skill-eval" / "workspace"
EVALS_PATH = ROOT / "evals" / "evals.json"
UPSTREAM_PATH = ROOT / ".skill-eval" / "upstream.json"
UPSTREAM_CHECKOUT = ROOT / ".skill-eval" / "anthropic-skills"
SKILL_PATH = ROOT


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {error}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tool_path(*parts: str) -> Path:
    path = TOOLS.joinpath(*parts)
    if not path.exists():
        fail("upstream skill-creator tooling is absent; run `py .skill-eval/pipeline.py bootstrap`")
    return path


def validate_evals(evals: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if evals.get("skill_name") != "lab-works":
        errors.append("evals.skill_name must equal 'lab-works'")
    cases = evals.get("evals")
    if not isinstance(cases, list) or not cases:
        return errors + ["evals.evals must be a non-empty array"]
    ids: set[int] = set()
    for case in cases:
        label = f"eval {case.get('id', '?')}"
        case_id = case.get("id")
        if not isinstance(case_id, int) or case_id in ids:
            errors.append(f"{label}: id must be unique integer")
        else:
            ids.add(case_id)
        for field in ("prompt", "expected_output"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                errors.append(f"{label}: {field} must be a non-empty string")
        if not isinstance(case.get("files", []), list):
            errors.append(f"{label}: files must be an array")
        fixtures = case.get("fixtures", {})
        if not isinstance(fixtures, dict) or not all(
            isinstance(target, str) and isinstance(source, str) for target, source in fixtures.items()
        ):
            errors.append(f"{label}: fixtures must map target paths to source paths")
        elif not set(case.get("files", [])).issubset(fixtures):
            errors.append(f"{label}: every declared input file must have a fixture mapping")
        expectations = case.get("expectations", case.get("assertions", []))
        if not isinstance(expectations, list) or not expectations:
            errors.append(f"{label}: add at least one verifiable expectation")
        elif not all(isinstance(item, str) and item.strip() for item in expectations):
            errors.append(f"{label}: expectations must be non-empty strings")
    return errors


def local_skill_validation() -> list[str]:
    path = SKILL_PATH / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL)
    if not match:
        return ["SKILL.md must start with YAML frontmatter"]
    frontmatter = match.group(1)
    name = re.search(r"^name:\s*([^\s#]+)\s*$", frontmatter, re.MULTILINE)
    description = re.search(r"^description:\s*[\"']?(.*?)[\"']?\s*$", frontmatter, re.MULTILINE)
    errors = []
    if not name or name.group(1) != "lab-works":
        errors.append("SKILL.md frontmatter name must equal 'lab-works'")
    if not description or not description.group(1).strip():
        errors.append("SKILL.md frontmatter requires a non-empty description")
    if len(content.splitlines()) > 750:
        errors.append("SKILL.md exceeds the local 750-line review threshold; split stable detail into references")
    for script_name in re.findall(r"`(?:scripts/)?([A-Za-z0-9_.-]+\.(?:py|js|ps1))`", content):
        if not (ROOT / "scripts" / script_name).is_file():
            errors.append(f"SKILL.md references missing scripts/{script_name}")
    return errors


def run(command: list[str]) -> None:
    if command and command[0] == sys.executable and "-X" not in command:
        command = [sys.executable, "-X", "utf8", *command[1:]]
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def cmd_check(_: argparse.Namespace) -> None:
    evals = read_json(EVALS_PATH)
    errors = validate_evals(evals) + local_skill_validation()
    quick_validate = tool_path("scripts", "quick_validate.py")
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(quick_validate), str(SKILL_PATH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as error:
        errors.append(f"could not execute upstream quick validator: {error}")
    else:
        if result.returncode:
            errors.append("upstream quick_validate failed: " + result.stdout.strip() + result.stderr.strip())
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print("Skill structure and eval catalog are valid.")


def evaluation_name(case: dict[str, Any]) -> str:
    prompt = re.sub(r"[^a-z0-9]+", "-", case["prompt"].lower()).strip("-")
    return f"eval-{case['id']:02d}-{prompt[:48] or 'scenario'}"


def copy_inputs(case: dict[str, Any], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    fixtures = case.get("fixtures", {})
    for relative_name in case.get("files", []):
        source_name = fixtures.get(relative_name, relative_name)
        source = ROOT / source_name
        if not source.exists():
            fail(f"fixture for `{relative_name}` is missing: {source_name}")
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def cmd_prepare(args: argparse.Namespace) -> None:
    cmd_check(args)
    iteration = args.iteration or datetime.now(timezone.utc).strftime("iteration-%Y%m%dT%H%M%SZ")
    if not iteration.startswith("iteration-"):
        iteration = "iteration-" + iteration
    destination = WORKSPACE / iteration
    if destination.exists():
        fail(f"iteration already exists: {destination.relative_to(ROOT)}")
    destination.mkdir(parents=True)
    shutil.copytree(SKILL_PATH, destination / "skill-snapshot", ignore=shutil.ignore_patterns(".git", ".skill-eval", "__pycache__", "*.pyc"))
    evals = read_json(EVALS_PATH)
    manifest = {
        "created_at": utc_now(),
        "skill_name": "lab-works",
        "skill_commit": git_revision(),
        "skill_snapshot": "skill-snapshot",
        "configurations": {
            "with_skill": "Use skill-snapshot/SKILL.md and bundled resources.",
            "without_skill": "Do not load lab-works or any snapshot of it.",
        },
        "runner_contract": "Write the agent's final response to outputs/response.md and produced files to outputs/. Then run grade and review.",
    }
    write_json(destination / "manifest.json", manifest)
    for case in evals["evals"]:
        eval_dir = destination / evaluation_name(case)
        metadata = {
            "eval_id": case["id"],
            "eval_name": eval_dir.name,
            "prompt": case["prompt"],
            "expected_output": case["expected_output"],
            "assertions": case.get("expectations", case.get("assertions", [])),
            "input_files": case.get("files", []),
        }
        write_json(eval_dir / "eval_metadata.json", metadata)
        copy_inputs(case, eval_dir / "inputs")
        for configuration in ("with_skill", "without_skill"):
            run_dir = eval_dir / configuration / "run-1"
            outputs = run_dir / "outputs"
            outputs.mkdir(parents=True)
            (outputs / "README.md").write_text(
                "Place the agent final response in response.md and all generated artifacts in this directory.\n",
                encoding="utf-8",
            )
            write_json(run_dir / "task.json", {
                **metadata,
                "configuration": configuration,
                "skill_path": "../../../../skill-snapshot" if configuration == "with_skill" else None,
                "inputs_path": "../../inputs",
            })
    print(f"Prepared {len(evals['evals'])} paired scenarios: {destination.relative_to(ROOT)}")
    print("Run each task independently, save its response/artifacts into outputs/, then use `grade`.")


def git_revision() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def text_for_run(run_dir: Path) -> str:
    outputs = run_dir / "outputs"
    chunks: list[str] = []
    for path in sorted(outputs.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".py", ".js", ".ps1"}:
            chunks.append(f"\n--- {path.relative_to(outputs)} ---\n{path.read_text(encoding='utf-8', errors='replace')}")
    return "".join(chunks)


def cmd_grade(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    graded = 0
    for metadata_path in sorted(iteration.glob("eval-*/eval_metadata.json")):
        metadata = read_json(metadata_path)
        for run_dir in sorted(metadata_path.parent.glob("*/run-*")):
            text = text_for_run(run_dir)
            expectations = []
            for assertion in metadata["assertions"]:
                expectations.append({
                    "text": assertion,
                    "passed": False,
                    "evidence": "Requires independent grader or human review; automated text assertions are intentionally not used for semantic agent behavior.",
                })
            grading = {
                "expectations": expectations,
                "summary": {"passed": 0, "failed": len(expectations), "total": len(expectations), "pass_rate": 0.0},
                "execution_metrics": {"total_tool_calls": 0, "errors_encountered": 0, "output_chars": len(text)},
                "user_notes_summary": {"uncertainties": [], "needs_review": ["Replace placeholder grades after reviewing the agent output."], "workarounds": []},
            }
            write_json(run_dir / "grading.json", grading)
            graded += 1
    print(f"Created reviewer-ready grading templates for {graded} runs in {iteration.relative_to(ROOT)}.")
    print("Update grading.json with an independent reviewer, then run `benchmark` and `review`.")


def require_iteration(name: str | None) -> Path:
    if not name:
        fail("--iteration is required")
    assert name is not None
    path = WORKSPACE / name
    if not path.is_dir():
        fail(f"unknown iteration: {path.relative_to(ROOT)}")
    return path


def cmd_benchmark(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    aggregator = tool_path("scripts", "aggregate_benchmark.py")
    run([sys.executable, str(aggregator), str(iteration), "--skill-name", "lab-works", "--skill-path", str(SKILL_PATH)])


def cmd_review(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    viewer = tool_path("eval-viewer", "generate_review.py")
    command = [sys.executable, str(viewer), str(iteration), "--skill-name", "lab-works", "--benchmark", str(iteration / "benchmark.json"), "--static", str(iteration / "review.html")]
    if args.previous:
        previous = require_iteration(args.previous)
        command.extend(["--previous-workspace", str(previous)])
    run(command)


def cmd_test(_: argparse.Namespace) -> None:
    run([sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_*.py"])
    run(["node", "--test", "scripts/tests/test_cdp.js"])


def cmd_bootstrap(_: argparse.Namespace) -> None:
    upstream = read_json(UPSTREAM_PATH)
    commit = upstream.get("commit")
    if not isinstance(commit, str) or not commit:
        fail(".skill-eval/upstream.json requires a pinned upstream commit")
    source = UPSTREAM_CHECKOUT / "skills" / "skill-creator"
    if not source.is_dir():
        fail(
            "pinned upstream checkout is absent; clone https://github.com/anthropics/skills.git "
            "into .skill-eval/anthropic-skills and check out the commit from upstream.json"
        )
    revision = subprocess.run(
        ["git", "-C", str(UPSTREAM_CHECKOUT), "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode or revision.stdout.strip() != commit:
        fail(f"pinned upstream checkout must be at {commit}")
    if TOOLS.exists():
        shutil.rmtree(TOOLS)
    shutil.copytree(source, TOOLS)
    print(f"Bootstrapped upstream tooling from {source.relative_to(ROOT)} at {commit}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Evaluate and improve the lab-works skill locally")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap", help="copy the pinned skill-creator checkout into .skill-eval").set_defaults(func=cmd_bootstrap)
    commands.add_parser("check", help="validate skill metadata, bundled references and eval catalog").set_defaults(func=cmd_check)
    prepare = commands.add_parser("prepare", help="create a timestamped paired-run iteration")
    prepare.add_argument("--iteration", help="name after iteration-; defaults to a UTC timestamp")
    prepare.set_defaults(func=cmd_prepare)
    grade = commands.add_parser("grade", help="create grading templates for a completed iteration")
    grade.add_argument("--iteration", required=True)
    grade.set_defaults(func=cmd_grade)
    benchmark = commands.add_parser("benchmark", help="aggregate completed grades with upstream tooling")
    benchmark.add_argument("--iteration", required=True)
    benchmark.set_defaults(func=cmd_benchmark)
    review = commands.add_parser("review", help="generate a static upstream review page")
    review.add_argument("--iteration", required=True)
    review.add_argument("--previous", help="previous iteration name for comparison")
    review.set_defaults(func=cmd_review)
    commands.add_parser("test", help="run deterministic bundled-script tests").set_defaults(func=cmd_test)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    args.func(args)
