#!/usr/bin/env python3
"""Passive exact-result verifier for db-wal-recovery task."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from typing import NoReturn

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
HEADER_KEYS = {
    "schema_version",
    "attempt_nonce",
    "run_id",
    "task_digest_sha256",
    "policy_digest_sha256",
    "artifact_digest_sha256",
    "evaluation_request_digest_sha256",
    "verifier_image_digest_sha256",
    "runner",
    "container",
    "stdout",
    "stderr",
}
CONTAINER_KEYS = {
    "container_id",
    "state",
    "exit_code",
    "oom_killed",
    "timed_out",
    "overflowed",
    "duration_seconds",
    "removed",
}
IO_KEYS = {"byte_count", "digest_sha256", "authority"}
SNAPSHOT_KEYS = {"schema_version", "status", "error", "submission"}

def _compute_transform(obs_bytes: bytes, kind: str, param: int) -> str:
    if kind == "xor":
        if not (0 <= param <= 255):
            raise EvidenceError("xor parameter out of range")
        res = bytes(b ^ param for b in obs_bytes)
    elif kind == "invert":
        if param != 0:
            raise EvidenceError("invert parameter must be 0")
        res = bytes((~b) & 0xff for b in obs_bytes)
    elif kind == "byteswap":
        if param == 2:
            res = bytes([obs_bytes[1], obs_bytes[0], obs_bytes[3], obs_bytes[2]])
        elif param == 4:
            res = bytes([obs_bytes[3], obs_bytes[2], obs_bytes[1], obs_bytes[0]])
        else:
            raise EvidenceError("byteswap parameter must be 2 or 4")
    elif kind == "rot":
        if not (0 <= param <= 255):
            raise EvidenceError("rot parameter out of range")
        res = bytes((b + param) & 0xff for b in obs_bytes)
    elif kind == "shift":
        if not (0 <= param <= 7):
            raise EvidenceError("shift parameter out of range")
        res = bytes((b >> param) & 0xff for b in obs_bytes)
    else:
        raise EvidenceError(f"unknown transform {kind}")
    return res.hex()


class EvidenceError(ValueError):
    """Malformed runner evidence or result payload."""


def _reject_constant(value: str) -> NoReturn:
    raise EvidenceError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _emit_result(
    *,
    outcome: str,
    reward: int | None,
    run_id: str,
    attempt_nonce: str,
    artifact_digest_sha256: str,
    runner_evidence_digest_sha256: str,
    evaluation_request_digest_sha256: str,
    verifier_image_digest_sha256: str,
) -> int:
    result = {
        "schema_version": VERIFIER_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt_nonce": attempt_nonce,
        "outcome": outcome,
        "reward": reward,
        "artifact_digest_sha256": artifact_digest_sha256,
        "runner_evidence_digest_sha256": runner_evidence_digest_sha256,
        "evaluation_request_digest_sha256": evaluation_request_digest_sha256,
        "verifier_image_digest_sha256": verifier_image_digest_sha256,
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_EVIDENCE_BYTES + 1)
    evidence_digest = hashlib.sha256(raw).hexdigest()
    fallback = {
        "run_id": "unknown",
        "attempt_nonce": "0" * 64,
        "artifact_digest_sha256": "0" * 64,
        "evaluation_request_digest_sha256": "0" * 64,
        "verifier_image_digest_sha256": "0" * 64,
    }

    def emit(outcome: str, reward: int | None, values: dict[str, str] = fallback) -> int:
        return _emit_result(
            outcome=outcome,
            reward=reward,
            run_id=values["run_id"],
            attempt_nonce=values["attempt_nonce"],
            artifact_digest_sha256=values["artifact_digest_sha256"],
            runner_evidence_digest_sha256=evidence_digest,
            evaluation_request_digest_sha256=values["evaluation_request_digest_sha256"],
            verifier_image_digest_sha256=values["verifier_image_digest_sha256"],
        )

    if not raw or len(raw) > MAX_EVIDENCE_BYTES:
        return emit("error", None)
    magic_len = len(RUNNER_EVIDENCE_MAGIC)
    if not raw.startswith(RUNNER_EVIDENCE_MAGIC) or len(raw) < magic_len + 8:
        return emit("error", None)
    header_length = struct.unpack(">Q", raw[magic_len : magic_len + 8])[0]
    header_start = magic_len + 8
    header_end = header_start + header_length
    if header_end > len(raw):
        return emit("error", None)
    header_bytes = raw[header_start:header_end]
    try:
        header = json.loads(
            header_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("error", None)
    if (
        not isinstance(header, dict)
        or set(header) != HEADER_KEYS
        or header_bytes != json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or header.get("schema_version") != RUNNER_EVIDENCE_SCHEMA_VERSION
        or not isinstance(header.get("run_id"), str)
        or not header.get("run_id")
        or any(
            not _is_sha256(header.get(field))
            for field in (
                "attempt_nonce",
                "task_digest_sha256",
                "policy_digest_sha256",
                "artifact_digest_sha256",
                "evaluation_request_digest_sha256",
                "verifier_image_digest_sha256",
            )
        )
    ):
        return emit("error", None)
    bound = {
        "run_id": str(header["run_id"]),
        "attempt_nonce": str(header["attempt_nonce"]),
        "artifact_digest_sha256": str(header["artifact_digest_sha256"]),
        "evaluation_request_digest_sha256": str(header["evaluation_request_digest_sha256"]),
        "verifier_image_digest_sha256": str(header["verifier_image_digest_sha256"]),
    }

    def reject() -> int:
        return emit("rejected", 0, bound)

    container = header.get("container")
    duration = container.get("duration_seconds") if isinstance(container, dict) else None
    if (
        not isinstance(container, dict)
        or set(container) != CONTAINER_KEYS
        or container.get("state") != "exited"
        or container.get("exit_code") != 0
        or container.get("oom_killed") is not False
        or container.get("timed_out") is not False
        or container.get("overflowed") is not False
        or container.get("removed") is not True
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        return reject()
    stdout_info = header.get("stdout")
    stderr_info = header.get("stderr")
    if (
        not isinstance(stdout_info, dict)
        or not isinstance(stderr_info, dict)
        or set(stdout_info) != IO_KEYS
        or set(stderr_info) != IO_KEYS
        or stdout_info.get("authority") != "untrusted"
        or stderr_info.get("authority") != "untrusted"
    ):
        return reject()
    stdout_len = stdout_info.get("byte_count")
    stderr_len = stderr_info.get("byte_count")
    if (
        isinstance(stdout_len, bool)
        or isinstance(stderr_len, bool)
        or not isinstance(stdout_len, int)
        or not isinstance(stderr_len, int)
        or stdout_len < 0
        or stderr_len < 0
    ):
        return reject()
    stdout_start = header_end
    stdout_end = stdout_start + stdout_len
    stderr_end = stdout_end + stderr_len
    if stderr_end != len(raw):
        return reject()
    stdout_bytes = raw[stdout_start:stdout_end]
    stderr_bytes = raw[stdout_end:stderr_end]
    if (
        hashlib.sha256(stdout_bytes).hexdigest() != stdout_info.get("digest_sha256")
        or hashlib.sha256(stderr_bytes).hexdigest() != stderr_info.get("digest_sha256")
        or stderr_bytes
    ):
        return reject()
    if b"\0" in stdout_bytes:
        return emit("rejected", 0, bound)
    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return emit("rejected", 0, bound)

    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != SNAPSHOT_KEYS
        or snapshot.get("schema_version") != "rolebench.db-wal-recovery-runner-snapshot/v1"
    ):
        return emit("rejected", 0, bound)

    if snapshot.get("status") != "parsed" or snapshot.get("error") is not None:
        return emit("rejected", 0, bound)

    sub = snapshot.get("submission")
    if not isinstance(sub, dict):
        return emit("rejected", 0, bound)

    # Load private expected state fixtures
    script_dir = Path(__file__).resolve().parent
    expected_path = script_dir / "expected_state.json"
    if not expected_path.is_file():
        expected_path = Path("/opt/rolebench/task/verifier-private/expected_state.json")
    if not expected_path.is_file():
        return emit("rejected", 0, bound)

    try:
        expected_state = json.loads(expected_path.read_text(encoding="utf-8"))
    except Exception:
        return emit("rejected", 0, bound)

    if not isinstance(expected_state, dict):
        return emit("rejected", 0, bound)

    raw_magic_hex = expected_state.get("observed_raw_magic_hex")
    valid_magic_hex = expected_state.get("valid_wal_magic_hex")
    if not isinstance(raw_magic_hex, str) or not isinstance(valid_magic_hex, str):
        return emit("rejected", 0, bound)
    try:
        obs_bytes = bytes.fromhex(raw_magic_hex)
    except ValueError:
        return emit("rejected", 0, bound)

    # 1. Verify and recompute each hypothesis triple (kind, param, computed_output)
    hypotheses = sub.get("hypothesis_testing")
    if not isinstance(hypotheses, list) or len(hypotheses) < 2 or len(hypotheses) > 10:
        return emit("rejected", 0, bound)

    seen_pairs: set[tuple[str, int]] = set()
    valid_triples: set[tuple[str, int, str]] = set()
    falsified_triples: set[tuple[str, int, str]] = set()

    for hyp in hypotheses:
        if not isinstance(hyp, dict):
            return emit("rejected", 0, bound)
        t_kind = hyp.get("transform_kind")
        param = hyp.get("parameter_int")
        wal_valid = hyp.get("wal_magic_valid")
        res_hex = hyp.get("resulting_magic_hex")

        if not isinstance(t_kind, str) or not isinstance(param, int) or not isinstance(res_hex, str) or not isinstance(wal_valid, bool):
            return emit("rejected", 0, bound)

        pair = (t_kind, param)
        if pair in seen_pairs:
            return emit("rejected", 0, bound)
        seen_pairs.add(pair)

        try:
            computed_hex = _compute_transform(obs_bytes, t_kind, param)
        except Exception:
            return emit("rejected", 0, bound)

        if res_hex.lower() != computed_hex.lower():
            return emit("rejected", 0, bound)

        is_magic = (computed_hex.lower() == valid_magic_hex.lower())
        if wal_valid != is_magic:
            return emit("rejected", 0, bound)

        triple = (t_kind, param, computed_hex.lower())
        if is_magic:
            valid_triples.add(triple)
        else:
            falsified_triples.add(triple)

    # Require at least one valid transform triple and at least one genuinely distinct falsified alternative triple
    if not valid_triples or not falsified_triples:
        return emit("rejected", 0, bound)

    # 2. Verify selected transform and exact membership in valid_triples
    canonical = expected_state.get("canonical_solution", {})
    sub_sel = sub.get("selected_transform")
    if not isinstance(sub_sel, dict):
        return emit("rejected", 0, bound)

    sel_kind = sub_sel.get("transform_kind")
    sel_param = sub_sel.get("parameter_int")
    sel_target = sub_sel.get("target_magic_hex")
    if not isinstance(sel_kind, str) or not isinstance(sel_param, int) or not isinstance(sel_target, str):
        return emit("rejected", 0, bound)

    sel_triple = (sel_kind, sel_param, sel_target.lower())
    if sel_triple not in valid_triples:
        return emit("rejected", 0, bound)

    if (
        sel_kind != canonical.get("transform_kind")
        or sel_param != canonical.get("parameter_int")
        or sel_target.lower() != valid_magic_hex.lower()
        or sel_target.lower() != canonical.get("target_magic_hex", "").lower()
    ):
        return emit("rejected", 0, bound)

    # 3. Verify diagnosis cross-agreement
    sub_diag = sub.get("diagnosis")
    if not isinstance(sub_diag, dict):
        return emit("rejected", 0, bound)
    if (
        sub_diag.get("sidecar_file") != "main.db-wal"
        or sub_diag.get("observed_magic_hex", "").lower() != raw_magic_hex.lower()
        or sub_diag.get("expected_magic_hex", "").lower() != sel_target.lower()
        or sub_diag.get("corruption_mechanism") != canonical.get("corruption_mechanism")
    ):
        return emit("rejected", 0, bound)

    # 4. Verify recovery verification
    exp_rec_ver = expected_state.get("recovery_verification", {})
    sub_rec_ver = sub.get("recovery_verification")
    if not isinstance(sub_rec_ver, dict):
        return emit("rejected", 0, bound)
    if (
        sub_rec_ver.get("base_record_count") != exp_rec_ver.get("base_record_count")
        or sub_rec_ver.get("recovered_record_count") != exp_rec_ver.get("recovered_record_count")
        or sub_rec_ver.get("checkpoint_applied") != exp_rec_ver.get("checkpoint_applied")
    ):
        return emit("rejected", 0, bound)

    # 5. Verify exact recovered records
    exp_records = expected_state.get("recovered_records")
    sub_records = sub.get("recovered_records")
    if sub_records != exp_records:
        return emit("rejected", 0, bound)

    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
