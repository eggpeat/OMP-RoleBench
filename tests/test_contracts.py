from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from jsonschema import Draft202012Validator

from rolebench.cli import run
from rolebench.contracts import (
    BUILTIN_ROLES,
    canonical_digest,
    canonical_json,
    canonical_sha256,
    discover_root,
    load_repository,
    validate_artifact,
    validate_repository,
)


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"


class ContractFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")

    def read_json(self, relative: str) -> dict[str, object]:
        value = json.loads((self.root / relative).read_text(encoding="utf-8"))
        self.assertIsInstance(value, dict)
        return value

    def write_json(self, relative: str, value: dict[str, object]) -> None:
        (self.root / relative).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def messages(self) -> str:
        return "\n".join(
            f"{item.file}:{item.json_path}: {item.message}"
            for item in validate_repository(self.root).diagnostics
        )


class SuccessfulRepositoryTests(ContractFixture):
    def test_all_role_repository_is_valid(self) -> None:
        result = validate_repository(self.root)
        self.assertTrue(result.valid, self.messages())
        repository = load_repository(self.root)
        self.assertEqual(tuple(role for role, _ in repository.manifests), BUILTIN_ROLES)

    def test_v1_role_registry_repository_remains_valid(self) -> None:
        registry = self.read_json("contracts/role-registry.json")
        registry["schema_version"] = "omp.role-registry/v1"
        task_packs = registry["task_packs"]
        self.assertIsInstance(task_packs, dict)
        task_packs.pop("default")
        self.write_json("contracts/role-registry.json", registry)
        (self.root / "contracts/task-packs/default-v1.json").unlink()

        result = validate_repository(self.root)

        self.assertTrue(result.valid, result.diagnostics)
        repository = load_repository(self.root)
        self.assertEqual(
            tuple(role for role, _ in repository.task_packs),
            ("task", "smol", "slow"),
        )

    def test_contracts_validate_cli_reports_repository_valid(self) -> None:
        output = StringIO()
        status = run(
            ["--root", str(self.root), "contracts", "validate", "--json"],
            stdout=output,
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"valid": True, "diagnostics": []},
        )

    def test_root_discovery_walks_upward_to_registry(self) -> None:
        nested = self.root / "one/two/three"
        nested.mkdir(parents=True)
        self.assertEqual(discover_root(nested), self.root.resolve())

    def test_every_schema_passes_draft_2020_12_check_schema(self) -> None:
        for path in sorted((self.root / "contracts/schemas").glob("*.json")):
            with self.subTest(schema=path.name):
                Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_canonical_json_and_digest_ignore_source_formatting_and_key_order(self) -> None:
        repository = load_repository(self.root)
        first = canonical_digest(repository)
        manifest_path = self.root / "contracts/roles/default.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reversed_manifest = dict(reversed(tuple(manifest.items())))
        manifest_path.write_text(json.dumps(reversed_manifest, indent=4), encoding="utf-8")
        second = canonical_digest(load_repository(self.root))
        self.assertEqual(first, second)
        self.assertEqual(canonical_json({"z": 1, "a": 2}), '{"a":2,"z":1}')

    def test_digest_cli_has_plain_and_machine_readable_forms(self) -> None:
        plain = StringIO()
        self.assertEqual(run(["--root", str(self.root), "contracts", "digest"], stdout=plain), 0)
        expected = canonical_digest(load_repository(self.root))
        self.assertEqual(plain.getvalue(), expected + "\n")

        machine = StringIO()
        self.assertEqual(
            run(["--root", str(self.root), "contracts", "digest", "--json"], stdout=machine),
            0,
        )
        self.assertEqual(json.loads(machine.getvalue()), {"algorithm": "sha256", "digest": expected})

    def test_show_prints_canonical_manifest_and_rejects_unknown_role(self) -> None:
        output = StringIO()
        self.assertEqual(
            run(["--root", str(self.root), "contracts", "show", "vision"], stdout=output),
            0,
        )
        manifest = self.read_json("contracts/roles/vision.json")
        self.assertEqual(output.getvalue(), canonical_json(manifest) + "\n")

        error = StringIO()
        self.assertEqual(
            run(["--root", str(self.root), "contracts", "show", "not-a-role"], stderr=error),
            2,
        )
        self.assertIn("unknown role", error.getvalue())


class InvalidRepositoryTests(ContractFixture):
    def test_malformed_json_reports_file_and_root_path(self) -> None:
        path = self.root / "contracts/roles/smol.json"
        path.write_text("{not json", encoding="utf-8")
        result = validate_repository(self.root)
        self.assertFalse(result.valid)
        diagnostic = next(item for item in result.diagnostics if item.file == "contracts/roles/smol.json")
        self.assertEqual(diagnostic.json_path, "$")
        self.assertIn("invalid JSON", diagnostic.message)

    def test_missing_and_extra_manifest_files_are_rejected(self) -> None:
        (self.root / "contracts/roles/tiny.json").unlink()
        shutil.copyfile(
            self.root / "contracts/roles/default.json",
            self.root / "contracts/roles/unexpected.json",
        )
        messages = self.messages()
        self.assertIn("contracts/roles/tiny.json:$: required manifest file is missing", messages)
        self.assertIn("contracts/roles/unexpected.json:$: unexpected manifest file", messages)

    def test_every_published_schema_is_required(self) -> None:
        schema_directory = self.root / "contracts/schemas"
        for path in sorted(schema_directory.glob("*.json")):
            with self.subTest(schema=path.name):
                content = path.read_bytes()
                path.unlink()
                try:
                    result = validate_repository(self.root)
                finally:
                    path.write_bytes(content)
                self.assertTrue(
                    any(
                        diagnostic.file == f"contracts/schemas/{path.name}"
                        and diagnostic.message
                        == "required schema file is missing or invalid"
                        for diagnostic in result.diagnostics
                    ),
                    result.diagnostics,
                )

    def test_registry_requires_exact_order_coverage_and_paths(self) -> None:
        registry = self.read_json("contracts/role-registry.json")
        roles = registry["roles"]
        self.assertIsInstance(roles, list)
        roles[0], roles[1] = roles[1], roles[0]
        contracts = registry["contracts"]
        self.assertIsInstance(contracts, dict)
        contracts.pop("advisor")
        contracts["other"] = "contracts/roles/default.json"
        contracts["smol"] = "contracts/roles/default.json"
        self.write_json("contracts/role-registry.json", registry)
        messages = self.messages()
        self.assertIn("roles must exactly equal", messages)
        self.assertIn("contract coverage mismatch", messages)
        self.assertIn("$.contracts.smol: must be 'contracts/roles/smol.json'", messages)

    def test_filename_role_mismatch_is_rejected(self) -> None:
        manifest = self.read_json("contracts/roles/task.json")
        manifest["role"] = "advisor"
        self.write_json("contracts/roles/task.json", manifest)
        self.assertIn("$.role: role must match filename role 'task'", self.messages())

    def test_duplicate_array_items_are_rejected_with_item_path(self) -> None:
        manifest = self.read_json("contracts/roles/default.json")
        capabilities = manifest["required_capabilities"]
        self.assertIsInstance(capabilities, list)
        capabilities.append(capabilities[0])
        self.write_json("contracts/roles/default.json", manifest)
        messages = self.messages()
        self.assertIn("$.required_capabilities", messages)
        self.assertIn("duplicate array item", messages)

    def test_duplicate_contract_ids_are_rejected(self) -> None:
        first = self.read_json("contracts/roles/default.json")
        second = self.read_json("contracts/roles/smol.json")
        second["contract_id"] = first["contract_id"]
        self.write_json("contracts/roles/smol.json", second)
        self.assertIn("$.contract_id: contract_id duplicates contracts/roles/default.json", self.messages())

    def test_calibration_required_thresholds_must_all_be_null(self) -> None:
        manifest = self.read_json("contracts/roles/slow.json")
        thresholds = manifest["thresholds"]
        self.assertIsInstance(thresholds, dict)
        self.assertEqual(thresholds["status"], "calibration-required")
        thresholds["quality_floor"] = 0.75
        self.write_json("contracts/roles/slow.json", manifest)
        self.assertIn(
            "$.thresholds.quality_floor: must be null while threshold status is calibration-required",
            self.messages(),
        )

    def test_validation_json_collects_diagnostics_deterministically(self) -> None:
        manifest = self.read_json("contracts/roles/designer.json")
        manifest["role"] = "vision"
        capabilities = manifest["required_capabilities"]
        self.assertIsInstance(capabilities, list)
        capabilities.append(capabilities[0])
        self.write_json("contracts/roles/designer.json", manifest)
        output = StringIO()
        status = run(
            ["--root", str(self.root), "contracts", "validate", "--json"],
            stdout=output,
        )
        self.assertEqual(status, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["valid"])
        diagnostics = payload["diagnostics"]
        self.assertEqual(
            diagnostics,
            sorted(diagnostics, key=lambda item: (item["file"], item["json_path"], item["message"])),
        )
        self.assertGreaterEqual(len(diagnostics), 2)

    def test_canonical_v2_policy_file_required(self) -> None:
        v2_path = self.root / "contracts/scored-worker-policy-v2.json"
        if v2_path.exists():
            v2_path.unlink()
        result = validate_repository(self.root)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                diagnostic.file == "contracts/scored-worker-policy-v2.json"
                and diagnostic.message == "required canonical policy file is missing or invalid"
                for diagnostic in result.diagnostics
            ),
            result.diagnostics,
        )

    def test_preserved_v1_policy_file_required(self) -> None:
        v1_path = self.root / "contracts/scored-worker-policy.json"
        if v1_path.exists():
            v1_path.unlink()
        result = validate_repository(self.root)
        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                diagnostic.file == "contracts/scored-worker-policy.json"
                and diagnostic.message
                == "required legacy policy file is missing or invalid"
                for diagnostic in result.diagnostics
            ),
            result.diagnostics,
        )



class ArtifactValidationTests(ContractFixture):
    def write_artifact(self, value: dict[str, object]) -> Path:
        relative = Path("artifact.json")
        self.write_json(relative.as_posix(), value)
        return relative

    def policy(self) -> dict[str, object]:
        checksum = "0" * 64

        def snapshot(schema_version: str, snapshot_id: str) -> dict[str, object]:
            return {
                "schema_version": schema_version,
                "snapshot_id": snapshot_id,
                "checksum_sha256": checksum,
            }

        return {
            "schema_version": "omp.route-policy/v1",
            "policy_id": "policy-test",
            "created_at": "2026-08-13T12:00:00Z",
            "valid_from": "2026-08-13T12:00:00Z",
            "valid_until": "2026-08-13T13:00:00Z",
            "role_registry_snapshot": {
                "schema_version": "omp.role-registry/v1",
                "registry_id": "registry-test",
                "checksum_sha256": checksum,
            },
            "evidence_snapshot": snapshot("omp.evidence-row/v1", "evidence-test"),
            "capability_snapshot": snapshot(
                "omp.capability-snapshot/v1", "capability-test"
            ),
            "capacity_snapshot": snapshot(
                "omp.capacity-snapshot/v1", "capacity-test"
            ),
            "demand_snapshot": snapshot("omp.demand-snapshot/v1", "demand-test"),
            "optimizer_version": "test",
            "allocation_algorithm": "weighted-rendezvous-v1",
            "allocation_seed": "test",
            "roles": {
                "smol": {
                    "quality_floor": 0.0,
                    "reliability_floor": 0.0,
                    "confidence": 0.0,
                    "stickiness_scope": "operation",
                    "routes": [
                        {
                            "route_id": "openai:gpt-5.6/xhigh",
                            "weight_bps": 10000,
                        }
                    ],
                    "emergency_fallback": ["openai:gpt-5.6/xhigh"],
                }
            },
            "checksum_sha256": checksum,
        }

    def decision(self) -> dict[str, object]:
        return {
            "schema_version": "omp.routing-decision/v1",
            "decision_id": "decision-test",
            "decided_at": "2026-08-13T12:00:00Z",
            "mode": "enforce",
            "policy_id": "policy-test",
            "policy_checksum_sha256": "0" * 64,
            "role": "smol",
            "routing_key_hash": "1" * 64,
            "health_epoch": "health-test",
            "capacity_epoch": "capacity-test",
            "explicit_override": {"active": False, "route_id": None},
            "candidates": [
                {
                    "route_id": "route-a",
                    "weight_bps": 10000,
                    "eligible": True,
                    "reason_codes": [],
                    "rank": 1,
                    "rendezvous_hash": "0123456789abcdef",
                },
                {
                    "route_id": "route-b",
                    "weight_bps": 10000,
                    "eligible": False,
                    "reason_codes": ["unavailable"],
                    "rank": None,
                    "rendezvous_hash": None,
                },
            ],
            "selected_route": "route-a",
            "fallback_ranking": ["route-b"],
            "recovery": {
                "status": "not-needed",
                "attempted_routes": [],
                "selected_route": None,
                "reason": None,
            },
            "omp_version": "test",
        }

    def diagnostic_messages(
        self,
        schema_name: str,
        value: dict[str, object],
    ) -> str:
        result = validate_artifact(
            self.root,
            schema_name,
            self.write_artifact(value),
        )
        return "\n".join(
            f"{item.json_path}: {item.message}" for item in result.diagnostics
        )

    def test_frozen_role_contract_validates(self) -> None:
        contract = self.read_json("contracts/roles/default.json")
        thresholds = contract["thresholds"]
        self.assertIsInstance(thresholds, dict)
        thresholds.update(
            {
                "status": "frozen",
                "quality_floor": 0.0,
                "reliability_floor": 0.0,
                "confidence": 0.0,
                "latency_slo_seconds": 1.0,
            }
        )
        result = validate_artifact(
            self.root,
            "role-contract",
            self.write_artifact(contract),
        )
        self.assertTrue(result.valid, result.diagnostics)

    def test_namespaced_schema_version_is_required(self) -> None:
        contract = self.read_json("contracts/roles/default.json")
        contract["schema_version"] = "role-contract/v1"
        messages = self.diagnostic_messages("role-contract", contract)
        self.assertIn("$.schema_version", messages)
        self.assertIn("omp.role-contract/v1", messages)

    def test_spec_style_route_id_and_null_upstream_validate(self) -> None:
        route = {
            "schema_version": "omp.route/v1",
            "route_id": "openai:gpt-5.6/xhigh",
            "provider": "openai",
            "model": "gpt-5.6",
            "thinking": "xhigh",
            "transport": "responses",
            "upstream": None,
            "capacity_pool": "openai",
            "credential_selection": "provider-managed",
            "omp_version": "test",
        }
        result = validate_artifact(
            self.root,
            "route",
            self.write_artifact(route),
        )
        self.assertTrue(result.valid, result.diagnostics)

    def test_route_policy_accepts_v1_and_v2_role_registry_snapshots(self) -> None:
        for schema_version in (
            "omp.role-registry/v1",
            "omp.role-registry/v2",
        ):
            with self.subTest(schema_version=schema_version):
                policy = self.policy()
                role_registry_snapshot = policy["role_registry_snapshot"]
                self.assertIsInstance(role_registry_snapshot, dict)
                role_registry_snapshot["schema_version"] = schema_version
                result = validate_artifact(
                    self.root,
                    "route-policy",
                    self.write_artifact(policy),
                )
                self.assertTrue(result.valid, result.diagnostics)

    def test_policy_rejects_wrong_sum_duplicate_routes_and_validity_order(self) -> None:
        policy = self.policy()
        roles = policy["roles"]
        self.assertIsInstance(roles, dict)
        smol = roles["smol"]
        self.assertIsInstance(smol, dict)
        routes = smol["routes"]
        self.assertIsInstance(routes, list)
        first = routes[0]
        self.assertIsInstance(first, dict)
        first["weight_bps"] = 5000
        routes.append(
            {
                "route_id": first["route_id"],
                "weight_bps": 4000,
            }
        )
        smol["emergency_fallback"] = ["route-a", "route-a"]
        policy["valid_until"] = policy["valid_from"]
        messages = self.diagnostic_messages("route-policy", policy)
        self.assertIn("weight_bps values must sum to 10000; got 9000", messages)
        self.assertIn("duplicate route_id", messages)
        self.assertIn("duplicate emergency fallback", messages)
        self.assertIn("valid_until must be later than valid_from", messages)

    def test_format_errors_surface_from_artifact_validation(self) -> None:
        policy = self.policy()
        policy["valid_from"] = "not-a-date"
        messages = self.diagnostic_messages("route-policy", policy)
        self.assertIn("$.valid_from", messages)
        self.assertIn("not a 'date-time'", messages)

    def test_decision_requires_eligible_selection_without_override(self) -> None:
        decision = self.decision()
        decision["selected_route"] = "route-b"
        messages = self.diagnostic_messages("routing-decision", decision)
        self.assertIn("selected_route must identify an eligible candidate", messages)

        decision = self.decision()
        decision["selected_route"] = "operator-route"
        decision["explicit_override"] = {
            "active": True,
            "route_id": "operator-route",
        }
        result = validate_artifact(
            self.root,
            "routing-decision",
            self.write_artifact(decision),
        )
        self.assertTrue(result.valid, result.diagnostics)

    def test_decision_rejects_duplicate_ranks_and_fallback_routes(self) -> None:
        decision = self.decision()
        candidates = decision["candidates"]
        self.assertIsInstance(candidates, list)
        second = candidates[1]
        self.assertIsInstance(second, dict)
        second.update(
            {
                "eligible": True,
                "reason_codes": [],
                "rank": 1,
                "rendezvous_hash": "fedcba9876543210",
            }
        )
        decision["fallback_ranking"] = ["route-b", "route-b"]
        recovery = decision["recovery"]
        self.assertIsInstance(recovery, dict)
        recovery["attempted_routes"] = ["route-a", "route-a"]
        messages = self.diagnostic_messages("routing-decision", decision)
        self.assertIn("duplicate candidate rank", messages)
        self.assertIn("duplicate fallback route", messages)
        self.assertIn("$.recovery.attempted_routes", messages)

    def test_decision_rejects_null_selection_when_candidate_is_eligible(self) -> None:
        decision = self.decision()
        decision["selected_route"] = None
        messages = self.diagnostic_messages("routing-decision", decision)
        self.assertIn("selected_route may be null only when no candidate is eligible", messages)

    def test_artifact_cli_emits_deterministic_json_diagnostics(self) -> None:
        policy = self.policy()
        policy["checksum_sha256"] = "not-a-checksum"
        artifact = self.write_artifact(policy)
        output = StringIO()
        status = run(
            [
                "--root",
                str(self.root),
                "artifacts",
                "validate",
                "route-policy",
                str(artifact),
                "--json",
            ],
            stdout=output,
        )
        self.assertEqual(status, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["valid"])
        diagnostics = payload["diagnostics"]
        self.assertEqual(
            diagnostics,
            sorted(
                diagnostics,
                key=lambda item: (item["file"], item["json_path"], item["message"]),
            ),
        )
        self.assertTrue(
            any(item["json_path"] == "$.checksum_sha256" for item in diagnostics)
        )

    def test_diagnostic_task_policy_version_dispatch(self) -> None:
        policy_v1 = {
            "schema_version": "omp.scored-worker-policy/v1",
            "policy_id": "v1-policy",
            "executor": {
                "user": {"uid": 10001, "gid": 10001},
                "privileged": False,
                "read_only_root": True,
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],
            },
            "network": {
                "mode": "isolated-bridge",
                "direct_egress": False,
                "proxy": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": 8080,
                    "ca_cert_path": "certs/ca.pem",
                    "allowed_domains": ["api.openai.com"],
                },
            },
            "resources": {
                "cpu_limit": 2.0,
                "memory_limit_mb": 4096,
                "pids_limit": 256,
            },
            "timeouts": {
                "agent_seconds": 300,
                "runner_seconds": 60,
                "verifier_seconds": 60,
            },
            "handoff": {
                "mode": "immutable-content-addressed",
                "digest_algorithm": "sha256",
                "require_agent_exit": True,
                "quarantine_on_failure": True,
            },
            "verifier": {
                "user": {"uid": 10002, "gid": 10002},
                "network_mode": "none",
                "read_only_root": True,
                "require_isolated_container": True,
            },
            "accounting": {
                "quarantine_on_suspicion": True,
                "zero_score_on_rejection": True,
            },
        }
        self.write_json("contracts/scored-worker-policy.json", policy_v1)
        self.write_json("contracts/scored-worker-policy-v2.json", policy_v1)

        v1_digest = canonical_sha256(policy_v1)

        task_v1 = self.read_json(
            "contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/task.json"
        )
        task_v1["schema_version"] = "omp.diagnostic-task/v1"
        task_v1["policy"] = {
            "path": "contracts/scored-worker-policy.json",
            "digest_sha256": v1_digest,
        }
        task_v1_wrong_path = dict(task_v1)
        task_v1_wrong_path["policy"] = {
            "path": "contracts/scored-worker-policy-v2.json",
            "digest_sha256": v1_digest,
        }
        messages = self.diagnostic_messages("diagnostic-task", task_v1_wrong_path)
        self.assertIn("must be 'contracts/scored-worker-policy.json'", messages)

        task_v2_wrong_path = dict(task_v1)
        task_v2_wrong_path["schema_version"] = "omp.diagnostic-task/v2"
        task_v2_wrong_path["policy"] = {
            "path": "contracts/scored-worker-policy.json",
            "digest_sha256": v1_digest,
        }
        messages_v2 = self.diagnostic_messages("diagnostic-task", task_v2_wrong_path)
        self.assertIn("must be 'contracts/scored-worker-policy-v2.json'", messages_v2)

    def test_task_pack_frozen_status_enforces_qualification_version_and_decision(self) -> None:
        contract = self.read_json("contracts/roles/default.json")
        task_path = "contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/task.json"
        task = self.read_json(task_path)
        qual_path = "contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/qualification.json"
        qual = self.read_json(qual_path)

        pack = {
            "schema_version": "omp.task-pack/v1",
            "pack_id": "test-pack",
            "role": "default",
            "task_mix": "default-v1",
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
                "reviewer_id": "independent-reviewer",
                "independent": True,
                "review_digest_sha256": "0" * 64,
            },
            "entries": [
                {
                    "task": {
                        "path": task_path,
                        "digest_sha256": canonical_sha256(task),
                    },
                    "qualification": {
                        "path": qual_path,
                        "digest_sha256": canonical_sha256(qual),
                    },
                }
            ],
        }

        qual_v1 = dict(qual)
        qual_v1["schema_version"] = "omp.task-qualification/v1"
        self.write_json(qual_path, qual_v1)
        pack["entries"][0]["qualification"]["digest_sha256"] = canonical_sha256(qual_v1)
        messages = self.diagnostic_messages("task-pack", pack)
        self.assertIn("v1 qualifications cannot freeze a task pack", messages)

        qual_rejected = dict(qual)
        qual_rejected["decision"] = "rejected"
        qual_rejected["checks"] = dict(qual["checks"])
        qual_rejected["checks"]["determinism"] = "fail"
        self.write_json(qual_path, qual_rejected)
        pack["entries"][0]["qualification"]["digest_sha256"] = canonical_sha256(qual_rejected)
        messages = self.diagnostic_messages("task-pack", pack)
        self.assertIn("frozen packs require calibration-required qualifications", messages)

        pack["status"] = "calibration"
        pack["routing_eligible"] = False
        messages = self.diagnostic_messages("task-pack", pack)
        self.assertIn("calibration packs require calibration-required qualifications", messages)

    def test_approved_task_review_evidence_requires_all_checks_to_pass(self) -> None:
        evidence_path = (
            "contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/reviews/privacy.json"
        )
        evidence = self.read_json(evidence_path)

        result = validate_artifact(
            self.root,
            "task-review-evidence",
            self.write_artifact(evidence),
        )
        self.assertTrue(result.valid, result.diagnostics)

        failing_approved = dict(evidence)
        failing_approved["checks"] = dict(evidence["checks"])  # type: ignore[arg-type]
        failing_approved["checks"]["secret_scrubbing"] = "fail"
        messages = self.diagnostic_messages("task-review-evidence", failing_approved)
        self.assertIn("must be 'pass' for approved review evidence", messages)
        self.assertIn("$.checks.secret_scrubbing", messages)

        failing_rejected = dict(failing_approved)
        failing_rejected["decision"] = "rejected"
        result_rejected = validate_artifact(
            self.root,
            "task-review-evidence",
            self.write_artifact(failing_rejected),
        )
        self.assertTrue(result_rejected.valid, result_rejected.diagnostics)

        task_path = "contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/task.json"
        task = self.read_json(task_path)
        self.write_json(evidence_path, failing_approved)
        task_reviews = task["reviews"]
        self.assertIsInstance(task_reviews, dict)
        privacy_review = task_reviews["privacy"]
        self.assertIsInstance(privacy_review, dict)
        privacy_review["evidence_digest_sha256"] = canonical_sha256(failing_approved)
        self.write_json(task_path, task)
        task_result = validate_artifact(
            self.root,
            "diagnostic-task",
            Path(task_path),
        )
        self.assertFalse(task_result.valid)
        task_messages = "\n".join(item.message for item in task_result.diagnostics)
        self.assertIn("must be 'pass' for approved review evidence", task_messages)

if __name__ == "__main__":
    unittest.main()
