#!/usr/bin/env python3
"""Bounded headless-Vim runner for the large-scale text-editing anchor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import NoReturn

MAX_ARTIFACT_BYTES = 8 * 1024
MAX_SCRIPT_BYTES = 4 * 1024
ROWS = 2048
SCHEMA_VERSION = "rolebench.vim-macro-submission/v1"
SNAPSHOT_VERSION = "rolebench.vim-macro-runner-snapshot/v1"
SETREG = re.compile(r"^call setreg\('([abc])',\s*\"((?:[^\"\\]|\\.)*)\"\)$")
RUN = re.compile(r"^%normal! @([abc])$")
FORBIDDEN = re.compile(
    r"(?i)(?:^|[^a-z])(?:system|execute|source|read|write|edit|function|autocmd|terminal|python|perl|ruby|lua|job_start|channel|sockconnect)(?:[^a-z]|$)|:!|`|\x00"
)


class SubmissionError(ValueError):
    """Malformed or unsafe candidate artifact."""


def _reject_constant(value: str) -> NoReturn:
    raise SubmissionError(f"non-finite JSON number {value!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SubmissionError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _decode_vim_string(value: str) -> str:
    """Decode only the escapes needed to count literal Vim macro keystrokes."""
    output: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\":
            output.append(value[index])
            index += 1
            continue
        if value.startswith("\\<", index):
            end = value.find(">", index + 2)
            if end < 0:
                raise SubmissionError("unterminated Vim key notation")
            output.append("\x01")
            index = end + 1
            continue
        if value.startswith("\\\\", index) or value.startswith('\\"', index):
            output.append(value[index + 1])
            index += 2
            continue
        raise SubmissionError("unsupported escape in macro")
    return "".join(output)


def _validate_script(script: object) -> tuple[str, int]:
    if not isinstance(script, str):
        raise SubmissionError("script must be a string")
    encoded = script.encode("utf-8")
    if not encoded or len(encoded) > MAX_SCRIPT_BYTES:
        raise SubmissionError("script must contain 1 to 4096 UTF-8 bytes")
    if "\r" in script or FORBIDDEN.search(script):
        raise SubmissionError("script contains a forbidden command or character")

    lines = [line.strip() for line in script.splitlines() if line.strip()]
    if len(lines) != 7 or lines[-1] != "wq":
        raise SubmissionError("script must contain three macro definitions, three runs, then wq")

    macros: dict[str, str] = {}
    runs: list[str] = []
    for line in lines[:-1]:
        match = SETREG.fullmatch(line)
        if match:
            register, content = match.groups()
            if register in macros:
                raise SubmissionError("each macro register must be defined once")
            decoded = _decode_vim_string(content)
            if not decoded:
                raise SubmissionError("macro content must be non-empty")
            macros[register] = decoded
            continue
        match = RUN.fullmatch(line)
        if match:
            runs.append(match.group(1))
            continue
        raise SubmissionError("script contains a command outside the allowlist")

    if set(macros) != {"a", "b", "c"} or runs != ["a", "b", "c"]:
        raise SubmissionError("registers a, b, and c must each be defined and run in order")
    if len(set(macros.values())) != 3:
        raise SubmissionError("macros a, b, and c must be distinct")
    total = sum(len(value) for value in macros.values())
    if total >= 200:
        raise SubmissionError("macro keystroke total must be below 200")
    return "\n".join(lines) + "\n", total


def _row(index: int) -> tuple[str, str]:
    first = f"item{index:04d}"
    second = f"group{(index * 17) % 997:03d}"
    third = f"zone{(index * 29) % 101:03d}"
    pads = ("", " ", "  ", "   ")
    raw = (
        f"{pads[index % 4]}{first}{pads[(index + 1) % 4]},"
        f"{pads[(index + 2) % 4]}{second}{pads[(index + 3) % 4]},"
        f"{pads[(index + 1) % 4]}{third}{pads[index % 4]}"
    )
    expected = f"{third.upper()};{second.upper()};{first.upper()};OK"
    return raw, expected


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _snapshot(*, status: str, error: str | None, metrics: dict[str, object] | None) -> str:
    return json.dumps(
        {
            "schema_version": SNAPSHOT_VERSION,
            "status": status,
            "error": error,
            "metrics": metrics,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        sys.stdout.write(_snapshot(status="rejected", error="artifact exceeds 8 KiB", metrics=None))
        return 0
    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(parsed, dict) or set(parsed) != {"schema_version", "script"}:
            raise SubmissionError("submission must contain exactly schema_version and script")
        if parsed.get("schema_version") != SCHEMA_VERSION:
            raise SubmissionError("unsupported submission schema")
        script, total = _validate_script(parsed.get("script"))
    except (UnicodeDecodeError, json.JSONDecodeError, SubmissionError, TypeError, RecursionError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=str(error), metrics=None))
        return 0

    try:
        work = Path("/workspace")
        rows = [_row(index) for index in range(ROWS)]
        source = work / "input.csv"
        source.write_text(
            "\n".join(raw for raw, _expected in rows) + "\n",
            encoding="utf-8",
        )
        expected_digest = hashlib.sha256(
            ("\n".join(expected for _raw, expected in rows) + "\n").encode("utf-8")
        ).hexdigest()
        script_path = work / "script.vim"
        script_path.write_text(script + "\n", encoding="utf-8")

        result = subprocess.run(
            [
                "/usr/bin/vim",
                "-Z",
                "-Nu",
                "NONE",
                "-n",
                "-es",
                "-S",
                str(script_path),
                str(source),
            ],
            cwd=work,
            env={
                "HOME": str(work),
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin",
                "SHELL": "/bin/false",
            },
            check=False,
            capture_output=True,
            timeout=30,
        )
        output_digest = _digest(source)
        metrics = {
            "distinct_macros": True,
            "macro_keystrokes": total,
            "rows": ROWS,
            "vim_exit_code": result.returncode,
            "output_sha256": output_digest,
            "expected_sha256": expected_digest,
            "transformation_matches": (
                result.returncode == 0 and output_digest == expected_digest
            ),
        }
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        sys.stdout.write(_snapshot(status="rejected", error=f"Vim execution failed: {error}", metrics=None))
        return 0

    sys.stdout.write(_snapshot(status="executed", error=None, metrics=metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
