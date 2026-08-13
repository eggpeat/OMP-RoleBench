from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from rolebench.contracts import canonical_json, validate_artifact
from rolebench import worker


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"
AGENT_IMAGE = "example.invalid/agent@sha256:" + "1" * 64
VERIFIER_IMAGE = "example.invalid/verifier@sha256:" + "2" * 64
ARTIFACT = b"deterministic\x00artifact\n"


def _result(code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> worker._CommandResult:
    return worker._CommandResult(code, stdout, stderr)


class FakeDocker:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.stream_calls: list[tuple[list[str], bytes | None, int]] = []
        self.info: dict[str, object] = {
            "ServerVersion": "fixture",
            "SecurityOptions": ["name=rootless"],
            "Runtimes": {
                "runsc": {"path": "/fixture/rolebench-runsc-wrapper"}
            },
            "CgroupVersion": "2",
            "CgroupDriver": "systemd",
        }
        self.wrapper_report: dict[str, object] = {
            "schema_version": "omp.runsc-wrapper-doctor/v1",
            "ready": True,
            "controllers": ["cpu", "memory", "pids"],
        }
        self.created: dict[str, bool] = {}
        self.finished: dict[str, bool] = {}
        self.agent_stream = worker._StreamResult(0, ARTIFACT, b"private agent log")
        self.verifier_stream = worker._StreamResult(
            0, b'{"outcome":"accepted","reward":1}', b"private verifier log"
        )
        self.inspect_mutator = None
        self.verifier_input: bytes | None = None
        self.events: list[str] = []
        self.oom_agent = False
        self.agent_state_exit = 0
        self.verifier_state_exit = 0
        self.remove_failure: str | None = None

    def run(
        self, argv: list[str], *, timeout: float | None = None, input_bytes: bytes | None = None
    ) -> worker._CommandResult:
        self.commands.append(list(argv))
        if argv[1:4] == ["info", "--format", "{{json .}}"]:
            return _result(stdout=json.dumps(self.info).encode())
        if argv == ["/fixture/rolebench-runsc-wrapper", "--rolebench-doctor"]:
            return _result(stdout=json.dumps(self.wrapper_report).encode())
        if argv[1] == "create":
            name = argv[argv.index("--name") + 1]
            container = "verifier-id" if "verifier" in name else "agent-id"
            self.created[container] = True
            self.finished[container] = False
            return _result(stdout=(container + "\n").encode())
        if argv[1] == "inspect":
            container = argv[2]
            self.events.append(f"inspect:{container}:{self.finished.get(container, False)}")
            value = self._inspect_value(container)
            if self.inspect_mutator is not None:
                self.inspect_mutator(container, value)
            return _result(stdout=json.dumps([value]).encode())
        if argv[1] == "kill":
            self.finished[argv[2]] = True
            return _result()
        if argv[1] == "rm":
            container = argv[-1]
            return _result(code=1 if container == self.remove_failure else 0)
        raise AssertionError(f"unexpected command: {argv!r}")

    def stream(
        self,
        argv: list[str],
        *,
        input_bytes: bytes | None,
        timeout: float,
        output_limit: int,
    ) -> worker._StreamResult:
        self.stream_calls.append((list(argv), input_bytes, output_limit))
        container = argv[-1]
        self.events.append(f"stream:{container}")
        self.finished[container] = True
        if container == "agent-id":
            return self.agent_stream
        self.verifier_input = input_bytes
        return self.verifier_stream

    def _inspect_value(self, container: str) -> dict[str, object]:
        verifier = container == "verifier-id"
        uid = 2000 if verifier else 1000
        gid = uid
        tmpfs: dict[str, str] = {}
        if not verifier:
            tmpfs = {
                "/workspace": (
                    "rw,nosuid,nodev,noexec,size=8589934592,"
                    "uid=1000,gid=1000,mode=0700"
                )
            }
        exit_code = self.verifier_state_exit if verifier else self.agent_state_exit
        return {
            "Config": {"User": f"{uid}:{gid}", "OpenStdin": verifier},
            "HostConfig": {
                "Runtime": "runsc",
                "Privileged": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges=true"],
                "NetworkMode": "none",
                "PidMode": "",
                "IpcMode": "private",
                "UTSMode": "",
                "UsernsMode": "",
                "CgroupnsMode": "private",
                "Devices": [],
                "Tmpfs": tmpfs,
                "NanoCpus": 4_000_000_000,
                "Memory": 8589934592,
                "MemorySwap": 8589934592,
                "PidsLimit": 512,
                "Ulimits": [{"Name": "nofile", "Soft": 4096, "Hard": 4096}],
            },
            "Mounts": [],
            "State": {
                "ExitCode": exit_code,
                "OOMKilled": self.oom_agent and not verifier,
                "Status": "exited" if self.finished.get(container) else "created",
            },
        }


class WorkerRuntimeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")
        policy_path = self.root / "contracts/scored-worker-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(canonical_json(policy).encode()).hexdigest()
        self.manifest = {
            "schema_version": "omp.worker-run-manifest/v1",
            "run_id": "fixture-1",
            "role": "task",
            "task": {"digest_sha256": "3" * 64},
            "policy": {
                "path": "contracts/scored-worker-policy.json",
                "digest_sha256": digest,
            },
            "provider": {"enabled": False},
            "agent": {"image": AGENT_IMAGE, "argv": ["/agent.sh", "--fixture"]},
            "verifier": {"image": VERIFIER_IMAGE, "argv": ["/verifier.sh"]},
        }
        self.manifest_path = self.root / "worker.json"
        self._write_manifest()
        self.fake = FakeDocker()
        patcher = mock.patch.object(worker, "_ADAPTER_FACTORY", new=lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def run_worker(self) -> dict[str, object]:
        return worker.run_worker(self.root, Path("worker.json"), docker="docker-fixture")


class ManifestRuntimeGuardTests(WorkerRuntimeFixture):
    def test_distinct_repositories_with_same_image_digest_are_rejected(self) -> None:
        self.manifest["verifier"]["image"] = (
            "another.invalid/verifier@sha256:" + "1" * 64
        )
        self._write_manifest()

        with self.assertRaisesRegex(
            worker.WorkerError, "agent and verifier image digests must differ"
        ):
            self.run_worker()
        self.assertEqual(self.fake.commands, [])


class DoctorTests(WorkerRuntimeFixture):
    def test_doctor_uses_exact_info_argv_and_requires_every_prerequisite(self) -> None:
        report = worker.doctor_worker(
            self.root, Path("contracts/scored-worker-policy.json"), docker="docker-fixture"
        )
        self.assertTrue(report["ready"])
        self.assertEqual(
            self.fake.commands[0],
            ["docker-fixture", "info", "--format", "{{json .}}"],
        )
        self.assertEqual(
            self.fake.commands[1],
            ["/fixture/rolebench-runsc-wrapper", "--rolebench-doctor"],
        )
        self.assertTrue(report["resource_enforcement"])

        mutations = {
            "rootless": lambda value: value.update(SecurityOptions=[]),
            "runsc": lambda value: value.update(Runtimes={"runc": {}}),
            "cgroup-v2": lambda value: value.update(CgroupVersion="1"),
            "delegation": lambda value: value.update(CgroupDriver="cgroupfs"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.fake.info = {
                    "ServerVersion": "fixture",
                    "SecurityOptions": ["name=rootless"],
                    "Runtimes": {
                        "runsc": {
                            "path": "/fixture/rolebench-runsc-wrapper"
                        }
                    },
                    "CgroupVersion": "2",
                    "CgroupDriver": "systemd",
                }
                mutate(self.fake.info)
                report = worker.doctor_worker(
                    self.root, Path("contracts/scored-worker-policy.json"), docker="docker-fixture"
                )
                self.assertFalse(report["ready"])
                self.assertIn(f"requirement-failed:{label}", report["diagnostics"])

        self.fake.info = {
            "ServerVersion": "fixture",
            "SecurityOptions": ["name=rootless"],
            "Runtimes": {
                "runsc": {"path": "/fixture/rolebench-runsc-wrapper"}
            },
            "CgroupVersion": "2",
            "CgroupDriver": "systemd",
        }
        self.fake.wrapper_report["ready"] = False
        report = worker.doctor_worker(
            self.root,
            Path("contracts/scored-worker-policy.json"),
            docker="docker-fixture",
        )
        self.assertFalse(report["ready"])
        self.assertFalse(report["resource_enforcement"])
        self.assertIn(
            "requirement-failed:resource-enforcement",
            report["diagnostics"],
        )


class SuccessfulRuntimeTests(WorkerRuntimeFixture):
    def test_exact_fail_closed_commands_and_immutable_distinct_handoff(self) -> None:
        report = self.run_worker()
        creates = [command for command in self.fake.commands if command[1] == "create"]
        self.assertEqual(len(creates), 2)
        agent, verifier = creates
        self.assertEqual(agent[:4], ["docker-fixture", "create", "--name", "rolebench-agent-fixture-1"])
        for command in creates:
            self.assertIn(["--runtime", "runsc"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn(["--network", "none"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn(["--cap-drop", "ALL"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn("--read-only", command)
            self.assertNotIn("--mount", command)
            self.assertNotIn("--volume", command)
            self.assertNotIn("--device", command)
        self.assertIn("--tmpfs", agent)
        self.assertNotIn("--tmpfs", verifier)
        self.assertNotIn("--interactive", agent)
        self.assertIn("--interactive", verifier)
        self.assertEqual(agent[-3:], [AGENT_IMAGE, "/agent.sh", "--fixture"])
        self.assertEqual(verifier[-2:], [VERIFIER_IMAGE, "/verifier.sh"])
        self.assertEqual(
            self.fake.stream_calls[0][0],
            ["docker-fixture", "start", "--attach", "agent-id"],
        )
        self.assertEqual(
            self.fake.stream_calls[1][0],
            ["docker-fixture", "start", "--attach", "-i", "verifier-id"],
        )
        self.assertIn(
            ["docker-fixture", "rm", "--force", "--volumes", "agent-id"],
            self.fake.commands,
        )
        self.assertIn(
            ["docker-fixture", "rm", "--force", "--volumes", "verifier-id"],
            self.fake.commands,
        )
        self.assertEqual(self.fake.verifier_input, ARTIFACT)
        self.assertEqual(report["artifact_digest_sha256"], hashlib.sha256(ARTIFACT).hexdigest())
        self.assertTrue(report["isolation"]["immutable_handoff"])
        self.assertTrue(report["isolation"]["artifact_frozen_after_agent_exit"])
        self.assertLess(self.fake.events.index("stream:agent-id"), self.fake.events.index("inspect:agent-id:True"))
        self.assertTrue(report["passed"])
        self.assertEqual(report["external_provider_calls"], 0)
        self.assertFalse(report["observation"]["provider"]["request_started"])
        self.assertEqual(report["outcome"]["disposition"], "retryable-invalid")
        self.assertEqual(report["outcome"]["reason_code"], "incomplete-observation")

        encoded = canonical_json(report)
        self.assertNotIn("private agent log", encoded)
        self.assertNotIn("private verifier log", encoded)
        self.assertNotIn("deterministic", encoded)

    def test_public_observation_and_outcome_contracts_validate(self) -> None:
        report = self.run_worker()
        for schema, value in (
            ("attempt-observation", report["observation"]),
            ("attempt-outcome", report["outcome"]),
        ):
            path = self.root / f"{schema}.json"
            path.write_text(canonical_json(value), encoding="utf-8")
            result = validate_artifact(self.root, schema, path)
            self.assertTrue(result.valid, result.diagnostics)


class IsolationAndFailureTests(WorkerRuntimeFixture):
    def test_effective_inspect_mismatch_rejects_before_agent_start(self) -> None:
        def mutate(container: str, value: dict[str, object]) -> None:
            if container == "agent-id":
                value["HostConfig"]["Runtime"] = "runc"

        self.fake.inspect_mutator = mutate
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("sandbox-violation", report["observation"]["issues"])
        self.assertFalse(any(call[0][-1] == "agent-id" for call in self.fake.stream_calls))
        self.assertEqual(report["outcome"]["disposition"], "quarantined")

    def test_artifact_overflow_kills_before_freeze_and_maps_collection(self) -> None:
        self.fake.agent_stream = worker._StreamResult(137, b"x", b"secret", overflowed=True)
        report = self.run_worker()
        self.assertIn("artifact-collection", report["observation"]["issues"])
        self.assertIsNone(report["artifact_digest_sha256"])
        self.assertFalse(report["observation"]["lifecycle"]["artifact_frozen"])
        self.assertTrue(any(command[1:3] == ["kill", "agent-id"] for command in self.fake.commands))

    def test_agent_timeout_maps_orchestrator_timeout_not_model_deadline(self) -> None:
        self.fake.agent_stream = worker._StreamResult(-9, b"", b"secret", timed_out=True)
        report = self.run_worker()
        self.assertEqual(report["observation"]["termination"]["kind"], "orchestrator-timeout")
        self.assertEqual(report["outcome"]["reason_code"], "orchestrator-timeout")
        self.assertNotEqual(report["observation"]["termination"]["kind"], "model-deadline")

    def test_pre_provider_oom_is_unscored_runner_failure(self) -> None:
        self.fake.oom_agent = True
        self.fake.agent_state_exit = 137
        report = self.run_worker()
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertEqual(
            report["observation"]["termination"]["kind"],
            "resource-limit",
        )
        self.assertEqual(
            report["observation"]["termination"]["oom_scope"],
            "attempt",
        )
        self.assertFalse(report["observation"]["provider"]["request_started"])
        self.assertNotEqual(report["outcome"]["disposition"], "scored")
        self.assertEqual(report["outcome"]["reason_code"], "runner-failure")

    def test_verifier_timeout_remains_a_verifier_failure(self) -> None:
        self.fake.verifier_stream = worker._StreamResult(
            -9,
            b"",
            b"private timeout",
            timed_out=True,
        )
        report = self.run_worker()
        self.assertEqual(report["observation"]["termination"]["kind"], "completed")
        self.assertEqual(report["outcome"]["reason_code"], "verifier-crash")
        self.assertEqual(report["outcome"]["failure_domain"], "verifier")
        self.assertFalse(report["passed"])

    def test_cleanup_failure_fails_the_pipeline_closed(self) -> None:
        self.fake.remove_failure = "verifier-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn("verifier-container-cleanup-failed", report["diagnostics"])
        self.assertEqual(report["outcome"]["reason_code"], "runner-failure")

    def test_agent_cleanup_failure_skips_verifier_and_fails_closed(self) -> None:
        self.fake.remove_failure = "agent-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn("agent-container-cleanup-failed", report["diagnostics"])
        self.assertFalse(
            any(
                command[1] == "create"
                and "rolebench-verifier-fixture-1" in command
                for command in self.fake.commands
            )
        )
        self.assertEqual(report["outcome"]["reason_code"], "runner-failure")

    def test_verifier_nonzero_missing_and_malformed_are_unscored(self) -> None:
        cases = (
            (worker._StreamResult(2, b"", b"secret"), 2, "verifier-crash"),
            (worker._StreamResult(0, b"", b"secret"), 0, "verifier-result-missing"),
            (worker._StreamResult(0, b"not-json", b"secret"), 0, "verifier-result-malformed"),
        )
        for stream, state_exit, issue in cases:
            with self.subTest(issue=issue):
                self.fake = FakeDocker()
                self.fake.verifier_stream = stream
                self.fake.verifier_state_exit = state_exit
                report = self.run_worker()
                self.assertIn(issue, report["observation"]["issues"])
                self.assertFalse(report["passed"])
                self.assertNotEqual(report["outcome"]["disposition"], "scored")
                self.assertNotIn("secret", canonical_json(report))


if __name__ == "__main__":
    unittest.main()
