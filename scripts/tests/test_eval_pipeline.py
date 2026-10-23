# -*- coding: utf-8 -*-
import importlib.util
import json
import tempfile
from contextlib import redirect_stderr
from io import StringIO
import unittest
from pathlib import Path


PIPELINE_PATH = Path(__file__).resolve().parents[2] / ".skill-eval" / "pipeline.py"
spec = importlib.util.spec_from_file_location("eval_pipeline", PIPELINE_PATH)
assert spec and spec.loader
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class EvalPipelineTest(unittest.TestCase):
    def test_select_cases_preserves_catalog_order(self):
        catalog = {"evals": [{"id": 4}, {"id": 5}, {"id": 7}]}
        self.assertEqual([case["id"] for case in pipeline.select_cases(catalog, [7, 4])], [4, 7])
        self.assertEqual(pipeline.select_cases(catalog, None), catalog["evals"])
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            pipeline.select_cases(catalog, [99])

    def test_create_run_sandbox_copies_inputs_without_aliasing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            source = inputs / "ЛР1" / "content.json"
            source.parent.mkdir()
            source.write_text("[]", encoding="utf-8")

            sandbox = pipeline.create_run_sandbox(inputs, root / "run-1")
            copied = sandbox / "ЛР1" / "content.json"
            self.assertEqual(copied.read_text(encoding="utf-8"), "[]")
            copied.write_text('[{"p": "sandbox"}]', encoding="utf-8")
            self.assertEqual(source.read_text(encoding="utf-8"), "[]")

    def test_postprocess_benchmark_keeps_only_measured_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            iteration = Path(directory)
            for configuration in ("with_skill", "without_skill"):
                run = iteration / "eval-05-case" / configuration / "run-1"
                run.mkdir(parents=True)
                (iteration / "eval-05-case" / "eval_metadata.json").write_text(
                    json.dumps({"eval_id": 5}), encoding="utf-8"
                )
                if configuration == "with_skill":
                    (run / "timing.json").write_text(
                        json.dumps({"total_duration_seconds": 2.5, "total_tokens": 100}),
                        encoding="utf-8",
                    )
            benchmark = {
                "metadata": {"skill_name": "lab-works", "evals_run": [5], "runs_per_configuration": 3},
                "runs": [
                    {"eval_id": 5, "configuration": "with_skill", "run_number": 1, "result": {"pass_rate": 1.0, "time_seconds": 0.0, "tokens": 999}},
                    {"eval_id": 5, "configuration": "without_skill", "run_number": 1, "result": {"pass_rate": 0.5, "time_seconds": 0.0, "tokens": 888}},
                ],
            }
            (iteration / "benchmark.json").write_text(json.dumps(benchmark), encoding="utf-8")

            pipeline.postprocess_benchmark(iteration)
            actual = json.loads((iteration / "benchmark.json").read_text(encoding="utf-8"))

            self.assertEqual(actual["metadata"]["runs_per_configuration"], 1)
            self.assertTrue(actual["metadata"]["measured_metrics_only"])
            self.assertEqual(actual["runs"][0]["result"]["time_seconds"], 2.5)
            self.assertEqual(actual["runs"][0]["result"]["tokens"], 100)
            self.assertIsNone(actual["runs"][1]["result"]["time_seconds"])
            self.assertIsNone(actual["runs"][1]["result"]["tokens"])
            self.assertIsNone(actual["run_summary"]["without_skill"]["time_seconds"])
            self.assertIsNone(actual["run_summary"]["delta"]["time_seconds"])


if __name__ == "__main__":
    unittest.main()
