from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_model_calibration.py"
SPEC = importlib.util.spec_from_file_location("live_model_calibration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
live = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = live
SPEC.loader.exec_module(live)


def _make_http_error(test_case: unittest.TestCase, url: str, code: int, msg: str, headers: dict | None = None) -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(url, code, msg, headers or {}, None)
    test_case.addCleanup(err.close)
    return err


class OmpSelectorTests(unittest.TestCase):
    def test_omp_selector_combines_provider_and_model(self) -> None:
        route = {"provider": "xai", "model": "grok-4"}
        self.assertEqual(live._omp_selector(route), "xai/grok-4")

    def test_omp_selector_handles_namespaced_provider(self) -> None:
        route = {"provider": "google-antigravity", "model": "gemini-3.7-flash"}
        self.assertEqual(live._omp_selector(route), "google-antigravity/gemini-3.7-flash")


class ParseOmpAgentEndTests(unittest.TestCase):
    def test_extract_text_from_agent_end(self) -> None:
        jsonl = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "final assistant text"}],
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                        "stopReason": "stop",
                    }
                ],
            }
        )
        text, usage = live._parse_omp_agent_end(jsonl)
        self.assertEqual(text, "final assistant text")
        self.assertEqual(usage.get("input_tokens"), 10)
        self.assertEqual(usage.get("output_tokens"), 20)
        self.assertEqual(usage.get("_stop_reason"), "stop")

    def test_message_end_fallback(self) -> None:
        jsonl = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "fallback text from message_end"}],
                    "usage": {"total_tokens": 35},
                    "stopReason": "end_turn",
                },
            }
        )
        text, usage = live._parse_omp_agent_end(jsonl)
        self.assertEqual(text, "fallback text from message_end")
        self.assertEqual(usage.get("total_tokens"), 35)
        self.assertEqual(usage.get("_stop_reason"), "end_turn")

    def test_turn_end_fallback(self) -> None:
        jsonl = json.dumps(
            {
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "turn end output"}],
                },
            }
        )
        text, usage = live._parse_omp_agent_end(jsonl)
        self.assertEqual(text, "turn end output")
        self.assertEqual(usage.get("_stop_reason"), "")

    def test_joins_multiple_text_blocks(self) -> None:
        jsonl = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Line 1"},
                            {"type": "text", "text": "Line 2"},
                        ],
                    }
                ],
            }
        )
        text, _ = live._parse_omp_agent_end(jsonl)
        self.assertEqual(text, "Line 1\nLine 2")

    def test_returns_empty_when_no_assistant_text(self) -> None:
        jsonl = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "User question"}],
                    }
                ],
            }
        )
        text, usage = live._parse_omp_agent_end(jsonl)
        self.assertEqual(text, "")
        self.assertEqual(usage.get("_stop_reason"), "")

    def test_ignores_malformed_and_non_json_lines(self) -> None:
        lines = [
            "OMP session started",
            "{broken json",
            json.dumps({"type": "tool_call", "tool": "grep"}),
            json.dumps(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "recovered text"}],
                        }
                    ],
                }
            ),
        ]
        text, _ = live._parse_omp_agent_end("\n".join(lines))
        self.assertEqual(text, "recovered text")


class RouteSupportsImagesTests(unittest.TestCase):
    def test_route_supports_images_requires_image_modality(self) -> None:
        self.assertTrue(live._route_supports_images({"input_modalities": ["text", "image"]}))
        self.assertFalse(live._route_supports_images({"input_modalities": ["text"]}))
        self.assertFalse(live._route_supports_images({}))
        self.assertFalse(live._route_supports_images({"provider": "xai-oauth"}))
        self.assertFalse(live._route_supports_images({"input_modalities": "image"}))


class CollectTaskImagesTests(unittest.TestCase):
    def test_collect_task_images_returns_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "public" / "workspace"
            ws.mkdir(parents=True)
            png_file = ws / "code.png"
            png_file.write_bytes(b"\x89PNG fake")
            jpg_file = ws / "photo.jpg"
            jpg_file.write_bytes(b"\xff\xd8 fake jpg")
            txt_file = ws / "notes.txt"
            txt_file.write_text("hello")
            bin_file = ws / "tool.bin"
            bin_file.write_bytes(b"\x00\x01")

            images = live.collect_task_images(Path(tmp))
            self.assertEqual(
                sorted(images),
                sorted([str(png_file.resolve()), str(jpg_file.resolve())]),
            )
            for path in images:
                self.assertTrue(Path(path).is_absolute())

    def test_collect_task_images_ignores_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "public" / "workspace"
            ws.mkdir(parents=True)
            img_file = ws / "large.png"
            img_file.write_bytes(b"x")
            with mock.patch("pathlib.Path.stat") as mock_stat:
                stat_result = mock.MagicMock()
                stat_result.st_size = 17 * 1024 * 1024
                mock_stat.return_value = stat_result
                images = live.collect_task_images(Path(tmp))
                self.assertEqual(images, [])


class CallLiveModelDispatchTests(unittest.TestCase):
    def _mock_proc(self, stdout_text: str = "", stderr_text: str = "", returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout_text.encode("utf-8"),
            stderr=stderr_text.encode("utf-8"),
        )

    def test_call_live_model_argv_construction(self) -> None:
        route = {"route_id": "xai/grok-4", "provider": "xai", "model": "grok-4"}
        payload = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "result content"}],
                    }
                ],
            }
        )
        with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
            text, latency = live.call_live_model(route, "solve this")

        self.assertEqual(text, "result content")
        self.assertGreaterEqual(latency, 0.0)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(
            cmd,
            [
                "omp",
                "--no-session",
                "--auto-approve",
                "--model",
                "xai/grok-4",
                "--mode",
                "json",
                "--no-tools",
                "-p",
                "solve this",
            ],
        )
        self.assertEqual(mock_run.call_args[1]["timeout"], 900)

    def test_call_live_model_with_system_prompt(self) -> None:
        route = {"route_id": "xai/grok-4", "provider": "xai", "model": "grok-4"}
        payload = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ],
            }
        )
        with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
            live.call_live_model(route, "user prompt", system_prompt="system instructions")

        cmd = mock_run.call_args[0][0]
        self.assertIn("-p", cmd)
        p_index = cmd.index("-p")
        self.assertEqual(cmd[p_index + 1], "system instructions\n\nuser prompt")

    def test_call_live_model_thinking_flag(self) -> None:
        route_high = {"provider": "deepseek", "model": "r1", "thinking": "high"}
        payload = json.dumps(
            {"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]}
        )
        with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
            live.call_live_model(route_high, "prompt")
        cmd = mock_run.call_args[0][0]
        self.assertIn("--thinking", cmd)
        thinking_idx = cmd.index("--thinking")
        self.assertEqual(cmd[thinking_idx + 1], "high")

    def test_call_live_model_thinking_off_none_auto_omitted(self) -> None:
        payload = json.dumps(
            {"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]}
        )
        for val in ("off", "none", "auto", "OFF", "None", "Auto"):
            route = {"provider": "deepseek", "model": "chat", "thinking": val}
            with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
                live.call_live_model(route, "prompt")
            cmd = mock_run.call_args[0][0]
            self.assertNotIn("--thinking", cmd)

    def test_call_live_model_multimodal_image_args(self) -> None:
        route = {
            "provider": "google-antigravity",
            "model": "gemini-3.7-flash",
            "input_modalities": ["text", "image"],
        }
        payload = json.dumps(
            {"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]}
        )
        with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
            live.call_live_model(route, "describe image", image_paths=["/tmp/diagram.png", "/tmp/chart.jpg"])

        cmd = mock_run.call_args[0][0]
        self.assertIn("@/tmp/diagram.png", cmd)
        self.assertIn("@/tmp/chart.jpg", cmd)
        p_index = cmd.index("-p")
        self.assertEqual(cmd[p_index + 1], "describe image")

    def test_call_live_model_text_only_withholds_images_and_adds_note(self) -> None:
        route = {"provider": "deepseek", "model": "deepseek-chat", "input_modalities": ["text"]}
        payload = json.dumps(
            {"type": "agent_end", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]}
        )
        with mock.patch.object(live.subprocess, "run", return_value=self._mock_proc(payload)) as mock_run:
            live.call_live_model(route, "solve task", image_paths=["/tmp/schema.png"])

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("@/tmp/schema.png", cmd)
        p_index = cmd.index("-p")
        full_prompt = cmd[p_index + 1]
        self.assertIn("solve task", full_prompt)
        self.assertIn("[NOTE: This task references image file(s) /tmp/schema.png", full_prompt)
        self.assertIn("text-only", full_prompt)

    def test_call_live_model_raises_timeout_error_on_subprocess_timeout(self) -> None:
        route = {"provider": "deepseek", "model": "deepseek-chat"}
        with mock.patch.object(
            live.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd=["omp"], timeout=600),
        ):
            with self.assertRaises(TimeoutError) as ctx:
                live.call_live_model(route, "prompt")
            self.assertIn("omp dispatch timed out after 600s", str(ctx.exception))

    def test_call_live_model_raises_runtime_error_on_nonzero_exit(self) -> None:
        route = {"provider": "xai", "model": "grok-4"}
        proc = self._mock_proc(stderr_text="Authentication failed: invalid token", returncode=1)
        with mock.patch.object(live.subprocess, "run", return_value=proc):
            with self.assertRaises(RuntimeError) as ctx:
                live.call_live_model(route, "prompt")
            self.assertIn("omp exited 1", str(ctx.exception))
            self.assertIn("Authentication failed", str(ctx.exception))


class ScoreDockerInjectTests(unittest.TestCase):
    def test_observation_artifact_collection_is_malformed_output(self) -> None:
        target = live.EvalTaskTarget(
            role="commit",
            routing_lane=None,
            task_id="omp-native.diff-commit-message",
            task_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/task.json"),
            qualification_path=Path(
                "contracts/tasks/omp-native.diff-commit-message/1.0.0/qualification.json"
            ),
            is_private=False,
        )
        report = {
            "observation": {
                "issues": ["artifact-collection"],
                "verifier": {"outcome": "not-run", "reward": None},
            }
        }
        with (
            mock.patch.object(live, "prepare_worker_manifest"),
            mock.patch.object(live, "run_worker", return_value=report),
        ):
            outcome, score, _, _ = live.score_docker_inject(
                Path("."),
                target,
                "live-fixture",
                b"too-big",
            )
        self.assertEqual(outcome, "malformed-output")
        self.assertIsNone(score)

    def test_root_issues_are_ignored(self) -> None:
        target = live.EvalTaskTarget(
            role="commit",
            routing_lane=None,
            task_id="omp-native.diff-commit-message",
            task_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/task.json"),
            qualification_path=Path(
                "contracts/tasks/omp-native.diff-commit-message/1.0.0/qualification.json"
            ),
            is_private=False,
        )
        report = {
            "issues": ["artifact-collection"],
            "observation": {
                "issues": [],
                "verifier": {"outcome": "rejected", "reward": 0.0},
            },
        }
        with (
            mock.patch.object(live, "prepare_worker_manifest"),
            mock.patch.object(live, "run_worker", return_value=report),
        ):
            outcome, score, _, _ = live.score_docker_inject(
                Path("."),
                target,
                "live-fixture",
                b"ok",
            )
        self.assertEqual(outcome, "scored-fail")
        self.assertEqual(score, 0.0)

    def test_infra_issues_return_infra_error(self) -> None:
        target = live.EvalTaskTarget(
            role="commit",
            routing_lane=None,
            task_id="omp-native.diff-commit-message",
            task_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/task.json"),
            qualification_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/qualification.json"),
            is_private=False,
        )
        report = {
            "observation": {
                "issues": ["image-pull"],
                "verifier": {},
            }
        }
        with (
            mock.patch.object(live, "prepare_worker_manifest"),
            mock.patch.object(live, "run_worker", return_value=report),
        ):
            outcome, score, _, _ = live.score_docker_inject(Path("."), target, "run-1", b"")
        self.assertEqual(outcome, "infra-error")
        self.assertIsNone(score)

    def test_accepted_outcome_returns_scored_pass(self) -> None:
        target = live.EvalTaskTarget(
            role="commit",
            routing_lane=None,
            task_id="omp-native.diff-commit-message",
            task_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/task.json"),
            qualification_path=Path("contracts/tasks/omp-native.diff-commit-message/1.0.0/qualification.json"),
            is_private=False,
        )
        report = {
            "observation": {
                "issues": [],
                "verifier": {"outcome": "accepted", "reward": 0.95},
            }
        }
        with (
            mock.patch.object(live, "prepare_worker_manifest"),
            mock.patch.object(live, "run_worker", return_value=report),
        ):
            outcome, score, _, _ = live.score_docker_inject(Path("."), target, "run-1", b"")
        self.assertEqual(outcome, "scored-pass")
        self.assertEqual(score, 0.95)


class ScoreFromVerifierPayloadTests(unittest.TestCase):
    def test_verdict_pass_and_accepted(self) -> None:
        self.assertEqual(live.score_from_verifier_payload({"verdict": "pass", "score": 1.0}), ("scored-pass", 1.0))
        self.assertEqual(live.score_from_verifier_payload({"outcome": "accepted"}), ("scored-pass", 1.0))
        self.assertEqual(live.score_from_verifier_payload({"verdict": "pass", "reward": 0.75}), ("scored-pass", 0.75))

    def test_verdict_fail_and_rejected(self) -> None:
        self.assertEqual(live.score_from_verifier_payload({"verdict": "fail", "score": 0.0}), ("scored-fail", 0.0))
        self.assertEqual(live.score_from_verifier_payload({"outcome": "rejected"}), ("scored-fail", 0.0))
        self.assertEqual(live.score_from_verifier_payload({"outcome": "fail", "reward": 0.2}), ("scored-fail", 0.2))

    def test_verdict_error_and_malformed(self) -> None:
        self.assertEqual(live.score_from_verifier_payload({"verdict": "error"}), ("infra-error", None))
        self.assertEqual(live.score_from_verifier_payload({"verdict": "other"}), ("malformed-output", None))
        self.assertEqual(live.score_from_verifier_payload({}), ("malformed-output", None))


class BuildTaskPromptTests(unittest.TestCase):
    def test_binary_file_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            prompt_file = task_dir / "public" / "prompt.txt"
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text("Solve the problem.", encoding="utf-8")

            ws_dir = task_dir / "public" / "workspace"
            ws_dir.mkdir(parents=True, exist_ok=True)

            # Text files that should be included (paths relative to public/).
            (ws_dir / "main.py").write_text("print('hello')", encoding="utf-8")
            (ws_dir / "notes.txt").write_text("Important notes.", encoding="utf-8")

            # Undecodable binaries / images: skipped (images go to image blocks).
            skipped = ["image.png", "photo.jpg", "photo.jpeg", "anim.gif", "tool.bin", "module.wasm", "UPPER.PNG"]
            # Small analysis binaries: base64-embedded so the model can reason about them.
            embedded = ["data.db", "data.wal", "store.sqlite", "store.sqlite3", "UPPER.DB"]
            for bname in skipped + embedded:
                (ws_dir / bname).write_bytes(b"\x00\x01\x02\x03\xff\xfe")

            nested_dir = ws_dir / "nested" / "sub"
            nested_dir.mkdir(parents=True, exist_ok=True)
            (nested_dir / "nested_binary.bin").write_bytes(b"\xde\xad\xbe\xef")
            (nested_dir / "nested_text.py").write_text("x = 1", encoding="utf-8")

            result = live.build_task_prompt(task_dir)

            self.assertIn("Solve the problem.", result)
            self.assertIn("--- File: workspace/main.py ---\nprint('hello')", result)
            self.assertIn("--- File: workspace/notes.txt ---\nImportant notes.", result)
            self.assertIn("--- File: workspace/nested/sub/nested_text.py ---\nx = 1", result)

            # Images and undecodable binaries are not inlined as text.
            for bname in skipped:
                self.assertNotIn(f"--- File: workspace/{bname} ---", result)
            self.assertNotIn("nested_binary.bin", result)
            # Analysis binaries are base64-embedded with an explicit marker.
            for bname in embedded:
                self.assertIn(f"--- File: workspace/{bname} (base64-encoded", result)

    def test_oversized_text_file_truncated_not_dropped(self) -> None:
        """bottle.py (175KB) must not be silently dropped — fix-code-vulnerability regression."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            (task_dir / "public").mkdir(parents=True)
            (task_dir / "public" / "prompt.txt").write_text("Review the framework.", encoding="utf-8")
            ws = task_dir / "public" / "workspace"
            ws.mkdir()
            (ws / "bottle.py").write_text("x = 1\n" * 20000, encoding="utf-8")  # ~120KB, was >32KB cap
            result = live.build_task_prompt(task_dir)
            self.assertIn("--- File: workspace/bottle.py ---", result)
            self.assertIn("x = 1", result)

    def test_non_workspace_public_dirs_are_loaded(self) -> None:
        """multi-source-data-merger keeps sources under public/data/ — regression."""
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            (task_dir / "public").mkdir(parents=True)
            (task_dir / "public" / "prompt.txt").write_text("Merge the sources.", encoding="utf-8")
            data = task_dir / "public" / "data" / "source_a"
            data.mkdir(parents=True)
            (data / "users.json").write_text('[{"user_id": 1}]', encoding="utf-8")
            result = live.build_task_prompt(task_dir)
            self.assertIn("--- File: data/source_a/users.json ---", result)
            self.assertIn('{"user_id": 1}', result)

    def test_prompt_txt_not_double_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            (task_dir / "public").mkdir(parents=True)
            (task_dir / "public" / "prompt.txt").write_text("UNIQUEPROMPT", encoding="utf-8")
            result = live.build_task_prompt(task_dir)
            self.assertEqual(result.count("UNIQUEPROMPT"), 1)


class ProviderTimeoutLookupTests(unittest.TestCase):
    def test_provider_timeouts_dict(self) -> None:
        self.assertEqual(live.PROVIDER_TIMEOUTS["alibaba-token-plan"], 900)
        self.assertEqual(live.PROVIDER_TIMEOUTS["xai-oauth"], 900)
        self.assertEqual(live.PROVIDER_TIMEOUTS["kimi-code"], 900)
        self.assertEqual(live.PROVIDER_TIMEOUTS["google-antigravity"], 900)
        self.assertEqual(live.PROVIDER_TIMEOUTS["deepseek"], 600)
        self.assertEqual(live.PROVIDER_TIMEOUTS["devin"], 600)
        self.assertEqual(live.PROVIDER_TIMEOUTS["zai"], 900)

    def test_get_provider_timeout_lookup(self) -> None:
        self.assertEqual(live.get_provider_timeout("alibaba-token-plan"), 900)
        self.assertEqual(live.get_provider_timeout("xai-oauth"), 900)
        self.assertEqual(live.get_provider_timeout("xai"), 900)
        self.assertEqual(live.get_provider_timeout("kimi-code"), 900)
        self.assertEqual(live.get_provider_timeout("google-antigravity"), 900)
        self.assertEqual(live.get_provider_timeout("deepseek"), 600)
        self.assertEqual(live.get_provider_timeout("devin"), 600)
        self.assertEqual(live.get_provider_timeout("zai"), 900)
        self.assertEqual(live.get_provider_timeout("unknown-provider"), 900)
        self.assertEqual(live.get_provider_timeout(None), 900)


class CallLiveModelWithRetryTests(unittest.TestCase):
    def test_retry_accumulates_total_on_repeated_timeouts(self) -> None:
        route = {"route_id": "alibaba/qwen3.8-max", "provider": "alibaba-token-plan", "model": "qwen3.8-max"}
        with (
            mock.patch.object(
                live,
                "call_live_model",
                side_effect=TimeoutError("timeout"),
            ) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "")
            self.assertEqual(total, 3600.0)  # 4 * 900
            self.assertIsNotNone(error)
            self.assertTrue(error.startswith("timeout:"))
            self.assertEqual(mock_call.call_count, live.MAX_TRANSIENT_ATTEMPTS)

    def test_retry_accumulates_total_on_first_timeout_then_success(self) -> None:
        route = {"route_id": "deepseek/deepseek-chat", "provider": "deepseek", "model": "deepseek-chat"}
        with (
            mock.patch.object(
                live,
                "call_live_model",
                side_effect=[TimeoutError("timed out"), ("success response", 14.5)],
            ) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "success response")
            self.assertEqual(total, 614.5)  # 600 + 14.5
            self.assertIsNone(error)
            self.assertEqual(mock_call.call_count, 2)

    def test_non_timeout_error_does_not_retry(self) -> None:
        route = {"route_id": "deepseek/deepseek-chat", "provider": "deepseek", "model": "deepseek-chat"}
        with mock.patch.object(
            live,
            "call_live_model",
            side_effect=ValueError("Unauthorized API key"),
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "")
            self.assertEqual(total, 0.0)
            self.assertIsNotNone(error)
            self.assertTrue(error.startswith("infra-error:"))
            self.assertEqual(mock_call.call_count, 1)

    def test_empty_response_triggers_retry_and_succeeds(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        with mock.patch.object(
            live,
            "call_live_model",
            side_effect=[("", 5.0), ("final generated code", 8.0)],
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "final generated code")
            self.assertEqual(total, 13.0)  # 5.0 + 8.0
            self.assertIsNone(error)
            self.assertEqual(mock_call.call_count, 2)

    def test_whitespace_response_triggers_retry_and_succeeds(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        with mock.patch.object(
            live,
            "call_live_model",
            side_effect=[("   \n\t ", 3.2), ("patch content", 4.1)],
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "patch content")
            self.assertAlmostEqual(total, 7.3)
            self.assertIsNone(error)
            self.assertEqual(mock_call.call_count, 2)

    def test_empty_response_on_both_attempts(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        with mock.patch.object(
            live,
            "call_live_model",
            side_effect=[("", 2.5), ("", 3.5)],
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(route, "prompt")
            self.assertEqual(text, "")
            self.assertEqual(total, 6.0)
            self.assertIsNone(error)
            self.assertEqual(mock_call.call_count, 2)

    def test_retry_passes_image_paths_to_call_live_model(self) -> None:
        route = {"route_id": "xai/grok-vision", "provider": "xai", "model": "grok-vision"}
        with mock.patch.object(
            live,
            "call_live_model",
            return_value=("output", 2.0),
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(
                route,
                "prompt",
                image_paths=["/path/to/img.png"],
            )
            self.assertEqual(text, "output")
            self.assertIsNone(error)
            mock_call.assert_called_once_with(
                route,
                "prompt",
                system_prompt=None,
                max_tokens=live.DEFAULT_MAX_TOKENS,
                temperature=0.0,
                image_paths=["/path/to/img.png"],
            )


class TransientNetworkRetryTests(unittest.TestCase):
    def test_dns_failure_retries_then_succeeds(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        dns_error = urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
        with (
            mock.patch.object(
                live, "call_live_model", side_effect=[dns_error, ("recovered", 3.0)]
            ) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, total, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual(text, "recovered")
        self.assertIsNone(error)
        self.assertEqual(mock_call.call_count, 2)

    def test_persistent_dns_failure_is_infra_error(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        dns_error = urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
        with (
            mock.patch.object(live, "call_live_model", side_effect=dns_error) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual(text, "")
        self.assertTrue(error.startswith("infra-error:"))
        self.assertEqual(mock_call.call_count, live.MAX_TRANSIENT_ATTEMPTS)

    def test_auth_error_still_no_retry(self) -> None:
        route = {"route_id": "deepseek/x", "provider": "deepseek", "model": "x"}
        with mock.patch.object(
            live, "call_live_model", side_effect=ValueError("No API key")
        ) as mock_call:
            _, _, error = live.call_live_model_with_retry(route, "p")
        self.assertTrue(error.startswith("infra-error:"))
        self.assertEqual(mock_call.call_count, 1)

    def test_connection_reset_retries(self) -> None:
        route = {"route_id": "deepseek/x", "provider": "deepseek", "model": "x"}
        conn_error = ConnectionResetError("Connection reset by peer")
        with (
            mock.patch.object(live, "call_live_model", side_effect=[conn_error, ("recovered", 2.0)]) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual(text, "recovered")
        self.assertIsNone(error)
        self.assertEqual(mock_call.call_count, 2)


class RateLimitRetryTests(unittest.TestCase):
    def test_429_retries_then_succeeds(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        rate_limit = _make_http_error(self, "https://x", 429, "Too Many Requests")
        with (
            mock.patch.object(live, "call_live_model", side_effect=[rate_limit, ("ok", 2.0)]) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual((text, error), ("ok", None))
        self.assertEqual(mock_call.call_count, 2)

    def test_401_never_retried(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        auth = _make_http_error(self, "https://x", 401, "Unauthorized")
        with mock.patch.object(live, "call_live_model", side_effect=auth) as mock_call:
            _, _, error = live.call_live_model_with_retry(route, "p")
        self.assertTrue(error.startswith("infra-error:"))
        self.assertEqual(mock_call.call_count, 1)


class BackoffRetryTests(unittest.TestCase):
    def test_529_retries_with_backoff_then_succeeds(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        overload = _make_http_error(self, "https://x", 529, "Server Error")
        with (
            mock.patch.object(live, "call_live_model", side_effect=[overload, overload, ("ok", 2.0)]) as mock_call,
            mock.patch.object(live.time, "sleep") as sleep_mock,
        ):
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual((text, error), ("ok", None))
        self.assertEqual(mock_call.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_529_persistent_gives_up_after_max_attempts(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        overload = _make_http_error(self, "https://x", 529, "Server Error")
        with (
            mock.patch.object(live, "call_live_model", side_effect=overload) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual(text, "")
        self.assertTrue(error.startswith("infra-error:"))
        self.assertEqual(mock_call.call_count, live.MAX_TRANSIENT_ATTEMPTS)

    def test_retry_after_header_respected(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        headers = {"Retry-After": "7"}
        overload = _make_http_error(self, "https://x", 429, "Too Many", headers)
        with (
            mock.patch.object(live, "call_live_model", side_effect=[overload, ("ok", 1.0)]),
            mock.patch.object(live.time, "sleep") as sleep_mock,
        ):
            live.call_live_model_with_retry(route, "p")
        sleep_mock.assert_called_once_with(7.0)

    def test_timeout_still_two_attempts_no_extra(self) -> None:
        route = {"route_id": "deepseek/x", "provider": "deepseek", "model": "x"}
        with (
            mock.patch.object(live, "call_live_model", side_effect=TimeoutError("timed out")) as mock_call,
            mock.patch.object(live.time, "sleep"),
        ):
            _, _, error = live.call_live_model_with_retry(route, "p")
        self.assertTrue(error.startswith("timeout:"))
        self.assertEqual(mock_call.call_count, live.MAX_TRANSIENT_ATTEMPTS)


class ErrorClassificationTests(unittest.TestCase):
    def test_is_timeout_error(self) -> None:
        self.assertTrue(live.is_timeout_error(TimeoutError("request timed out")))
        self.assertTrue(live.is_timeout_error(socket.timeout("timed out")))
        self.assertTrue(live.is_timeout_error(urllib.error.URLError(socket.timeout())))
        self.assertTrue(live.is_timeout_error(RuntimeError("Task timeout reached")))
        self.assertFalse(live.is_timeout_error(ValueError("invalid format")))

    def test_is_transient_network_error(self) -> None:
        e429 = _make_http_error(self, "url", 429, "Rate limit")
        e503 = _make_http_error(self, "url", 503, "Unavailable")
        e529 = _make_http_error(self, "url", 529, "Overloaded")
        e401 = _make_http_error(self, "url", 401, "Unauthorized")
        e404 = _make_http_error(self, "url", 404, "Not Found")
        self.assertTrue(live.is_transient_network_error(e429))
        self.assertTrue(live.is_transient_network_error(e503))
        self.assertTrue(live.is_transient_network_error(e529))
        self.assertTrue(live.is_transient_network_error(urllib.error.URLError("Connection reset by peer")))
        self.assertTrue(live.is_transient_network_error(ConnectionResetError()))
        self.assertTrue(live.is_transient_network_error(socket.gaierror()))
        self.assertFalse(live.is_transient_network_error(e401))
        self.assertFalse(live.is_transient_network_error(e404))
        self.assertFalse(live.is_transient_network_error(ValueError("Bad payload")))


class ExtractCleanJsonTests(unittest.TestCase):
    def test_prose_then_fenced_json_extracts_fence(self) -> None:
        raw = 'Here is my analysis.\n\n```json\n{"a": 1, "b": 2}\n```\n\nHope this helps.'
        self.assertEqual(live.extract_clean_json_or_patch(raw), '{"a": 1, "b": 2}')

    def test_bare_json_with_leading_prose(self) -> None:
        raw = 'The answer is:\n{"schema_version": "v1", "x": 1}'
        self.assertEqual(live.extract_clean_json_or_patch(raw), '{"schema_version": "v1", "x": 1}')

    def test_multiple_fences_takes_last(self) -> None:
        raw = '```python\nprint("draft")\n```\nActually, final:\n```json\n{"final": true}\n```'
        self.assertEqual(live.extract_clean_json_or_patch(raw), '{"final": true}')

    def test_fenced_code_source_extracted(self) -> None:
        # cancel-async: model wraps source in a python fence; we score the source.
        raw = '```python\nasync def run_tasks(tasks, max_concurrent):\n    pass\n```'
        out = live.extract_clean_json_or_patch(raw)
        self.assertIn("async def run_tasks", out)
        self.assertNotIn("```", out)

    def test_plain_json_passthrough(self) -> None:
        raw = '{"schema_version": "x", "findings": []}'
        self.assertEqual(live.extract_clean_json_or_patch(raw), raw)

    def test_json_with_nested_braces_and_strings(self) -> None:
        raw = 'Result: {"a": {"b": "}"}, "c": [1,2]} done'
        self.assertEqual(live.extract_clean_json_or_patch(raw), '{"a": {"b": "}"}, "c": [1,2]}')

    def test_empty_passthrough(self) -> None:
        self.assertEqual(live.extract_clean_json_or_patch("   "), "")


if __name__ == "__main__":
    unittest.main()
