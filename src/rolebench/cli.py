"""Command-line interface for role contract operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence, TextIO

from .accounting import AccountingError, classify_attempt, summarize_outcomes

from .contracts import (
    BUILTIN_ROLES,
    ContractError,
    Diagnostic,
    canonical_digest,
    canonical_json,
    load_repository,
    resolve_root,
    validate_artifact,
    validate_repository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rolebench")
    parser.add_argument("--root", type=Path, help="rolebench repository root")
    groups = parser.add_subparsers(dest="group", required=True)
    contracts = groups.add_parser("contracts", help="inspect contract artifacts")
    commands = contracts.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate all contract artifacts")
    validate.add_argument("--json", action="store_true", dest="as_json")

    digest = commands.add_parser("digest", help="compute the canonical contract digest")
    digest.add_argument("--json", action="store_true", dest="as_json")

    show = commands.add_parser("show", help="show one role manifest")
    show.add_argument("role")

    artifacts = groups.add_parser("artifacts", help="validate individual artifacts")
    artifact_commands = artifacts.add_subparsers(dest="command", required=True)
    artifact_validate = artifact_commands.add_parser(
        "validate",
        help="validate an artifact against a named schema",
    )
    artifact_validate.add_argument("schema_name")
    artifact_validate.add_argument("path", type=Path)
    artifact_validate.add_argument("--json", action="store_true", dest="as_json")
    accounting = groups.add_parser(
        "accounting",
        help="classify attempts without blaming models for system failures",
    )
    accounting_commands = accounting.add_subparsers(dest="command", required=True)
    classify = accounting_commands.add_parser(
        "classify",
        help="classify one normalized attempt observation",
    )
    classify.add_argument("path", type=Path)

    summarize = accounting_commands.add_parser(
        "summarize",
        help="summarize model quality and unscored attempts separately",
    )
    summarize.add_argument("paths", type=Path, nargs="+")
    summarize.add_argument("--json", action="store_true", dest="as_json")

    return parser


def _diagnostic_object(diagnostic: Diagnostic) -> dict[str, str]:
    return {
        "file": diagnostic.file,
        "json_path": diagnostic.json_path,
        "message": diagnostic.message,
    }


def _print_diagnostics(
    diagnostics: tuple[Diagnostic, ...],
    *,
    as_json: bool,
    stdout: TextIO,
    success_message: str = "contracts are valid",
) -> None:
    if as_json:
        payload: dict[str, object] = {
            "valid": not diagnostics,
            "diagnostics": [_diagnostic_object(item) for item in diagnostics],
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stdout)
        return
    if not diagnostics:
        print(success_message, file=stdout)
        return
    for diagnostic in diagnostics:
        print(f"{diagnostic.file}:{diagnostic.json_path}: {diagnostic.message}", file=stdout)


def _read_json_object(root: Path, artifact_path: Path) -> dict[str, object]:
    path = artifact_path.expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ContractError(str(error), path.as_posix()) from error
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            path.as_posix(),
        ) from error
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", path.as_posix())
    return value


def _print_accounting_summary(summary: dict[str, object], stdout: TextIO) -> None:
    quality_score = summary["quality_score"]
    accepted = summary["accepted"]
    rejected = summary["rejected"]
    if isinstance(quality_score, float):
        print(
            f"Quality score: {quality_score:.1%} "
            f"({accepted} accepted, {rejected} rejected)",
            file=stdout,
        )
    else:
        print("Quality score: not available (no completed model results)", file=stdout)

    not_scored = summary["not_scored"]
    if not isinstance(not_scored, dict):
        raise AccountingError("summary not_scored value must be an object")
    print(
        "Not scored: "
        f"{not_scored['retryable_invalid']} system failures, "
        f"{not_scored['quarantined']} quarantined, "
        f"{not_scored['cancelled']} cancelled",
        file=stdout,
    )


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run the CLI and return its process exit status."""

    arguments = build_parser().parse_args(argv)
    try:
        root = resolve_root(arguments.root)
        if arguments.group == "artifacts" and arguments.command == "validate":
            result = validate_artifact(root, arguments.schema_name, arguments.path)
            _print_diagnostics(
                result.diagnostics,
                as_json=arguments.as_json,
                stdout=stdout,
                success_message="artifact is valid",
            )
            return 0 if result.valid else 1

        if arguments.group == "accounting" and arguments.command == "classify":
            result = validate_artifact(root, "attempt-observation", arguments.path)
            if not result.valid:
                _print_diagnostics(
                    result.diagnostics,
                    as_json=False,
                    stdout=stderr,
                    success_message="",
                )
                return 1
            observation = _read_json_object(root, arguments.path)
            print(canonical_json(classify_attempt(observation)), file=stdout)
            return 0

        if arguments.group == "accounting" and arguments.command == "summarize":
            diagnostics = tuple(
                sorted(
                    {
                        diagnostic
                        for path in arguments.paths
                        for diagnostic in validate_artifact(
                            root,
                            "attempt-outcome",
                            path,
                        ).diagnostics
                    }
                )
            )
            if diagnostics:
                _print_diagnostics(
                    diagnostics,
                    as_json=arguments.as_json,
                    stdout=stderr,
                    success_message="",
                )
                return 1
            outcomes = [
                _read_json_object(root, path)
                for path in arguments.paths
            ]
            summary = summarize_outcomes(outcomes)
            if arguments.as_json:
                print(canonical_json(summary), file=stdout)
            else:
                _print_accounting_summary(summary, stdout)
            return 0

        if arguments.group == "contracts" and arguments.command == "validate":
            result = validate_repository(root)
            _print_diagnostics(result.diagnostics, as_json=arguments.as_json, stdout=stdout)
            return 0 if result.valid else 1

        if arguments.group == "contracts" and arguments.command == "digest":
            result = validate_repository(root)
            if not result.valid:
                _print_diagnostics(result.diagnostics, as_json=arguments.as_json, stdout=stderr)
                return 1
            value = canonical_digest(load_repository(root))
            if arguments.as_json:
                print(canonical_json({"algorithm": "sha256", "digest": value}), file=stdout)
            else:
                print(value, file=stdout)
            return 0

        if arguments.group == "contracts" and arguments.command == "show":
            role = arguments.role
            if role not in BUILTIN_ROLES:
                raise ContractError(f"unknown role: {role}")
            repository = load_repository(root)
            manifests = dict(repository.manifests)
            print(canonical_json(manifests[role]), file=stdout)
            return 0

        raise ContractError(f"unknown command: {arguments.command}")
    except (AccountingError, ContractError) as error:
        print(f"error: {error}", file=stderr)
        return 2


def main() -> None:
    raise SystemExit(run())
