#!/usr/bin/env python3
"""Live multi-model benchmark evaluation runner for OMP RoleBench.

Dispatches actual task prompts to candidate model provider APIs, then scores
the returned artifact through Docker runner/verifier images. Stub tautology
tasks are excluded. Infra timeouts are not scored as quality 0.00.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rolebench.contracts import discover_root
from rolebench.ledger import append_worker_report
from rolebench.task_workflow import prepare_worker_manifest
from rolebench.worker import run_worker


STUB_TASK_IDS = frozenset(
    {
        "omp-native.api-contract-test-modernization",
        "omp-native.architecture-dependency-audit",
        "omp-native.deprecation-annotation-pass",
        "omp-native.distributed-lease-deadlock",
        "omp-native.event-stream-multiplexer",
        "omp-native.feature-matrix-compiler",
        "omp-native.modernize-scientific-stack",
        "omp-native.pytest-fixture-migration",
        "omp-native.schema-enum-sync",
        "omp-native.sync-to-async-client-refactor",
        "omp-native.vulnerability-impact-trace",
    }
)
HOMEMADE_ENVELOPE_TASK_IDS = frozenset(
    {
        "omp-native.code-review-defect-recall",
        "omp-native.code-review-precision-control",
    }
)
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".db",
        ".wal",
        ".bin",
        ".wasm",
        ".sqlite",
        ".sqlite3",
    }
)
PROVIDER_TIMEOUTS = {
    "alibaba-token-plan": 900,
    "xai": 900,
    "xai-oauth": 900,
    "kimi-code": 900,
    "google-antigravity": 900,
    "deepseek": 600,
    "devin": 600,
    "zai": 900,
}
DEFAULT_PROVIDER_TIMEOUT = 900
DEFAULT_MAX_TOKENS = 16384
MAX_PROVIDER_ATTEMPTS = 2


def get_provider_timeout(provider: str | None) -> int:
    if provider is None:
        return DEFAULT_PROVIDER_TIMEOUT
    return PROVIDER_TIMEOUTS.get(provider, DEFAULT_PROVIDER_TIMEOUT)

def slugify(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in text)


@dataclass
class EvalTaskTarget:
    role: str
    routing_lane: str | None
    task_id: str
    task_path: Path
    qualification_path: Path
    is_private: bool


@dataclass
class LiveEvalResult:
    route_id: str
    task_id: str
    role: str
    routing_lane: str | None
    passed: bool
    quality_score: float | None
    generation_seconds: float
    verifier_seconds: float
    total_seconds: float
    raw_response_snippet: str
    error: str | None
    report_path: Path | None
    outcome_class: str

def _omp_selector(route: dict[str, Any]) -> str:
    """Canonical OMP model selector for a route (provider/model)."""
    return f"{route['provider']}/{route['model']}"


def _parse_omp_agent_end(jsonl_text: str) -> tuple[str, dict[str, Any]]:
    """Extract final assistant text and usage from OMP --mode json output.

    OMP streams JSONL events; the terminal `agent_end` (falling back to the last
    `message_end`/`turn_end`) carries the final assistant message with content
    blocks and usage. Returns (text, usage_dict)."""
    final_text = ""
    usage: dict[str, Any] = {}
    stop_reason = ""
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype not in ("agent_end", "message_end", "turn_end"):
            continue
        # agent_end carries messages[]; message_end/turn_end carry a single message.
        messages = evt.get("messages") or ([evt["message"]] if evt.get("message") else [])
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            texts = [
                c.get("text", "")
                for c in msg.get("content", [])
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
            ]
            if texts:
                final_text = "\n".join(texts)
            if isinstance(msg.get("usage"), dict):
                usage = msg["usage"]
            if msg.get("stopReason"):
                stop_reason = msg["stopReason"]
    usage["_stop_reason"] = stop_reason
    return final_text.strip(), usage


def call_live_model(
    route: dict[str, Any],
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.0,
    images: list[tuple[str, bytes]] | None = None,
    image_paths: list[str] | None = None,
) -> tuple[str, float]:
    """Dispatch one task prompt through OMP itself.

    RoleBench ranks routes FOR OMP ROLES, so the score must reflect the model
    as OMP drives it: OMP's agent loop, prompt framing, auth, protocol
    (including Codeium Cascade for devin), image handling, and retry. We invoke
    `omp --no-session --mode json` and parse the final assistant message.

    Images: OMP accepts image file paths on the prompt for multimodal routes;
    text-only routes get a disclosure note instead."""
    t0 = time.monotonic()
    selector = _omp_selector(route)
    thinking = route.get("thinking")
    image_paths = image_paths or []

    full_prompt = prompt
    if system_prompt:
        full_prompt = f"{system_prompt}\n\n{prompt}"

    if image_paths and not _route_supports_images(route):
        names = ", ".join(image_paths)
        full_prompt += (
            f"\\n\\n[NOTE: This task references image file(s) {names}, but this route is "
            "text-only and the image content was not provided. Respond per the contract "
            "schema as best you can.]"
        )
        image_paths = []

    cmd = [
        "omp", "--no-session", "--auto-approve",
        "--model", selector, "--mode", "json",
    ]
    if thinking and str(thinking).lower() not in ("off", "none", "auto"):
        cmd.extend(["--thinking", str(thinking)])
    cmd.append("--no-tools")
    # OMP attaches images as separate @path CLI args (multimodal routes only).
    for path in image_paths:
        cmd.append(f"@{path}")
    # Prompt goes via stdin ("-p -"): avoids argv length limits (bottle.py 192KB
    # prompt -> E2BIG) and embedded-null-byte errors on binary-derived content.
    cmd.extend(["-p", "-"])

    timeout = get_provider_timeout(str(route.get("provider", "")))
    try:
        proc = subprocess.run(
            cmd,
            input=full_prompt.encode("utf-8", errors="replace"),
            capture_output=True,
            timeout=timeout,
            cwd=str(discover_root(Path(__file__).resolve().parent)),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"omp dispatch timed out after {timeout}s") from exc

    stdout_text = proc.stdout.decode("utf-8", errors="replace")
    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"omp exited {proc.returncode}: {stderr_text.strip()[:300]}")

    text, _usage = _parse_omp_agent_end(stdout_text)
    return text, time.monotonic() - t0


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif"})


def _route_supports_images(route: dict[str, Any]) -> bool:
    """True only when the route explicitly declares image input.

    Reads the route's input_modalities (default text-only). This is the
    single source of truth for whether a vision task's image is injected,
    replacing the old provider-name heuristic that mis-served glm (sent
    images to a text-only model) and kimi/devin (withheld images from
    multimodal models)."""
    modalities = route.get("input_modalities")
    if isinstance(modalities, list):
        return "image" in modalities
    return False


def collect_task_images(task_dir: Path) -> list[str]:
    """Collect image asset absolute paths from the task public tree.

    OMP accepts image file paths for multimodal routes, so we hand paths (not
    bytes) to the dispatch."""
    images: list[str] = []
    for fpath in _collect_workspace_files(task_dir):
        if _matches_binary(fpath.name, IMAGE_EXTENSIONS) and fpath.stat().st_size <= 16 * 1024 * 1024:
            images.append(str(fpath.resolve()))
    return images


# Extensions whose raw bytes are useless/harmful as prompt text. These are
# base64-embedded instead (small ones) or injected as images (.png etc.).
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bin",
        ".wasm",
    }
)
# Binary-but-textual-analysis extensions: small enough to embed as base64 so the
# model can reason about them (e.g. SQLite db/wal forensics).
BASE64_EMBEDDABLE_EXTENSIONS = frozenset({".db", ".wal", ".sqlite", ".sqlite3", ".encrypted", ".db-wal", ".db-shal", ".db-wal.encrypted", ".db.encrypted"})
BASE64_EMBED_MAX_BYTES = 256 * 1024


def _matches_binary(name: str, exts: frozenset[str]) -> bool:
    """Match compound extensions: main.db-wal must match .wal, not fall to text."""
    lower = name.lower()
    return any(lower.endswith(e) for e in exts)
# Text files larger than the inline cap are truncated with an explicit marker
# rather than silently dropped. Silently dropping bottle.py (175KB) made
# fix-code-vulnerability unanswerable in v1.
TEXT_INLINE_MAX_BYTES = 512 * 1024


def _collect_workspace_files(task_dir: Path) -> list[Path]:
    """All loadable files under public/, across every subdirectory.

    Reads all of public/ (workspace/, data/, etc.), not just workspace/, so
    tasks like multi-source-data-merger (sources under public/data/) actually
    deliver their inputs to the model."""
    public_dir = task_dir / "public"
    out: list[Path] = []
    if public_dir.exists():
        for fpath in sorted(public_dir.rglob("*")):
            if fpath.is_file() and fpath.name != "prompt.txt":
                out.append(fpath)
    return out


def build_task_prompt(task_dir: Path) -> str:
    """Build full prompt context from task public assets and workspace files."""
    prompt_file = task_dir / "public/prompt.txt"
    base_prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "Execute the assigned software engineering task."

    public_dir = task_dir / "public"
    workspace_context = []
    for fpath in _collect_workspace_files(task_dir):
        suffix = fpath.suffix.lower()
        name = fpath.name
        size = fpath.stat().st_size
        rel = fpath.relative_to(public_dir).as_posix()
        try:
            if _matches_binary(name, IMAGE_EXTENSIONS):
                continue  # injected separately as image content blocks
            if _matches_binary(name, BASE64_EMBEDDABLE_EXTENSIONS):
                if size <= BASE64_EMBED_MAX_BYTES:
                    b64 = base64.b64encode(fpath.read_bytes()).decode()
                    workspace_context.append(
                        f"--- File: {rel} (base64-encoded {suffix} binary, {size} bytes) ---\n{b64}\n"
                    )
                else:
                    workspace_context.append(
                        f"--- File: {rel} ({suffix} binary, {size} bytes, too large to embed) ---\n[binary content omitted]\n"
                    )
                continue
            if _matches_binary(name, BINARY_EXTENSIONS):
                continue  # undecodable binary, not an image; skip
            # Text-ish file.
            if size <= TEXT_INLINE_MAX_BYTES:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            else:
                raw = fpath.read_bytes()[:TEXT_INLINE_MAX_BYTES]
                text = raw.decode("utf-8", errors="replace")
                text += f"\n[... truncated: {size} bytes total, showing first {TEXT_INLINE_MAX_BYTES} ...]"
            workspace_context.append(f"--- File: {rel} ---\n{text}\n")
        except Exception:
            pass

    context_str = "\n".join(workspace_context)
    if context_str:
        return f"{base_prompt}\n\nWorkspace Context:\n{context_str}\n\nReturn the solution adhering strictly to the contract schema."
    return base_prompt


def extract_clean_json_or_patch(raw: str) -> str:
    """Extract the substantive payload from a model response.

    Models frequently wrap the answer in prose plus a fenced block. Be liberal
    in what we accept: prefer the last fenced block, else the largest balanced
    JSON object, else strip leading/trailing fences. Never let surrounding
    conversational text turn a correct answer into a scored-fail."""
    text = raw.strip()
    if not text:
        return text

    # 1. Prefer the last fenced code block (models put the final answer last).
    fence_blocks = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)```", text, flags=re.DOTALL)
    if fence_blocks:
        candidate = fence_blocks[-1].strip()
        if candidate:
            return candidate

    # 2. Largest balanced top-level JSON object/array in the text.
    best = _largest_balanced_json(text)
    if best is not None:
        return best

    # 3. Strip a single leading/trailing fence pair.
    if text.startswith("```"):
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _largest_balanced_json(text: str) -> str | None:
    """Return the largest balanced {...} or [...] span, or None."""
    best: str | None = None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_str = False
            escape = False
            end = -1
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end != -1:
                span = text[start:end + 1]
                if best is None or len(span) > len(best):
                    best = span
                start = text.find(opener, end + 1)
            else:
                break
    if best is not None:
        stripped = best.strip()
        # Only accept if it actually parses as JSON.
        try:
            json.loads(stripped)
            return stripped
        except (ValueError, json.JSONDecodeError):
            return None
    return None


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError | socket.timeout | TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, socket.timeout):
        return True
    message = str(exc).lower()
    return "timed out" in message or "timeout" in message


def is_transient_network_error(exc: BaseException) -> bool:
    """Transient DNS/connection/rate-limit errors worth one retry; not scored as quality."""
    if is_timeout_error(exc):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 425, 429, 500, 502, 503, 504, 529}
    if isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason).lower()
        return any(
            token in reason
            for token in (
                "temporary failure in name resolution",
                "name or service not known",
                "connection reset",
                "connection refused",
                "connection aborted",
                "network is unreachable",
                "econnreset",
            )
        )
    if isinstance(exc, ConnectionError | socket.gaierror | socket.herror):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "temporary failure in name resolution",
            "connection reset by peer",
            "remote end closed connection",
            "eof occurred in violation of protocol",
            "502 bad gateway",
            "503 service unavailable",
            "504 gateway",
        )
    )


MAX_TRANSIENT_ATTEMPTS = 4


def _retry_after_seconds(exc: BaseException) -> float | None:
    if isinstance(exc, urllib.error.HTTPError) and exc.headers:
        raw = exc.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), 120.0)
            except ValueError:
                return None
    return None


def call_live_model_with_retry(
    route: dict[str, Any],
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.0,
    image_paths: list[str] | None = None,
) -> tuple[str, float, str | None]:
    last_error: BaseException | None = None
    last_text = ""
    total = 0.0
    max_attempts = MAX_PROVIDER_ATTEMPTS
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            text, elapsed = call_live_model(
                route,
                prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                image_paths=image_paths,
            )
            total += elapsed
            last_text = text
            if text.strip() or attempt >= max_attempts:
                return text, total, None
            continue
        except Exception as exc:
            last_error = exc
            if is_timeout_error(exc):
                total += get_provider_timeout(str(route.get("provider", "")))
            if not is_transient_network_error(exc):
                break
            # Transient: allow more attempts with backoff for rate-limit/overload.
            max_attempts = MAX_TRANSIENT_ATTEMPTS
            if attempt >= max_attempts:
                break
            delay = _retry_after_seconds(exc)
            if delay is None:
                delay = min(2.0 ** attempt, 30.0)
            time.sleep(delay)
    assert last_error is not None or not last_text.strip()
    if last_error is not None:
        if is_timeout_error(last_error):
            return "", total, f"timeout: {last_error}"
        return "", total, f"infra-error: {last_error}"
    return last_text, total, None

def score_from_verifier_payload(payload: dict[str, Any]) -> tuple[str, float | None]:
    verdict = payload.get("verdict") or payload.get("outcome")
    score = payload.get("score")
    if score is None:
        score = payload.get("reward")
    if verdict in {"pass", "accepted"}:
        return "scored-pass", 1.0 if score is None else float(score)
    if verdict in {"fail", "rejected"}:
        return "scored-fail", 0.0 if score is None else float(score)
    if verdict == "error":
        return "infra-error", None
    return "malformed-output", None


def score_homemade_envelope(task_dir: Path, artifact: bytes) -> tuple[str, float | None, dict[str, Any], float]:
    started = time.monotonic()
    runner_py = task_dir / "runner.py"
    verifier_py = task_dir / "verifier-private/verifier.py"
    runner = subprocess.run(
        ["python3", str(runner_py)],
        input=artifact,
        capture_output=True,
        cwd=task_dir,
    )
    verifier = subprocess.run(
        ["python3", str(verifier_py)],
        input=runner.stdout,
        capture_output=True,
        cwd=task_dir,
    )
    elapsed = time.monotonic() - started
    raw = verifier.stdout.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "malformed-output", None, {"raw": raw[:300], "runner_stderr": runner.stderr.decode("utf-8", "replace")[:300]}, elapsed
    outcome, score = score_from_verifier_payload(payload)
    return outcome, score, payload, elapsed


def score_docker_inject(
    root: Path,
    target: EvalTaskTarget,
    run_id: str,
    artifact: bytes,
) -> tuple[str, float | None, dict[str, Any], float]:
    started = time.monotonic()
    runs_dir = root / ".rolebench/runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = runs_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    prepare_worker_manifest(
        root,
        target.task_path,
        target.qualification_path,
        run_id=run_id,
        output_path=manifest_path,
    )
    report = run_worker(root, manifest_path, injected_artifact=artifact)
    elapsed = time.monotonic() - started
    observation = report.get("observation") or {}
    issues = observation.get("issues") or []
    verifier = observation.get("verifier") or {}
    outcome_name = verifier.get("outcome")
    reward = verifier.get("reward")
    if any(issue in {"runtime-incompatible", "image-pull", "environment-startup", "runner-failure"} for issue in issues):
        return "infra-error", None, report, elapsed
    if "artifact-collection" in issues:
        return "malformed-output", None, report, elapsed
    if outcome_name == "accepted":
        return "scored-pass", 1.0 if reward is None else float(reward), report, elapsed
    if outcome_name == "rejected":
        return "scored-fail", 0.0 if reward is None else float(reward), report, elapsed
    if outcome_name == "error":
        return "infra-error", None, report, elapsed
    return "infra-error", None, report, elapsed


def execute_live_eval(
    root: Path,
    route: dict[str, Any],
    target: EvalTaskTarget,
    eval_index: int,
    total_evals: int,
) -> LiveEvalResult:
    """Call the live model, then score through Docker runner/verifier images."""
    del eval_index, total_evals
    route_id = route["route_id"]
    route_slug = slugify(route_id)
    task_slug = slugify(target.task_id)
    run_id = f"live-{route_slug}-{task_slug}"

    reports_dir = root / ".rolebench/reports" / route_slug
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{task_slug}-report.json"
    task_dir = target.task_path.parent
    t_start = time.monotonic()

    prompt = build_task_prompt(task_dir)
    image_paths = collect_task_images(task_dir)
    raw_response, gen_time, provider_error = call_live_model_with_retry(route, prompt, image_paths=image_paths)
    if provider_error:
        outcome_class = "timeout" if provider_error.startswith("timeout:") else "infra-error"
        total_time = time.monotonic() - t_start
        worker_report = {
            "schema_version": "omp.worker-report/v2",
            "passed": False,
            "run_id": run_id,
            "role": target.role,
            "routing_lane": target.routing_lane,
            "task_id": target.task_id,
            "model_route": route,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timing": {
                "generation_seconds": gen_time,
                "verifier_seconds": 0.0,
                "total_seconds": total_time,
            },
            "outcome": {
                "classification": outcome_class,
                "quality_score": None,
                "failure_mode": outcome_class,
            },
            "raw_response_snippet": raw_response[:200],
            "error": provider_error,
        }
        report_path.write_text(json.dumps(worker_report, indent=2, sort_keys=True), encoding="utf-8")
        return LiveEvalResult(
            route_id=route_id,
            task_id=target.task_id,
            role=target.role,
            routing_lane=target.routing_lane,
            passed=False,
            quality_score=None,
            generation_seconds=gen_time,
            verifier_seconds=0.0,
            total_seconds=total_time,
            raw_response_snippet=raw_response[:80].replace("\n", " "),
            error=provider_error,
            report_path=report_path,
            outcome_class=outcome_class,
        )

    if not raw_response.strip():
        outcome_class = "malformed-output"
        total_time = time.monotonic() - t_start
        return LiveEvalResult(
            route_id=route_id,
            task_id=target.task_id,
            role=target.role,
            routing_lane=target.routing_lane,
            passed=False,
            quality_score=None,
            generation_seconds=gen_time,
            verifier_seconds=0.0,
            total_seconds=total_time,
            raw_response_snippet="",
            error="empty model response",
            report_path=None,
            outcome_class=outcome_class,
        )

    artifact = extract_clean_json_or_patch(raw_response).encode("utf-8")
    try:
        if target.task_id in HOMEMADE_ENVELOPE_TASK_IDS:
            outcome_class, score, payload, ver_time = score_homemade_envelope(task_dir, artifact)
        else:
            outcome_class, score, payload, ver_time = score_docker_inject(root, target, run_id, artifact)
    except Exception as exc:
        outcome_class = "timeout" if is_timeout_error(exc) else "infra-error"
        total_time = time.monotonic() - t_start
        return LiveEvalResult(
            route_id=route_id,
            task_id=target.task_id,
            role=target.role,
            routing_lane=target.routing_lane,
            passed=False,
            quality_score=None,
            generation_seconds=gen_time,
            verifier_seconds=0.0,
            total_seconds=total_time,
            raw_response_snippet=raw_response[:80].replace("\n", " "),
            error=str(exc),
            report_path=None,
            outcome_class=outcome_class,
        )

    total_time = time.monotonic() - t_start
    passed = outcome_class == "scored-pass"
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    worker_report = {
        "schema_version": "omp.worker-report/v2",
        "passed": passed,
        "run_id": run_id,
        "role": target.role,
        "routing_lane": target.routing_lane,
        "task_id": target.task_id,
        "model_route": route,
        "observed_at": now_iso,
        "timing": {
            "generation_seconds": gen_time,
            "verifier_seconds": ver_time,
            "total_seconds": total_time,
        },
        "outcome": {
            "classification": outcome_class,
            "quality_score": score,
            "failure_mode": None if passed else outcome_class,
        },
        "raw_response_snippet": raw_response[:200],
        "verifier_result": payload,
    }
    report_path.write_text(json.dumps(worker_report, indent=2, sort_keys=True), encoding="utf-8")
    ledger_path = root / ".rolebench/ledger/journal.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        append_worker_report(root, ledger_path, worker_report)
    except Exception:
        pass
    return LiveEvalResult(
        route_id=route_id,
        task_id=target.task_id,
        role=target.role,
        routing_lane=target.routing_lane,
        passed=passed,
        quality_score=score,
        generation_seconds=gen_time,
        verifier_seconds=ver_time,
        total_seconds=total_time,
        raw_response_snippet=raw_response[:80].replace("\n", " "),
        error=None if outcome_class in {"scored-pass", "scored-fail"} else outcome_class,
        report_path=report_path,
        outcome_class=outcome_class,
    )


def discover_evaluation_tasks(root: Path) -> list[EvalTaskTarget]:
    targets: list[EvalTaskTarget] = []
    seen_ids: set[str] = set()

    for pack_path in sorted((root / "contracts/task-packs").glob("*.json")):
        pack_data = json.load(open(pack_path))
        role = pack_data.get("role", "default")
        for entry in pack_data.get("entries", []):
            task_rel = entry["task"]["path"]
            qual_rel = entry["qualification"]["path"]
            task_path = root / task_rel
            qual_path = root / qual_rel
            if not task_path.exists() or not qual_path.exists():
                continue
            task_data = json.load(open(task_path))
            task_id = task_data["task_id"]
            if task_id in seen_ids or task_id in STUB_TASK_IDS:
                continue
            seen_ids.add(task_id)
            routing_lane = task_data.get("routing_lane") or entry.get("routing_lane")
            targets.append(
                EvalTaskTarget(
                    role=role,
                    routing_lane=routing_lane,
                    task_id=task_id,
                    task_path=task_path,
                    qualification_path=qual_path,
                    is_private=False,
                )
            )

    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Model RoleBench Calibration Evaluation Runner")
    parser.add_argument("--routes", default=".rolebench/candidate-routes.json", type=Path)
    parser.add_argument("--workers", default=4, type=int, help="Parallel model dispatch threads")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--task-id", action="append", default=[], help="Restrict to these task ids")
    parser.add_argument("--route-id", action="append", default=[], help="Restrict to these route ids")
    args = parser.parse_args()

    root = discover_root()
    routes_path = (root / args.routes) if not args.routes.is_absolute() else args.routes
    routes_data = json.load(open(routes_path))
    routes = routes_data.get("routes", [])
    if args.route_id:
        routes = [route for route in routes if route.get("route_id") in set(args.route_id)]
    targets = discover_evaluation_tasks(root)
    if args.task_id:
        wanted = set(args.task_id)
        targets = [target for target in targets if target.task_id in wanted]

    total_evals = len(routes) * len(targets)
    print("=" * 76)
    print("  OMP RoleBench LIVE Multi-Model Benchmark Calibration Runner")
    print("=" * 76)
    print(f"Candidate Models ({len(routes)}):")
    for r in routes:
        print(f"  • {r['route_id']} [{r['provider']}]")
    print(f"\nTask Corpus: {len(targets)} executable anchors (stub tautologies excluded)")
    print(f"Total Live Model API Evaluations to Dispatch: {total_evals}")
    print(f"Concurrency: {args.workers} worker threads")
    print("-" * 76)

    tasks_to_run = [(r, t) for r in routes for t in targets]
    results: list[LiveEvalResult] = []
    completed_count = 0
    start_time = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(execute_live_eval, root, route, target, idx + 1, total_evals): (route, target)
            for idx, (route, target) in enumerate(tasks_to_run)
        }

        for future in concurrent.futures.as_completed(future_map):
            completed_count += 1
            route, target = future_map[future]
            try:
                res = future.result()
                results.append(res)
                lane_str = f" [{res.routing_lane}]" if res.routing_lane else ""
                status_str = {
                    "scored-pass": "PASS",
                    "scored-fail": "FAIL",
                    "timeout": "TIMEOUT",
                    "infra-error": "INFRA",
                    "malformed-output": "MALFORMED",
                }.get(res.outcome_class, res.outcome_class.upper())
                score_str = "score=n/a" if res.quality_score is None else f"score={res.quality_score:.2f}"
                time_str = f"gen={res.generation_seconds:.1f}s, tot={res.total_seconds:.1f}s"
                print(
                    f"[{completed_count:>3}/{total_evals}] ({status_str}) {res.route_id} -> "
                    f"@{res.role}{lane_str} / {res.task_id} ({score_str}, {time_str})"
                )
                if res.error:
                    print(f"      {res.outcome_class}: {res.error}", file=sys.stderr)
            except Exception as e:
                print(f"[{completed_count:>3}/{total_evals}] (CRITICAL EXCEPTION) {route['route_id']} -> {target.task_id}: {e}", file=sys.stderr)

    total_duration = time.monotonic() - start_time
    print("\n" + "=" * 76)
    print(f"Live Evaluation Completed in {total_duration:.1f}s ({total_duration/60:.2f} minutes)")
    print("=" * 76)

    # Compute Summary Statistics
    summary_by_route: dict[str, dict[str, Any]] = {}
    for r in routes:
        rid = r["route_id"]
        route_results = [res for res in results if res.route_id == rid]
        scored = [res for res in route_results if res.outcome_class in {"scored-pass", "scored-fail"}]
        passed_count = sum(1 for res in scored if res.passed)
        scores = [res.quality_score for res in scored if res.quality_score is not None]
        avg_score = sum(scores) / len(scores) if scores else None
        avg_gen_time = sum(res.generation_seconds for res in route_results) / len(route_results) if route_results else 0.0
        outcome_counts: dict[str, int] = {}
        for res in route_results:
            outcome_counts[res.outcome_class] = outcome_counts.get(res.outcome_class, 0) + 1

        role_scores: dict[str, list[float]] = {}
        for res in scored:
            if res.quality_score is not None:
                role_scores.setdefault(res.role, []).append(res.quality_score)
        avg_by_role = {role: sum(s) / len(s) for role, s in role_scores.items()}

        # By Lane
        lane_scores: dict[str, list[float]] = {}
        for res in scored:
            if res.routing_lane and res.quality_score is not None:
                lane_scores.setdefault(res.routing_lane, []).append(res.quality_score)
        avg_by_lane = {lane: sum(s) / len(s) for lane, s in lane_scores.items()}

        summary_by_route[rid] = {
            "total_tasks": len(route_results),
            "scored_tasks": len(scored),
            "passed_tasks": passed_count,
            "pass_rate": passed_count / len(scored) if scored else None,
            "average_quality_score": avg_score,
            "average_generation_latency_seconds": avg_gen_time,
            "outcome_counts": outcome_counts,
            "role_quality": avg_by_role,
            "lane_quality": avg_by_lane,
        }

    print("\n--- Live Model Calibration Leaderboard ---")
    sorted_routes = sorted(
        summary_by_route.items(),
        key=lambda item: item[1]["average_quality_score"] if item[1]["average_quality_score"] is not None else -1.0,
        reverse=True,
    )
    for rank, (rid, stats) in enumerate(sorted_routes, 1):
        scored_n = stats["scored_tasks"]
        pass_rate = stats["pass_rate"]
        avg_quality = stats["average_quality_score"]
        pass_txt = "n/a" if pass_rate is None else f"{pass_rate * 100:5.1f}%"
        quality_txt = "n/a" if avg_quality is None else f"{avg_quality:.3f}"
        print(
            f"#{rank} {rid:<40} Scored: {stats['passed_tasks']:>2}/{scored_n:<2} "
            f"({pass_txt}) | Avg Quality: {quality_txt} | Avg Latency: {stats['average_generation_latency_seconds']:.1f}s | {stats['outcome_counts']}"
        )

    # Save artifact
    workspace_dir = root / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    summary_report_path = workspace_dir / "live-model-calibration-report.json"
    summary_payload = {
        "schema_version": "omp.live-calibration-summary/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_evaluations": len(results),
        "total_duration_seconds": total_duration,
        "candidate_routes": routes,
        "leaderboard": summary_by_route,
        "evaluations": [
            {
                "route_id": r.route_id,
                "task_id": r.task_id,
                "role": r.role,
                "routing_lane": r.routing_lane,
                "passed": r.passed,
                "outcome_class": r.outcome_class,
                "quality_score": r.quality_score,
                "generation_seconds": r.generation_seconds,
                "verifier_seconds": r.verifier_seconds,
                "total_seconds": r.total_seconds,
                "snippet": r.raw_response_snippet,
                "error": r.error,
            }
            for r in results
        ],
    }
    with open(summary_report_path, "w") as f:
        json.dump(summary_payload, f, indent=2, sort_keys=True)
    print(f"\nLive benchmark evaluation artifact saved to: {summary_report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
