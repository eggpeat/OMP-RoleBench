from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
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
DOCKER_PREFIX = [
    "docker-fixture",
    "--host",
    f"unix:///run/user/{os.getuid()}/docker.sock",
]


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
        self.ownership: str | None = None
        self.inspect_mutator = None
        self.verifier_input: bytes | None = None
        self.reconcile_after_create_error: str | None = None
        self.inspect_failure: str | None = None
        self.events: list[str] = []
        self.oom_agent = False
        self.agent_state_exit = 0
        self.verifier_state_exit = 0
        self.remove_failure: str | None = None

    def run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        input_source: bytes | object | None = None,
    ) -> worker._CommandResult:
        self.commands.append(list(argv))
        if argv[len(DOCKER_PREFIX):len(DOCKER_PREFIX) + 3] == ["info", "--format", "{{json .}}"]:
            return _result(stdout=json.dumps(self.info).encode())
        if argv == ["/fixture/rolebench-runsc-wrapper", "--rolebench-doctor"]:
            return _result(stdout=json.dumps(self.wrapper_report).encode())
        command = argv[len(DOCKER_PREFIX)] if argv[:len(DOCKER_PREFIX)] == DOCKER_PREFIX else None
        if command == "create":
            name = argv[argv.index("--name") + 1]
            container = "verifier-id" if "verifier" in name else "agent-id"
            self.created[container] = True
            self.finished[container] = False
            self.ownership = argv[argv.index("--label") + 1].split("=", 1)[1]
            if self.reconcile_after_create_error == container:
                raise subprocess.TimeoutExpired(argv, 1)
            return _result(stdout=(container + "\n").encode())
        if command == "inspect":
            container = argv[-1]
            if self.inspect_failure == container:
                return _result(code=1, stderr=b"daemon unavailable")
            if container in {
                "rolebench-agent-fixture-1",
                "rolebench-verifier-fixture-1",
            }:
                container = "verifier-id" if "verifier" in container else "agent-id"
            if container not in self.created:
                return _result(code=1, stderr=b"Error: No such container")
            self.events.append(f"inspect:{container}:{self.finished.get(container, False)}")
            value = self._inspect_value(container)
            if self.inspect_mutator is not None:
                self.inspect_mutator(container, value)
            return _result(stdout=json.dumps([value]).encode())
        if command == "kill":
            self.finished[argv[-1]] = True
            return _result()
        if command == "rm":
            container = argv[-1]
            return _result(code=1 if container == self.remove_failure else 0)
        raise AssertionError(f"unexpected command: {argv!r}")

    def stream(
        self,
        argv: list[str],
        *,
        input_source: bytes | object | None,
        timeout: float,
        output_limit: int,
        output_sink: object | None = None,
    ) -> worker._StreamResult:
        payload = input_source if isinstance(input_source, bytes) else None
        self.stream_calls.append((list(argv), payload, output_limit))
        container = argv[-1]
        self.events.append(f"stream:{container}")
        self.finished[container] = True
        if container == "agent-id":
            if output_sink is not None:
                output_sink.write(self.agent_stream.stdout)
                return worker._StreamResult(
                    self.agent_stream.returncode,
                    b"",
                    self.agent_stream.stderr,
                    self.agent_stream.timed_out,
                    self.agent_stream.overflowed,
                )
            return self.agent_stream
        if input_source is not None and not isinstance(input_source, bytes):
            input_source.seek(0)
            payload = input_source.read()
        self.verifier_input = payload
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
            "Id": container,
            "Config": {
                "User": f"{uid}:{gid}",
                "OpenStdin": verifier,
                "Image": VERIFIER_IMAGE if verifier else AGENT_IMAGE,
                "Labels": {"org.omp.rolebench.owner": self.ownership},
            },
            "HostConfig": {
                "Runtime": "runsc",
                "Privileged": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges=true"],
                "NetworkMode": "none",
                "PidMode": "",
                "LogConfig": {"Type": "none", "Config": {}},
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
            worker.WorkerError,
            "must use a different image digest from the agent",
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
            [*DOCKER_PREFIX, "info", "--format", "{{json .}}"],
        )
        self.assertEqual(
            self.fake.commands[1],
            ["/fixture/rolebench-runsc-wrapper", "--rolebench-doctor"],
        )
        self.assertTrue(report["resource_enforcement"])

        mutations = {
            "rootless": lambda value: value.update(SecurityOptions=["name=notrootless"]),
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
                    self.root,
                    Path("contracts/scored-worker-policy.json"),
                    docker="docker-fixture",
                )
                self.assertFalse(report["ready"])
                self.assertIn(f"requirement-failed:{label}", report["diagnostics"])

        for near_match in (
            ["description=rootless daemon"],
            [{"name": "notrootless"}],
            [{"description": "name=rootless"}],
        ):
            with self.subTest(near_match=near_match):
                self.fake.info["SecurityOptions"] = near_match
                report = worker.doctor_worker(
                    self.root,
                    Path("contracts/scored-worker-policy.json"),
                    docker="docker-fixture",
                )
                self.assertFalse(report["rootless"])
                self.assertFalse(report["ready"])
        self.fake.info["SecurityOptions"] = [{"name": "rootless"}]
        report = worker.doctor_worker(
            self.root,
            Path("contracts/scored-worker-policy.json"),
            docker="docker-fixture",
        )
        self.assertTrue(report["rootless"])

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
        creates = [
            command
            for command in self.fake.commands
            if command[:len(DOCKER_PREFIX)] == DOCKER_PREFIX
            and command[len(DOCKER_PREFIX)] == "create"
        ]
        self.assertEqual(len(creates), 2)
        agent, verifier = creates
        self.assertEqual(
            agent[:len(DOCKER_PREFIX) + 3],
            [*DOCKER_PREFIX, "create", "--name", "rolebench-agent-fixture-1"],
        )
        for command in creates:
            self.assertIn(["--runtime", "runsc"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn(["--network", "none"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn(["--cap-drop", "ALL"], [command[index:index + 2] for index in range(len(command) - 1)])
            self.assertIn(
                ["--log-driver", "none"],
                [command[index:index + 2] for index in range(len(command) - 1)],
            )
            self.assertIn("--read-only", command)
            self.assertNotIn("--mount", command)
            self.assertNotIn("--volume", command)
            self.assertNotIn("--device", command)
        self.assertIn("--tmpfs", agent)
        self.assertNotIn("--tmpfs", verifier)
        self.assertNotIn("--interactive", agent)
        self.assertEqual(agent[-4:], ["--", AGENT_IMAGE, "/agent.sh", "--fixture"])
        self.assertEqual(verifier[-3:], ["--", VERIFIER_IMAGE, "/verifier.sh"])
        self.assertEqual(
            self.fake.stream_calls[0][0],
            [*DOCKER_PREFIX, "start", "--attach", "agent-id"],
        )
        self.assertEqual(
            self.fake.stream_calls[1][0],
            [*DOCKER_PREFIX, "start", "--attach", "-i", "verifier-id"],
        )
        self.assertIn(
            [*DOCKER_PREFIX, "rm", "--force", "--volumes", "agent-id"],
            self.fake.commands,
        )
        self.assertIn(
            [*DOCKER_PREFIX, "rm", "--force", "--volumes", "verifier-id"],
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

    def test_effective_image_mismatch_rejects_before_agent_start(self) -> None:
        def mutate(container: str, value: dict[str, object]) -> None:
            if container == "agent-id":
                value["Config"]["Image"] = "mutable.invalid/agent:latest"

        self.fake.inspect_mutator = mutate
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("sandbox-violation", report["observation"]["issues"])
        self.assertFalse(any(call[0][-1] == "agent-id" for call in self.fake.stream_calls))

    def test_agent_stream_nonzero_never_freezes_partial_artifact(self) -> None:
        self.fake.agent_stream = worker._StreamResult(1, ARTIFACT, b"attach failed")
        self.fake.agent_state_exit = 0
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertFalse(report["observation"]["lifecycle"]["artifact_frozen"])
        self.assertIsNone(report["artifact_digest_sha256"])
        self.assertEqual(report["outcome"]["reason_code"], "runner-failure")


    def test_artifact_overflow_kills_before_freeze_and_maps_collection(self) -> None:
        self.fake.agent_stream = worker._StreamResult(137, b"x", b"secret", overflowed=True)
        report = self.run_worker()
        self.assertIn("artifact-collection", report["observation"]["issues"])
        self.assertIsNone(report["artifact_digest_sha256"])
        self.assertFalse(report["observation"]["lifecycle"]["artifact_frozen"])
        self.assertTrue(
            any(
                command[:len(DOCKER_PREFIX) + 2]
                == [*DOCKER_PREFIX, "kill", "agent-id"]
                for command in self.fake.commands
            )
        )

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

    def test_verifier_overflow_remains_malformed_not_crash(self) -> None:
        self.fake.verifier_stream = worker._StreamResult(
            137,
            b"{",
            b"",
            overflowed=True,
        )
        report = self.run_worker()
        self.assertEqual(
            report["outcome"]["reason_code"],
            "verifier-result-malformed",
        )
        self.assertNotIn("verifier-crash", report["observation"]["issues"])

    def test_create_timeout_reconciles_only_owned_container(self) -> None:
        self.fake.reconcile_after_create_error = "agent-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn(
            [*DOCKER_PREFIX, "rm", "--force", "--volumes", "agent-id"],
            self.fake.commands,
        )

    def test_cleanup_failure_fails_the_pipeline_closed(self) -> None:
        self.fake.remove_failure = "verifier-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn("verifier-container-cleanup-failed", report["diagnostics"])
        self.assertEqual(report["outcome"]["reason_code"], "runner-failure")

    def test_ambiguous_reconciliation_failure_is_not_absence(self) -> None:
        self.fake.reconcile_after_create_error = "agent-id"
        self.fake.inspect_failure = "rolebench-agent-fixture-1"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("agent-container-cleanup-failed", report["diagnostics"])
        self.assertIn("runner-failure", report["observation"]["issues"])

    def test_agent_cleanup_failure_skips_verifier_and_fails_closed(self) -> None:
        self.fake.remove_failure = "agent-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn("agent-container-cleanup-failed", report["diagnostics"])
        self.assertFalse(
            any(
                command[:len(DOCKER_PREFIX)] == DOCKER_PREFIX
                and command[len(DOCKER_PREFIX)] == "create"
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
