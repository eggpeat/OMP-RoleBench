#!/usr/bin/env python3
"""Passive exact-result verifier for the diff-to-commit task."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import sys
from typing import NoReturn

RUNNER_EVIDENCE_MAGIC = b"OMP-RUNNER-EVIDENCE-V1\n"
RUNNER_EVIDENCE_SCHEMA_VERSION = "omp.runner-evidence/v1"
VERIFIER_RESULT_SCHEMA_VERSION = "omp.verifier-result/v1"
OUTCOME_ACTIONS = frozenset(
    {
        "capture",
        "collect",
        "count",
        "emit",
        "expose",
        "increment",
        "log",
        "measure",
        "observe",
        "persist",
        "publish",
        "record",
        "report",
        "store",
        "track",
    }
)
INVALIDATION_WORD = r"\binvalidat(?:e|es|ed|ing|ion|ions)\b"
DELETION_WORD = r"\bdelet(?:e|es|ed|ing|ion|ions)\b"
L1_INVALIDATION = (
    rf"(?:"
    rf"{INVALIDATION_WORD}\s+(?:the\s+)?\bl1\b"
    rf"(?:\s+(?:cache(?:\s+entry)?|entry))?"
    rf"|"
    rf"\bl1\b(?:\s+(?:cache(?:\s+entry)?|entry))?"
    rf"\s+{INVALIDATION_WORD}"
    rf")"
)
BACKING_STORE = r"\bbacking(?:[\s-]+)store\b"
BACKING_STORE_DELETION = (
    rf"(?:"
    rf"{DELETION_WORD}\s+"
    rf"(?:(?:the\s+)?(?:(?:cache\s+)?(?:entry|item|record|value)|cache)"
    rf"\s+(?:from|of)\s+)?"
    rf"(?:(?:from|of)\s+)?(?:the\s+)?{BACKING_STORE}"
    rf"(?:\s+(?:entry|item|record|value))?"
    rf"|"
    rf"{BACKING_STORE}"
    rf"(?:\s+(?:(?:cache\s+)?(?:entry|item|record|value)|cache))?"
    rf"\s+{DELETION_WORD}"
    rf")"
)
ORDER_MODIFIER = r"(?:(?:a|the)\s+)?(?:cache\s+)?lookup"
PURPOSE_SUFFIX = (
    r"(?:\s+(?:"
    r"to\s+(?:"
    r"prevent\s+stale\s+reads"
    r"|preserve\s+cache\s+consistency"
    r"|avoid\s+stale\s+data"
    r")"
    r"|so\s+that\s+cache\s+state\s+remains\s+consistent"
    r"|so\s+as\s+to\s+preserve\s+cache\s+consistency"
    r"|rather\s+than\s+leave\s+stale\s+data"
    r"))?"
)
ORDERED_CLAIM = re.compile(
    rf"(?:"
    rf"{L1_INVALIDATION}\s+(?:before|prior\s+to|ahead\s+of)\s+"
    rf"{BACKING_STORE_DELETION}"
    rf"|"
    rf"{L1_INVALIDATION}\s*,?\s+then\s+{BACKING_STORE_DELETION}"
    rf"|"
    rf"{L1_INVALIDATION}\s+after\s+{ORDER_MODIFIER}\s*,\s+then\s+"
    rf"{BACKING_STORE_DELETION}"
    rf"|"
    rf"{BACKING_STORE_DELETION}\s+(?:after|following)\s+{L1_INVALIDATION}"
    rf"|"
    rf"(?:before|prior\s+to|ahead\s+of)\s+{BACKING_STORE_DELETION}"
    rf"\s*,\s*{L1_INVALIDATION}"
    rf"|"
    rf"(?:after|following)\s+{L1_INVALIDATION}\s*,\s*"
    rf"{BACKING_STORE_DELETION}"
    rf"|"
    rf"{L1_INVALIDATION}\s+preced(?:e|es|ing)\s+"
    rf"{BACKING_STORE_DELETION}"
    rf"|"
    rf"{BACKING_STORE_DELETION}\s+follow(?:s|ing)\s+{L1_INVALIDATION}"
    rf"|"
    rf"{L1_INVALIDATION}\s+(?:is|was|will\s+be)\s+followed\s+by\s+"
    rf"{BACKING_STORE_DELETION}"
    rf"|"
    rf"{BACKING_STORE_DELETION}\s+(?:is|was|will\s+be)\s+preceded\s+by\s+"
    rf"{L1_INVALIDATION}"
    rf")"
    rf"{PURPOSE_SUFFIX}"
)
OUTCOME_CLAIM = re.compile(
    rf"(?:"
    rf"(?:{'|'.join(sorted(OUTCOME_ACTIONS))}) "
    rf"hit and miss outcomes for cache deletions"
    rf"|"
    rf"increment cache deletion metrics with hit and miss outcomes"
    rf")"
)
MAX_CLAIM_CHARS = 512
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
SNAPSHOT_KEYS = {'status', 'submission', 'error', 'schema_version', 'patch_sha256'}


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


def _normalize_claim(text: str, *, require_period: bool) -> str | None:
    if text != text.strip():
        return None
    if require_period:
        if not text.endswith(".") or text.endswith(".."):
            return None
        text = text[:-1]
    elif text.endswith((".", "!", "?")):
        return None
    if not text:
        return None
    return text.casefold()


def _states_invalidation_before_deletion(
    text: str,
    *,
    require_imperative: bool = False,
) -> bool:
    normalized = _normalize_claim(text, require_period=not require_imperative)
    return (
        normalized is not None
        and (
            not require_imperative
            or (
                text == text.casefold()
                and re.match(r"(?:delete|invalidate)\b", normalized) is not None
            )
        )
        and ORDERED_CLAIM.fullmatch(normalized) is not None
    )


def _states_outcome_recording(text: str) -> bool:
    normalized = _normalize_claim(text, require_period=True)
    return normalized is not None and OUTCOME_CLAIM.fullmatch(normalized) is not None


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


def _valid_submission(submission: object) -> bool:
    if not isinstance(submission, dict):
        return False
    subject = submission.get("subject")
    body = submission.get("body")
    if (
        set(submission)
        != {
            "schema_version",
            "type",
            "scope",
            "subject",
            "body",
            "evidence",
            "breaking",
        }
        or submission.get("schema_version") != "rolebench.commit-message/v1"
        or submission.get("type") != "fix"
        or submission.get("scope") != "cache"
        or submission.get("evidence")
        != ["change.patch:7-9", "change.patch:10-11"]
        or submission.get("breaking") is not False
        or not isinstance(subject, str)
        or not isinstance(body, list)
        or len(body) != 2
        or any(not isinstance(sentence, str) for sentence in body)
        or len(subject) > 72
        or any(len(sentence) > MAX_CLAIM_CHARS for sentence in body)
    ):
        return False
    texts = (subject, *body)
    return (
        not any(
            re.search(r"\btest[a-z]*\b", text.casefold()) is not None
            for text in texts
        )
        and _states_invalidation_before_deletion(
            subject,
            require_imperative=True,
        )
        and _states_invalidation_before_deletion(body[0])
        and _states_outcome_recording(body[1])
    )


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
    try:
        snapshot = json.loads(
            stdout_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, EvidenceError):
        return reject()
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != SNAPSHOT_KEYS
        or stdout_bytes != json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        or snapshot.get("schema_version") != 'rolebench.commit-message-runner-snapshot/v1'
        or snapshot.get("status") != "executed"
        or snapshot.get("error") is not None
    ):
        return reject()
    if snapshot.get("patch_sha256") != "4a667d7a5dc3ee32ba5fb31cbf4b54922bd504e51e4a88cb83fdcbb830c3a635":
        return reject()
    if not _valid_submission(snapshot.get("submission")):
        return reject()
    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
