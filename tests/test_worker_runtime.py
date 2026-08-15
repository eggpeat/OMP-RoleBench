from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from rolebench.contracts import canonical_json, validate_artifact
from rolebench.runner_protocol import (
    RUNNER_EVIDENCE_MAGIC,
    RUNNER_EVIDENCE_SCHEMA_VERSION,
    VERIFIER_RESULT_SCHEMA_VERSION,
    EVALUATION_REQUEST_SCHEMA_VERSION,
    build_runner_evidence,
    compute_evaluation_request_digest,
    parse_runner_evidence,
    validate_verifier_result,
)
from rolebench import worker, worker_v1


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"
AGENT_IMAGE = "example.invalid/agent@sha256:" + "1" * 64
RUNNER_IMAGE = "example.invalid/runner@sha256:" + "2" * 64
VERIFIER_IMAGE = "example.invalid/verifier@sha256:" + "3" * 64
ARTIFACT = b"deterministic\x00artifact\n"
RUNNER_STDOUT = b"runner stdout line\n"
RUNNER_STDERR = b"runner stderr line\n"
QUALIFICATION_DIGEST = "4" * 64
PUBLIC_TREE_DIGEST = "5" * 64
PRIVATE_TREE_DIGEST = "6" * 64
AGENT_CONFIG_DIGEST = "7" * 64
RUNNER_CONFIG_DIGEST = "8" * 64
VERIFIER_CONFIG_DIGEST = "9" * 64
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
TASK_ROLE_LABEL = "org.omp.rolebench.task.role"
TASK_STAGE_LABEL = "org.omp.rolebench.task.stage"
PUBLIC_TREE_LABEL = "org.omp.rolebench.task.public-tree-sha256"
PRIVATE_TREE_LABEL = "org.omp.rolebench.task.verifier-private-tree-sha256"


class RunnerProtocolTests(unittest.TestCase):
    def test_evaluation_request_digest_is_canonical_and_binds_every_mapping(
        self,
    ) -> None:
        runner = {
            "image": RUNNER_IMAGE,
            "config_digest_sha256": RUNNER_CONFIG_DIGEST,
            "platform": {
                "os": "linux",
                "architecture": "amd64",
                "variant": None,
            },
            "argv": ["/runner.py"],
        }
        verifier = {
            "image": VERIFIER_IMAGE,
            "config_digest_sha256": VERIFIER_CONFIG_DIGEST,
            "platform": {
                "os": "linux",
                "architecture": "amd64",
                "variant": None,
            },
            "argv": ["/verifier.py"],
        }
        nonce = "a" * 64
        actual = compute_evaluation_request_digest(
            attempt_nonce=nonce,
            run_id="fixture-1",
            task_digest_sha256="b" * 64,
            runner_manifest=runner,
            verifier_manifest=verifier,
        )
        expected_request = {
            "schema_version": EVALUATION_REQUEST_SCHEMA_VERSION,
            "attempt_nonce": nonce,
            "run_id": "fixture-1",
            "task_digest_sha256": "b" * 64,
            "runner": runner,
            "verifier": verifier,
        }
        self.assertEqual(
            actual,
            hashlib.sha256(
                canonical_json(expected_request).encode("utf-8")
            ).hexdigest(),
        )
        self.assertNotEqual(
            actual,
            compute_evaluation_request_digest(
                attempt_nonce="c" * 64,
                run_id="fixture-1",
                task_digest_sha256="b" * 64,
                runner_manifest=runner,
                verifier_manifest=verifier,
            ),
        )


def _result(
    code: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> worker._CommandResult:
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
            "Runtimes": {"runsc": {"path": "/fixture/rolebench-runsc-wrapper"}},
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
        self.agent_stream = worker._StreamResult(
            0, ARTIFACT, b"private agent log"
        )
        self.runner_stream = worker._StreamResult(
            0, RUNNER_STDOUT, RUNNER_STDERR
        )
        self.verifier_stream: worker._StreamResult | None = None
        self.verifier_outcome = "accepted"
        self.verifier_reward: float | None = 1.0
        self.custom_verifier_payload: dict[str, object] | None = None
        self.ownership: str | None = None
        self.inspect_mutator = None
        self.runner_input: bytes | None = None
        self.verifier_input: bytes | None = None
        self.reconcile_after_create_error: str | None = None
        self.inspect_failure: str | None = None
        self.events: list[str] = []
        self.oom_agent = False
        self.oom_runner = False
        self.agent_state_exit = 0
        self.runner_state_exit = 0
        self.verifier_state_exit = 0
        self.remove_failure: str | None = None
        self.image_inspects: dict[str, dict[str, object]] = {}
        self.container_config_digests: dict[str, str] = {}
        self.container_task_labels: dict[str, dict[str, str]] = {}

    def run(
        self,
        argv: list[str],
        *,
        timeout: float | None = None,
        input_source: bytes | object | None = None,
    ) -> worker._CommandResult:
        self.commands.append(list(argv))
        command = (
            argv[len(DOCKER_PREFIX)]
            if argv[: len(DOCKER_PREFIX)] == DOCKER_PREFIX
            else None
        )
        if argv[len(DOCKER_PREFIX) : len(DOCKER_PREFIX) + 3] == [
            "info",
            "--format",
            "{{json .}}",
        ]:
            return _result(stdout=json.dumps(self.info).encode())
        if argv == ["/fixture/rolebench-runsc-wrapper", "--rolebench-doctor"]:
            return _result(stdout=json.dumps(self.wrapper_report).encode())
        if command == "image":
            image = argv[len(DOCKER_PREFIX) + 2]
            inspect = self.image_inspects.get(image)
            if inspect is None:
                return _result(code=1, stderr=b"image unavailable")
            return _result(stdout=json.dumps(inspect).encode())

        if command == "create":
            name = argv[argv.index("--name") + 1]
            if "verifier" in name:
                container = "verifier-id"
            elif "runner" in name:
                container = "runner-id"
            else:
                container = "agent-id"
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
                "rolebench-runner-fixture-1",
                "rolebench-verifier-fixture-1",
            }:
                if "verifier" in container:
                    container = "verifier-id"
                elif "runner" in container:
                    container = "runner-id"
                else:
                    container = "agent-id"
            if container not in self.created:
                return _result(code=1, stderr=b"Error: No such container")
            self.events.append(
                f"inspect:{container}:{self.finished.get(container, False)}"
            )
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
        if container == "runner-id":
            if input_source is not None and not isinstance(input_source, bytes):
                input_source.seek(0)
                self.runner_input = input_source.read()
            return self.runner_stream
        if input_source is not None and not isinstance(input_source, bytes):
            input_source.seek(0)
            self.verifier_input = input_source.read()
        if self.verifier_stream is not None:
            return self.verifier_stream

        # Dynamically build a valid verifier result echoing runner evidence bindings
        if self.custom_verifier_payload is not None:
            stdout_bytes = json.dumps(
                self.custom_verifier_payload
            ).encode("utf-8")
        elif self.verifier_input is not None:
            header, _, _ = parse_runner_evidence(self.verifier_input)
            payload_data = {
                "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
                "run_id": header["run_id"],
                "attempt_nonce": header["attempt_nonce"],
                "outcome": self.verifier_outcome,
                "reward": self.verifier_reward,
                "artifact_digest_sha256": header["artifact_digest_sha256"],
                "runner_evidence_digest_sha256": hashlib.sha256(
                    self.verifier_input
                ).hexdigest(),
                "evaluation_request_digest_sha256": header[
                    "evaluation_request_digest_sha256"
                ],
                "verifier_image_digest_sha256": VERIFIER_IMAGE.rsplit(
                    "@sha256:", 1
                )[1],
            }
            stdout_bytes = json.dumps(payload_data).encode("utf-8")
        else:
            stdout_bytes = b'{"outcome":"accepted","reward":1}'
        return worker._StreamResult(0, stdout_bytes, b"private verifier log")

    def _inspect_value(self, container: str) -> dict[str, object]:
        verifier = container == "verifier-id"
        runner = container == "runner-id"
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
        exit_code = (
            self.verifier_state_exit
            if verifier
            else self.runner_state_exit
            if runner
            else self.agent_state_exit
        )
        labels = {"org.omp.rolebench.owner": self.ownership}
        labels.update(self.container_task_labels.get(container, {}))
        image_name = (
            VERIFIER_IMAGE
            if verifier
            else RUNNER_IMAGE
            if runner
            else AGENT_IMAGE
        )
        oom = (
            (self.oom_agent and container == "agent-id")
            or (self.oom_runner and container == "runner-id")
        )
        value: dict[str, object] = {
            "Id": container,
            "Config": {
                "User": f"{uid}:{gid}",
                "OpenStdin": verifier or runner,
                "Image": image_name,
                "Labels": labels,
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
                "OOMKilled": oom,
                "Status": (
                    "exited" if self.finished.get(container) else "created"
                ),
            },
        }
        config_digest = self.container_config_digests.get(container)
        if config_digest is not None:
            value["Image"] = f"sha256:{config_digest}"
        return value


class WorkerRuntimeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")
        policy_path = self.root / "contracts/scored-worker-policy-v2.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(canonical_json(policy).encode()).hexdigest()
        platform = {"os": "linux", "architecture": "amd64", "variant": None}
        self.manifest = {
            "schema_version": "omp.worker-run-manifest/v2",
            "run_id": "fixture-1",
            "role": "task",
            "task": {"digest_sha256": "3" * 64},
            "policy": {
                "path": "contracts/scored-worker-policy-v2.json",
                "digest_sha256": digest,
            },
            "provider": {"enabled": False},
            "agent": {
                "image": AGENT_IMAGE,
                "config_digest_sha256": AGENT_CONFIG_DIGEST,
                "platform": platform,
                "argv": ["/agent.sh", "--fixture"],
            },
            "runner": {
                "image": RUNNER_IMAGE,
                "config_digest_sha256": RUNNER_CONFIG_DIGEST,
                "platform": platform,
                "argv": ["/runner.sh"],
            },
            "verifier": {
                "image": VERIFIER_IMAGE,
                "config_digest_sha256": VERIFIER_CONFIG_DIGEST,
                "platform": platform,
                "argv": ["/verifier.sh"],
            },
        }
        self.manifest_path = self.root / "worker.json"
        self._write_manifest()
        self.fake = FakeDocker()

        for image, config_digest in (
            (AGENT_IMAGE, AGENT_CONFIG_DIGEST),
            (RUNNER_IMAGE, RUNNER_CONFIG_DIGEST),
            (VERIFIER_IMAGE, VERIFIER_CONFIG_DIGEST),
        ):
            self.fake.image_inspects[image] = {
                "Id": f"sha256:{config_digest}",
                "RepoDigests": [image],
                "Descriptor": {
                    "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                    "digest": image.rsplit("@", 1)[1],
                },
                "Os": "linux",
                "Architecture": "amd64",
                "Variant": None,
                "Config": {"Labels": {}},
            }
        self.fake.container_config_digests = {
            "agent-id": AGENT_CONFIG_DIGEST,
            "runner-id": RUNNER_CONFIG_DIGEST,
            "verifier-id": VERIFIER_CONFIG_DIGEST,
        }

        patcher = mock.patch.object(
            worker, "_ADAPTER_FACTORY", new=lambda: self.fake
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def run_worker(self) -> dict[str, object]:
        return worker.run_worker(
            self.root, Path("worker.json"), docker="docker-fixture"
        )

    def enable_task_binding(self) -> None:
        self.manifest["task"].update(
            {
                "qualification_digest_sha256": QUALIFICATION_DIGEST,
                "public_tree_digest_sha256": PUBLIC_TREE_DIGEST,
                "verifier_private_tree_digest_sha256": PRIVATE_TREE_DIGEST,
                "evidence_use": "calibration-only",
            }
        )
        agent_labels = {
            TASK_ROLE_LABEL: "task",
            TASK_STAGE_LABEL: "agent",
            PUBLIC_TREE_LABEL: PUBLIC_TREE_DIGEST,
        }
        runner_labels = {
            TASK_ROLE_LABEL: "task",
            TASK_STAGE_LABEL: "runner",
            PUBLIC_TREE_LABEL: PUBLIC_TREE_DIGEST,
        }
        verifier_labels = {
            TASK_ROLE_LABEL: "task",
            TASK_STAGE_LABEL: "verifier",
            PRIVATE_TREE_LABEL: PRIVATE_TREE_DIGEST,
        }
        for image, config_digest, labels in (
            (AGENT_IMAGE, AGENT_CONFIG_DIGEST, agent_labels),
            (RUNNER_IMAGE, RUNNER_CONFIG_DIGEST, runner_labels),
            (VERIFIER_IMAGE, VERIFIER_CONFIG_DIGEST, verifier_labels),
        ):
            self.fake.image_inspects[image] = {
                "Id": f"sha256:{config_digest}",
                "RepoDigests": [image],
                "Descriptor": {
                    "mediaType": OCI_MANIFEST_MEDIA_TYPE,
                    "digest": image.rsplit("@", 1)[1],
                },
                "Os": "linux",
                "Architecture": "amd64",
                "Variant": None,
                "Config": {"Labels": labels.copy()},
            }
        self.fake.container_config_digests = {
            "agent-id": AGENT_CONFIG_DIGEST,
            "runner-id": RUNNER_CONFIG_DIGEST,
            "verifier-id": VERIFIER_CONFIG_DIGEST,
        }
        self.fake.container_task_labels = {
            "agent-id": agent_labels.copy(),
            "runner-id": runner_labels.copy(),
            "verifier-id": verifier_labels.copy(),
        }
        self._write_manifest()


class ManifestRuntimeGuardTests(WorkerRuntimeFixture):
    def test_distinct_repositories_with_same_image_digest_are_rejected(
        self,
    ) -> None:
        self.manifest["verifier"]["image"] = (
            "another.invalid/verifier@sha256:" + "1" * 64
        )
        self._write_manifest()

        with self.assertRaises(worker.WorkerError):
            self.run_worker()
        self.assertEqual(self.fake.commands, [])

    def test_runner_and_agent_same_image_digest_rejected(self) -> None:
        self.manifest["runner"]["image"] = (
            "another.invalid/runner@sha256:" + "1" * 64
        )
        self._write_manifest()

        with self.assertRaises(worker.WorkerError):
            self.run_worker()
        self.assertEqual(self.fake.commands, [])

    def test_same_runner_and_verifier_config_digest_rejected_at_manifest_load(
        self,
    ) -> None:
        self.manifest["verifier"]["config_digest_sha256"] = RUNNER_CONFIG_DIGEST
        self._write_manifest()

        with self.assertRaises(worker.WorkerError):
            self.run_worker()
        self.assertEqual(self.fake.commands, [])

    def test_v2_manifest_is_captured_once_before_execution(self) -> None:
        original_capture = worker._capture_object
        manifest_captures = 0

        def capture_once(
            root: Path,
            path: Path,
            *,
            label: str,
        ) -> tuple[
            dict[str, object],
            Path,
            tuple[int, int, int, int, int],
            str,
        ]:
            nonlocal manifest_captures
            if label == "worker run manifest":
                manifest_captures += 1
                if manifest_captures > 1:
                    raise AssertionError("manifest was captured more than once")
            return original_capture(root, path, label=label)

        with mock.patch.object(worker, "_capture_object", side_effect=capture_once):
            report = self.run_worker()

        self.assertTrue(report["passed"], report["diagnostics"])
        self.assertEqual(manifest_captures, 1)


class DoctorTests(WorkerRuntimeFixture):
    def test_doctor_uses_exact_info_argv_and_requires_every_prerequisite(
        self,
    ) -> None:
        report = worker.doctor_worker(
            self.root,
            Path("contracts/scored-worker-policy-v2.json"),
            docker="docker-fixture",
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


class LegacyRuntimeCompatibilityTests(WorkerRuntimeFixture):
    def _configure_v1_manifest(self) -> tuple[dict[str, object], str]:
        policy_path = self.root / "contracts/scored-worker-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        expected_digest = hashlib.sha256(
            canonical_json(policy).encode("utf-8")
        ).hexdigest()
        self.manifest["schema_version"] = "omp.worker-run-manifest/v1"
        self.manifest.pop("runner")
        for container_name in ("agent", "verifier"):
            self.manifest[container_name].pop("config_digest_sha256")
            self.manifest[container_name].pop("platform")
        self.manifest["policy"] = {
            "path": "contracts/scored-worker-policy.json",
            "digest_sha256": expected_digest,
        }
        self.fake.verifier_stream = worker._StreamResult(
            0,
            b'{"outcome":"accepted","reward":1}',
            b"private verifier log",
        )
        self._write_manifest()
        return policy, expected_digest

    def test_v1_manifest_executes_without_a_runner_container(self) -> None:
        policy, expected_digest = self._configure_v1_manifest()
        self.assertEqual(policy["schema_version"], "omp.scored-worker-policy/v1")
        self.assertEqual(policy["policy_id"], "rolebench-scored-worker-v1")
        self.assertEqual(
            expected_digest,
            "c773b99f959a64065d0237ac96af2c70f3e3fd6c676c87383645b94f8d1983ac",
        )

        report = self.run_worker()

        self.assertTrue(report["passed"], report["diagnostics"])
        self.assertEqual(len(self.fake.stream_calls), 2)
        self.assertEqual(self.fake.verifier_input, ARTIFACT)
        self.assertEqual(
            report["observation"]["schema_version"],
            "omp.attempt-observation/v1",
        )
        self.assertEqual(
            report["outcome"]["schema_version"],
            "omp.attempt-outcome/v1",
        )

    def test_v1_manifest_is_not_recaptured_after_dispatch(self) -> None:
        self._configure_v1_manifest()
        original_capture = worker_v1._capture_object
        manifest_captures = 0

        def capture_non_manifest(
            root: Path,
            path: Path,
            *,
            label: str,
        ) -> tuple[
            dict[str, object],
            Path,
            tuple[int, int, int, int, int],
            str,
        ]:
            nonlocal manifest_captures
            if label == "worker run manifest":
                manifest_captures += 1
                raise AssertionError("legacy runtime recaptured the manifest")
            return original_capture(root, path, label=label)

        with mock.patch.object(
            worker_v1,
            "_capture_object",
            side_effect=capture_non_manifest,
        ):
            report = self.run_worker()

        self.assertTrue(report["passed"], report["diagnostics"])
        self.assertEqual(manifest_captures, 0)

    def test_v1_manifest_with_tampered_policy_digest_fails_closed(self) -> None:
        self._configure_v1_manifest()
        self.manifest["policy"]["digest_sha256"] = "0" * 64
        self._write_manifest()

        with self.assertRaises(worker.WorkerError):
            self.run_worker()

class ImageBindingAndIsolationTests(WorkerRuntimeFixture):
    def _assert_binding_rejected_before_stream(self) -> dict[str, object]:
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("sandbox-violation", report["observation"]["issues"])
        self.assertEqual(report["outcome"]["disposition"], "quarantined")
        self.assertEqual(self.fake.stream_calls, [])
        return report

    def test_unbound_valid_manifest_preflight_verifies_all_images(
        self,
    ) -> None:
        report = self.run_worker()
        self.assertTrue(report["passed"])
        self.assertEqual(len(self.fake.stream_calls), 3)
        self.assertTrue(report["isolation"]["image_binding_verified"])

    def test_unbound_runner_config_id_mismatch_rejected_before_agent_stream(
        self,
    ) -> None:
        self.fake.image_inspects[RUNNER_IMAGE]["Id"] = "sha256:" + "0" * 64
        self._assert_binding_rejected_before_stream()

    def test_unbound_verifier_platform_mismatch_rejected_before_agent_stream(
        self,
    ) -> None:
        self.fake.image_inspects[VERIFIER_IMAGE]["Architecture"] = "arm64"
        self._assert_binding_rejected_before_stream()

    def test_unbound_agent_manifest_digest_mismatch_rejected_before_agent_stream(
        self,
    ) -> None:
        self.fake.image_inspects[AGENT_IMAGE]["Descriptor"]["digest"] = (
            "sha256:" + "0" * 64
        )
        self._assert_binding_rejected_before_stream()

    def test_effective_runner_container_config_id_mismatch_rejected(
        self,
    ) -> None:
        def mutate(container: str, value: dict[str, object]) -> None:
            if container == "runner-id":
                value["Image"] = "sha256:" + "0" * 64

        self.fake.inspect_mutator = mutate
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("sandbox-violation", report["observation"]["issues"])


class TaskImageBindingTests(WorkerRuntimeFixture):
    def _assert_binding_rejected_before_stream(self) -> dict[str, object]:
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("sandbox-violation", report["observation"]["issues"])
        self.assertEqual(report["outcome"]["disposition"], "quarantined")
        self.assertEqual(self.fake.stream_calls, [])
        return report

    def _assert_image_label_rejected(
        self,
        image: str,
        label: str,
        replacement: str | None,
    ) -> None:
        self.enable_task_binding()
        labels = self.fake.image_inspects[image]["Config"]["Labels"]
        if replacement is None:
            labels.pop(label)
        else:
            labels[label] = replacement
        self._assert_binding_rejected_before_stream()

    def test_valid_binding_reaches_pipeline_and_records_bound_digests(
        self,
    ) -> None:
        self.enable_task_binding()

        report = self.run_worker()

        self.assertTrue(report["passed"])
        self.assertEqual(len(self.fake.stream_calls), 3)
        digests = report["observation"]["digests"]
        self.assertEqual(
            {
                key: digests[key]
                for key in (
                    "config",
                    "qualification",
                    "task_public_tree",
                    "verifier_private_tree",
                    "agent_image_config",
                    "runner_image_config",
                    "verifier_image_config",
                )
            },
            {
                "config": hashlib.sha256(
                    canonical_json(self.manifest).encode()
                ).hexdigest(),
                "qualification": QUALIFICATION_DIGEST,
                "task_public_tree": PUBLIC_TREE_DIGEST,
                "verifier_private_tree": PRIVATE_TREE_DIGEST,
                "agent_image_config": AGENT_CONFIG_DIGEST,
                "runner_image_config": RUNNER_CONFIG_DIGEST,
                "verifier_image_config": VERIFIER_CONFIG_DIGEST,
            },
        )
        self.assertTrue(report["isolation"]["image_binding_verified"])

    def test_missing_runner_stage_label_rejected_before_stream(self) -> None:
        self._assert_image_label_rejected(RUNNER_IMAGE, TASK_STAGE_LABEL, None)

    def test_wrong_runner_stage_label_rejected_before_stream(self) -> None:
        self._assert_image_label_rejected(
            RUNNER_IMAGE, TASK_STAGE_LABEL, "agent"
        )

    def test_missing_runner_public_tree_label_rejected_before_stream(
        self,
    ) -> None:
        self._assert_image_label_rejected(RUNNER_IMAGE, PUBLIC_TREE_LABEL, None)


class SuccessfulThreeContainerRuntimeTests(WorkerRuntimeFixture):
    def test_exact_three_container_lifecycle_and_evidence_protocol(
        self,
    ) -> None:
        report = self.run_worker()
        creates = [
            command
            for command in self.fake.commands
            if command[: len(DOCKER_PREFIX)] == DOCKER_PREFIX
            and command[len(DOCKER_PREFIX)] == "create"
        ]
        self.assertEqual(len(creates), 3)
        agent, runner, verifier = creates
        self.assertEqual(
            agent[: len(DOCKER_PREFIX) + 3],
            [*DOCKER_PREFIX, "create", "--name", "rolebench-agent-fixture-1"],
        )
        self.assertEqual(
            runner[: len(DOCKER_PREFIX) + 3],
            [*DOCKER_PREFIX, "create", "--name", "rolebench-runner-fixture-1"],
        )
        self.assertEqual(
            verifier[: len(DOCKER_PREFIX) + 3],
            [
                *DOCKER_PREFIX,
                "create",
                "--name",
                "rolebench-verifier-fixture-1",
            ],
        )

        for command in creates:
            self.assertIn(
                ["--runtime", "runsc"],
                [
                    command[idx : idx + 2]
                    for idx in range(len(command) - 1)
                ],
            )
            self.assertIn(
                ["--network", "none"],
                [
                    command[idx : idx + 2]
                    for idx in range(len(command) - 1)
                ],
            )
            self.assertIn(
                ["--cap-drop", "ALL"],
                [
                    command[idx : idx + 2]
                    for idx in range(len(command) - 1)
                ],
            )
            self.assertIn(
                ["--log-driver", "none"],
                [
                    command[idx : idx + 2]
                    for idx in range(len(command) - 1)
                ],
            )
            self.assertIn("--read-only", command)
            self.assertNotIn("--mount", command)
            self.assertNotIn("--volume", command)
            self.assertNotIn("--device", command)

        self.assertIn("--tmpfs", agent)
        self.assertIn("--tmpfs", runner)
        self.assertNotIn("--tmpfs", verifier)
        self.assertNotIn("--interactive", agent)
        self.assertIn("--interactive", runner)
        self.assertIn("--interactive", verifier)

        self.assertEqual(len(self.fake.stream_calls), 3)
        self.assertEqual(
            self.fake.stream_calls[0][0],
            [*DOCKER_PREFIX, "start", "--attach", "agent-id"],
        )
        self.assertEqual(
            self.fake.stream_calls[1][0],
            [*DOCKER_PREFIX, "start", "--attach", "-i", "runner-id"],
        )
        self.assertEqual(
            self.fake.stream_calls[2][0],
            [*DOCKER_PREFIX, "start", "--attach", "-i", "verifier-id"],
        )

        # Runner input is the sealed agent artifact
        self.assertEqual(self.fake.runner_input, ARTIFACT)

        # Verifier input is the sealed runner evidence
        self.assertIsNotNone(self.fake.verifier_input)
        assert self.fake.verifier_input is not None
        self.assertTrue(
            self.fake.verifier_input.startswith(RUNNER_EVIDENCE_MAGIC)
        )
        evidence_header, ev_stdout, ev_stderr = parse_runner_evidence(
            self.fake.verifier_input
        )
        self.assertEqual(ev_stdout, RUNNER_STDOUT)
        self.assertEqual(ev_stderr, RUNNER_STDERR)
        self.assertEqual(evidence_header["stdout"]["authority"], "untrusted")
        self.assertEqual(evidence_header["stderr"]["authority"], "untrusted")
        self.assertEqual(
            evidence_header["artifact_digest_sha256"],
            hashlib.sha256(ARTIFACT).hexdigest(),
        )
        self.assertEqual(
            evidence_header["verifier_image_digest_sha256"],
            VERIFIER_IMAGE.rsplit("@sha256:", 1)[1],
        )

        # Report checks
        self.assertTrue(report["passed"])
        self.assertEqual(
            report["artifact_digest_sha256"],
            hashlib.sha256(ARTIFACT).hexdigest(),
        )
        self.assertEqual(
            report["runner_evidence_digest_sha256"],
            hashlib.sha256(self.fake.verifier_input).hexdigest(),
        )
        self.assertTrue(
            report["isolation"]["artifact_frozen_after_agent_exit"]
        )
        self.assertTrue(
            report["isolation"]["runner_evidence_frozen_after_runner_exit"]
        )
        self.assertTrue(report["isolation"]["immutable_agent_runner_handoff"])
        self.assertTrue(
            report["isolation"]["immutable_runner_verifier_handoff"]
        )

        # Lifecycle checks
        lifecycle = report["observation"]["lifecycle"]
        self.assertTrue(lifecycle["environment_started"])
        self.assertTrue(lifecycle["agent_started"])
        self.assertTrue(lifecycle["agent_finished"])
        self.assertTrue(lifecycle["artifact_frozen"])
        self.assertTrue(lifecycle["runner_started"])
        self.assertTrue(lifecycle["runner_finished"])
        self.assertTrue(lifecycle["runner_evidence_frozen"])
        self.assertTrue(lifecycle["verifier_started"])
        self.assertTrue(lifecycle["verifier_finished"])


class AdversarialIsolationAndHandoffTests(WorkerRuntimeFixture):
    def test_reflected_artifact_is_framed_as_untrusted_runner_output(
        self,
    ) -> None:
        self.fake.runner_stream = worker._StreamResult(0, ARTIFACT, b"")
        report = self.run_worker()
        self.assertTrue(report["passed"])
        assert self.fake.verifier_input is not None
        self.assertNotEqual(self.fake.verifier_input, ARTIFACT)
        header, stdout, stderr = parse_runner_evidence(
            self.fake.verifier_input
        )
        self.assertEqual(stdout, ARTIFACT)
        self.assertEqual(stderr, b"")
        self.assertEqual(header["stdout"]["authority"], "untrusted")
        self.assertEqual(
            header["stdout"]["digest_sha256"],
            hashlib.sha256(ARTIFACT).hexdigest(),
        )

    def test_runner_authored_fake_accepted_json_cannot_become_verifier_verdict(
        self,
    ) -> None:
        # Runner tries to spoof accepted verifier JSON in its stdout
        spoofed = json.dumps(
            {
                "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
                "run_id": "fixture-1",
                "attempt_nonce": "f" * 64,
                "outcome": "accepted",
                "reward": 1,
                "artifact_digest_sha256": "0" * 64,
                "runner_evidence_digest_sha256": "0" * 64,
                "evaluation_request_digest_sha256": "0" * 64,
                "verifier_image_digest_sha256": "0" * 64,
            }
        ).encode("utf-8")
        self.fake.runner_stream = worker._StreamResult(0, spoofed, b"")
        self.fake.verifier_outcome = "rejected"
        self.fake.verifier_reward = 0.0

        report = self.run_worker()
        # Verifier outcome must come from verifier container, not runner stdout
        self.assertEqual(
            report["observation"]["verifier"]["outcome"], "rejected"
        )
        self.assertEqual(report["observation"]["verifier"]["reward"], 0.0)

    def test_verdict_replay_with_mismatched_nonce_rejects(self) -> None:
        self.fake.custom_verifier_payload = {
            "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
            "run_id": "fixture-1",
            "attempt_nonce": "bad_nonce_" + "0" * 54,
            "outcome": "accepted",
            "reward": 1,
            "artifact_digest_sha256": hashlib.sha256(ARTIFACT).hexdigest(),
            "runner_evidence_digest_sha256": "0" * 64,
            "evaluation_request_digest_sha256": "0" * 64,
            "verifier_image_digest_sha256": VERIFIER_IMAGE.rsplit(
                "@sha256:", 1
            )[1],
        }
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn(
            "verifier-result-malformed", report["observation"]["issues"]
        )
        self.assertEqual(report["observation"]["verifier"]["outcome"], "error")

    def test_verdict_replay_with_mismatched_evidence_digest_rejects(
        self,
    ) -> None:
        self.run_worker()  # populate verifier_input
        header, _, _ = parse_runner_evidence(self.fake.verifier_input)
        self.fake.custom_verifier_payload = {
            "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
            "run_id": header["run_id"],
            "attempt_nonce": header["attempt_nonce"],
            "outcome": "accepted",
            "reward": 1,
            "artifact_digest_sha256": header["artifact_digest_sha256"],
            "runner_evidence_digest_sha256": "e" * 64,  # wrong digest
            "evaluation_request_digest_sha256": header[
                "evaluation_request_digest_sha256"
            ],
            "verifier_image_digest_sha256": VERIFIER_IMAGE.rsplit(
                "@sha256:", 1
            )[1],
        }
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn(
            "verifier-result-malformed", report["observation"]["issues"]
        )

    def test_runner_timeout_fails_closed_and_prevents_verifier_execution(
        self,
    ) -> None:
        self.fake.runner_stream = worker._StreamResult(
            -9, b"", b"timeout", timed_out=True
        )
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertEqual(
            report["observation"]["termination"]["kind"], "orchestrator-timeout"
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["runner_evidence_frozen"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["verifier_started"]
        )

    def test_runner_nonzero_exit_fails_closed_and_skips_verifier(
        self,
    ) -> None:
        self.fake.runner_stream = worker._StreamResult(1, b"", b"failed")
        self.fake.runner_state_exit = 1
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertFalse(
            report["observation"]["lifecycle"]["runner_evidence_frozen"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["verifier_started"]
        )

    def test_runner_cleanup_uncertainty_fails_closed_before_evidence_sealing(
        self,
    ) -> None:
        self.fake.remove_failure = "runner-id"
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn(
            "runner-container-cleanup-failed", report["diagnostics"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["runner_evidence_frozen"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["verifier_started"]
        )
    def test_runner_stderr_only_overflow_fails_closed_and_prevents_evidence_sealing(
        self,
    ) -> None:
        self.fake.runner_stream = worker._StreamResult(
            137, b"", b"x" * 100, overflowed=True
        )
        report = self.run_worker()
        self.assertFalse(report["passed"])
        self.assertIn("runner-failure", report["observation"]["issues"])
        self.assertIn(
            "runner-output-size-limit-exceeded", report["diagnostics"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["runner_evidence_frozen"]
        )
        self.assertFalse(
            report["observation"]["lifecycle"]["verifier_started"]
        )


class SubprocessAdapterCombinedLimitTests(unittest.TestCase):
    def test_stderr_only_overflow_returns_overflowed_and_bounds_output(self) -> None:
        adapter = worker._SubprocessAdapter()
        code = "import sys; sys.stderr.write('e' * 50); sys.stderr.flush()"
        result = adapter.stream(
            ["python3", "-c", code],
            input_source=None,
            timeout=5.0,
            output_limit=20,
        )
        self.assertTrue(result.overflowed)
        self.assertEqual(len(result.stderr), 20)
        self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
