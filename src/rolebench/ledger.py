"""A local, non-authoritative, tamper-evident experiment journal."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from .accounting import AccountingError, classify_attempt
from .contracts import (
    ContractError,
    JSONObject,
    canonical_json,
    canonical_sha256,
    validate_value,
)

_MAX_RECORD_BYTES = 1 << 20
_MAX_LEDGER_BYTES = 64 << 20
_MAX_APPEND_RECORDS = 1024
_ALLOWED_PAYLOAD_SCHEMAS = frozenset(
    ("attempt-observation", "attempt-outcome", "evidence-row", "task-qualification")
)


class LedgerError(Exception):
    """A user-facing experiment ledger error."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LedgerError("ledger timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ledger_path(root: Path, ledger_path: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    selected = ledger_path.expanduser()
    if not selected.is_absolute():
        selected = resolved_root / selected
    resolved_parent = selected.parent.resolve()
    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as error:
        raise LedgerError("ledger path escapes the repository root") from error
    return resolved_parent / selected.name


def _open_ledger(root: Path, ledger_path: Path, *, create: bool) -> int:
    selected = _ledger_path(root, ledger_path)
    flags = os.O_RDWR | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(selected, flags, 0o600)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise LedgerError("ledger path must not be a symbolic link") from error
        raise LedgerError(f"cannot open ledger: {error.strerror or 'operating system error'}") from error
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise LedgerError("ledger must be a regular file")
        if info.st_uid != os.geteuid():
            raise LedgerError("ledger must be owned by the current user")
        if info.st_nlink != 1:
            raise LedgerError("ledger must have exactly one hard link")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise LedgerError("ledger mode must be 0600")
        if info.st_size > _MAX_LEDGER_BYTES:
            raise LedgerError("ledger exceeds the maximum file size")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_file(fd: int, size: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, min(1 << 20, size - offset), offset)
        if not chunk:
            raise LedgerError("ledger was truncated while being read")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _validation_error(root: Path, schema_name: str, value: JSONObject, label: str) -> None:
    try:
        result = validate_value(root, schema_name, value, Path(label))
    except ContractError as error:
        raise LedgerError(f"cannot validate {label}: {error}") from error
    if result.valid:
        return
    details = "; ".join(
        f"{item.json_path}: {item.message}" for item in result.diagnostics
    )
    raise LedgerError(f"invalid {label}: {details}")


def _decode_line(root: Path, line: bytes, expected_sequence: int, previous: str | None) -> JSONObject:
    if len(line) > _MAX_RECORD_BYTES:
        raise LedgerError(f"ledger record {expected_sequence} exceeds the maximum size")
    try:
        text = line[:-1].decode("utf-8")
    except UnicodeDecodeError as error:
        raise LedgerError(f"ledger record {expected_sequence} is not UTF-8") from error
    try:
        decoded: object = json.loads(text)
    except (json.JSONDecodeError, ValueError) as error:
        raise LedgerError(f"ledger record {expected_sequence} is invalid JSON") from error
    if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
        raise LedgerError(f"ledger record {expected_sequence} must be a JSON object")
    entry: JSONObject = decoded
    try:
        canonical = canonical_json(entry).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise LedgerError(f"ledger record {expected_sequence} contains an invalid JSON value") from error
    if canonical != line:
        raise LedgerError(f"ledger record {expected_sequence} is not canonical JSONL")
    if entry.get("sequence") != expected_sequence:
        raise LedgerError(f"ledger record {expected_sequence} has a non-continuous sequence")
    if entry.get("previous_entry_sha256") != previous:
        raise LedgerError(f"ledger record {expected_sequence} breaks the hash chain")
    if entry.get("authority") != "local-non-authoritative":
        raise LedgerError(
            f"ledger record {expected_sequence} has invalid non-authoritative status"
        )
    payload_schema = entry.get("payload_schema")
    payload = entry.get("payload")
    if payload_schema not in _ALLOWED_PAYLOAD_SCHEMAS or not isinstance(payload, dict):
        raise LedgerError(f"ledger record {expected_sequence} has an invalid payload envelope")
    if entry.get("payload_digest_sha256") != canonical_sha256(payload):
        raise LedgerError(f"ledger record {expected_sequence} has an invalid payload digest")
    _validation_error(root, str(payload_schema), payload, f"ledger record {expected_sequence} payload")
    _validation_error(root, "experiment-ledger-entry", entry, f"ledger record {expected_sequence}")
    return entry


def _verify_locked(root: Path, fd: int) -> tuple[int, int | None, str | None, int]:
    info = os.fstat(fd)
    if info.st_size > _MAX_LEDGER_BYTES:
        raise LedgerError("ledger exceeds the maximum file size")
    data = _read_file(fd, info.st_size)
    after_read = os.fstat(fd)
    if (
        after_read.st_size != info.st_size
        or after_read.st_mtime_ns != info.st_mtime_ns
        or after_read.st_ctime_ns != info.st_ctime_ns
    ):
        raise LedgerError("ledger changed while being verified")
    if data and not data.endswith(b"\n"):
        raise LedgerError("ledger ends with a truncated record")
    count = 0
    previous: str | None = None
    for line in data.splitlines(keepends=True):
        _decode_line(root, line, count, previous)
        previous = sha256(line).hexdigest()
        count += 1
    last_sequence = count - 1 if count else None
    return count, last_sequence, previous, info.st_size


def _verify_report(count: int, last_sequence: int | None, head: str | None) -> JSONObject:
    return {
        "authority": "local-non-authoritative",
        "valid": True,
        "record_count": count,
        "last_sequence": last_sequence,
        "head_sha256": head,
    }


def verify_ledger(root: Path, ledger_path: Path) -> JSONObject:
    """Verify a local journal; this report is never admission or routing authority."""

    fd = _open_ledger(root, ledger_path, create=False)
    try:
        count, last_sequence, head, _ = _verify_locked(root, fd)
        return _verify_report(count, last_sequence, head)
    except LedgerError:
        raise
    except OSError as error:
        raise LedgerError(f"cannot verify ledger: {error.strerror or 'operating system error'}") from error
    finally:
        os.close(fd)


def _prepare_entry(
    root: Path,
    sequence: int,
    previous: str | None,
    schema_name: str,
    payload: JSONObject,
    now: Callable[[], datetime],
) -> tuple[bytes, str]:
    if schema_name not in _ALLOWED_PAYLOAD_SCHEMAS:
        raise LedgerError(f"payload schema {schema_name!r} is not allowed in the ledger")
    _validation_error(root, schema_name, payload, f"payload {sequence}")
    try:
        payload_digest = canonical_sha256(payload)
    except (TypeError, ValueError) as error:
        raise LedgerError(f"payload {sequence} contains an invalid JSON value") from error
    entry: JSONObject = {
        "schema_version": "omp.experiment-ledger-entry/v1",
        "authority": "local-non-authoritative",
        "sequence": sequence,
        "timestamp": _timestamp(now()),
        "previous_entry_sha256": previous,
        "payload_schema": schema_name,
        "payload_digest_sha256": payload_digest,
        "payload": payload,
    }
    _validation_error(root, "experiment-ledger-entry", entry, f"ledger record {sequence}")
    encoded = canonical_json(entry).encode("utf-8") + b"\n"
    if len(encoded) > _MAX_RECORD_BYTES:
        raise LedgerError(f"ledger record {sequence} exceeds the maximum size")
    return encoded, sha256(encoded).hexdigest()


def append_artifacts(
    root: Path,
    ledger_path: Path,
    records: Iterable[tuple[str, JSONObject]],
    *,
    now: Callable[[], datetime] = _utc_now,
) -> JSONObject:
    """Append to a local journal that cannot authorize admission or routing.

    In particular, a recorded qualification remains subject to independent
    task cross-validation by the admission workflow.
    """

    captured: list[tuple[str, JSONObject]] = []
    for record in records:
        if len(captured) == _MAX_APPEND_RECORDS:
            raise LedgerError("too many artifacts in one ledger append")
        captured.append(record)
    if not captured:
        raise LedgerError("at least one artifact is required")
    fd = _open_ledger(root, ledger_path, create=True)
    try:
        count, _, head, size = _verify_locked(root, fd)
        first_sequence = count
        prepared: list[bytes] = []
        for offset, record in enumerate(captured):
            if not isinstance(record, tuple) or len(record) != 2:
                raise LedgerError("each ledger record must be a (schema, payload) tuple")
            schema_name, payload = record
            if not isinstance(schema_name, str) or not isinstance(payload, dict):
                raise LedgerError("each ledger record must contain a schema name and JSON object")
            encoded, head = _prepare_entry(
                root, first_sequence + offset, head, schema_name, payload, now
            )
            prepared.append(encoded)
        block = b"".join(prepared)
        if size + len(block) > _MAX_LEDGER_BYTES:
            raise LedgerError("append would exceed the maximum ledger file size")
        if os.fstat(fd).st_size != size:
            raise LedgerError(
                "ledger changed while preparing the append"
            )
        write_offset = 0
        while write_offset < len(block):
            written = os.write(fd, block[write_offset:])
            if written <= 0:
                raise LedgerError(
                    "ledger append was incomplete"
                )
            write_offset += written
        os.fsync(fd)
        final_count = count + len(prepared)
        return {
            "authority": "local-non-authoritative",
            "valid": True,
            "appended_count": len(prepared),
            "record_count": final_count,
            "first_sequence": first_sequence,
            "last_sequence": final_count - 1,
            "head_sha256": head,
        }
    except LedgerError:
        raise
    except OSError as error:
        raise LedgerError(f"cannot append ledger: {error.strerror or 'operating system error'}") from error
    finally:
        os.close(fd)


def _attempt_id(value: JSONObject, label: str) -> str:
    attempt = value.get("attempt")
    if not isinstance(attempt, dict) or not isinstance(attempt.get("attempt_id"), str):
        raise LedgerError(f"worker report {label} has no normalized attempt id")
    return str(attempt["attempt_id"])


def append_worker_report(
    root: Path,
    ledger_path: Path,
    report: JSONObject,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> JSONObject:
    """Append only validated observation and outcome fields from a worker report.

    Report diagnostics, provider error bodies, and every other top-level field
    are deliberately excluded.
    """

    if report.get("schema_version") != "omp.worker-run-report/v1":
        raise LedgerError("worker report has an invalid schema version")
    run_id = report.get("run_id")
    observation = report.get("observation")
    outcome = report.get("outcome")
    if not isinstance(run_id, str) or not run_id:
        raise LedgerError("worker report run_id must be a non-empty string")
    if not isinstance(observation, dict) or not isinstance(outcome, dict):
        raise LedgerError("worker report must contain normalized observation and outcome objects")
    _validation_error(root, "attempt-observation", observation, "worker report observation")
    _validation_error(root, "attempt-outcome", outcome, "worker report outcome")
    if _attempt_id(observation, "observation") != run_id or _attempt_id(outcome, "outcome") != run_id:
        raise LedgerError("worker report run_id does not match its normalized artifacts")
    if outcome.get("observation_id") != observation.get("observation_id"):
        raise LedgerError("worker report outcome does not reference its observation")
    if outcome.get("observation_digest_sha256") != canonical_sha256(observation):
        raise LedgerError("worker report outcome observation digest does not match")
    try:
        expected_outcome = classify_attempt(observation)
    except AccountingError as error:
        raise LedgerError(
            "worker report observation cannot be classified"
        ) from error
    if outcome != expected_outcome:
        raise LedgerError(
            "worker report outcome was not derived from its observation"
        )
    return append_artifacts(
        root,
        ledger_path,
        (("attempt-observation", observation), ("attempt-outcome", outcome)),
        now=now,
    )
