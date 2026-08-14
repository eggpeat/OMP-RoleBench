from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from rolebench.contracts import canonical_json, validate_artifact, validate_repository


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"


class WorkerManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")
        self.manifest_path = self.root / "worker-run-manifest.json"

    def policy_digest(self) -> str:
        policy = json.loads(
            (self.root / "contracts/scored-worker-policy.json").read_text(encoding="utf-8")
        )
        return sha256(canonical_json(policy).encode("utf-8")).hexdigest()

    def manifest(self) -> dict[str, object]:
        platform = {"os": "linux", "architecture": "amd64", "variant": None}
        return {
            "schema_version": "omp.worker-run-manifest/v2",
            "run_id": "worker-run_001",
            "role": "task",
            "task": {"digest_sha256": "1" * 64},
            "policy": {
                "path": "contracts/scored-worker-policy.json",
                "digest_sha256": self.policy_digest(),
            },
            "provider": {"enabled": False},
            "agent": {
                "image": f"example.invalid/rolebench-agent@sha256:{'a' * 64}",
                "config_digest_sha256": "e" * 64,
                "platform": platform,
                "argv": ["/agent.sh", "--fixture"],
            },
            "runner": {
                "image": f"example.invalid/rolebench-runner@sha256:{'c' * 64}",
                "config_digest_sha256": "7" * 64,
                "platform": platform,
                "argv": ["/runner.sh"],
            },
            "verifier": {
                "image": f"example.invalid/rolebench-verifier@sha256:{'b' * 64}",
                "config_digest_sha256": "f" * 64,
                "platform": platform,
                "argv": ["/verifier.sh"],
            },
        }

    def validate(self, manifest: dict[str, object] | None = None):
        value = self.manifest() if manifest is None else manifest
        self.manifest_path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return validate_artifact(
            self.root,
            "worker-run-manifest",
            self.manifest_path,
        )

    @staticmethod
    def rendered(result) -> tuple[str, ...]:
        return tuple(
            f"{item.file}:{item.json_path}: {item.message}"
            for item in result.diagnostics
        )

    def test_valid_digest_pinned_manifest(self) -> None:
        result = self.validate()
        self.assertTrue(result.valid, self.rendered(result))

    def test_v1_manifest_uses_the_v1_referenced_policy_schema(self) -> None:
        policy_path = self.root / "contracts/scored-worker-policy-v1.json"
        policy = json.loads(
            (self.root / "contracts/scored-worker-policy.json").read_text(
                encoding="utf-8"
            )
        )
        policy["schema_version"] = "omp.scored-worker-policy/v1"
        policy["policy_id"] = "rolebench-scored-worker-v1"
        del policy["timeouts"]["runner_seconds"]
        policy["handoff"] = {
            "mode": "immutable-content-addressed",
            "digest_algorithm": "sha256",
            "require_agent_exit": True,
            "verifier_read_only": True,
        }
        policy_path.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest = self.manifest()
        manifest["schema_version"] = "omp.worker-run-manifest/v1"
        del manifest["runner"]
        manifest["policy"] = {
            "path": "contracts/scored-worker-policy-v1.json",
            "digest_sha256": sha256(
                canonical_json(policy).encode("utf-8")
            ).hexdigest(),
        }

        result = self.validate(manifest)
        self.assertTrue(result.valid, self.rendered(result))

    def test_missing_runner_is_rejected(self) -> None:
        manifest = self.manifest()
        del manifest["runner"]
        result = self.validate(manifest)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                "runner" in item.message or item.json_path in ("$", "$.runner")
                for item in result.diagnostics
            )
        )

    def test_missing_config_or_platform_is_rejected_unconditionally(self) -> None:
        for container_name in ("agent", "runner", "verifier"):
            for field in ("config_digest_sha256", "platform"):
                with self.subTest(container=container_name, field=field):
                    manifest = self.manifest()
                    del manifest[container_name][field]  # type: ignore[index]
                    result = self.validate(manifest)
                    self.assertFalse(result.valid)
                    self.assertTrue(
                        any(
                            item.json_path in (f"$.{container_name}", f"$.{container_name}.{field}")
                            and (field in item.message or "required" in item.message)
                            for item in result.diagnostics
                        )
                    )
    def test_evidence_use_requires_exact_qualification_binding(
        self,
    ) -> None:
        base = self.manifest()
        task = base["task"]
        self.assertIsInstance(task, dict)
        task.update(
            {
                "public_tree_digest_sha256": "c" * 64,
                "verifier_private_tree_digest_sha256": "d" * 64,
                "evidence_use": "admission-only",
            }
        )
        self.assertTrue(
            self.validate(base).valid,
            self.rendered(self.validate(base)),
        )

        admission_with_qualification = json.loads(
            json.dumps(base)
        )
        admission_with_qualification["task"][
            "qualification_digest_sha256"
        ] = "9" * 64
        self.assertFalse(
            self.validate(admission_with_qualification).valid
        )

        calibration_without_qualification = json.loads(
            json.dumps(base)
        )
        calibration_without_qualification["task"][
            "evidence_use"
        ] = "calibration-only"
        self.assertFalse(
            self.validate(calibration_without_qualification).valid
        )

        calibration_without_qualification["task"][
            "qualification_digest_sha256"
        ] = "9" * 64
        self.assertTrue(
            self.validate(calibration_without_qualification).valid,
            self.rendered(
                self.validate(calibration_without_qualification)
            ),
        )

    def test_repository_validation_does_not_require_a_run_manifest(self) -> None:
        result = validate_repository(self.root)
        self.assertTrue(result.valid, result.diagnostics)

    def test_schema_version_and_role_are_closed_enums(self) -> None:
        manifest = self.manifest()
        manifest["schema_version"] = "omp.worker-run-manifest/v99"
        manifest["role"] = "unknown-role"
        result = self.validate(manifest)
        paths = {item.json_path for item in result.diagnostics}
        self.assertIn("$.schema_version", paths)
        self.assertIn("$.role", paths)
        self.assertIn(
            "worker-run-manifest.json:$.role: must be one of the built-in roles "
            "['default', 'smol', 'slow', 'vision', 'plan', 'designer', 'commit', "
            "'tiny', 'task', 'advisor']",
            self.rendered(result),
        )

    def test_images_must_be_digest_pinned(self) -> None:
        manifest = self.manifest()
        agent = manifest["agent"]
        self.assertIsInstance(agent, dict)
        agent["image"] = "example.invalid/rolebench-agent:latest"
        result = self.validate(manifest)
        diagnostic = next(
            item for item in result.diagnostics if item.json_path == "$.agent.image"
        )
        self.assertIn("does not match", diagnostic.message)

    def test_option_shaped_image_is_rejected(self) -> None:
        manifest = self.manifest()
        agent = manifest["agent"]
        self.assertIsInstance(agent, dict)
        agent["image"] = f"--env-file=/tmp/host-secrets@sha256:{'a' * 64}"
        result = self.validate(manifest)
        self.assertIn(
            "$.agent.image",
            {item.json_path for item in result.diagnostics},
        )

    def test_argv_must_be_nonempty_and_start_with_a_nonempty_string(self) -> None:
        manifest = self.manifest()
        agent = manifest["agent"]
        runner = manifest["runner"]
        verifier = manifest["verifier"]
        self.assertIsInstance(agent, dict)
        self.assertIsInstance(runner, dict)
        self.assertIsInstance(verifier, dict)
        agent["argv"] = []
        runner["argv"] = [""]
        verifier["argv"] = [""]
        messages = self.rendered(self.validate(manifest))
        self.assertIn(
            "worker-run-manifest.json:$.agent.argv: must contain at least one argument",
            messages,
        )
        self.assertIn(
            "worker-run-manifest.json:$.runner.argv[0]: first argument must be nonempty",
            messages,
        )
        self.assertIn(
            "worker-run-manifest.json:$.verifier.argv[0]: first argument must be nonempty",
            messages,
        )

    def test_provider_is_exactly_disabled(self) -> None:
        manifest = self.manifest()
        manifest["provider"] = {"enabled": True, "endpoint": "https://example.invalid"}
        result = self.validate(manifest)
        paths = {item.json_path for item in result.diagnostics}
        self.assertIn("$.provider.enabled", paths)
        self.assertIn("$.provider", paths)

    def test_policy_path_cannot_escape_repository(self) -> None:
        for path in ("../outside-policy.json", "/tmp/outside-policy.json"):
            with self.subTest(path=path):
                manifest = self.manifest()
                policy = manifest["policy"]
                self.assertIsInstance(policy, dict)
                policy["path"] = path
                messages = self.rendered(self.validate(manifest))
                self.assertIn(
                    "worker-run-manifest.json:$.policy.path: must be a relative path "
                    "that resolves within the repository",
                    messages,
                )

    def test_policy_digest_must_match_canonical_policy_json(self) -> None:
        manifest = self.manifest()
        policy = manifest["policy"]
        self.assertIsInstance(policy, dict)
        policy["digest_sha256"] = "0" * 64
        expected = self.policy_digest()
        self.assertIn(
            "worker-run-manifest.json:$.policy.digest_sha256: "
            f"must equal canonical policy SHA-256 {expected}",
            self.rendered(self.validate(manifest)),
        )

    def test_agent_runner_and_verifier_images_must_differ(self) -> None:
        for duplicate_target, duplicate_source, error_path, error_source in (
            ("runner", "agent", "$.runner.image", "agent"),
            ("verifier", "agent", "$.verifier.image", "agent"),
            ("verifier", "runner", "$.verifier.image", "runner"),
        ):
            with self.subTest(duplicate_target=duplicate_target, duplicate_source=duplicate_source):
                manifest = self.manifest()
                manifest[duplicate_target]["image"] = manifest[duplicate_source]["image"]  # type: ignore[index]
                self.assertIn(
                    f"worker-run-manifest.json:{error_path}: must use a different image digest from the {error_source}",
                    self.rendered(self.validate(manifest)),
                )

    def test_agent_runner_and_verifier_configs_must_differ(self) -> None:
        base = self.manifest()
        for duplicate_target, duplicate_source, error_path, error_source in (
            ("runner", "agent", "$.runner.config_digest_sha256", "agent"),
            ("verifier", "agent", "$.verifier.config_digest_sha256", "agent"),
            ("verifier", "runner", "$.verifier.config_digest_sha256", "runner"),
        ):
            with self.subTest(duplicate_target=duplicate_target, duplicate_source=duplicate_source):
                manifest = json.loads(json.dumps(base))
                manifest[duplicate_target]["config_digest_sha256"] = manifest[duplicate_source]["config_digest_sha256"]
                self.assertIn(
                    f"worker-run-manifest.json:{error_path}: must use a different config digest from the {error_source}",
                    self.rendered(self.validate(manifest)),
                )

    def test_image_aliases_cannot_reuse_the_same_digest(self) -> None:
        manifest = self.manifest()
        agent = manifest["agent"]
        verifier = manifest["verifier"]
        self.assertIsInstance(agent, dict)
        self.assertIsInstance(verifier, dict)
        verifier["image"] = f"other.invalid/alias@sha256:{'a' * 64}"
        self.assertIn(
            "worker-run-manifest.json:$.verifier.image: must use a different image digest from the agent",
            self.rendered(self.validate(manifest)),
        )

    def test_argv_rejects_nul_with_exact_argument_path(self) -> None:
        manifest = self.manifest()
        verifier = manifest["verifier"]
        self.assertIsInstance(verifier, dict)
        verifier["argv"] = ["/verifier.sh", "bad\0argument"]
        self.assertIn(
            "worker-run-manifest.json:$.verifier.argv[1]: must not contain NUL",
            self.rendered(self.validate(manifest)),
        )

    def test_validation_is_deterministic_across_policy_formatting(self) -> None:
        manifest = self.manifest()
        first = self.validate(manifest)
        self.assertTrue(first.valid, self.rendered(first))

        policy_path = self.root / "contracts/scored-worker-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy_path.write_text(
            json.dumps(dict(reversed(tuple(policy.items()))), indent=4),
            encoding="utf-8",
        )
        second = self.validate(manifest)
        self.assertEqual(first.diagnostics, second.diagnostics)
        self.assertTrue(second.valid, self.rendered(second))

        invalid = self.manifest()
        invalid_policy = invalid["policy"]
        self.assertIsInstance(invalid_policy, dict)
        invalid_policy["path"] = "../../escape.json"
        diagnostics = self.validate(invalid).diagnostics
        self.assertEqual(diagnostics, tuple(sorted(set(diagnostics))))
        self.assertEqual(diagnostics, self.validate(invalid).diagnostics)


if __name__ == "__main__":
    unittest.main()
