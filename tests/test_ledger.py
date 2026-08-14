from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from rolebench.accounting import classify_attempt
from rolebench.contracts import canonical_json, canonical_sha256
from rolebench.ledger import LedgerError, append_artifacts, append_worker_report, verify_ledger


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = PRODUCT_ROOT / "contracts"
SHA = "0" * 64
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


class LedgerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(CONTRACTS_ROOT, self.root / "contracts")
        self.ledger = Path("experiment.jsonl")

    def observation(self, run_id: str = "run-test") -> dict[str, object]:
        return {
            "schema_version": "omp.attempt-observation/v1",
            "observation_id": f"{run_id}-observation",
            "observed_at": "2026-08-14T11:59:00Z",
            "attempt": {"attempt_id": run_id, "number": 1, "previous_attempt_id": None},
            "stage": "complete",
            "lifecycle": {"environment_started": True, "agent_started": True, "agent_finished": True, "artifact_frozen": True, "verifier_started": True, "verifier_finished": True},
            "readiness": {"environment": "ready", "runner": "healthy", "provider": "available"},
            "issues": [],
            "provider": {"request_started": True, "http_status": 200},
            "termination": {"kind": "completed", "exit_code": 0, "signal": None, "oom_scope": "none"},
            "verifier": {"outcome": "accepted", "result_valid": True, "reward": 1},
            "integrity": {"state": "verified"},
            "digests": {"task": SHA, "config": SHA, "agent_image": SHA, "verifier_image": SHA, "runtime_policy": SHA, "artifact": SHA, "trajectory": SHA},
        }

    def outcome(self, observation: dict[str, object]) -> dict[str, object]:
        return classify_attempt(observation)

    def append(self, *records: tuple[str, dict[str, object]]) -> dict[str, object]:
        return append_artifacts(self.root, self.ledger, records, now=lambda: NOW)

    def lines(self) -> list[bytes]:
        return (self.root / self.ledger).read_bytes().splitlines(keepends=True)

    def write_entries(self, entries: list[dict[str, object]]) -> None:
        (self.root / self.ledger).write_bytes(
            b"".join(canonical_json(item).encode() + b"\n" for item in entries)
        )
        os.chmod(self.root / self.ledger, 0o600)


class LedgerAppendTests(LedgerFixture):
    def test_genesis_and_multi_record_append_form_a_continuous_chain(self) -> None:
        observation = self.observation()
        first = self.append(("attempt-observation", observation))
        outcome = self.outcome(observation)
        second = self.append(
            ("attempt-outcome", outcome),
            ("attempt-observation", self.observation("run-next")),
        )
        lines = self.lines()
        entries = [json.loads(line) for line in lines]
        self.assertEqual(first["first_sequence"], 0)
        self.assertEqual(second["appended_count"], 2)
        self.assertEqual([item["sequence"] for item in entries], [0, 1, 2])
        self.assertEqual({item["authority"] for item in entries}, {"local-non-authoritative"})
        self.assertIsNone(entries[0]["previous_entry_sha256"])
        self.assertEqual(entries[1]["previous_entry_sha256"], sha256(lines[0]).hexdigest())
        self.assertEqual(entries[2]["previous_entry_sha256"], sha256(lines[1]).hexdigest())
        self.assertEqual(entries[0]["payload_digest_sha256"], canonical_sha256(observation))
        self.assertEqual(second["head_sha256"], sha256(lines[-1]).hexdigest())
        self.assertEqual(
            verify_ledger(self.root, self.ledger),
            {"authority": "local-non-authoritative", "valid": True, "record_count": 3, "last_sequence": 2, "head_sha256": sha256(lines[-1]).hexdigest()},
        )

    def test_append_refuses_invalid_payload_without_writing(self) -> None:
        observation = self.observation()
        del observation["stage"]
        with self.assertRaisesRegex(LedgerError, "invalid payload"):
            self.append(("attempt-observation", observation))
        self.assertEqual((self.root / self.ledger).read_bytes(), b"")


class LedgerVerificationTests(LedgerFixture):
    def setUp(self) -> None:
        super().setUp()
        self.append(("attempt-observation", self.observation()))

    def entry(self) -> dict[str, object]:
        return json.loads(self.lines()[0])

    def test_rejects_payload_tampering_and_invalid_digest(self) -> None:
        entry = self.entry()
        payload = entry["payload"]
        self.assertIsInstance(payload, dict)
        payload["observation_id"] = "tampered"
        self.write_entries([entry])
        with self.assertRaisesRegex(LedgerError, "payload digest"):
            verify_ledger(self.root, self.ledger)

    def test_rejects_invalid_payload_even_with_matching_digest(self) -> None:
        entry = self.entry()
        payload = entry["payload"]
        self.assertIsInstance(payload, dict)
        del payload["stage"]
        entry["payload_digest_sha256"] = canonical_sha256(payload)
        self.write_entries([entry])
        with self.assertRaisesRegex(LedgerError, "invalid ledger record 0 payload"):
            verify_ledger(self.root, self.ledger)

    def test_rejects_invalid_sequence_and_authority(self) -> None:
        entry = self.entry()
        entry["sequence"] = 1
        self.write_entries([entry])
        with self.assertRaisesRegex(LedgerError, "non-continuous sequence"):
            verify_ledger(self.root, self.ledger)
        entry["sequence"] = 0
        entry["authority"] = "admission-authority"
        self.write_entries([entry])
        with self.assertRaisesRegex(LedgerError, "non-authoritative status"):
            verify_ledger(self.root, self.ledger)
        del entry["authority"]
        self.write_entries([entry])
        with self.assertRaisesRegex(LedgerError, "non-authoritative status"):
            verify_ledger(self.root, self.ledger)

    def test_rejects_broken_multi_record_hash_chain(self) -> None:
        self.append(("attempt-observation", self.observation("run-next")))
        entries = [json.loads(line) for line in self.lines()]
        entries[1]["previous_entry_sha256"] = SHA
        self.write_entries(entries)
        with self.assertRaisesRegex(LedgerError, "breaks the hash chain"):
            verify_ledger(self.root, self.ledger)

    def test_rejects_noncanonical_json_and_truncated_tail(self) -> None:
        entry = self.entry()
        (self.root / self.ledger).write_text(json.dumps(entry) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "not canonical JSONL"):
            verify_ledger(self.root, self.ledger)
        (self.root / self.ledger).write_bytes(canonical_json(entry).encode())
        with self.assertRaisesRegex(LedgerError, "truncated record"):
            verify_ledger(self.root, self.ledger)

    def test_rejects_symlink_hardlink_and_unsafe_mode(self) -> None:
        original = self.root / self.ledger
        target = self.root / "target.jsonl"
        original.rename(target)
        original.symlink_to(target.name)
        with self.assertRaises(LedgerError):
            verify_ledger(self.root, self.ledger)
        original.unlink()
        os.link(target, original)
        with self.assertRaisesRegex(LedgerError, "hard link"):
            verify_ledger(self.root, self.ledger)
        original.unlink()
        os.chmod(target, 0o644)
        with self.assertRaisesRegex(LedgerError, "0600"):
            verify_ledger(self.root, Path("target.jsonl"))


class WorkerReportLedgerTests(LedgerFixture):
    def test_only_normalized_observation_and_outcome_are_persisted(self) -> None:
        observation = self.observation()
        outcome = self.outcome(observation)
        report: dict[str, object] = {
            "schema_version": "omp.worker-run-report/v1",
            "run_id": "run-test",
            "passed": False,
            "observation": observation,
            "outcome": outcome,
            "diagnostics": ["private provider body"],
            "provider_error_body": "secret upstream response",
            "doctor": {"diagnostics": ["host-private detail"]},
        }
        summary = append_worker_report(self.root, self.ledger, report, now=lambda: NOW)
        self.assertEqual(summary["appended_count"], 2)
        entries = [json.loads(line) for line in self.lines()]
        self.assertEqual([item["payload_schema"] for item in entries], ["attempt-observation", "attempt-outcome"])
        self.assertEqual(entries[0]["payload"], observation)
        self.assertEqual(entries[1]["payload"], outcome)
        encoded = (self.root / self.ledger).read_text(encoding="utf-8")
        self.assertNotIn("private provider body", encoded)
        self.assertNotIn("secret upstream response", encoded)
        self.assertNotIn("host-private detail", encoded)

    def test_rejects_mismatched_run_id_and_observation_digest(self) -> None:
        observation = self.observation()
        outcome = self.outcome(observation)
        report: dict[str, object] = {"schema_version": "omp.worker-run-report/v1", "run_id": "wrong-run", "observation": observation, "outcome": outcome, "diagnostics": []}
        with self.assertRaisesRegex(LedgerError, "run_id does not match"):
            append_worker_report(self.root, self.ledger, report, now=lambda: NOW)
        report["run_id"] = "run-test"
        changed = deepcopy(outcome)
        changed["observation_digest_sha256"] = SHA
        report["outcome"] = changed
        with self.assertRaises(LedgerError):
            append_worker_report(self.root, self.ledger, report, now=lambda: NOW)
        self.assertFalse((self.root / self.ledger).exists())

    def test_rejects_schema_valid_outcome_not_derived_from_observation(
        self,
    ) -> None:
        observation = self.observation()
        rejected_observation = deepcopy(observation)
        rejected_observation["verifier"] = {
            "outcome": "rejected",
            "result_valid": True,
            "reward": 0,
        }
        forged = classify_attempt(rejected_observation)
        forged["observation_digest_sha256"] = canonical_sha256(
            observation
        )
        report = {
            "schema_version": "omp.worker-run-report/v1",
            "run_id": "run-test",
            "observation": observation,
            "outcome": forged,
        }

        with self.assertRaisesRegex(
            LedgerError,
            "was not derived",
        ):
            append_worker_report(
                self.root,
                self.ledger,
                report,
                now=lambda: NOW,
            )


if __name__ == "__main__":
    unittest.main()
