from __future__ import annotations

from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from rolebench.cli import build_parser, run
from rolebench.contracts import JSONObject, canonical_json
from rolebench.worker import WorkerError


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path("contracts/scored-worker-policy.json")
MANIFEST_PATH = Path("fixtures/docker-runsc/worker-run-manifest.json")
POLICY_SHA = "a" * 64
ARTIFACT_SHA = "b" * 64


def doctor_report(*, ready: bool = True) -> JSONObject:
    return {
        "schema_version": "omp.worker-doctor-report/v1",
        "ready": ready,
        "docker_executable": True,
        "docker_server": True,
        "rootless": ready,
        "runsc": ready,
        "cgroup_v2": True,
        "delegation": ready,
        "diagnostics": (
            []
            if ready
            else ["Docker server is not rootless", "runsc runtime is unavailable"]
        ),
    }


def worker_run_report(*, passed: bool = True) -> JSONObject:
    disposition = "retryable-invalid"
    outcome = {
        "disposition": disposition,
        "reason_code": "incomplete-observation",
        "failure_domain": "orchestrator",
    }
    return {
        "schema_version": "omp.worker-run-report/v1",
        "run_id": "fixture-run",
        "passed": passed,
        "external_provider_calls": 0,
        "policy_digest_sha256": POLICY_SHA,
        "artifact_digest_sha256": ARTIFACT_SHA if passed else None,
        "observation": {"schema_version": "omp.attempt-observation/v1"},
        "outcome": outcome,
        "doctor": doctor_report(ready=passed),
        "isolation": {
            "agent_runtime_runsc": passed,
            "distinct_images": True,
            "immutable_handoff": passed,
            "network_mode": "none",
        },
        "diagnostics": [] if passed else ["agent container did not start"],
    }


class WorkerRunCliTests(unittest.TestCase):
    def test_parser_accepts_doctor_options(self) -> None:
        arguments = build_parser().parse_args(
            [
                "worker",
                "doctor",
                "policy.json",
                "--docker",
                "/usr/bin/docker",
                "--json",
            ]
        )

        self.assertEqual((arguments.group, arguments.command), ("worker", "doctor"))
        self.assertEqual(arguments.policy, Path("policy.json"))
        self.assertEqual(arguments.docker, "/usr/bin/docker")
        self.assertTrue(arguments.as_json)

    def test_parser_accepts_run_options(self) -> None:
        arguments = build_parser().parse_args(
            ["worker", "run", "manifest.json", "--docker", "podman-docker", "--json"]
        )

        self.assertEqual((arguments.group, arguments.command), ("worker", "run"))
        self.assertEqual(arguments.manifest, Path("manifest.json"))
        self.assertEqual(arguments.docker, "podman-docker")
        self.assertTrue(arguments.as_json)

    @patch("rolebench.cli.doctor_worker")
    def test_doctor_json_is_canonical_and_ready_returns_zero(
        self,
        doctor: object,
    ) -> None:
        report = doctor_report()
        doctor.return_value = report
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "doctor",
                str(POLICY_PATH),
                "--docker",
                "/opt/docker",
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")
        doctor.assert_called_once_with(PRODUCT_ROOT, POLICY_PATH, docker="/opt/docker")

    @patch("rolebench.cli.doctor_worker")
    def test_doctor_plain_nonready_lists_every_failed_requirement(
        self,
        doctor: object,
    ) -> None:
        doctor.return_value = doctor_report(ready=False)
        output = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "worker", "doctor", str(POLICY_PATH)],
            stdout=output,
        )

        self.assertEqual(status, 1)
        self.assertEqual(
            output.getvalue(),
            "Worker doctor: NOT READY\n"
            "Required checks:\n"
            "  Docker executable: PASS\n"
            "  Docker server: PASS\n"
            "  Rootless Docker: FAIL\n"
            "  runsc runtime: FAIL\n"
            "  cgroup v2: PASS\n"
            "  cgroup delegation: FAIL\n"
            "Diagnostics:\n"
            "  - Docker server is not rootless\n"
            "  - runsc runtime is unavailable\n",
        )

    @patch("rolebench.cli.run_worker")
    def test_run_json_is_canonical_and_passed_returns_zero(
        self,
        worker: object,
    ) -> None:
        report = worker_run_report()
        worker.return_value = report
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "worker",
                "run",
                str(MANIFEST_PATH),
                "--docker",
                "/opt/docker",
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")
        worker.assert_called_once_with(
            PRODUCT_ROOT,
            MANIFEST_PATH,
            docker="/opt/docker",
        )

    @patch("rolebench.cli.run_worker")
    def test_run_plain_pass_shows_accounting_isolation_and_no_diagnostics(
        self,
        worker: object,
    ) -> None:
        worker.return_value = worker_run_report()
        output = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "worker", "run", str(MANIFEST_PATH)],
            stdout=output,
        )

        self.assertEqual(status, 0)
        self.assertEqual(
            output.getvalue(),
            "Worker run: PASS\n"
            "External provider calls: 0\n"
            f"Policy SHA-256: {POLICY_SHA}\n"
            f"Artifact SHA-256: {ARTIFACT_SHA}\n"
            "Outcome: disposition='retryable-invalid', "
            "reason='incomplete-observation', domain='orchestrator'\n"
            "Isolation:\n"
            "  agent_runtime_runsc: true\n"
            "  distinct_images: true\n"
            "  immutable_handoff: true\n"
            "  network_mode: none\n"
            "Diagnostics:\n"
            "  none\n",
        )

    @patch("rolebench.cli.run_worker")
    def test_run_plain_failure_returns_one_and_never_invents_artifact_digest(
        self,
        worker: object,
    ) -> None:
        report = worker_run_report(passed=False)
        worker.return_value = report
        output = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "worker", "run", str(MANIFEST_PATH)],
            stdout=output,
        )

        self.assertEqual(status, 1)
        text = output.getvalue()
        self.assertIn("Worker run: FAIL\n", text)
        self.assertIn("Artifact SHA-256: unavailable\n", text)
        self.assertIn("  agent_runtime_runsc: false\n", text)
        self.assertIn("  - agent container did not start\n", text)

    @patch("rolebench.cli.doctor_worker")
    def test_worker_error_is_user_facing_status_two(self, doctor: object) -> None:
        doctor.side_effect = WorkerError("policy digest mismatch")
        output = StringIO()
        error = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "worker", "doctor", str(POLICY_PATH)],
            stdout=output,
            stderr=error,
        )

        self.assertEqual(status, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "error: policy digest mismatch\n")


if __name__ == "__main__":
    unittest.main()
