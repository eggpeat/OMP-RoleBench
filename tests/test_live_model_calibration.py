from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_model_calibration.py"
SPEC = importlib.util.spec_from_file_location("live_model_calibration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
live = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = live
SPEC.loader.exec_module(live)


class MockHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> MockHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_model_calibration.py"
SPEC = importlib.util.spec_from_file_location("live_model_calibration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
live = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = live
SPEC.loader.exec_module(live)


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


class BuildTaskPromptTests(unittest.TestCase):
    def test_binary_file_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_dir = Path(tmpdir)
            prompt_file = task_dir / "public" / "prompt.txt"
            prompt_file.parent.mkdir(parents=True, exist_ok=True)
            prompt_file.write_text("Solve the problem.", encoding="utf-8")

            ws_dir = task_dir / "public" / "workspace"
            ws_dir.mkdir(parents=True, exist_ok=True)

            # Text files that should be included
            (ws_dir / "main.py").write_text("print('hello')", encoding="utf-8")
            (ws_dir / "notes.txt").write_text("Important notes.", encoding="utf-8")

            # Binary extensions that must be excluded (.png, .jpg, .jpeg, .gif, .db, .wal, .bin, .wasm, .sqlite, .sqlite3)
            binary_files = [
                "image.png",
                "photo.jpg",
                "photo.jpeg",
                "anim.gif",
                "data.db",
                "data.wal",
                "tool.bin",
                "module.wasm",
                "store.sqlite",
                "store.sqlite3",
                "UPPER.PNG",
                "UPPER.DB",
            ]
            for bname in binary_files:
                (ws_dir / bname).write_bytes(b"\x00\x01\x02\x03\xff\xfe")

            nested_dir = ws_dir / "nested" / "sub"
            nested_dir.mkdir(parents=True, exist_ok=True)
            (nested_dir / "nested_binary.bin").write_bytes(b"\xde\xad\xbe\xef")
            (nested_dir / "nested_text.py").write_text("x = 1", encoding="utf-8")

            result = live.build_task_prompt(task_dir)

            self.assertIn("Solve the problem.", result)
            self.assertIn("--- File: main.py ---\nprint('hello')", result)
            self.assertIn("--- File: notes.txt ---\nImportant notes.", result)
            self.assertIn("--- File: nested/sub/nested_text.py ---\nx = 1", result)

            for bname in binary_files:
                self.assertNotIn(bname, result)
            self.assertNotIn("nested_binary.bin", result)


class CallLiveModelZaiTests(unittest.TestCase):
    def test_zai_text_block_used_when_present(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        payload = {
            "content": [
                {"type": "thinking", "thinking": "Internal thoughts..."},
                {"type": "text", "text": "def solution(): return 42"},
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"zai": {"key": "test-key"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Write solution")
            self.assertEqual(text, "def solution(): return 42")
            self.assertGreaterEqual(latency, 0.0)

    def test_zai_thinking_fallback_when_no_text_blocks(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        payload = {
            "content": [
                {"type": "thinking", "thinking": "Exhausted tokens while thinking: output = 100"},
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"zai": {"key": "test-key"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Write solution")
            self.assertEqual(text, "Exhausted tokens while thinking: output = 100")
            self.assertGreaterEqual(latency, 0.0)

    def test_zai_thinking_fallback_when_text_block_is_empty(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        payload = {
            "content": [
                {"type": "thinking", "thinking": "Thinking fallback text"},
                {"type": "text", "text": "   "},
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"zai": {"key": "test-key"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Write solution")
            self.assertEqual(text, "Thinking fallback text")
            self.assertGreaterEqual(latency, 0.0)


class CallLiveModelKimiTests(unittest.TestCase):
    def test_kimi_content_used_when_present(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "Kimi final answer",
                        "reasoning_content": "Kimi reasoning step",
                    }
                }
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"kimi-code": {"access": "test-token"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Solve this")
            self.assertEqual(text, "Kimi final answer")
            self.assertGreaterEqual(latency, 0.0)

    def test_kimi_reasoning_content_fallback_when_content_empty(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        payload = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "Kimi reasoning fallback output",
                    }
                }
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"kimi-code": {"access": "test-token"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Solve this")
            self.assertEqual(text, "Kimi reasoning fallback output")
            self.assertGreaterEqual(latency, 0.0)

    def test_kimi_reasoning_content_fallback_when_content_none(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        payload = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": "Kimi reasoning fallback from None",
                    }
                }
            ]
        }
        with (
            mock.patch.object(
                live,
                "get_provider_credentials",
                return_value={"kimi-code": {"access": "test-token"}},
            ),
            mock.patch.object(
                live.urllib.request,
                "urlopen",
                return_value=MockHTTPResponse(payload),
            ),
        ):
            text, latency = live.call_live_model(route, "Solve this")
            self.assertEqual(text, "Kimi reasoning fallback from None")
            self.assertGreaterEqual(latency, 0.0)

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

if __name__ == "__main__":
    unittest.main()


class ImageInjectionTests(unittest.TestCase):
    def _route(self, provider: str, model: str = "m") -> dict:
        return {"route_id": f"{provider}/{model}", "provider": provider, "model": model}

    def _creds(self) -> dict:
        return {
            "deepseek": {"key": "k"},
            "xai-oauth": {"access": "t"},
            "alibaba-token-plan": {"key": "t"},
            "kimi-code": {"access": "t"},
            "zai": {"key": "k"},
            "google": {"key": "k"},
        }

    def test_collect_task_images_finds_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "public" / "workspace"
            ws.mkdir(parents=True)
            (ws / "code.png").write_bytes(b"\x89PNG fake")
            (ws / "notes.txt").write_text("hello")
            images = live.collect_task_images(Path(tmp))
            self.assertEqual([(name, len(data)) for name, data in images], [("code.png", 9)])

    def test_xai_image_block_included(self) -> None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _ = live.call_live_model(
                self._route("xai-oauth", "grok-4.6"), "p", images=[("code.png", b"PNG")]
            )
        self.assertEqual(text, "ok")
        user = captured["data"]["messages"][1]["content"]
        self.assertEqual(user[0]["type"], "text")
        self.assertEqual(user[1]["type"], "image_url")
        self.assertTrue(user[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_gemini_inline_data_included(self) -> None:
        payload = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _ = live.call_live_model(
                self._route("google-antigravity", "gemini-3.7-flash"),
                "p",
                images=[("schematic.png", b"PNG")],
            )
        self.assertEqual(text, "ok")
        parts = captured["data"]["contents"][0]["parts"]
        self.assertIn("text", parts[0])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "image/png")

    def test_zai_anthropic_image_block(self) -> None:
        payload = {"content": [{"type": "text", "text": "ok"}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _ = live.call_live_model(
                self._route("zai", "glm-5.2"), "p", images=[("code.png", b"PNG")]
            )
        self.assertEqual(text, "ok")
        content = captured["data"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["type"], "base64")
        self.assertEqual(content[-1]["type"], "text")

    def test_alibaba_image_block_included(self) -> None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _ = live.call_live_model(
                self._route("alibaba-token-plan", "qwen3.8-max"),
                "p",
                images=[("code.png", b"PNG")],
            )
        self.assertEqual(text, "ok")
        user = captured["data"]["messages"][1]["content"]
        self.assertEqual(user[1]["type"], "image_url")

    def test_text_only_provider_gets_note_not_image(self) -> None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _ = live.call_live_model(
                self._route("deepseek", "deepseek-v4-flash"),
                "p",
                images=[("code.png", b"PNG")],
            )
        self.assertEqual(text, "ok")
        user = captured["data"]["messages"][1]["content"]
        self.assertIsInstance(user, str)
        self.assertIn("text-only", user)
        self.assertIn("code.png", user)

    def test_deepseek_uses_route_model_not_hardcoded(self) -> None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            live.call_live_model(self._route("deepseek", "deepseek-v4-flash"), "p")
        self.assertEqual(captured["data"]["model"], "deepseek-v4-flash")

    def test_retry_passes_images(self) -> None:
        payload = {"choices": [{"message": {"content": "ok"}}]}
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["data"] = json.loads(req.data.decode())
            return MockHTTPResponse(payload)

        with (
            mock.patch.object(live, "get_provider_credentials", return_value=self._creds()),
            mock.patch.object(live.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            text, _, err = live.call_live_model_with_retry(
                self._route("xai-oauth", "grok-4.6"), "p", images=[("code.png", b"PNG")]
            )
        self.assertIsNone(err)
        self.assertEqual(text, "ok")
        user = captured["data"]["messages"][1]["content"]
        self.assertEqual(user[1]["type"], "image_url")


class DeepseekReasoningFallbackTests(unittest.TestCase):
    def test_deepseek_reasoning_content_fallback(self) -> None:
        payload = {"choices": [{"message": {"content": "", "reasoning_content": "deep think answer"}}]}
        route = {"route_id": "deepseek/deepseek-v4-flash:max", "provider": "deepseek", "model": "deepseek-v4-flash"}
        with (
            mock.patch.object(live, "get_provider_credentials", return_value={"deepseek": {"key": "k"}}),
            mock.patch.object(live.urllib.request, "urlopen", return_value=MockHTTPResponse(payload)),
        ):
            text, _ = live.call_live_model(route, "p")
        self.assertEqual(text, "deep think answer")

    def test_deepseek_content_preferred_over_reasoning(self) -> None:
        payload = {"choices": [{"message": {"content": "final", "reasoning_content": "trace"}}]}
        route = {"route_id": "deepseek/deepseek-v4-flash:max", "provider": "deepseek", "model": "deepseek-v4-flash"}
        with (
            mock.patch.object(live, "get_provider_credentials", return_value={"deepseek": {"key": "k"}}),
            mock.patch.object(live.urllib.request, "urlopen", return_value=MockHTTPResponse(payload)),
        ):
            text, _ = live.call_live_model(route, "p")
        self.assertEqual(text, "final")

    def test_deepseek_null_content_does_not_crash(self) -> None:
        payload = {"choices": [{"message": {"content": None}}]}
        route = {"route_id": "deepseek/deepseek-v4-flash:max", "provider": "deepseek", "model": "deepseek-v4-flash"}
        with (
            mock.patch.object(live, "get_provider_credentials", return_value={"deepseek": {"key": "k"}}),
            mock.patch.object(live.urllib.request, "urlopen", return_value=MockHTTPResponse(payload)),
        ):
            text, _ = live.call_live_model(route, "p")
        self.assertEqual(text, "")


class TransientNetworkRetryTests(unittest.TestCase):
    def test_dns_failure_retries_then_succeeds(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        dns_error = live.urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
        with mock.patch.object(
            live, "call_live_model", side_effect=[dns_error, ("recovered", 3.0)]
        ) as mock_call:
            text, total, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual(text, "recovered")
        self.assertIsNone(error)
        self.assertEqual(mock_call.call_count, 2)

    def test_persistent_dns_failure_is_infra_error(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        dns_error = live.urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
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


class RateLimitRetryTests(unittest.TestCase):
    def test_429_retries_then_succeeds(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        rate_limit = live.urllib.error.HTTPError("https://x", 429, "Too Many Requests", {}, None)
        with mock.patch.object(
            live, "call_live_model", side_effect=[rate_limit, ("ok", 2.0)]
        ) as mock_call:
            text, _, error = live.call_live_model_with_retry(route, "p")
        self.assertEqual((text, error), ("ok", None))
        self.assertEqual(mock_call.call_count, 2)

    def test_401_never_retried(self) -> None:
        route = {"route_id": "kimi-code/k3:max", "provider": "kimi-code", "model": "k3"}
        auth = live.urllib.error.HTTPError("https://x", 401, "Unauthorized", {}, None)
        with mock.patch.object(live, "call_live_model", side_effect=auth) as mock_call:
            _, _, error = live.call_live_model_with_retry(route, "p")
        self.assertTrue(error.startswith("infra-error:"))
        self.assertEqual(mock_call.call_count, 1)


class BackoffRetryTests(unittest.TestCase):
    def test_529_retries_with_backoff_then_succeeds(self) -> None:
        route = {"route_id": "zai/glm-5.2", "provider": "zai", "model": "glm-5.2"}
        overload = live.urllib.error.HTTPError("https://x", 529, "Server Error", {}, None)
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
        overload = live.urllib.error.HTTPError("https://x", 529, "Server Error", {}, None)
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
        overload = live.urllib.error.HTTPError("https://x", 429, "Too Many", headers, None)
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
