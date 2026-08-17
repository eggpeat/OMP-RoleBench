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

def get_provider_credentials() -> dict[str, dict[str, Any]]:
    """Load all active credentials from ~/.omp/agent/agent.db."""
    conn = sqlite3.connect("/home/eggpeat/.omp/agent/agent.db")
    rows = conn.execute(
        "SELECT provider, data FROM auth_credentials WHERE disabled_cause IS NULL ORDER BY id DESC"
    ).fetchall()
    creds: dict[str, dict[str, Any]] = {}
    for prov, data_str in rows:
        if prov not in creds:
            try:
                creds[prov] = json.loads(data_str)
            except Exception:
                pass
    return creds


def _mime_for(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(suffix, "image/png")


def call_live_model(
    route: dict[str, Any],
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.0,
    images: list[tuple[str, bytes]] | None = None,
) -> tuple[str, float]:
    """Dispatch completion to a live model provider endpoint. Returns (response_text, latency_seconds)."""
    route_id = route["route_id"]
    provider = route["provider"]
    model = route["model"]
    creds = get_provider_credentials()
    images = images or []
    if images and provider not in VISION_CAPABLE_PROVIDERS:
        names = ", ".join(name for name, _ in images)
        prompt = (
            prompt
            + f"\n\n[NOTE: This task references image file(s) {names}, but this evaluation channel is text-only and the image content was not provided to you. Respond per the contract schema as best you can.]"
        )

    t0 = time.monotonic()
    sys_content = system_prompt or "You are an expert software engineering and review agent. Output ONLY the raw solution/JSON/code adhering strictly to the contract schema without conversational text."

    if provider == "deepseek":
        key = creds.get("deepseek", {}).get("key") or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("No API key for DeepSeek")
        url = "https://api.deepseek.com/chat/completions"
        req_data = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(req_data).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["deepseek"]) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if not content and msg.get("reasoning_content"):
            content = str(msg.get("reasoning_content")).strip()
        return content, time.monotonic() - t0

    elif provider in ("xai", "xai-oauth"):
        cred = creds.get("xai-oauth") or creds.get("xai")
        token = (cred.get("access") if cred else None) or (cred.get("key") if cred else None)
        if not token:
            raise ValueError("No OAuth token or API key for xAI")
        url = "https://api.x.ai/v1/chat/completions"
        if images:
            user_content: Any = [{"type": "text", "text": prompt}]
            for name, blob in images:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_mime_for(name)};base64,{base64.b64encode(blob).decode()}"},
                    }
                )
        else:
            user_content = prompt
        req_data = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(req_data).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["xai-oauth"]) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip(), time.monotonic() - t0

    elif provider == "alibaba-token-plan":
        cred = creds.get("alibaba-token-plan", {})
        key_raw = cred.get("key", "")
        token = key_raw
        cookie = None
        if key_raw.startswith("{"):
            try:
                obj = json.loads(key_raw)
                token = obj.get("token", token)
                cookie = obj.get("cookie")
            except Exception:
                pass
        if not token:
            raise ValueError("No token for Alibaba Token Plan")
        url = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        if images:
            user_content: Any = [{"type": "text", "text": prompt}]
            for name, blob in images:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_mime_for(name)};base64,{base64.b64encode(blob).decode()}"},
                    }
                )
        else:
            user_content = prompt
        req_data = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(url, data=json.dumps(req_data).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["alibaba-token-plan"]) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip(), time.monotonic() - t0

    elif provider == "kimi-code":
        cred = creds.get("kimi-code", {})
        token = cred.get("access")
        if not token:
            raise ValueError("No OAuth token for Kimi Code")
        url = "https://api.kimi.com/coding/v1/chat/completions"
        req_data = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 1.0,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(req_data).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["kimi-code"]) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if not content and msg.get("reasoning_content"):
            content = str(msg.get("reasoning_content")).strip()
        return content, time.monotonic() - t0

    elif provider == "zai":
        cred = creds.get("zai", {})
        key = cred.get("key")
        if not key:
            raise ValueError("No API key for ZAI")
        url = "https://api.z.ai/api/anthropic/v1/messages"
        if images:
            user_content: Any = []
            for name, blob in images:
                user_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _mime_for(name),
                            "data": base64.b64encode(blob).decode(),
                        },
                    }
                )
            user_content.append({"type": "text", "text": prompt})
        else:
            user_content = prompt
        req_data = {
            "model": model,
            "system": sys_content,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(req_data).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["zai"]) as resp:
            data = json.loads(resp.read())
        texts = [
            c["text"]
            for c in data.get("content", [])
            if c.get("type") == "text" and c.get("text", "").strip()
        ]
        if not texts:
            thinking_blocks = [
                c.get("thinking") or c.get("text") or ""
                for c in data.get("content", [])
                if c.get("type") == "thinking" and (c.get("thinking") or c.get("text"))
            ]
            if thinking_blocks:
                return "\n".join(thinking_blocks).strip(), time.monotonic() - t0
        return "\n".join(texts).strip(), time.monotonic() - t0

    elif provider == "google-antigravity":
        cred = creds.get("google", {})
        key = cred.get("key")
        if key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            parts: list[dict[str, Any]] = [{"text": f"{sys_content}\n\n{prompt}"}]
            for name, blob in images:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": _mime_for(name),
                            "data": base64.b64encode(blob).decode(),
                        }
                    }
                )
            req_data = {
                "contents": [
                    {"parts": parts}
                ],
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(req_data).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["google-antigravity"]) as resp:
                data = json.loads(resp.read())
            # Defensive: safety-blocked or empty candidates lack content.parts.
            candidates = data.get("candidates") or []
            for cand in candidates:
                for part in (cand.get("content") or {}).get("parts") or []:
                    text = (part.get("text") or "").strip()
                    if text:
                        return text, time.monotonic() - t0
            # No usable text: surface finish reason for diagnosis, treat as empty.
            finish = candidates[0].get("finishReason") if candidates else None
            if finish and finish not in ("STOP", "MAX_TOKENS"):
                raise ValueError(f"gemini candidate blocked: finishReason={finish}")
            return "", time.monotonic() - t0
        else:
            raise ValueError("No API key for google-antigravity (google credential missing)")

    elif provider == "devin":
        cred = creds.get("devin", {})
        key = cred.get("key")
        if not key:
            raise ValueError("No API key for devin")
        url = "https://api.devin.ai/v1/completions"
        req_data = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
        req = urllib.request.Request(url, data=json.dumps(req_data).encode(), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=PROVIDER_TIMEOUTS["devin"]) as resp:
            data = json.loads(resp.read())
        return str(data.get("content", "")).strip(), time.monotonic() - t0

    else:
        raise ValueError(f"Unsupported provider: {provider}")


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif"})
VISION_CAPABLE_PROVIDERS = frozenset({"google-antigravity", "xai", "xai-oauth", "alibaba-token-plan", "zai"})


def collect_task_images(task_dir: Path) -> list[tuple[str, bytes]]:
    """Collect image assets from the task workspace for vision-capable providers."""
    workspace_dir = task_dir / "public/workspace"
    images: list[tuple[str, bytes]] = []
    if workspace_dir.exists():
        for fpath in sorted(workspace_dir.glob("**/*")):
            if fpath.is_file() and fpath.suffix.lower() in IMAGE_EXTENSIONS and fpath.stat().st_size <= 4 * 1024 * 1024:
                rel = fpath.relative_to(workspace_dir).as_posix()
                images.append((rel, fpath.read_bytes()))
    return images


def build_task_prompt(task_dir: Path) -> str:
    """Build full prompt context from task public assets and workspace files."""
    prompt_file = task_dir / "public/prompt.txt"
    base_prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "Execute the assigned software engineering task."

    workspace_dir = task_dir / "public/workspace"
    workspace_context = []
    if workspace_dir.exists():
        for fpath in sorted(workspace_dir.glob("**/*")):
            if fpath.is_file() and fpath.suffix.lower() not in BINARY_EXTENSIONS and fpath.stat().st_size < 32 * 1024:
                try:
                    rel = fpath.relative_to(workspace_dir)
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    workspace_context.append(f"--- File: {rel} ---\n{text}\n")
                except Exception:
                    pass

    context_str = "\n".join(workspace_context)
    if context_str:
        return f"{base_prompt}\n\nWorkspace Context:\n{context_str}\n\nReturn the solution adhering strictly to the contract schema."
    return base_prompt


def extract_clean_json_or_patch(raw: str) -> str:
    """Strip markdown formatting or code blocks from model response."""
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```diff"):
        text = text[7:]
    elif text.startswith("```python"):
        text = text[9:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


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
    images: list[tuple[str, bytes]] | None = None,
) -> tuple[str, float, str | None]:
    last_error: BaseException | None = None
    last_text = ""
    total = 0.0
    max_attempts = MAX_PROVIDER_ATTEMPTS
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            if system_prompt is not None or max_tokens != DEFAULT_MAX_TOKENS or temperature != 0.0 or images:
                text, elapsed = call_live_model(
                    route,
                    prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    images=images,
                )
            else:
                text, elapsed = call_live_model(route, prompt)
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
    images = collect_task_images(task_dir)
    raw_response, gen_time, provider_error = call_live_model_with_retry(route, prompt, images=images)
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
