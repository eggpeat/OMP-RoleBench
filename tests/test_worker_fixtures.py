from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from rolebench.contracts import canonical_json, validate_artifact
from rolebench.worker import WorkerError, run_worker


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PRODUCT_ROOT / "fixtures" / "docker-runsc"
DIAGNOSTIC_FIXTURE_ROOT = PRODUCT_ROOT / "fixtures" / "diagnostic-task"
MANIFEST_PATH = FIXTURE_ROOT / "worker-run-manifest.json"
POLICY_PATH = PRODUCT_ROOT / "contracts" / "scored-worker-policy.json"
BASE = "busybox@sha256:7a3ebe5bfd1a4a19797d20b0c0bb39d44393e9a03fd852c0865b0f540d868df0"
PLACEHOLDER_DIGESTS = {"0" * 64, "f" * 64}
PAYLOAD = "rolebench-docker-runsc-fixture-v1"


class DockerRunscFixtureTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (FIXTURE_ROOT / name).read_text(encoding="utf-8")

    def manifest(self) -> dict[str, object]:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def test_manifest_is_schema_valid_and_pins_the_canonical_policy(self) -> None:
        result = validate_artifact(PRODUCT_ROOT, "worker-run-manifest", MANIFEST_PATH)
        self.assertTrue(result.valid, result.diagnostics)

        manifest = self.manifest()
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        expected_policy_digest = sha256(canonical_json(policy).encode("utf-8")).hexdigest()
        self.assertEqual(manifest["policy"], {
            "path": "contracts/scored-worker-policy.json",
            "digest_sha256": expected_policy_digest,
        })
        self.assertEqual(manifest["provider"], {"enabled": False})
        self.assertEqual(
            manifest["task"],
            {"digest_sha256": sha256(b"docker-runsc-fixture-v1").hexdigest()},
        )

    def test_manifest_uses_distinct_explicit_placeholder_images(self) -> None:
        manifest = self.manifest()
        agent = manifest["agent"]
        verifier = manifest["verifier"]
        self.assertIsInstance(agent, dict)
        self.assertIsInstance(verifier, dict)
        self.assertNotEqual(agent["image"], verifier["image"])
        digests = {
            str(agent["image"]).rsplit("@sha256:", 1)[1],
            str(verifier["image"]).rsplit("@sha256:", 1)[1],
        }
        self.assertEqual(digests, PLACEHOLDER_DIGESTS)
        self.assertEqual(agent["argv"], ["/usr/local/bin/rolebench-agent"])
        self.assertEqual(verifier["argv"], ["/usr/local/bin/rolebench-verifier"])

    def test_placeholder_manifest_is_rejected_before_any_docker_call(self) -> None:
        with patch(
            "rolebench.worker.subprocess.run",
            side_effect=AssertionError("placeholder validation must precede Docker"),
        ) as docker:
            with self.assertRaises(WorkerError):
                run_worker(PRODUCT_ROOT, MANIFEST_PATH)
        docker.assert_not_called()

    def test_images_share_only_the_pinned_base_and_use_distinct_users(self) -> None:
        agent = self.read("Dockerfile.agent")
        verifier = self.read("Dockerfile.verifier")
        self.assertEqual(agent.splitlines()[0], f"FROM {BASE}")
        self.assertEqual(verifier.splitlines()[0], f"FROM {BASE}")
        self.assertIn("USER 1000:1000", agent)
        self.assertIn("USER 2000:2000", verifier)
        self.assertIn('CMD ["/usr/local/bin/rolebench-agent"]', agent)
        self.assertIn('CMD ["/usr/local/bin/rolebench-verifier"]', verifier)
        for dockerfile in (agent, verifier):
            self.assertNotIn("VOLUME", dockerfile)
            self.assertNotIn("ADD ", dockerfile)
        self.assertNotIn("/workspace", verifier)
        self.assertNotIn("/tmp", verifier)

    def test_scripts_are_executable_posix_shell_programs(self) -> None:
        for name in ("agent.sh", "verifier.sh"):
            path = FIXTURE_ROOT / name
            self.assertEqual(self.read(name).splitlines()[0], "#!/bin/sh")
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR)

    def test_agent_probes_isolation_before_stdout_becomes_the_payload(self) -> None:
        script = self.read("agent.sh")
        required_probes = (
            "id -u",
            "id -g",
            "root filesystem is writable",
            "/workspace is not writable",
            "NoNewPrivs:",
            "CapEff:",
            "/proc/net/dev",
            "/proc/net/route",
            "/proc/net/ipv6_route",
        )
        for probe in required_probes:
            with self.subTest(probe=probe):
                self.assertIn(probe, script)

        payload_write = f"printf '%s' '{PAYLOAD}'"
        self.assertEqual(script.count(payload_write), 1)
        self.assertLess(script.index("NoNewPrivs:"), script.index(payload_write))
        self.assertLess(script.index("/proc/net/ipv6_route"), script.index(payload_write))

    def test_verifier_requires_the_exact_raw_payload_without_storage(self) -> None:
        script = self.read("verifier.sh")
        self.assertIn("dd bs=34 count=1", script)
        self.assertIn(
            f"[ \"$payload\" = '{PAYLOAD}' ]",
            script,
        )
        self.assertNotIn("tar ", script)
        self.assertNotIn("mktemp", script)
        self.assertNotIn("/tmp", script)
        self.assertIn("'{\"outcome\":\"accepted\",\"reward\":1}'", script)
        self.assertIn("'{\"outcome\":\"rejected\",\"reward\":0}'", script)


class DiagnosticTaskFixtureTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DIAGNOSTIC_FIXTURE_ROOT / name).read_text(encoding="utf-8")

    def test_images_pin_base_and_separate_public_from_private_assets(
        self,
    ) -> None:
        agent = self.read("Dockerfile.agent")
        verifier = self.read("Dockerfile.verifier")
        self.assertEqual(agent.splitlines()[0], f"FROM {BASE}")
        self.assertEqual(verifier.splitlines()[0], f"FROM {BASE}")
        self.assertIn("COPY public/", agent)
        self.assertNotIn("verifier-private", agent)
        self.assertIn("COPY verifier-private/", verifier)
        self.assertNotIn("COPY public/", verifier)
        self.assertIn(
            "org.omp.rolebench.task.public-tree-sha256",
            agent,
        )
        self.assertIn(
            "org.omp.rolebench.task.verifier-private-tree-sha256",
            verifier,
        )
        self.assertIn("USER 1000:1000", agent)
        self.assertIn("USER 2000:2000", verifier)

    def test_fixture_payload_and_programs_are_exact_and_executable(
        self,
    ) -> None:
        public_input = (
            DIAGNOSTIC_FIXTURE_ROOT / "public/workspace/input.txt"
        ).read_bytes()
        expected = (
            DIAGNOSTIC_FIXTURE_ROOT
            / "verifier-private/expected.txt"
        ).read_bytes()
        self.assertEqual(public_input, expected)
        self.assertEqual(public_input, b"rolebench-diagnostic-workflow-v1")
        for relative in ("probe.sh", "verifier-private/verifier.sh"):
            path = DIAGNOSTIC_FIXTURE_ROOT / relative
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines()[0],
                "#!/bin/sh",
            )
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
