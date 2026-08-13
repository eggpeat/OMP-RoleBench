from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = PRODUCT_ROOT / "scripts/rolebench-runsc-wrapper"


def load_wrapper() -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader(
        "rolebench_runsc_wrapper_test",
        str(WRAPPER_PATH),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load runsc wrapper spec")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RunscWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wrapper = load_wrapper()

    def test_runtime_argv_replaces_systemd_cgroups_without_a_shell(self) -> None:
        argv = self.wrapper._runsc_argv(
            Path("/opt/gvisor/runsc"),
            [
                "--root",
                "/run/runsc",
                "--systemd-cgroup",
                "create",
                "--bundle",
                "/run/bundle",
                "a" * 64,
            ],
        )

        self.assertEqual(argv[0], "/opt/gvisor/runsc")
        self.assertEqual(argv.count("--ignore-cgroups"), 1)
        self.assertIn("--gvisor-marker-file", argv)
        self.assertIn("--sidecar-release-enforcement-policy=ALWAYS", argv)
        self.assertNotIn("--systemd-cgroup", argv)
        self.assertEqual(argv[-1], "a" * 64)

    def test_doctor_returns_json_when_host_uid_resolution_fails(self) -> None:
        with (
            mock.patch.object(
                self.wrapper,
                "_runsc_path",
                side_effect=RuntimeError("runsc missing"),
            ),
            mock.patch.object(
                self.wrapper,
                "_host_uid",
                side_effect=RuntimeError("uid unavailable"),
            ),
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(self.wrapper._doctor(), 1)

        report = output.call_args.args[0]
        self.assertIn('"ready":false', report)
        self.assertIn('"cgroup_parent":null', report)

    def test_oci_limits_map_to_delegated_cgroup_v2_files(self) -> None:
        values = self.wrapper._resource_values(
            {
                "linux": {
                    "resources": {
                        "memory": {"limit": 67_108_864, "swap": 67_108_864},
                        "pids": {"limit": 512},
                        "cpu": {"quota": 400_000, "period": 100_000},
                    }
                }
            }
        )

        self.assertEqual(
            values,
            {
                "memory.max": "67108864",
                "memory.swap.max": "0",
                "pids.max": "512",
                "cpu.max": "400000 100000",
            },
        )

    def test_missing_or_unbounded_oci_limit_fails_closed(self) -> None:
        invalid = {
            "linux": {
                "resources": {
                    "memory": {"limit": 67_108_864, "swap": 67_108_864},
                    "pids": {"limit": 512},
                    "cpu": {},
                }
            }
        }
        with self.assertRaisesRegex(RuntimeError, "must be positive integers"):
            self.wrapper._resource_values(invalid)

    def test_container_id_cannot_escape_delegated_scope(self) -> None:
        parent = Path("/sys/fs/cgroup/user.slice")
        scope = self.wrapper._scope_path(parent, "a" * 64)
        self.assertEqual(
            scope,
            parent / f"rolebench-runsc-{'a' * 48}.scope",
        )
        for invalid in ("short", "../escape", "A" * 64):
            with self.subTest(container_id=invalid):
                with self.assertRaisesRegex(RuntimeError, "64-character lowercase hex"):
                    self.wrapper._scope_path(parent, invalid)

    def test_scope_limits_are_written_and_process_is_attached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            with mock.patch.object(self.wrapper, "_enable_controllers"):
                scope = self.wrapper._create_scope(
                    parent,
                    "b" * 64,
                    {
                        "memory.max": "1048576",
                        "memory.swap.max": "0",
                        "pids.max": "512",
                        "cpu.max": "100000 100000",
                    },
                )
            self.assertEqual((scope / "memory.max").read_text(), "1048576")
            self.assertEqual((scope / "memory.swap.max").read_text(), "0")
            self.assertEqual((scope / "pids.max").read_text(), "512")
            self.assertEqual((scope / "cpu.max").read_text(), "100000 100000")
            self.assertGreater(int((scope / "cgroup.procs").read_text()), 0)

    def test_partial_scope_setup_cleans_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            container_id = "f" * 64
            with (
                mock.patch.object(self.wrapper, "_enable_controllers"),
                mock.patch.object(
                    self.wrapper,
                    "_write",
                    side_effect=OSError("kernel rejected value"),
                ),
                mock.patch.object(self.wrapper, "_cleanup_scope") as cleanup,
            ):
                with self.assertRaisesRegex(OSError, "kernel rejected value"):
                    self.wrapper._create_scope(
                        parent,
                        container_id,
                        {"memory.max": "1048576"},
                    )

            cleanup.assert_called_once_with(parent, container_id)

    def test_scope_cleanup_retries_after_killing_remaining_processes(self) -> None:
        scope = mock.MagicMock()
        kill_file = mock.MagicMock()
        scope.__truediv__.return_value = kill_file
        scope.rmdir.side_effect = [OSError("busy"), None]
        with (
            mock.patch.object(self.wrapper, "_scope_path", return_value=scope),
            mock.patch.object(self.wrapper, "_write") as write_value,
            mock.patch.object(
                self.wrapper.time,
                "monotonic",
                side_effect=[0.0, 0.1],
            ),
            mock.patch.object(self.wrapper.time, "sleep") as sleep,
        ):
            self.wrapper._cleanup_scope(Path("/cgroup"), "c" * 64)

        self.assertEqual(scope.rmdir.call_count, 2)
        write_value.assert_called_once_with(kill_file, "1")
        sleep.assert_called_once_with(0.05)

    def test_scope_cleanup_fails_closed_after_deadline(self) -> None:
        scope = mock.MagicMock()
        scope.rmdir.side_effect = OSError("busy")
        with (
            mock.patch.object(self.wrapper, "_scope_path", return_value=scope),
            mock.patch.object(self.wrapper, "_write"),
            mock.patch.object(
                self.wrapper.time,
                "monotonic",
                side_effect=[0.0, 5.0],
            ),
            mock.patch.object(self.wrapper.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "cgroup cleanup failed"):
                self.wrapper._cleanup_scope(Path("/cgroup"), "d" * 64)

        sleep.assert_not_called()

    def test_failed_runsc_create_cleans_the_delegated_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            (bundle / "config.json").write_text("{}")
            container_id = "e" * 64
            completed = mock.Mock(returncode=1)
            with (
                mock.patch.object(
                    self.wrapper.sys,
                    "argv",
                    [
                        str(WRAPPER_PATH),
                        "create",
                        "--bundle",
                        str(bundle),
                        container_id,
                    ],
                ),
                mock.patch.object(
                    self.wrapper,
                    "_runsc_path",
                    return_value=Path("/opt/gvisor/runsc"),
                ),
                mock.patch.object(
                    self.wrapper,
                    "_resource_values",
                    return_value={"pids.max": "1"},
                ),
                mock.patch.object(self.wrapper, "_create_scope"),
                mock.patch.object(
                    self.wrapper.subprocess,
                    "run",
                    return_value=completed,
                ),
                mock.patch.object(self.wrapper, "_cleanup_scope") as cleanup,
            ):
                self.assertEqual(self.wrapper.main(), 1)

        cleanup.assert_called_once_with(
            self.wrapper._cgroup_parent(self.wrapper._host_uid()),
            container_id,
        )


if __name__ == "__main__":
    unittest.main()
