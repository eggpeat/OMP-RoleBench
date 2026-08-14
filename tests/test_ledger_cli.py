from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rolebench.cli import build_parser, run
from rolebench.contracts import canonical_json
from rolebench.ledger import LedgerError


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "local-non-authoritative"


class LedgerCliTests(unittest.TestCase):
    @patch("rolebench.cli.verify_ledger")
    def test_verify_human_output_reports_local_non_authority(self, verify: object) -> None:
        report = {
            "valid": True,
            "authority": AUTHORITY,
            "entries": 3,
            "diagnostics": [],
        }
        verify.return_value = report
        output = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "ledger", "verify", "journal.jsonl"],
            stdout=output,
        )

        self.assertEqual(status, 0)
        verify.assert_called_once_with(PRODUCT_ROOT, Path("journal.jsonl"))
        self.assertEqual(
            output.getvalue(),
            "authority: local-non-authoritative\n"
            "entries: 3\n"
            "valid: true\n"
            "diagnostics:\n"
            "  none\n",
        )

    @patch("rolebench.cli.verify_ledger")
    def test_verify_json_is_canonical_and_invalid_returns_one(self, verify: object) -> None:
        report = {
            "valid": False,
            "authority": AUTHORITY,
            "entries": 2,
            "diagnostics": ["entry 2 hash mismatch"],
        }
        verify.return_value = report
        output = StringIO()

        status = run(
            [
                "--root",
                str(PRODUCT_ROOT),
                "ledger",
                "verify",
                "journal.jsonl",
                "--json",
            ],
            stdout=output,
        )

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")
        self.assertEqual(json.loads(output.getvalue())["authority"], AUTHORITY)

    @patch("rolebench.cli.append_artifacts")
    def test_append_loads_object_and_maps_schema_and_value(self, append: object) -> None:
        artifact = {"attempt_id": "attempt-7", "accepted": True}
        report = {
            "authority": AUTHORITY,
            "appended": 1,
            "valid": True,
            "diagnostics": [],
        }
        append.return_value = report
        output = StringIO()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifact.json").write_text(
                canonical_json(artifact) + "\n", encoding="utf-8"
            )
            with patch(
                "rolebench.cli.resolve_root",
                return_value=root,
            ):
                status = run(
                    [
                        "--root",
                        str(root),
                        "ledger",
                        "append",
                        "journal.jsonl",
                        "attempt-outcome",
                        "artifact.json",
                        "--json",
                    ],
                    stdout=output,
                )

            append.assert_called_once_with(
                root,
                Path("journal.jsonl"),
                (("attempt-outcome", artifact),),
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), canonical_json(report) + "\n")
        self.assertEqual(json.loads(output.getvalue())["authority"], AUTHORITY)

    @patch("rolebench.cli.append_worker_report")
    def test_append_worker_report_passes_loaded_report_for_normalization(
        self, normalize: object
    ) -> None:
        worker_report = {
            "schema_version": "omp.worker-run-report/v1",
            "run_id": "run-12",
            "outcome": {"reason_code": "verifier-accepted"},
        }
        ledger_report = {
            "authority": AUTHORITY,
            "appended": 2,
            "valid": True,
            "diagnostics": [],
        }
        normalize.return_value = ledger_report
        output = StringIO()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "worker-report.json").write_text(
                canonical_json(worker_report) + "\n", encoding="utf-8"
            )
            with patch(
                "rolebench.cli.resolve_root",
                return_value=root,
            ):
                status = run(
                    [
                        "--root",
                        str(root),
                        "ledger",
                        "append-worker-report",
                        "journal.jsonl",
                        "worker-report.json",
                    ],
                    stdout=output,
                )

            normalize.assert_called_once_with(
                root,
                Path("journal.jsonl"),
                worker_report,
            )

        self.assertEqual(status, 0)
        self.assertIn("authority: local-non-authoritative\n", output.getvalue())
        self.assertIn("appended: 2\n", output.getvalue())

    @patch("rolebench.cli.verify_ledger")
    def test_ledger_error_returns_two(self, verify: object) -> None:
        verify.side_effect = LedgerError("entry hash is malformed")
        error = StringIO()

        status = run(
            ["--root", str(PRODUCT_ROOT), "ledger", "verify", "journal.jsonl"],
            stderr=error,
        )

        self.assertEqual(status, 2)
        self.assertEqual(error.getvalue(), "error: entry hash is malformed\n")

    def test_parser_maps_ledger_routes_and_json_flags(self) -> None:
        verify = build_parser().parse_args(
            ["ledger", "verify", "ledger.jsonl", "--json"]
        )
        append = build_parser().parse_args(
            ["ledger", "append", "ledger.jsonl", "task", "task.json", "--json"]
        )
        worker = build_parser().parse_args(
            [
                "ledger",
                "append-worker-report",
                "ledger.jsonl",
                "report.json",
                "--json",
            ]
        )

        self.assertEqual((verify.group, verify.command), ("ledger", "verify"))
        self.assertEqual(verify.ledger, Path("ledger.jsonl"))
        self.assertTrue(verify.as_json)
        self.assertEqual((append.group, append.command), ("ledger", "append"))
        self.assertEqual(append.ledger, Path("ledger.jsonl"))
        self.assertEqual(append.schema, "task")
        self.assertEqual(append.artifact, Path("task.json"))
        self.assertTrue(append.as_json)
        self.assertEqual(
            (worker.group, worker.command), ("ledger", "append-worker-report")
        )
        self.assertEqual(worker.ledger, Path("ledger.jsonl"))
        self.assertEqual(worker.report, Path("report.json"))
        self.assertTrue(worker.as_json)


if __name__ == "__main__":
    unittest.main()
