"""Command-line interface for role contract operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence, TextIO

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
    except ContractError as error:
        print(f"error: {error}", file=stderr)
        return 2


def main() -> None:
    raise SystemExit(run())
