#!/usr/bin/env python3
"""Deterministic, unprivileged runner for sanitize-git-repo patch application."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


MAX_ARTIFACT_BYTES: int = 1024 * 1024  # 1 MiB limit for unified diff patch
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


def _apply_patch(patch_text: str, workspace_dir: Path) -> tuple[bool, str | None]:
    """Parse and apply unified diff within workspace_dir using git apply or tolerant patch."""
    if not patch_text or not any(line.strip() for line in patch_text.splitlines()):
        return True, None

    patch_bytes = patch_text.encode("utf-8")
    resolved_workspace = workspace_dir.resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        staging_dir = Path(tmpdir) / "workspace"
        shutil.copytree(resolved_workspace, staging_dir, symlinks=False)

        strategies = [
            ["git", "apply", "--whitespace=nowarn", "-p1", "-"],
            ["git", "apply", "--whitespace=nowarn", "-p0", "-"],
            ["git", "apply", "--whitespace=nowarn", "--recount", "--unidiff-zero", "-p1", "-"],
            ["git", "apply", "--whitespace=nowarn", "--recount", "--unidiff-zero", "-p0", "-"],
        ]

        applied = False
        last_error = "patch did not apply"

        # 1. Try applying full patch with git apply
        for cmd in strategies:
            res = subprocess.run(
                cmd,
                input=patch_bytes,
                cwd=str(staging_dir),
                capture_output=True,
            )
            if res.returncode == 0:
                applied = True
                break
            err = res.stderr.decode("utf-8", errors="replace").strip()
            if err:
                last_error = err

        # 2. Try applying per-file patches if full patch failed
        if not applied:
            lines = patch_text.splitlines(keepends=True)
            in_git_diff = any(l.startswith("diff --git ") for l in lines)
            split_prefix = "diff --git " if in_git_diff else "--- "

            file_patches = []
            cur: list[str] = []
            for line in lines:
                if line.startswith(split_prefix):
                    if cur:
                        file_patches.append("".join(cur))
                        cur = []
                cur.append(line)
            if cur:
                file_patches.append("".join(cur))

            if len(file_patches) > 1:
                all_ok = True
                for fp in file_patches:
                    fp_applied = False
                    for cmd in strategies:
                        res = subprocess.run(
                            cmd,
                            input=fp.encode("utf-8"),
                            cwd=str(staging_dir),
                            capture_output=True,
                        )
                        if res.returncode == 0:
                            fp_applied = True
                            break
                    if not fp_applied:
                        all_ok = False
                        break
                if all_ok:
                    applied = True

        # 3. Try patch utility if git apply did not succeed
        if not applied:
            for p_num in ["1", "0"]:
                res = subprocess.run(
                    ["patch", f"-p{p_num}", "--fuzz=0", "--batch", "--silent", "-i", "-"],
                    input=patch_bytes,
                    cwd=str(staging_dir),
                    capture_output=True,
                )
                if res.returncode == 0:
                    applied = True
                    break
        if not applied:
            return False, last_error

        # Atomic cutover to workspace_dir
        for item in resolved_workspace.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in staging_dir.iterdir():
            if item.is_dir():
                shutil.copytree(item, resolved_workspace / item.name, symlinks=False)
            else:
                shutil.copy2(item, resolved_workspace / item.name)
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
