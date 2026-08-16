#!/usr/bin/env python3
"""Passive rendered-layout verifier for the responsive UI task."""

from __future__ import annotations

import hashlib
import json
import math
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
SNAPSHOT_KEYS = {'status', 'desktop', 'source_checks', 'mobile', 'error', 'schema_version', 'brief_sha256'}


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

def _is_contrast_ratio(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 1 <= value <= 21
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
        or snapshot.get("schema_version") != 'rolebench.ui-runner-snapshot/v1'
        or snapshot.get("status") != "executed"
        or snapshot.get("error") is not None
    ):
        return reject()
    if snapshot.get("brief_sha256") != "122f25e21766044c558bc08a9a586a0b1ec12cc860a340c1b974cbc3d609e654":
        return reject()
    checks = snapshot.get("source_checks")
    if not isinstance(checks, dict) or set(checks) != {
        "semantic_structure",
        "stylesheet_linked",
        "active_navigation_labelled",
        "mobile_menu_labelled",
        "audit_hooks_present",
        "brief_content_complete",
        "responsive_rule_present",
        "focus_visible_rule_present",
    } or any(value is not True for value in checks.values()):
        return reject()
    surface_keys = {
        "viewport_width",
        "horizontal_overflow",
        "sidebar_visible",
        "sidebar_left_of_grid",
        "mobile_menu_visible",
        "mobile_menu_button",
        "mobile_menu_focusable",
        "mobile_menu_focus_indicator",
        "cards_horizontally_in_view",
        "grid_columns",
        "primary_width",
        "primary_height",
        "primary_contrast",
        "minimum_text_contrast",
        "brief_content_rendered",
        "body_contrast",
        "solid_text_backgrounds",
        "text_contrast_aa",
        "focus_indicator",
        "disclosure_visible_after_open",
        "navigation_rendered",
        "active_navigation_current",
        "screenshot_bytes",
        "screenshot_sha256",
    }
    desktop = snapshot.get("desktop")
    mobile = snapshot.get("mobile")
    for surface in (desktop, mobile):
        if not isinstance(surface, dict) or set(surface) != surface_keys:
            return reject()
        if (
            surface.get("horizontal_overflow") is not False
            or isinstance(surface.get("grid_columns"), bool)
            or not isinstance(surface.get("grid_columns"), int)
            or not isinstance(surface.get("primary_width"), (int, float))
            or not isinstance(surface.get("primary_height"), (int, float))
            or surface.get("primary_width") < 44
            or surface.get("primary_height") < 44
            or not all(
                _is_contrast_ratio(surface.get(key))
                for key in (
                    "primary_contrast",
                    "body_contrast",
                    "minimum_text_contrast",
                )
            )
            or any(
                surface.get(key) is not True
                for key in (
                    "brief_content_rendered",
                    "focus_indicator",
                    "disclosure_visible_after_open",
                    "navigation_rendered",
                    "active_navigation_current",
                    "mobile_menu_button",
                    "solid_text_backgrounds",
                    "cards_horizontally_in_view",
                    "text_contrast_aa",
                )
            )
            or isinstance(surface.get("screenshot_bytes"), bool)
            or not isinstance(surface.get("screenshot_bytes"), int)
            or surface.get("screenshot_bytes") < 4096
            or not _is_sha256(surface.get("screenshot_sha256"))
        ):
            return reject()
    if (
        desktop.get("viewport_width") != 1280
        or desktop.get("sidebar_visible") is not True
        or desktop.get("sidebar_left_of_grid") is not True
        or desktop.get("mobile_menu_visible") is not False
        or desktop.get("mobile_menu_focus_indicator") is not False
        or desktop.get("grid_columns") != 3
        or mobile.get("viewport_width") != 390
        or mobile.get("sidebar_visible") is not False
        or mobile.get("mobile_menu_visible") is not True
        or mobile.get("mobile_menu_focusable") is not True
        or mobile.get("mobile_menu_focus_indicator") is not True
        or mobile.get("grid_columns") != 1
    ):
        return reject()
    return emit("accepted", 1, bound)


if __name__ == "__main__":
    raise SystemExit(main())
