"""Command-line interface for role contract operations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import stat
from typing import Sequence, TextIO

from .accounting import AccountingError, classify_attempt, summarize_outcomes

from .contracts import (
    JSONObject,
    JSONValue,
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
from .fault_harness import FaultHarnessError, run_fault_check
from .ledger import (
    LedgerError,
    append_artifacts,
    append_worker_report,
    verify_ledger,
)
from .task_workflow import (
    TaskAdmissionError,
    TaskWorkflowError,
    check_task_qualification,
    generate_task_qualification,
    import_omp_gym_task,
    prepare_admission_worker_manifest,
    prepare_worker_manifest,
    scan_session_candidates,
    verify_task_pack,
)
from .worker import WorkerError, doctor_worker, run_worker


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
    worker = groups.add_parser(
        "worker",
        help="check scored-worker policy and accounting conformance",
    )
    worker_commands = worker.add_subparsers(dest="command", required=True)
    fault_check = worker_commands.add_parser(
        "fault-check",
        help="run deterministic scored-worker fault accounting checks",
    )
    fault_check.add_argument("policy", type=Path)
    fault_check.add_argument("--json", action="store_true", dest="as_json")

    doctor = worker_commands.add_parser(
        "doctor",
        help="check whether the Docker/runsc worker is ready",
    )
    doctor.add_argument("policy", type=Path)
    doctor.add_argument("--docker", default="docker", metavar="PATH")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    worker_run = worker_commands.add_parser(
        "run",
        help="run a provider-disabled scored-worker manifest",
    )
    worker_run.add_argument("manifest", type=Path)
    worker_run.add_argument("--docker", default="docker", metavar="PATH")
    worker_run.add_argument("--json", action="store_true", dest="as_json")
    worker_run.add_argument(
        "--report",
        type=Path,
        help="write the immutable worker report as canonical JSON",
    )
    tasks = groups.add_parser(
        "tasks",
        help="author, review, and prepare diagnostic tasks",
    )
    task_commands = tasks.add_subparsers(dest="command", required=True)
    scan_session = task_commands.add_parser(
        "scan-session",
        help="privately scan one explicit OMP session for candidate signals",
    )
    scan_session.add_argument("session", type=Path)
    scan_session.add_argument("--role-hint", choices=BUILTIN_ROLES)

    import_omp_gym = task_commands.add_parser(
        "import-omp-gym",
        help="import one omp-gym task into the private candidate area",
    )
    import_omp_gym.add_argument("source", type=Path)
    import_omp_gym.add_argument("destination", type=Path)
    import_omp_gym.add_argument("--source-version", required=True)
    import_omp_gym.add_argument("--license", required=True, dest="license_expression")
    import_omp_gym.add_argument("--role-hint", choices=BUILTIN_ROLES)
    import_omp_gym.add_argument(
        "--capability",
        action="append",
        default=[],
        dest="capability_hints",
        metavar="TAG",
    )

    qualification_check = task_commands.add_parser(
        "qualification-check",
        help="cross-check one task and reviewed qualification",
    )
    qualification_check.add_argument("task", type=Path)
    qualification_check.add_argument("qualification", type=Path)
    qualification_check.add_argument("--json", action="store_true", dest="as_json")

    qualify = task_commands.add_parser(
        "qualify",
        help="generate reviewed qualification from healthy worker evidence",
    )
    qualify.add_argument("task", type=Path)
    qualify.add_argument(
        "--baseline-report",
        action="append",
        required=True,
        type=Path,
    )
    qualify.add_argument(
        "--reference-report",
        action="append",
        required=True,
        type=Path,
    )
    qualify.add_argument(
        "--tamper-report",
        action="append",
        required=True,
        type=Path,
    )
    qualify.add_argument("--reviewer", required=True)
    qualify.add_argument("--output", type=Path, required=True)
    qualify.add_argument("--json", action="store_true", dest="as_json")

    pack_verify = task_commands.add_parser(
        "pack-verify",
        help="validate one versioned task pack and its admission links",
    )
    pack_verify.add_argument("pack", type=Path)
    pack_verify.add_argument("--json", action="store_true", dest="as_json")

    prepare_admission = task_commands.add_parser(
        "prepare-admission-run",
        help="prepare a content-bound baseline, reference, or tamper run",
    )
    prepare_admission.add_argument("task", type=Path)
    prepare_admission.add_argument(
        "--probe",
        required=True,
        choices=("baseline", "reference", "tamper"),
    )
    prepare_admission.add_argument("--run-id", required=True)
    prepare_admission.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    prepare_admission.add_argument(
        "--docker",
        default="docker",
        metavar="PATH",
    )

    prepare_run = task_commands.add_parser(
        "prepare-run",
        help="prepare a content-bound provider-disabled worker manifest",
    )
    prepare_run.add_argument("task", type=Path)
    prepare_run.add_argument("qualification", type=Path)
    prepare_run.add_argument("--run-id", required=True)
    prepare_run.add_argument("--output", type=Path, required=True)
    prepare_run.add_argument("--docker", default="docker", metavar="PATH")

    ledger = groups.add_parser(
        "ledger",
        help="maintain a local non-authoritative experiment journal",
    )
    ledger_commands = ledger.add_subparsers(dest="command", required=True)
    ledger_verify = ledger_commands.add_parser(
        "verify",
        help="verify a local journal hash chain",
    )
    ledger_verify.add_argument("ledger", type=Path)
    ledger_verify.add_argument("--json", action="store_true", dest="as_json")

    ledger_append = ledger_commands.add_parser(
        "append",
        help="append one normalized artifact to a local journal",
    )
    ledger_append.add_argument("ledger", type=Path)
    ledger_append.add_argument("schema")
    ledger_append.add_argument("artifact", type=Path)
    ledger_append.add_argument("--json", action="store_true", dest="as_json")

    append_report = ledger_commands.add_parser(
        "append-worker-report",
        help="append normalized observation and outcome from a worker report",
    )
    append_report.add_argument("ledger", type=Path)
    append_report.add_argument("report", type=Path)
    append_report.add_argument("--json", action="store_true", dest="as_json")


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


def _read_json_object(root: Path, artifact_path: Path) -> JSONObject:
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


def _write_worker_report(
    root: Path,
    output_path: Path,
    report: JSONObject,
) -> None:
    resolved_root = root.expanduser().resolve(strict=True)
    selected = output_path.expanduser()
    if not selected.is_absolute():
        selected = resolved_root / selected
    candidate = Path(os.path.abspath(selected))
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError as error:
        raise WorkerError(
            "worker report must remain inside the repository"
        ) from error
    if not relative.parts:
        raise WorkerError(
            "worker report must name a repository file"
        )
    parent = resolved_root
    for part in relative.parts[:-1]:
        parent /= part
        try:
            os.mkdir(parent, 0o700)
        except FileExistsError:
            try:
                info = parent.lstat()
            except OSError as error:
                raise WorkerError(
                    "worker report parent is unavailable"
                ) from error
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise WorkerError(
                    "worker report parent is unsafe"
                )
        except OSError as error:
            raise WorkerError(
                "cannot create worker report parent"
            ) from error
    output = parent / relative.name
    encoded = (canonical_json(report) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError as error:
        raise WorkerError("cannot create worker report") from error
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                raise OSError("short worker report write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError as error:
        os.close(descriptor)
        output.unlink(missing_ok=True)
        raise WorkerError(
            "cannot persist worker report"
        ) from error
    else:
        os.close(descriptor)
    try:
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError as error:
        raise WorkerError(
            "cannot synchronize worker report parent"
        ) from error


def _print_accounting_summary(summary: JSONObject, stdout: TextIO) -> None:
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
        f"{not_scored['cancelled']} cancelled, "
        f"{not_scored['excluded']} excluded evidence",
        file=stdout,
    )

def _format_fault_outcome(outcome: JSONValue) -> str:
    if not isinstance(outcome, dict):
        raise FaultHarnessError("scenario outcome must be an object")
    fields = (
        "reason_code",
        "disposition",
        "failure_domain",
        "model_outcome",
        "verifier_outcome",
        "counts_toward_quality",
    )
    return ", ".join(f"{field}={outcome.get(field)!r}" for field in fields)


def _print_fault_check_report(report: JSONObject, stdout: TextIO) -> None:
    passed = report.get("passed") is True
    print(f"Worker fault check: {'PASS' if passed else 'FAIL'}", file=stdout)
    print(f"Policy SHA-256: {report['policy_digest_sha256']}", file=stdout)
    print(
        "Reason-code coverage: "
        f"{report['covered_reason_count']}/{report['expected_reason_count']} "
        f"({report['scenario_count']} scenarios)",
        file=stdout,
    )
    covered_reason_codes = report["covered_reason_codes"]
    if not isinstance(covered_reason_codes, list):
        raise FaultHarnessError("covered_reason_codes must be an array")
    print(
        "Covered reason codes: " + ", ".join(str(item) for item in covered_reason_codes),
        file=stdout,
    )
    print(f"External calls: {report['external_calls']}", file=stdout)
    print("Scenarios:", file=stdout)
    scenarios = report["scenarios"]
    if not isinstance(scenarios, list):
        raise FaultHarnessError("scenarios must be an array")
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise FaultHarnessError("scenario must be an object")
        result = "PASS" if scenario.get("passed") is True else "FAIL"
        print(
            f"[{result}] {scenario['scenario']}: "
            f"expected ({_format_fault_outcome(scenario['expected'])}); "
            f"actual ({_format_fault_outcome(scenario['actual'])}); "
            f"observation_valid={scenario['observation_valid']!r}; "
            f"outcome_valid={scenario['outcome_valid']!r}",
            file=stdout,
        )
        diagnostics = scenario["diagnostics"]
        if diagnostics:
            print(f"  diagnostics: {canonical_json(diagnostics)}", file=stdout)
    print(
        f"Scenarios passed: {report['passed_scenarios']}; "
        f"failed: {report['failed_scenarios']}",
        file=stdout,
    )
    quality_summary = report["quality_summary"]
    if not isinstance(quality_summary, dict):
        raise FaultHarnessError("quality_summary must be an object")
    _print_accounting_summary(quality_summary, stdout)


_WORKER_DOCTOR_CHECKS = (
    ("policy_valid", "Worker policy"),
    ("local_socket", "Local rootless Docker socket"),
    ("docker_executable", "Docker executable"),
    ("docker_server", "Docker server"),
    ("rootless", "Rootless Docker"),
    ("runsc", "runsc runtime"),
    ("cgroup_v2", "cgroup v2"),
    ("delegation", "cgroup delegation"),
    ("resource_enforcement", "runsc resource enforcement"),
)


def _format_report_value(value: JSONValue) -> str:
    if isinstance(value, str):
        return value
    return canonical_json(value)


def _print_workflow_report(report: JSONObject, stdout: TextIO) -> None:
    for key in sorted(report):
        if key == "diagnostics":
            continue
        print(
            f"{key.replace('_', ' ')}: "
            f"{_format_report_value(report[key])}",
            file=stdout,
        )
    diagnostics = report.get("diagnostics")
    if isinstance(diagnostics, list):
        print("diagnostics:", file=stdout)
        if not diagnostics:
            print("  none", file=stdout)
        for diagnostic in diagnostics:
            print(f"  - {_format_report_value(diagnostic)}", file=stdout)


def _print_report_diagnostics(report: JSONObject, stdout: TextIO) -> None:
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise WorkerError("report diagnostics must be an array")
    print("Diagnostics:", file=stdout)
    if not diagnostics:
        print("  none", file=stdout)
        return
    for diagnostic in diagnostics:
        print(f"  - {_format_report_value(diagnostic)}", file=stdout)


def _print_worker_doctor_report(report: JSONObject, stdout: TextIO) -> None:
    ready = report.get("ready") is True
    print(f"Worker doctor: {'READY' if ready else 'NOT READY'}", file=stdout)
    print("Required checks:", file=stdout)
    for key, label in _WORKER_DOCTOR_CHECKS:
        result = "PASS" if report.get(key) is True else "FAIL"
        print(f"  {label}: {result}", file=stdout)
    _print_report_diagnostics(report, stdout)


def _print_worker_run_report(report: JSONObject, stdout: TextIO) -> None:
    passed = report.get("passed") is True
    print(f"Worker run: {'PASS' if passed else 'FAIL'}", file=stdout)
    print(
        f"External provider calls: {report.get('external_provider_calls')}",
        file=stdout,
    )
    print(f"Policy SHA-256: {report.get('policy_digest_sha256')}", file=stdout)
    artifact_digest = report.get("artifact_digest_sha256")
    artifact_text = (
        artifact_digest if isinstance(artifact_digest, str) else "unavailable"
    )
    print(f"Artifact SHA-256: {artifact_text}", file=stdout)

    outcome = report.get("outcome")
    if not isinstance(outcome, dict):
        raise WorkerError("run report outcome must be an object")
    print(
        "Outcome: "
        f"disposition={outcome.get('disposition')!r}, "
        f"reason={outcome.get('reason_code')!r}, "
        f"domain={outcome.get('failure_domain')!r}",
        file=stdout,
    )

    isolation = report.get("isolation")
    if not isinstance(isolation, dict):
        raise WorkerError("run report isolation must be an object")
    print("Isolation:", file=stdout)
    if not isolation:
        print("  none", file=stdout)
    else:
        for key in sorted(isolation):
            print(f"  {key}: {_format_report_value(isolation[key])}", file=stdout)
    _print_report_diagnostics(report, stdout)




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
        if arguments.group == "tasks" and arguments.command == "scan-session":
            candidate = scan_session_candidates(
                arguments.session,
                role_hint=arguments.role_hint,
            )
            print(canonical_json(candidate), file=stdout)
            return 0

        if arguments.group == "tasks" and arguments.command == "import-omp-gym":
            candidate = import_omp_gym_task(
                root,
                arguments.source,
                arguments.destination,
                source_version=arguments.source_version,
                license_expression=arguments.license_expression,
                role_hint=arguments.role_hint,
                capability_hints=tuple(arguments.capability_hints),
            )
            print(canonical_json(candidate), file=stdout)
            return 0

        if (
            arguments.group == "tasks"
            and arguments.command == "qualification-check"
        ):
            report = check_task_qualification(
                root,
                arguments.task,
                arguments.qualification,
            )
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_workflow_report(report, stdout)
            return (
                0
                if report.get("valid") is True
                and report.get("decision") == "admitted"
                else 1
            )

        if arguments.group == "tasks" and arguments.command == "qualify":
            qualification = generate_task_qualification(
                root,
                arguments.task,
                tuple(arguments.baseline_report),
                tuple(arguments.reference_report),
                tuple(arguments.tamper_report),
                arguments.output,
                reviewer=arguments.reviewer,
            )
            if arguments.as_json:
                print(canonical_json(qualification), file=stdout)
            else:
                _print_workflow_report(qualification, stdout)
            return (
                0
                if qualification.get("decision") == "admitted"
                else 1
            )

        if arguments.group == "tasks" and arguments.command == "pack-verify":
            report = verify_task_pack(root, arguments.pack)
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_workflow_report(report, stdout)
            return 0 if report.get("valid") is True else 1

        if (
            arguments.group == "tasks"
            and arguments.command == "prepare-admission-run"
        ):
            manifest = prepare_admission_worker_manifest(
                root,
                arguments.task,
                arguments.probe,
                arguments.run_id,
                arguments.output,
                docker=arguments.docker,
            )
            print(canonical_json(manifest), file=stdout)
            return 0

        if arguments.group == "tasks" and arguments.command == "prepare-run":
            manifest = prepare_worker_manifest(
                root,
                arguments.task,
                arguments.qualification,
                arguments.run_id,
                arguments.output,
                docker=arguments.docker,
            )
            print(canonical_json(manifest), file=stdout)
            return 0

        if arguments.group == "ledger" and arguments.command == "verify":
            report = verify_ledger(root, arguments.ledger)
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_workflow_report(report, stdout)
            return 0 if report.get("valid") is True else 1

        if arguments.group == "ledger" and arguments.command == "append":
            artifact = _read_json_object(root, arguments.artifact)
            report = append_artifacts(
                root,
                arguments.ledger,
                ((arguments.schema, artifact),),
            )
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_workflow_report(report, stdout)
            return 0

        if (
            arguments.group == "ledger"
            and arguments.command == "append-worker-report"
        ):
            worker_report = _read_json_object(root, arguments.report)
            report = append_worker_report(
                root,
                arguments.ledger,
                worker_report,
            )
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_workflow_report(report, stdout)
            return 0

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
        if arguments.group == "worker" and arguments.command == "doctor":
            report = doctor_worker(
                root,
                arguments.policy,
                docker=arguments.docker,
            )
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_worker_doctor_report(report, stdout)
            return 0 if report.get("ready") is True else 1

        if arguments.group == "worker" and arguments.command == "run":
            report = run_worker(
                root,
                arguments.manifest,
                docker=arguments.docker,
            )
            if arguments.report is not None:
                _write_worker_report(root, arguments.report, report)
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_worker_run_report(report, stdout)
            return 0 if report.get("passed") is True else 1

        if arguments.group == "worker" and arguments.command == "fault-check":
            result = validate_artifact(
                root,
                "scored-worker-policy",
                arguments.policy,
            )
            if not result.valid:
                _print_diagnostics(
                    result.diagnostics,
                    as_json=arguments.as_json,
                    stdout=stderr,
                    success_message="",
                )
                return 1
            report = run_fault_check(root, arguments.policy)
            if arguments.as_json:
                print(canonical_json(report), file=stdout)
            else:
                _print_fault_check_report(report, stdout)
            return 0 if report.get("passed") is True else 1


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
    except TaskAdmissionError as error:
        print(f"error: {error}", file=stderr)
        return 1
    except (
        AccountingError,
        ContractError,
        FaultHarnessError,
        LedgerError,
        TaskWorkflowError,
        WorkerError,
    ) as error:
        print(f"error: {error}", file=stderr)
        return 2


def main() -> None:
    raise SystemExit(run())
