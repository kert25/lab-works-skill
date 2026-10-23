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
import statistics
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


def select_cases(evals: dict[str, Any], selected_ids: list[int] | None) -> list[dict[str, Any]]:
    """Return all cases or a requested non-empty subset in catalog order."""
    cases = evals["evals"]
    if not selected_ids:
        return cases
    wanted = set(selected_ids)
    available = {case["id"] for case in cases}
    unknown = sorted(wanted - available)
    if unknown:
        fail(f"unknown eval id(s): {', '.join(map(str, unknown))}")
    return [case for case in cases if case["id"] in wanted]


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


def create_run_sandbox(inputs: Path, run_dir: Path) -> Path:
    """Give an executor a writable project-shaped copy, preserving fixtures."""
    sandbox = run_dir / "sandbox"
    shutil.copytree(inputs, sandbox)
    return sandbox


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
    cases = select_cases(evals, args.eval)
    manifest = {
        "created_at": utc_now(),
        "skill_name": "lab-works",
        "skill_commit": git_revision(),
        "skill_snapshot": "skill-snapshot",
        "configurations": {
            "with_skill": "Use skill-snapshot/SKILL.md and bundled resources.",
            "without_skill": "Do not load lab-works or any snapshot of it.",
        },
        "runner_contract": "Use the run sandbox as the working project. Write the final response to outputs/response.md, a transcript to transcript.md, and artifacts to outputs/. Then run grade and review.",
        "selected_evals": [case["id"] for case in cases],
        "runs_per_configuration": args.runs,
    }
    write_json(destination / "manifest.json", manifest)
    for case in cases:
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
            for run_number in range(1, args.runs + 1):
                run_dir = eval_dir / configuration / f"run-{run_number}"
                outputs = run_dir / "outputs"
                outputs.mkdir(parents=True)
                create_run_sandbox(eval_dir / "inputs", run_dir)
                (outputs / "README.md").write_text(
                    "Use ../sandbox as the writable project root. Place the final response in response.md and produced artifacts in this directory.\n",
                    encoding="utf-8",
                )
                write_json(run_dir / "task.json", {
                    **metadata,
                    "configuration": configuration,
                    "run_number": run_number,
                    "skill_path": "../../../../skill-snapshot" if configuration == "with_skill" else None,
                    "inputs_path": "../../inputs",
                    "sandbox_path": "sandbox",
                    "working_directory": "sandbox",
                    "outputs_path": "outputs",
                    "transcript_path": "transcript.md",
                    "timing_path": "timing.json",
                })
    print(f"Prepared {len(cases)} paired scenario(s), {args.runs} run(s) each: {destination.relative_to(ROOT)}")
    print("Run each task independently in its sandbox, save response/artifacts/transcript, then use `grade`.")


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


def run_directories(iteration: Path, selected_ids: list[int] | None) -> list[Path]:
    directories: list[Path] = []
    wanted = set(selected_ids or [])
    for metadata_path in sorted(iteration.glob("eval-*/eval_metadata.json")):
        metadata = read_json(metadata_path)
        if wanted and metadata["eval_id"] not in wanted:
            continue
        directories.extend(sorted(metadata_path.parent.glob("*/run-*")))
    return directories


def cmd_grade(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    graded = 0
    skipped = 0
    for run_dir in run_directories(iteration, args.eval):
        grading_path = run_dir / "grading.json"
        if grading_path.exists() and not args.overwrite:
            skipped += 1
            continue
        metadata = read_json(run_dir.parents[1] / "eval_metadata.json")
        text = text_for_run(run_dir)
        expectations = [{
            "text": assertion,
            "passed": False,
            "evidence": "Requires independent grader or human review; automated text assertions are intentionally not used for semantic agent behavior.",
        } for assertion in metadata["assertions"]]
        grading = {
            "expectations": expectations,
            "summary": {"passed": 0, "failed": len(expectations), "total": len(expectations), "pass_rate": 0.0},
            "execution_metrics": {"total_tool_calls": None, "errors_encountered": 0, "output_chars": len(text)},
            "user_notes_summary": {"uncertainties": [], "needs_review": ["Replace placeholder grades after reviewing the agent output."], "workarounds": []},
        }
        write_json(grading_path, grading)
        graded += 1
    print(f"Created {graded} grading template(s), skipped {skipped} existing grade(s) in {iteration.relative_to(ROOT)}.")
    print("Update grading.json with an independent reviewer, then run `benchmark` and `review`.")


def cmd_record_metrics(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    candidates = [path for path in run_directories(iteration, [args.eval]) if path.name == f"run-{args.run}" and path.parent.name == args.configuration]
    if len(candidates) != 1:
        fail(f"expected one run for eval {args.eval}, {args.configuration}, run {args.run}; found {len(candidates)}")
    if args.duration_ms is None and args.total_tokens is None:
        fail("provide --duration-ms and/or --total-tokens")
    timing: dict[str, Any] = {"recorded_at": utc_now()}
    if args.duration_ms is not None:
        if args.duration_ms < 0:
            fail("--duration-ms must be non-negative")
        timing["duration_ms"] = args.duration_ms
        timing["total_duration_seconds"] = round(args.duration_ms / 1000, 3)
    if args.total_tokens is not None:
        if args.total_tokens < 0:
            fail("--total-tokens must be non-negative")
        timing["total_tokens"] = args.total_tokens
    write_json(candidates[0] / "timing.json", timing)
    print(f"Recorded measured metrics: {candidates[0].relative_to(ROOT) / 'timing.json'}")


def require_iteration(name: str | None) -> Path:
    if not name:
        fail("--iteration is required")
    assert name is not None
    path = WORKSPACE / name
    if not path.is_dir():
        fail(f"unknown iteration: {path.relative_to(ROOT)}")
    return path


def metric_stats(values: list[float | int]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "mean": round(statistics.fmean(values), 4),
        "stddev": round(statistics.pstdev(values), 4),
        "min": min(values),
        "max": max(values),
    }


def postprocess_benchmark(iteration: Path) -> None:
    """Remove upstream placeholder metrics and report the actual run count."""
    path = iteration / "benchmark.json"
    benchmark = read_json(path)
    run_counts: dict[str, list[int]] = {}
    summaries: dict[str, dict[str, list[float | int]]] = {}
    for run_item in benchmark.get("runs", []):
        result = run_item.get("result", {})
        run_dir = next((candidate for candidate in run_directories(iteration, [run_item["eval_id"]]) if candidate.parent.name == run_item["configuration"] and candidate.name == f"run-{run_item["run_number"]}"), None)
        timing = read_json(run_dir / "timing.json") if run_dir and (run_dir / "timing.json").exists() else {}
        if "total_duration_seconds" not in timing:
            result["time_seconds"] = None
        else:
            result["time_seconds"] = timing["total_duration_seconds"]
        if "total_tokens" not in timing:
            result["tokens"] = None
        else:
            result["tokens"] = timing["total_tokens"]
        config = run_item["configuration"]
        summaries.setdefault(config, {"pass_rate": [], "time_seconds": [], "tokens": []})
        summaries[config]["pass_rate"].append(result.get("pass_rate", 0.0))
        for metric in ("time_seconds", "tokens"):
            if result.get(metric) is not None:
                summaries[config][metric].append(result[metric])
        run_counts.setdefault(config, []).append(run_item["run_number"])
    run_summary: dict[str, Any] = {}
    for config, metrics in summaries.items():
        run_summary[config] = {name: metric_stats(values) for name, values in metrics.items()}
    configs = list(summaries)
    if len(configs) >= 2:
        primary, baseline = configs[:2]
        pass_delta = (run_summary[primary]["pass_rate"] or {"mean": 0})["mean"] - (run_summary[baseline]["pass_rate"] or {"mean": 0})["mean"]
        delta: dict[str, str | None] = {"pass_rate": f"{pass_delta:+.2f}"}
        for metric, suffix in (("time_seconds", ".1f"), ("tokens", ".0f")):
            first = run_summary[primary][metric]
            second = run_summary[baseline][metric]
            delta[metric] = format(first["mean"] - second["mean"], f"+{suffix}") if first and second else None
        run_summary["delta"] = delta
    benchmark["run_summary"] = run_summary
    counts = [len(set(values)) for values in run_counts.values()]
    benchmark.setdefault("metadata", {})["runs_per_configuration"] = counts[0] if counts and len(set(counts)) == 1 else "variable"
    benchmark["metadata"]["measured_metrics_only"] = True
    write_json(path, benchmark)
    metadata = benchmark["metadata"]
    lines = [
        f"# Skill Benchmark: {metadata['skill_name']}",
        "",
        f"**Evals**: {', '.join(map(str, metadata.get('evals_run', [])))} ({metadata['runs_per_configuration']} measured run(s) per configuration)",
        "",
        "## Summary",
        "",
        "| Metric | With Skill | Without Skill | Delta |",
        "|---|---:|---:|---:|",
    ]
    for metric, label in (("pass_rate", "Pass rate"), ("time_seconds", "Time (s)"), ("tokens", "Tokens")):
        primary = run_summary.get("with_skill", {}).get(metric)
        baseline = run_summary.get("without_skill", {}).get(metric)
        delta = run_summary.get("delta", {}).get(metric)
        def format_stat(stat: dict[str, float | int] | None, percent: bool = False) -> str:
            if not stat:
                return "not measured"
            value = stat["mean"] * 100 if percent else stat["mean"]
            deviation = stat["stddev"] * 100 if percent else stat["stddev"]
            suffix = "%" if percent else ""
            return f"{value:.1f}{suffix} ± {deviation:.1f}{suffix}"
        lines.append(f"| {label} | {format_stat(primary, metric == 'pass_rate')} | {format_stat(baseline, metric == 'pass_rate')} | {delta or 'not measured'} |")
    (iteration / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_benchmark(args: argparse.Namespace) -> None:
    iteration = require_iteration(args.iteration)
    aggregator = tool_path("scripts", "aggregate_benchmark.py")
    run([sys.executable, str(aggregator), str(iteration), "--skill-name", "lab-works", "--skill-path", str(SKILL_PATH)])
    postprocess_benchmark(iteration)
    print("Normalized benchmark metadata and retained only explicitly recorded timing/token metrics.")


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
    run(["node", "--test", "scripts/tests/test_capture_cdp.js"])


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
    prepare.add_argument("--eval", type=int, action="append", help="eval id to prepare; repeat to select several")
    prepare.add_argument("--runs", type=int, default=1, choices=range(1, 10), metavar="N", help="independent runs per configuration (default: 1)")
    prepare.set_defaults(func=cmd_prepare)
    grade = commands.add_parser("grade", help="create grading templates for completed runs")
    grade.add_argument("--iteration", required=True)
    grade.add_argument("--eval", type=int, action="append", help="only grade selected eval id(s)")
    grade.add_argument("--overwrite", action="store_true", help="replace existing grading.json files")
    grade.set_defaults(func=cmd_grade)
    metrics = commands.add_parser("record-metrics", help="record duration/tokens reported by an executor")
    metrics.add_argument("--iteration", required=True)
    metrics.add_argument("--eval", type=int, required=True)
    metrics.add_argument("--configuration", required=True, choices=("with_skill", "without_skill"))
    metrics.add_argument("--run", type=int, default=1)
    metrics.add_argument("--duration-ms", type=int)
    metrics.add_argument("--total-tokens", type=int)
    metrics.set_defaults(func=cmd_record_metrics)
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
