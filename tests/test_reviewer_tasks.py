"""Tests for the reviewer role contract, reviewer task pack, and verifier mechanics."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
from rolebench.contracts import (
    BUILTIN_ROLES,
    canonical_sha256,
    discover_root,
    load_repository,
    validate_artifact,
)
from rolebench.task_workflow import check_task_qualification, verify_task_pack


class TestReviewerTasks(unittest.TestCase):
    def setUp(self) -> None:
        self.root = discover_root(Path(__file__).parent)

    def test_reviewer_role_contract(self) -> None:
        contract_path = self.root / "contracts/roles/reviewer.json"
        self.assertTrue(contract_path.exists())
        with open(contract_path, "r", encoding="utf-8") as f:
            contract = json.load(f)
        self.assertEqual(contract["role"], "reviewer")
        self.assertEqual(contract["contract_id"], "role-contract/reviewer/v1")
        self.assertIn("defect-recall", contract["required_capabilities"])
        self.assertIn("false-positive-control", contract["required_capabilities"])

        res = validate_artifact(self.root, "role-contract", contract_path)
        self.assertTrue(res.valid, f"role-contract diagnostics: {res.diagnostics}")

    def test_reviewer_task_pack_verifies_cleanly(self) -> None:
        pack_path = self.root / "contracts/task-packs/reviewer-v1.json"
        self.assertTrue(pack_path.exists())
        res = verify_task_pack(self.root, pack_path)
        self.assertTrue(res["valid"], f"pack-verify diagnostics: {res.get('diagnostics')}")

    def test_defect_recall_verifier_and_probes(self) -> None:
        task_dir = (self.root / "contracts/tasks/omp-native.code-review-defect-recall/1.0.0").resolve()
        runner_py = (task_dir / "runner.py").resolve()
        verifier_py = (task_dir / "verifier-private/verifier.py").resolve()

        # 1. Reference probe passes
        ref_probe = (task_dir / "probes/reference.py").resolve()
        p_res = subprocess.run(["python3", str(ref_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "pass")
        self.assertEqual(v_out["score"], 1.0)
        self.assertEqual(v_out["details"]["recall_ratio"], 1.0)
        self.assertEqual(v_out["details"]["false_positive_count"], 0)

        # 2. Baseline probe fails (0 recall)
        base_probe = (task_dir / "probes/baseline.py").resolve()
        p_res = subprocess.run(["python3", str(base_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "fail")
        self.assertEqual(v_out["score"], 0.0)

        # 3. Tamper probe fails (fabricated bugs)
        tamper_probe = (task_dir / "probes/tamper.py").resolve()
        p_res = subprocess.run(["python3", str(tamper_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "fail")
        self.assertEqual(v_out["score"], 0.0)

    def test_defect_recall_verifier_rejects_suffix_tricks_and_low_severity(self) -> None:
        task_dir = (self.root / "contracts/tasks/omp-native.code-review-defect-recall/1.0.0").resolve()
        sys.path.insert(0, str(task_dir / "verifier-private"))
        import verifier

        # 1. Suffix trick: 'e.py' should NOT match 'src/cache.py'
        suffix_trick_sub = {
            "verdict": "changes_requested",
            "findings": [
                {
                    "file": "e.py",
                    "line_start": 16,
                    "line_end": 28,
                    "severity": "high",
                    "category": "concurrency",
                    "description": "race condition",
                }
            ],
        }
        passed, reason, details = verifier.verify_submission(suffix_trick_sub)
        self.assertFalse(passed)
        self.assertIn("missed material defects", reason)
        self.assertEqual(details["recalled_defects"], 0)
        self.assertEqual(details["false_positive_count"], 1)

        # 2. Low severity on medium-minimum defect rejected
        low_sev_sub = {
            "verdict": "changes_requested",
            "findings": [
                {
                    "file": "src/cache.py",
                    "line_start": 16,
                    "line_end": 28,
                    "severity": "low",
                    "category": "concurrency",
                    "description": "race condition",
                }
            ],
        }
        passed, reason, details = verifier.verify_submission(low_sev_sub)
        self.assertFalse(passed)
        self.assertEqual(details["recalled_defects"], 0)

    def test_precision_control_verifier_and_probes(self) -> None:
        task_dir = (self.root / "contracts/tasks/omp-native.code-review-precision-control/1.0.0").resolve()
        runner_py = (task_dir / "runner.py").resolve()
        verifier_py = (task_dir / "verifier-private/verifier.py").resolve()

        # 1. Reference probe passes (0 false positives on clean change)
        ref_probe = (task_dir / "probes/reference.py").resolve()
        p_res = subprocess.run(["python3", str(ref_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "pass")
        self.assertEqual(v_out["score"], 1.0)
        self.assertEqual(v_out["details"]["false_positive_count"], 0)

        # 2. Baseline probe fails (reported false positive)
        base_probe = (task_dir / "probes/baseline.py").resolve()
        p_res = subprocess.run(["python3", str(base_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "fail")
        self.assertEqual(v_out["score"], 0.0)

        # 3. Tamper probe fails
        tamper_probe = (task_dir / "probes/tamper.py").resolve()
        p_res = subprocess.run(["python3", str(tamper_probe)], capture_output=True, check=True)
        r_res = subprocess.run(["python3", str(runner_py)], input=p_res.stdout, capture_output=True, check=True, cwd=task_dir)
        v_res = subprocess.run(["python3", str(verifier_py)], input=r_res.stdout, capture_output=True, check=True)
        v_out = json.loads(v_res.stdout)
        self.assertEqual(v_out["verdict"], "fail")
        self.assertEqual(v_out["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
