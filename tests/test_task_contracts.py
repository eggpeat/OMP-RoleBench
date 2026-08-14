from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from jsonschema import Draft202012Validator

from rolebench.contracts import (
    ContractError,
    canonical_digest,
    canonical_sha256,
    file_sha256,
    load_repository,
    task_content_sha256,
    tree_sha256,
    validate_repository,
    validate_value,
)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
IMAGE_A = f"example.invalid/agent@sha256:{SHA_A}"
IMAGE_B = f"example.invalid/verifier@sha256:{SHA_B}"
IMAGE_BASELINE = "example.invalid/baseline@sha256:" + "4" * 64
IMAGE_REFERENCE = "example.invalid/reference@sha256:" + "5" * 64
IMAGE_TAMPER = "example.invalid/tamper@sha256:" + "6" * 64


class TaskContractFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(PRODUCT_ROOT / "contracts", self.root / "contracts")

    def write_json(self, relative: str, value: dict[str, object]) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def messages(self, result: object) -> str:
        diagnostics = getattr(result, "diagnostics")
        return "\n".join(f"{item.file}:{item.json_path}: {item.message}" for item in diagnostics)

    def make_task_and_qualification(self) -> tuple[dict[str, object], dict[str, object]]:
        public_root = self.root / "contracts/tasks/example/public"
        workspace = public_root / "workspace"
        workspace.mkdir(parents=True)
        prompt = public_root / "prompt.txt"
        prompt.write_text("repair the example\n", encoding="utf-8")
        (workspace / "input.txt").write_text("broken\n", encoding="utf-8")
        private_root = self.root / "contracts/tasks/example/private"
        private_root.mkdir(parents=True)
        verifier_file = private_root / "verify.py"
        verifier_file.write_text("raise SystemExit(0)\n", encoding="utf-8")
        baseline = self.root / "contracts/tasks/example/evidence/baseline-1.json"
        baseline_repeat = self.root / "contracts/tasks/example/evidence/baseline-2.json"
        reference = self.root / "contracts/tasks/example/evidence/reference-1.json"
        reference_repeat = self.root / "contracts/tasks/example/evidence/reference-2.json"
        tamper = self.root / "contracts/tasks/example/evidence/tamper-1.json"
        tamper_repeat = self.root / "contracts/tasks/example/evidence/tamper-2.json"
        baseline.parent.mkdir(parents=True)
        baseline.write_text('{"exit_code":1,"trial":1}\n', encoding="utf-8")
        baseline_repeat.write_text('{"exit_code":1,"trial":2}\n', encoding="utf-8")
        reference.write_text('{"exit_code":0,"trial":1}\n', encoding="utf-8")
        reference_repeat.write_text('{"exit_code":0,"trial":2}\n', encoding="utf-8")
        tamper.write_text('{"tamper_blocked":true,"trial":1}\n', encoding="utf-8")
        tamper_repeat.write_text('{"tamper_blocked":true,"trial":2}\n', encoding="utf-8")

        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        policy = json.loads((self.root / "contracts/scored-worker-policy.json").read_text(encoding="utf-8"))
        public_digest = tree_sha256(public_root)
        private_digest = tree_sha256(private_root)
        platform = {"os": "linux", "architecture": "amd64", "variant": None}
        task: dict[str, object] = {
            "schema_version": "omp.diagnostic-task/v1",
            "task_id": "example/task",
            "task_version": "1.0.0",
            "content_digest_sha256": task_content_sha256(public_digest, private_digest),
            "routing_eligible": False,
            "family": {"family_id": "example-family", "variant_id": "repair-1"},
            "split": {
                "assignment_method": "independent-review",
                "author_id": "author-1",
                "reviewer_id": "split-reviewer-1",
                "provenance_digest_sha256": SHA_A,
                "confidentiality": "public",
            },
            "authorship": {"author": "author-1"},
            "reviews": {
                "privacy": {
                    "decision": "approved", "reviewer": "privacy-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_A,
                },
                "license": {
                    "decision": "approved", "reviewer": "license-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_B,
                    "expression": "MIT", "redistribution": "permitted",
                },
                "verifier": {
                    "decision": "approved", "reviewer": "verifier-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_C,
                    "image": IMAGE_B, "config_digest_sha256": SHA_B, "platform": platform,
                    "private_tree_digest_sha256": private_digest,
                },
                "split": {
                    "decision": "approved", "reviewer": "split-reviewer-1",
                    "reviewed_at": "2026-08-14T00:00:00Z", "evidence_digest_sha256": SHA_A,
                    "family_id": "example-family", "partition": "anchor",
                    "assignment_method": "independent-review", "confidentiality": "public",
                },
            },
            "role": "task",
            "role_contract": {
                "contract_id": contract["contract_id"],
                "digest_sha256": canonical_sha256(contract),
            },
            "task_mix": contract["task_mix"],
            "capability_tags": contract["required_capabilities"],
            "difficulty": "hard",
            "partition": "anchor",
            "source": {"kind": "authored", "version": "1", "digest_sha256": SHA_B},
            "license": {"expression": "MIT", "redistribution": "permitted"},
            "assets": {
                "public": {
                    "root": "contracts/tasks/example/public",
                    "digest_sha256": public_digest,
                    "prompt": {
                        "path": "contracts/tasks/example/public/prompt.txt",
                        "kind": "file",
                        "digest_sha256": file_sha256(prompt),
                    },
                    "workspace": {
                        "path": "contracts/tasks/example/public/workspace",
                        "kind": "tree",
                        "digest_sha256": tree_sha256(workspace),
                    },
                },
                "verifier_private": {
                    "root": "contracts/tasks/example/private",
                    "digest_sha256": private_digest,
                    "verifier": {
                        "path": "contracts/tasks/example/private/verify.py",
                        "kind": "file",
                        "digest_sha256": file_sha256(verifier_file),
                    },
                },
            },
            "policy": {
                "path": "contracts/scored-worker-policy.json",
                "digest_sha256": canonical_sha256(policy),
            },
            "agent": {
                "image": IMAGE_A,
                "config_digest_sha256": SHA_A,
                "platform": platform,
                "asset_tree_digest_sha256": public_digest,
                "argv": ["/agent"],
            },
            "admission_agents": {
                "baseline": {
                    "image": IMAGE_BASELINE,
                    "config_digest_sha256": "1" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/baseline"],
                },
                "reference": {
                    "image": IMAGE_REFERENCE,
                    "config_digest_sha256": "2" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/reference"],
                },
                "tamper": {
                    "image": IMAGE_TAMPER,
                    "config_digest_sha256": "3" * 64,
                    "platform": platform,
                    "asset_tree_digest_sha256": public_digest,
                    "argv": ["/tamper"],
                },
            },
            "verifier": {
                "image": IMAGE_B,
                "config_digest_sha256": SHA_B,
                "platform": platform,
                "asset_tree_digest_sha256": private_digest,
                "argv": ["/verify"],
            },
            "objective": {"mode": "executable-task-verifier", "scoring": "binary", "criteria": ["exit-zero"]},
            "unscored_failures": ["infrastructure", "provider", "runner", "verifier"],
        }
        self.write_json("contracts/tasks/example/task.json", task)
        task_digest = canonical_sha256(task)
        qualification: dict[str, object] = {
            "schema_version": "omp.task-qualification/v1",
            "task_id": task["task_id"],
            "task_version": task["task_version"],
            "content_digest_sha256": task["content_digest_sha256"],
            "source_task": {"path": "contracts/tasks/example/task.json", "digest_sha256": canonical_sha256(task)},
            "reviews": deepcopy(task["reviews"]),
            "verifier_provenance": {
                "reviewer_id": "verifier-reviewer-1",
                "verifier_image": IMAGE_B,
                "verifier_config_digest_sha256": SHA_B,
                "verifier_platform": platform,
                "review_digest_sha256": SHA_C,
            },
            "observed_mapping": {
                "task_digest_sha256": task_digest,
                "public_tree_digest_sha256": public_digest,
                "verifier_private_tree_digest_sha256": private_digest,
                "agent_config_digest_sha256": SHA_A,
                "verifier_config_digest_sha256": SHA_B,
            },
            "checks": {
                "privacy": "pass",
                "license": "pass",
                "baseline_fails": {
                    "result": "pass",
                    "command_digest_sha256": "1" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/baseline-1.json", "digest_sha256": file_sha256(baseline)},
                        {"path": "contracts/tasks/example/evidence/baseline-2.json", "digest_sha256": file_sha256(baseline_repeat)},
                    ],
                },
                "reference_passes": {
                    "result": "pass",
                    "command_digest_sha256": "2" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/reference-1.json", "digest_sha256": file_sha256(reference)},
                        {"path": "contracts/tasks/example/evidence/reference-2.json", "digest_sha256": file_sha256(reference_repeat)},
                    ],
                },
                "verifier_isolation": "pass",
                "tamper_resistance": {
                    "result": "pass",
                    "command_digest_sha256": "3" * 64,
                    "evidence": [
                        {"path": "contracts/tasks/example/evidence/tamper-1.json", "digest_sha256": file_sha256(tamper)},
                        {"path": "contracts/tasks/example/evidence/tamper-2.json", "digest_sha256": file_sha256(tamper_repeat)},
                    ],
                },
                "determinism": "pass",
                "infrastructure_classification": "pass",
                "discrimination": "calibration-required",
            },
            "decision": "calibration-required",
        }
        self.write_json("contracts/tasks/example/qualification.json", qualification)
        return task, qualification


class CanonicalPilotPackTests(TaskContractFixture):
    def test_repository_and_empty_authoring_packs_are_canonical(self) -> None:
        result = validate_repository(self.root)
        self.assertTrue(result.valid, self.messages(result))
        repository = load_repository(self.root)
        self.assertEqual(tuple(role for role, _ in repository.task_packs), ("task", "smol", "slow"))
        for role, pack in repository.task_packs:
            self.assertEqual(pack["role"], role)
            self.assertEqual(pack["status"], "authoring")
            self.assertIs(pack["routing_eligible"], False)
            self.assertEqual(pack["entries"], [])

    def test_mapped_pack_changes_canonical_repository_digest(self) -> None:
        before = canonical_digest(load_repository(self.root))
        pack_path = self.root / "contracts/task-packs/task-v1.json"
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        pack["pack_id"] = "task-v1-changed"
        self.write_json("contracts/task-packs/task-v1.json", pack)
        self.assertNotEqual(before, canonical_digest(load_repository(self.root)))

    def test_each_new_schema_is_draft_2020_12_valid(self) -> None:
        names = ("task-candidate", "diagnostic-task", "task-qualification", "task-pack", "experiment-ledger-entry")
        for name in names:
            schema = json.loads((self.root / f"contracts/schemas/{name}.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)


class DigestSafetyTests(TaskContractFixture):
    def test_file_and_tree_hashes_reject_links_special_files_and_parent_paths(self) -> None:
        regular = self.root / "regular"
        regular.write_bytes(b"data")
        link = self.root / "link"
        link.symlink_to(regular)
        with self.assertRaises(ContractError):
            file_sha256(link)
        oversized = self.root / "oversized"
        oversized.write_bytes(b"")
        os.truncate(oversized, 64 * 1024 * 1024 + 1)
        with self.assertRaises(ContractError):
            file_sha256(oversized)
        with self.assertRaises(ContractError):
            file_sha256(Path("x" * 4097))
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "link").symlink_to(regular)
        with self.assertRaises(ContractError):
            tree_sha256(tree)
        with self.assertRaises(ContractError):
            file_sha256(Path("safe/../escape"))
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ContractError):
            file_sha256(fifo)

    def test_tree_digest_is_deterministic_and_binds_executable_mode(self) -> None:
        tree = self.root / "tree"
        tree.mkdir()
        executable = tree / "run"
        executable.write_text("exit 0\n", encoding="utf-8")
        first = tree_sha256(tree)
        self.assertEqual(first, tree_sha256(tree))
        executable.chmod(0o700)
        self.assertNotEqual(first, tree_sha256(tree))


class ArtifactSemanticTests(TaskContractFixture):
    def test_valid_task_qualification_and_calibration_pack(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task_result = validate_value(self.root, "diagnostic-task", task, Path("contracts/tasks/example/task.json"))
        self.assertTrue(task_result.valid, self.messages(task_result))
        qualification_result = validate_value(self.root, "task-qualification", qualification, Path("contracts/tasks/example/qualification.json"))
        self.assertTrue(qualification_result.valid, self.messages(qualification_result))
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "task-calibration",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "calibration",
            "routing_eligible": False,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {"reviewer_id": "pack-reviewer-1", "independent": True, "review_digest_sha256": SHA_A},
            "entries": [{
                "task": {"path": "contracts/tasks/example/task.json", "digest_sha256": canonical_sha256(task)},
                "qualification": {"path": "contracts/tasks/example/qualification.json", "digest_sha256": canonical_sha256(qualification)},
            }],
        }
        result = validate_value(self.root, "task-pack", pack, Path("private/task-pack.json"))
        self.assertTrue(result.valid, self.messages(result))

    def test_v1_qualification_cannot_claim_admission(self) -> None:
        _, qualification = self.make_task_and_qualification()
        qualification["checks"]["discrimination"] = "pass"  # type: ignore[index]
        qualification["decision"] = "admitted"

        result = validate_value(
            self.root,
            "task-qualification",
            qualification,
            Path("contracts/tasks/example/qualification.json"),
        )

        self.assertFalse(result.valid)
        self.assertIn("$.checks.discrimination", self.messages(result))
        self.assertIn("$.decision", self.messages(result))

    def test_synthetic_fixture_cannot_enter_any_task_pack(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["source"]["kind"] = "synthetic-fixture"  # type: ignore[index]
        task_digest = canonical_sha256(task)
        qualification["source_task"]["digest_sha256"] = task_digest  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = task_digest  # type: ignore[index]
        self.write_json("contracts/tasks/example/task.json", task)
        self.write_json(
            "contracts/tasks/example/qualification.json",
            qualification,
        )
        contract = json.loads(
            (self.root / "contracts/roles/task.json").read_text(
                encoding="utf-8"
            )
        )
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "synthetic-fixture-pack",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {
                "contract_id": contract["contract_id"],
                "digest_sha256": canonical_sha256(contract),
            },
            "partition": "anchor",
            "status": "frozen",
            "routing_eligible": True,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "split_review": {
                "reviewer_id": "pack-reviewer-1",
                "independent": True,
                "review_digest_sha256": SHA_A,
            },
            "entries": [
                {
                    "task": {
                        "path": "contracts/tasks/example/task.json",
                        "digest_sha256": task_digest,
                    },
                    "qualification": {
                        "path": "contracts/tasks/example/qualification.json",
                        "digest_sha256": canonical_sha256(qualification),
                    },
                }
            ],
        }

        result = validate_value(
            self.root,
            "task-pack",
            pack,
            Path("private/task-pack.json"),
        )

        self.assertIn(
            "synthetic fixtures cannot enter task packs",
            self.messages(result),
        )

    def test_task_rejects_asset_image_policy_role_and_split_mismatches(self) -> None:
        task, _ = self.make_task_and_qualification()
        bad = deepcopy(task)
        bad["split"]["reviewer_id"] = bad["split"]["author_id"]  # type: ignore[index]
        bad["policy"]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["agent"]["asset_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["assets"]["verifier_private"]["root"] = bad["assets"]["public"]["root"]  # type: ignore[index]
        bad["verifier"]["image"] = bad["agent"]["image"]  # type: ignore[index]
        bad["role_contract"]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["reviews"]["privacy"]["reviewer"] = "author-1"  # type: ignore[index]
        bad["reviews"]["license"]["expression"] = "Apache-2.0"  # type: ignore[index]
        bad["reviews"]["verifier"]["private_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        result = validate_value(self.root, "diagnostic-task", bad, Path("bad-task.json"))
        messages = self.messages(result)
        for expected in ("independent", "exact task license", "exact verifier execution", "disjoint", "canonical policy", "public asset tree", "differ from the agent", "canonical role-contract"):
            self.assertIn(expected, messages)

    def test_qualification_rejects_non_deterministic_decision_and_stale_evidence_mapping(self) -> None:
        _, qualification = self.make_task_and_qualification()
        bad = deepcopy(qualification)
        bad["checks"]["privacy"] = "fail"  # type: ignore[index]
        bad["decision"] = "calibration-required"
        bad["observed_mapping"]["public_tree_digest_sha256"] = SHA_A  # type: ignore[index]
        bad["checks"]["baseline_fails"]["evidence"][0]["digest_sha256"] = SHA_A  # type: ignore[index]
        bad["reviews"]["privacy"]["evidence_digest_sha256"] = SHA_C  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", bad, Path("bad-qualification.json"))
        messages = self.messages(result)
        self.assertIn("must be 'rejected'", messages)
        self.assertIn("source task execution mapping", messages)
        self.assertIn("evidence file SHA-256", messages)
        self.assertIn("human review attestations", messages)

    def test_noassertion_cannot_be_admitted(self) -> None:
        task, qualification = self.make_task_and_qualification()
        task["license"] = {"expression": "NOASSERTION", "redistribution": "prohibited"}
        self.write_json("contracts/tasks/example/task.json", task)
        qualification["source_task"]["digest_sha256"] = canonical_sha256(task)  # type: ignore[index]
        qualification["observed_mapping"]["task_digest_sha256"] = canonical_sha256(task)  # type: ignore[index]
        result = validate_value(self.root, "task-qualification", qualification, Path("qualification.json"))
        self.assertIn("without approved redistribution", self.messages(result))

    def test_authoring_and_calibration_packs_cannot_enter_routing(self) -> None:
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        base = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "unsafe",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "anchor",
            "status": "authoring",
            "routing_eligible": True,
            "selection_method": "fixed-anchor",
            "required_capabilities": contract["required_capabilities"],
            "entries": [],
        }
        for status in ("authoring", "calibration"):
            bad = deepcopy(base)
            bad["status"] = status
            result = validate_value(self.root, "task-pack", bad, Path("private/unsafe.json"))
            self.assertFalse(result.valid)
            self.assertIn("routing_eligible", self.messages(result))

    def test_public_holdout_manifest_is_rejected(self) -> None:
        contract = json.loads((self.root / "contracts/roles/task.json").read_text(encoding="utf-8"))
        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "holdout",
            "role": "task",
            "task_mix": "task-v1",
            "role_contract": {"contract_id": contract["contract_id"], "digest_sha256": canonical_sha256(contract)},
            "partition": "holdout",
            "status": "authoring",
            "routing_eligible": False,
            "selection_method": "sealed-holdout",
            "required_capabilities": contract["required_capabilities"],
            "entries": [],
        }
        result = validate_value(self.root, "task-pack", pack, Path("contracts/task-packs/holdout.json"))
        self.assertIn("must not be holdout", self.messages(result))

    def test_session_candidate_has_opaque_ref_not_stable_source_digest(self) -> None:
        candidate = {
            "schema_version": "omp.task-candidate/v1",
            "origin": {"kind": "omp-session", "source_version": "3", "candidate_ref": "12345678-1234-4123-8123-123456789abc"},
            "capability_hints": [],
            "review": {"privacy": "required", "license": "required", "publication": "prohibited"},
            "entry_count": 3,
            "signals": [{"ordinal": 2, "kind": "late-test-failure"}],
        }
        self.assertTrue(validate_value(self.root, "task-candidate", candidate, Path("candidate.json")).valid)
        candidate["origin"]["digest_sha256"] = SHA_A  # type: ignore[index]
        self.assertFalse(validate_value(self.root, "task-candidate", candidate, Path("candidate.json")).valid)

    def test_ledger_is_non_authoritative_and_binds_normalized_payload(self) -> None:
        payload = {
            "schema_version": "omp.task-candidate/v1"
        }
        entry = {
            "schema_version": "omp.experiment-ledger-entry/v1",
            "authority": "routing-authority",
            "sequence": 0,
            "timestamp": "2026-08-14T00:00:00Z",
            "previous_entry_sha256": None,
            "payload_schema": "task-qualification",
            "payload_digest_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        result = validate_value(self.root, "experiment-ledger-entry", entry, Path("ledger.json"))
        self.assertFalse(result.valid)
        self.assertIn("local-non-authoritative", self.messages(result))
        entry["authority"] = "local-non-authoritative"
        entry["payload_digest_sha256"] = SHA_A
        result = validate_value(self.root, "experiment-ledger-entry", entry, Path("ledger.json"))
        self.assertIn("canonical payload", self.messages(result))


if __name__ == "__main__":
    unittest.main()
