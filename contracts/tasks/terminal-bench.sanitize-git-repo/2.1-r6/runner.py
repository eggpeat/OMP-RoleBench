#!/usr/bin/env python3
"""Deterministic, unprivileged runner for sanitize-git-repo patch application."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

MAX_ARTIFACT_BYTES: int = 1024 * 1024  # 1 MiB limit for unified diff patch
MAX_PATCH_PATH_BYTES: int = 1024
MAX_PATCH_PATH_COMPONENT_BYTES: int = 255
HUNK_HEADER_RE: re.Pattern[str] = re.compile(
    r"^@@ -([0-9]{1,9})(?:,([0-9]{1,9}))? "
    r"\+([0-9]{1,9})(?:,([0-9]{1,9}))? @@"
)
WORKSPACE_ROOT: Path = Path("/workspace")
DEFAULT_SOURCE: Path = Path("/opt/rolebench/task/public/workspace")


def _init_workspace(workspace_dir: Path, source_dir: Path) -> None:
    """Initialize workspace directory from source template if empty or missing."""
    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
    dclm_target = workspace_dir / "dclm"
    if not dclm_target.exists():
        dclm_src = source_dir / "dclm"
        if dclm_src.is_dir():
            shutil.copytree(dclm_src, dclm_target)


def _collect_snapshot(workspace_dir: Path) -> list[dict[str, object]]:
    """Deterministically scan workspace regular files in lexicographical path order."""
    entries: list[dict[str, object]] = []
    if not workspace_dir.exists():
        return entries

    found_paths: list[Path] = []
    for root, _, files in os.walk(workspace_dir):
        root_path = Path(root)
        for f in files:
            p = root_path / f
            try:
                st = p.lstat()
                if stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                    found_paths.append(p)
            except OSError:
                continue

    # Sort paths deterministically by UTF-8 encoded relative path
    rel_entries: list[tuple[str, Path]] = []
    for p in found_paths:
        try:
            rel = p.relative_to(workspace_dir).as_posix()
            rel_entries.append((rel, p))
        except ValueError:
            continue

    rel_entries.sort(key=lambda item: item[0].encode("utf-8"))

    for rel_posix, p in rel_entries:
        try:
            data = p.read_bytes()
            mode_oct = format(stat.S_IFREG | (p.stat().st_mode & 0o7777), "06o")
            entries.append({
                "path": rel_posix,
                "mode": mode_oct,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode("ascii"),
            })
        except OSError:
            continue

    return entries


def _clean_diff_path(raw: str) -> str:
    p = raw.strip()
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p


def _apply_patch(patch_text: str, workspace_dir: Path) -> tuple[bool, str | None]:
    """Parse and apply unified diff strictly within workspace_dir."""
    lines = patch_text.splitlines(keepends=True)
    if not lines or not any(l.strip() for l in lines):
        return True, None

    file_patches: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if line.startswith("--- "):
            if cur:
                file_patches.append(cur)
                cur = []
        cur.append(line)
    if cur:
        file_patches.append(cur)

    staged_changes: dict[Path, str] = {}
    resolved_workspace = workspace_dir.resolve()

    for fpatch in file_patches:
        if len(fpatch) < 2:
            return False, "truncated file patch header"
        old_hdr = fpatch[0]
        new_hdr = fpatch[1]
        if not old_hdr.startswith("--- ") or not new_hdr.startswith("+++ "):
            return False, "invalid file patch header"

        old_raw = old_hdr[4:].strip().split("\t")[0]
        new_raw = new_hdr[4:].strip().split("\t")[0]

        old_rel = _clean_diff_path(old_raw)
        new_rel = _clean_diff_path(new_raw)
        if old_rel == "/dev/null" or new_rel == "/dev/null":
            return False, "file creation and deletion are not permitted"
        if old_rel != new_rel:
            return False, "old and new patch paths do not match"
        target_rel = new_rel

        # Preflight path traversal and special characters
        if "\0" in target_rel:
            return False, "NUL byte in patch path"
        if target_rel.startswith("/") or target_rel.startswith("\\"):
            return False, "absolute path in patch"
        try:
            target_bytes = target_rel.encode("utf-8")
        except UnicodeEncodeError:
            return False, "patch path is not valid UTF-8"
        if not target_bytes or len(target_bytes) > MAX_PATCH_PATH_BYTES:
            return False, "patch path length is invalid"

        parts = Path(target_rel).parts
        if any(part in ("..", ".", "") for part in parts):
            return False, "invalid traversal component in patch path"
        if any(
            len(part.encode("utf-8")) > MAX_PATCH_PATH_COMPONENT_BYTES
            for part in parts
        ):
            return False, "patch path component is too long"

        # Must resolve within workspace_dir
        try:
            target_path = (workspace_dir / target_rel).resolve()
        except (OSError, RuntimeError):
            return False, "patch path cannot be resolved"
        try:
            target_path.relative_to(resolved_workspace)
        except ValueError:
            return False, "target path escapes workspace"
        if target_path in staged_changes:
            return False, f"duplicate target file patch: {target_rel}"

        # Ensure no symlinks along the path
        check_p = resolved_workspace
        for part in parts:
            check_p = check_p / part
            if check_p.is_symlink():
                return False, f"symlink encountered in target path: {part}"
            if check_p.exists() and not (check_p.is_file() or check_p.is_dir()):
                return False, f"special file in path: {part}"

        if not target_path.is_file():
            return False, f"target is not an existing regular file: {target_rel}"

        try:
            orig_text = target_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False, f"target file is not UTF-8: {target_rel}"
        except OSError:
            return False, f"target file cannot be read: {target_rel}"

        orig_lines = orig_text.splitlines(keepends=True)

        hunks = []
        cur_hunk = None

        for line in fpatch[2:]:
            if line.startswith("@@ "):
                m = HUNK_HEADER_RE.match(line)
                if not m:
                    return False, f"malformed hunk header: {line.strip()}"
                cur_hunk = {
                    "old_start": int(m.group(1)),
                    "old_count": int(m.group(2)) if m.group(2) is not None else 1,
                    "new_start": int(m.group(3)),
                    "new_count": int(m.group(4)) if m.group(4) is not None else 1,
                    "lines": [],
                }
                hunks.append(cur_hunk)
            elif cur_hunk is not None:
                cur_hunk["lines"].append(line)
            else:
                return False, "unexpected content before first hunk"

        if not hunks:
            return False, "no hunks found in patch"

        new_file_lines: list[str] = []
        orig_idx = 0
        old_side_closed = False
        new_side_closed = False

        for hunk in hunks:
            old_start = hunk["old_start"]
            old_count = hunk["old_count"]
            new_start = hunk["new_start"]
            new_count = hunk["new_count"]
            if (old_count > 0 and old_start == 0) or (
                new_count > 0 and new_start == 0
            ):
                return False, "non-empty hunk range must start at line one or later"

            h_start = old_start if old_count == 0 else old_start - 1
            new_h_start = new_start if new_count == 0 else new_start - 1
            if h_start < orig_idx:
                return False, "overlapping or out-of-order hunks"
            if h_start > len(orig_lines):
                return False, "hunk starts past end of target file"

            if h_start > orig_idx and (old_side_closed or new_side_closed):
                return False, "file content follows no-newline terminal record"
            new_file_lines.extend(orig_lines[orig_idx:h_start])
            orig_idx = h_start
            if new_h_start != len(new_file_lines):
                return False, "new-file hunk position does not match prior hunks"
            hunk_lines: list[str] = []
            for hline in hunk["lines"]:
                if not hline:
                    return False, "empty line in hunk"
                if not hline.startswith("\\"):
                    tag = hline[0]
                    if tag in " +-":
                        if old_side_closed and tag in " -":
                            return False, "old-file record follows no-newline terminal record"
                        if new_side_closed and tag in " +":
                            return False, "new-file record follows no-newline terminal record"
                        if not hline.endswith(("\n", "\r")):
                            return False, "unmarked hunk record lacks line terminator"
                    hunk_lines.append(hline)
                    continue
                if hline.rstrip("\r\n") != "\\ No newline at end of file":
                    return False, f"malformed no-newline marker: {hline.strip()}"
                if not hunk_lines or hunk_lines[-1][0] not in " +-":
                    return False, "misplaced no-newline marker"
                previous = hunk_lines[-1]
                if previous.endswith("\r\n"):
                    hunk_lines[-1] = previous[:-2]
                elif previous.endswith(("\n", "\r")):
                    hunk_lines[-1] = previous[:-1]
                else:
                    return False, "duplicate or spurious no-newline marker"
                previous_tag = previous[0]
                if previous_tag in " -":
                    old_side_closed = True
                if previous_tag in " +":
                    new_side_closed = True


            old_consumed = 0
            new_emitted = 0
            for hline in hunk_lines:
                if not hline:
                    return False, "empty line in hunk"
                tag = hline[0]
                text = hline[1:]
                if tag == " ":
                    if orig_idx >= len(orig_lines) or orig_lines[orig_idx] != text:
                        return False, f"context mismatch at line {orig_idx + 1}"
                    new_file_lines.append(text)
                    orig_idx += 1
                    old_consumed += 1
                    new_emitted += 1
                elif tag == "-":
                    if orig_idx >= len(orig_lines) or orig_lines[orig_idx] != text:
                        return False, f"deletion mismatch at line {orig_idx + 1}"
                    orig_idx += 1
                    old_consumed += 1
                elif tag == "+":
                    new_file_lines.append(text)
                    new_emitted += 1
                else:
                    return False, f"unexpected line prefix in hunk: {tag!r}"

            if old_consumed != old_count or new_emitted != new_count:
                return False, (
                    "hunk line counts do not match header "
                    f"(expected -{old_count}/+{new_count}, "
                    f"observed -{old_consumed}/+{new_emitted})"
                )

        if orig_idx < len(orig_lines) and (old_side_closed or new_side_closed):
            return False, "file content follows no-newline terminal record"
        new_file_lines.extend(orig_lines[orig_idx:])
        staged_changes[target_path] = "".join(new_file_lines)

    # Apply all changes atomically
    for target_path, content in staged_changes.items():
        target_path.write_text(content, encoding="utf-8")

    return True, None


def main() -> int:
    workspace_dir = WORKSPACE_ROOT if WORKSPACE_ROOT.is_dir() else Path.cwd() / "workspace"
    script_dir = Path(__file__).resolve().parent
    if DEFAULT_SOURCE.is_dir():
        source_dir = DEFAULT_SOURCE
    elif (script_dir / "public/workspace").is_dir():
        source_dir = script_dir / "public/workspace"
    else:
        source_dir = Path.cwd() / "public/workspace"

    _init_workspace(workspace_dir, source_dir)

    payload = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(payload) > MAX_ARTIFACT_BYTES:
        snapshot = {
            "schema_version": "rolebench.runner-snapshot/v1",
            "status": "rejected",
            "error": "artifact exceeds maximum allowed patch size (1 MiB)",
            "workspace": "dclm",
            "files": _collect_snapshot(workspace_dir),
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    if not payload:
        # Empty payload = baseline (no changes applied)
        snapshot = {
            "schema_version": "rolebench.runner-snapshot/v1",
            "status": "applied",
            "error": None,
            "workspace": "dclm",
            "files": _collect_snapshot(workspace_dir),
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    try:
        patch_text = payload.decode("utf-8")
    except UnicodeDecodeError:
        snapshot = {
            "schema_version": "rolebench.runner-snapshot/v1",
            "status": "rejected",
            "error": "patch payload is not valid UTF-8",
            "workspace": "dclm",
            "files": _collect_snapshot(workspace_dir),
        }
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    applied, err_msg = _apply_patch(patch_text, workspace_dir)
    status = "applied" if applied else "rejected"
    snapshot = {
        "schema_version": "rolebench.runner-snapshot/v1",
        "status": status,
        "error": err_msg,
        "workspace": "dclm",
        "files": _collect_snapshot(workspace_dir),
    }
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
