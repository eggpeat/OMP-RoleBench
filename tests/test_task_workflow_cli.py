from __future__ import annotations

from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from rolebench.cli import build_parser, run
from rolebench.contracts import canonical_json
from rolebench.task_workflow import TaskAdmissionError, TaskWorkflowError


PRODUCT_ROOT = Path(__file__).resolve().parents[1]


class TaskWorkflowCliTests(unittest.TestCase):
    @patch("rolebench.cli.scan_session_candidates")
    def test_scan_session_maps_explicit_path_and_role_and_prints_candidate(
        self, scan: object
    ) -> None:
        candidate = {"schema_version": "omp.task-candidate/v1", "role_hint": "task"}
        scan.return_value = candidate
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "scan-session",
                "sessions/explicit.jsonl",
                "--role-hint",
                "task",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        scan.assert_called_once_with(Path("sessions/explicit.jsonl"), role_hint="task")
        self.assertEqual(output.getvalue(), canonical_json(candidate) + "\n")

    @patch("rolebench.cli.import_omp_gym_task")
    def test_import_maps_provenance_and_repeated_capabilities(
        self, import_task: object
    ) -> None:
        candidate = {"schema_version": "omp.task-candidate/v1", "source": "omp-gym"}
        import_task.return_value = candidate
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "import-omp-gym",
                "gym/task.json",
                "private/candidate.json",
                "--source-version",
                "2026.08",
                "--license",
                "Apache-2.0",
                "--role-hint",
                "task",
                "--capability",
                "python",
                "--capability",
                "docker",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        import_task.assert_called_once_with(
            PRODUCT_ROOT,
            Path("gym/task.json"),
            Path("private/candidate.json"),
            source_version="2026.08",
            license_expression="Apache-2.0",
            role_hint="task",
            capability_hints=("python", "docker"),
        )
        self.assertEqual(output.getvalue(), canonical_json(candidate) + "\n")

    @patch("rolebench.cli.check_task_qualification")
    def test_qualification_check_admitted_json_returns_zero(self, check: object) -> None:
        report = {"valid": True, "decision": "admitted", "diagnostics": []}
        check.return_value = report
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "qualification-check",
                "task.json",
                "qualification.json",
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        check.assert_called_once_with(
            PRODUCT_ROOT, Path("task.json"), Path("qualification.json")
        )
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")

    @patch("rolebench.cli.check_task_qualification")
    def test_qualification_check_calibration_or_rejected_returns_one(
        self, check: object
    ) -> None:
        for decision in ("calibration-required", "rejected"):
            with self.subTest(decision=decision):
                check.reset_mock()
                check.return_value = {
                    "valid": True,
                    "decision": decision,
                    "diagnostics": [],
                }
                output = StringIO()

                status = run(
                    [
                        "--root",
                        str(PRODUCT_ROOT),
                        "tasks",
                        "qualification-check",
                        "task.json",
                        "qualification.json",
                    ],
                    stdout=output,
                )

                self.assertEqual(status, 1)
                self.assertIn(f"decision: {decision}\n", output.getvalue())
                self.assertIn("valid: true\n", output.getvalue())

    @patch("rolebench.cli.generate_task_qualification")
    def test_qualify_preserves_repeated_evidence_order_and_output(
        self,
        qualify: object,
    ) -> None:
        qualification = {
            "decision": "admitted",
            "schema_version": "omp.task-qualification/v2",
        }
        qualify.return_value = qualification
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "qualify",
                "task.json",
                "--baseline-report",
                "baseline-1.json",
                "--baseline-report",
                "baseline-2.json",
                "--reference-report",
                "reference-1.json",
                "--reference-report",
                "reference-2.json",
                "--tamper-report",
                "tamper-1.json",
                "--tamper-report",
                "tamper-2.json",
                "--reviewer",
                "operator@example.test",
                "--output",
                "reviewed/qualification.json",
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        qualify.assert_called_once_with(
            PRODUCT_ROOT,
            Path("task.json"),
            (
                Path("baseline-1.json"),
                Path("baseline-2.json"),
            ),
            (
                Path("reference-1.json"),
                Path("reference-2.json"),
            ),
            (
                Path("tamper-1.json"),
                Path("tamper-2.json"),
            ),
            Path("reviewed/qualification.json"),
            reviewer="operator@example.test",
        )
        self.assertEqual(
            output.getvalue(),
            canonical_json(qualification) + "\n",
        )

    @patch("rolebench.cli.generate_task_qualification")
    def test_qualify_non_admitted_returns_one(self, qualify: object) -> None:
        qualify.return_value = {"decision": "calibration-required", "diagnostics": []}

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "qualify",
                "task.json",
                "--baseline-report",
                "baseline.json",
                "--reference-report",
                "reference.json",
                "--tamper-report",
                "tamper.json",
                "--reviewer",
                "operator@example.test",
                "--output",
                "qualification.json",
            ],
            stdout=StringIO(),
        )

        self.assertEqual(status, 1)

    @patch("rolebench.cli.verify_task_pack")
    def test_pack_verify_valid_and_invalid_status_and_json(self, verify: object) -> None:
        for valid, expected_status in ((True, 0), (False, 1)):
            with self.subTest(valid=valid):
                report = {"valid": valid, "diagnostics": [] if valid else ["bad link"]}
                verify.reset_mock()
                verify.return_value = report
                output = StringIO()

                status = run(
                    [
                        "--root",
                        str(PRODUCT_ROOT),
                        "tasks",
                        "pack-verify",
                        "pack.json",
                        "--json",
                    ],
                    stdout=output,
                )

                self.assertEqual(status, expected_status)
                verify.assert_called_once_with(PRODUCT_ROOT, Path("pack.json"))
                self.assertEqual(output.getvalue(), canonical_json(report) + "\n")

    @patch("rolebench.cli.prepare_admission_worker_manifest")
    def test_prepare_admission_run_maps_probe_and_docker(
        self,
        prepare: object,
    ) -> None:
        manifest = {
            "schema_version": "omp.worker-run-manifest/v2",
            "run_id": "baseline-1",
        }
        prepare.return_value = manifest
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "prepare-admission-run",
                "task.json",
                "--probe",
                "baseline",
                "--run-id",
                "baseline-1",
                "--output",
                "runs/baseline-1.json",
                "--docker",
                "/opt/docker",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        prepare.assert_called_once_with(
            PRODUCT_ROOT,
            Path("task.json"),
            "baseline",
            "baseline-1",
            Path("runs/baseline-1.json"),
            docker="/opt/docker",
        )
        self.assertEqual(
            output.getvalue(),
            canonical_json(manifest) + "\n",
        )

    @patch("rolebench.cli.prepare_worker_manifest")
    def test_prepare_run_maps_docker_run_id_and_output(self, prepare: object) -> None:
        manifest = {"schema_version": "omp.worker-run-manifest/v2", "run_id": "run-17"}
        prepare.return_value = manifest
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "prepare-run",
                "task.json",
                "qualification.json",
                "--run-id",
                "run-17",
                "--output",
                "runs/manifest.json",
                "--docker",
                "/opt/docker",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        prepare.assert_called_once_with(
            PRODUCT_ROOT,
            Path("task.json"),
            Path("qualification.json"),
            "run-17",
            Path("runs/manifest.json"),
            docker="/opt/docker",
        )
        self.assertEqual(output.getvalue(), canonical_json(manifest) + "\n")

    @patch("rolebench.cli.prepare_worker_manifest")
    def test_prepare_run_admission_error_returns_one(self, prepare: object) -> None:
        prepare.side_effect = TaskAdmissionError("qualification is not admitted")
        error = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "tasks",
                "prepare-run",
                "task.json",
                "qualification.json",
                "--run-id",
                "run-17",
                "--output",
                "manifest.json",
            ],
            stderr=error,
        )

        self.assertEqual(status, 1)
        self.assertEqual(error.getvalue(), "error: qualification is not admitted\n")

    @patch("rolebench.cli.scan_session_candidates")
    def test_workflow_error_returns_two(self, scan: object) -> None:
        scan.side_effect = TaskWorkflowError("session path is unsafe")
        error = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "tasks", "scan-session", "session.jsonl"],
            stderr=error,
        )

        self.assertEqual(status, 2)
        self.assertEqual(error.getvalue(), "error: session path is unsafe\n")

    def test_parser_routes_preserve_task_argument_types(self) -> None:
        arguments = build_parser().parse_args(
            [
                "tasks",
                "prepare-run",
                "task.json",
                "qualification.json",
                "--run-id",
                "run-1",
                "--output",
                "manifest.json",
                "--docker",
                "podman",
            ]
        )

        self.assertEqual((arguments.group, arguments.command), ("tasks", "prepare-run"))
        self.assertEqual(arguments.task, Path("task.json"))
        self.assertEqual(arguments.qualification, Path("qualification.json"))
        self.assertEqual(arguments.run_id, "run-1")
        self.assertEqual(arguments.output, Path("manifest.json"))
        self.assertEqual(arguments.docker, "podman")


if __name__ == "__main__":
    unittest.main()
