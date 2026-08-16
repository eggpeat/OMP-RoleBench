"""Tests for task suite audit engine and CLI command."""

from __future__ import annotations

import io
import json
from pathlib import Path
import unittest

from rolebench.cli import build_parser, run
from rolebench.contracts import discover_root
from rolebench.suite_audit import (
    LaneAuditResult,
    RoleAuditResult,
    SuiteAuditReport,
    audit_task_suite,
    format_suite_audit_human,
)


class TestSuiteAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.root = discover_root(Path(__file__).parent)

    def test_audit_task_suite_engine(self) -> None:
        report = audit_task_suite(self.root)
        self.assertIsInstance(report, SuiteAuditReport)
        self.assertEqual(report.total_roles, 11)
        self.assertEqual(report.structurally_ready_roles, 11)
        self.assertEqual(report.total_task_lanes, 6)
        self.assertEqual(report.total_anchors, 16)
        self.assertFalse(report.structurally_frozen)
        self.assertFalse(report.routing_eligible)
        self.assertIn("STRUCTURAL_FREEZE_INCOMPLETE", report.global_reason_codes)
        self.assertIn("SUITE_CALIBRATION_REQUIRED", report.global_reason_codes)

        # Check role results
        self.assertIn("reviewer", report.role_results)
        rev = report.role_results["reviewer"]
        self.assertEqual(rev.actual_anchor_count, 2)
        self.assertTrue(rev.structurally_ready)
        self.assertEqual(rev.missing_capabilities, [])
        self.assertIn("ROLE_STRUCTURALLY_READY", rev.reason_codes)

        # Check lane results
        self.assertIn("task/implementation", report.lane_results)
        impl = report.lane_results["task/implementation"]
        self.assertEqual(impl.actual_anchor_count, 0)
        self.assertFalse(impl.structurally_ready)
        self.assertIn("LANE_ANCHORS_DEFICIT", impl.reason_codes)

    def test_format_suite_audit_human(self) -> None:
        report = audit_task_suite(self.root)
        rendered = format_suite_audit_human(report)
        self.assertIn("=== OMP RoleBench Task Suite v1 Audit ===", rendered)
        self.assertIn("@reviewer", rendered)
        self.assertIn("task/implementation", rendered)
        self.assertIn("STRUCTURAL_FREEZE_INCOMPLETE", rendered)

    def test_cli_suite_audit_human(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = run(["--root", str(self.root), "tasks", "suite-audit"], stdout=out, stderr=err)
        self.assertEqual(code, 0)
        self.assertIn("=== OMP RoleBench Task Suite v1 Audit ===", out.getvalue())

    def test_cli_suite_audit_json(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = run(["--root", str(self.root), "tasks", "suite-audit", "--json"], stdout=out, stderr=err)
        self.assertEqual(code, 0)
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["schema_version"], "omp.task-suite-audit-report/v1")
        self.assertEqual(parsed["total_roles"], 11)
        self.assertEqual(parsed["structurally_ready_roles"], 11)
        self.assertEqual(parsed["total_task_lanes"], 6)

    def test_cli_suite_audit_strict_fails_when_unfrozen(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        code = run(["--root", str(self.root), "tasks", "suite-audit", "--strict"], stdout=out, stderr=err)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
