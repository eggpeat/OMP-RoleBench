#!/usr/bin/env python3
"""Source-separated runner for the cancel-async-tasks diagnostic."""

from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import types
from typing import Any, Awaitable, Callable

MAX_ARTIFACT_BYTES: int = 128 * 1024
MAX_CHILD_OUTPUT_BYTES: int = 64 * 1024
WORKSPACE_ROOT: Path = Path(os.environ.get("ROLEBENCH_TASK_WORKSPACE", "/workspace"))
DEFAULT_SOURCE: Path = Path("/opt/rolebench/task/public/workspace")
SNAPSHOT_SCHEMA_VERSION: str = "rolebench.source-separated-runner-snapshot/v1"
OBSERVATION_AUTHORITY: str = "source-separated-service"

_ALLOWED_ASYNCIO_ATTRIBUTES = frozenset(
    {
        "ALL_COMPLETED",
        "BoundedSemaphore",
        "CancelledError",
        "Event",
        "FIRST_COMPLETED",
        "FIRST_EXCEPTION",
        "Future",
        "Lock",
        "Queue",
        "QueueEmpty",
        "QueueFull",
        "Semaphore",
        "Task",
        "TaskGroup",
        "TimeoutError",
        "as_completed",
        "create_task",
        "current_task",
        "ensure_future",
        "gather",
        "get_running_loop",
        "shield",
        "sleep",
        "timeout",
        "wait",
        "wait_for",
    }
)
_ALLOWED_TYPING_ATTRIBUTES = frozenset(
    {
        "Any",
        "Awaitable",
        "Callable",
        "Coroutine",
        "Dict",
        "Iterable",
        "List",
        "Optional",
        "Sequence",
        "Set",
        "Tuple",
        "TypeVar",
        "Union",
    }
)
_SAFE_BUILTIN_TYPE_NAMES = frozenset(
    {
        "bool",
        "bytes",
        "complex",
        "dict",
        "float",
        "int",
        "list",
        "object",
        "set",
        "str",
        "tuple",
    }
)
_ALLOWED_IMPORTS = frozenset({"asyncio", "typing"})
_ALLOWED_FROM_IMPORTS = {
    "__future__": frozenset({"annotations"}),
    "asyncio": _ALLOWED_ASYNCIO_ATTRIBUTES,
    "typing": _ALLOWED_TYPING_ATTRIBUTES,
}
_FORBIDDEN_NAMES = frozenset(
    {
        "__builtins__",
        "__import__",
        "breakpoint",
        "builtins",
        "compile",
        "ctypes",
        "delattr",
        "dir",
        "eval",
        "exec",
        "exit",
        "gc",
        "getattr",
        "globals",
        "hasattr",
        "help",
        "importlib",
        "input",
        "inspect",
        "io",
        "KeyboardInterrupt",
        "locals",
        "multiprocessing",
        "nt",
        "open",
        "os",
        "posix",
        "print",
        "quit",
        "setattr",
        "signal",
        "socket",
        "subprocess",
        "sys",
        "SystemExit",
        "threading",
        "vars",
    }
)
_FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__bases__",
        "__builtins__",
        "__class__",
        "__closure__",
        "__code__",
        "__dict__",
        "__globals__",
        "__import__",
        "__mro__",
        "__subclasses__",
        "_exit",
        "cr_code",
        "cr_frame",
        "delattr",
        "eval",
        "exec",
        "exit",
        "f_back",
        "f_builtins",
        "f_code",
        "f_globals",
        "f_locals",
        "fork",
        "get_coro",
        "get_loop",
        "getattr",
        "gi_code",
        "gi_frame",
        "hasattr",
        "kill",
        "killpg",
        "popen",
        "setattr",
        "system",
        "tb_frame",
    }
)
_FORBIDDEN_NODES = (
    ast.ClassDef,
    ast.Delete,
    ast.MatchClass,
    ast.MatchMapping,
    ast.MatchStar,
    ast.MatchAs,
)

class SourceValidationError(ValueError):
    """Candidate source violates the deliberately narrow execution profile."""


class RemoteTaskError(RuntimeError):
    """A source-separated observed task failed."""


class _InjectedTaskFailure(RuntimeError):
    """Trusted observer-side failure used by the exception scenario."""


def _init_workspace(workspace_dir: Path, source_dir: Path) -> None:
    """Initialize the mounted runner workspace from the public starter tree."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for child in workspace_dir.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            raise RuntimeError(f"unsupported workspace entry: {child.name}")
    if not source_dir.is_dir():
        return
    for source_child in source_dir.iterdir():
        target_child = workspace_dir / source_child.name
        if source_child.is_symlink():
            raise RuntimeError(f"unsupported source entry: {source_child.name}")
        if source_child.is_file():
            shutil.copyfile(source_child, target_child)
        elif source_child.is_dir():
            shutil.copytree(source_child, target_child)
        else:
            raise RuntimeError(f"unsupported source entry: {source_child.name}")


def _is_literal(node: ast.AST | None) -> bool:
    if node is None or isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_literal(item) for item in node.keys) and all(
            _is_literal(item) for item in node.values
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant) and isinstance(
            node.operand.value, (int, float, complex)
        )
    return False

def _is_safe_type_expression(
    node: ast.AST,
    typing_modules: set[str] | frozenset[str],
    safe_type_names: set[str] | frozenset[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in safe_type_names
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id in typing_modules
            and node.attr in _ALLOWED_TYPING_ATTRIBUTES
        )
    if isinstance(node, ast.Constant):
        return (
            node.value is None
            or node.value is Ellipsis
            or isinstance(node.value, str)
        )
    if isinstance(node, ast.Subscript):
        return _is_safe_type_expression(
            node.value, typing_modules, safe_type_names
        ) and _is_safe_type_expression(
            node.slice, typing_modules, safe_type_names
        )
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(
            _is_safe_type_expression(elt, typing_modules, safe_type_names)
            for elt in node.elts
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_safe_type_expression(
            node.left, typing_modules, safe_type_names
        ) and _is_safe_type_expression(
            node.right, typing_modules, safe_type_names
        )
    return False


def _is_safe_type_alias_value(
    node: ast.AST,
    typing_modules: set[str] | frozenset[str],
    safe_type_names: set[str] | frozenset[str],
) -> bool:
    return isinstance(
        node, (ast.Name, ast.Attribute, ast.Subscript, ast.BinOp)
    ) and _is_safe_type_expression(node, typing_modules, safe_type_names)


def _validate_candidate_source(source: str) -> ast.Module:
    """Reject code that could reach process, filesystem, or introspection APIs."""
    try:
        tree = ast.parse(source, filename="run.py", mode="exec")
    except (SyntaxError, ValueError) as exc:
        raise SourceValidationError(f"run.py is not valid Python: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if node not in tree.body:
                raise SourceValidationError("nested import statements are forbidden")

    module_aliases: dict[str, frozenset[str]] = {}
    typing_modules: set[str] = set()
    safe_type_names: set[str] = set(_SAFE_BUILTIN_TYPE_NAMES)
    run_defs: list[ast.AsyncFunctionDef] = []
    source_validated_type_nodes: set[ast.AST] = set()

    def clear_name_binding(name: str) -> None:
        module_aliases.pop(name, None)
        typing_modules.discard(name)
        safe_type_names.discard(name)

    def clear_target_bindings(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            clear_name_binding(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                clear_target_bindings(element)
        elif isinstance(target, ast.Starred):
            clear_target_bindings(target.value)

    def target_mutates_attribute(target: ast.AST) -> bool:
        if isinstance(target, ast.Attribute):
            return True
        if isinstance(target, ast.Starred):
            return target_mutates_attribute(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(target_mutates_attribute(element) for element in target.elts)
        return False

    def target_has_effectful_store(target: ast.AST) -> bool:
        if isinstance(target, (ast.Attribute, ast.Subscript)):
            return True
        if isinstance(target, ast.Starred):
            return target_has_effectful_store(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(target_has_effectful_store(element) for element in target.elts)
        return False

    for statement in tree.body:
        if isinstance(statement, ast.Expr):
            if not isinstance(statement.value, ast.Constant) or not isinstance(
                statement.value.value, str
            ):
                raise SourceValidationError(
                    "module-level expressions other than a docstring are not allowed"
                )
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name not in _ALLOWED_IMPORTS:
                    raise SourceValidationError(f"import {alias.name!r} is not allowed")
                bound = alias.asname or alias.name
                clear_name_binding(bound)
                if alias.name == "asyncio":
                    module_aliases[bound] = _ALLOWED_ASYNCIO_ATTRIBUTES
                else:
                    module_aliases[bound] = _ALLOWED_TYPING_ATTRIBUTES
                    typing_modules.add(bound)
        elif isinstance(statement, ast.ImportFrom):
            if statement.level != 0 or statement.module not in _ALLOWED_FROM_IMPORTS:
                raise SourceValidationError(
                    f"import from {statement.module!r} is not allowed"
                )
            allowed = _ALLOWED_FROM_IMPORTS[statement.module]
            for alias in statement.names:
                if alias.name == "*" or alias.name not in allowed:
                    raise SourceValidationError(
                        f"import of {alias.name!r} from {statement.module!r} is not allowed"
                    )
                bound = alias.asname or alias.name
                clear_name_binding(bound)
                if statement.module == "typing":
                    safe_type_names.add(bound)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.decorator_list:
                raise SourceValidationError("function decorators are not allowed")
            defaults = list(statement.args.defaults) + [
                value for value in statement.args.kw_defaults if value is not None
            ]
            if any(not _is_literal(value) for value in defaults):
                raise SourceValidationError("function defaults must be literals")
            clear_name_binding(statement.name)
            if isinstance(statement, ast.AsyncFunctionDef) and statement.name == "run_tasks":
                run_defs.append(statement)
        elif isinstance(statement, ast.Assign):
            if any(target_has_effectful_store(target) for target in statement.targets):
                raise SourceValidationError(
                    "module-level assignment targets must only bind names"
                )
            is_literal = _is_literal(statement.value)
            is_type_alias = (
                not is_literal
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and _is_safe_type_alias_value(
                    statement.value,
                    typing_modules,
                    safe_type_names,
                )
            )
            if not is_literal and not is_type_alias:
                raise SourceValidationError(
                    "module-level assignments must contain only literal values or safe type aliases"
                )
            for target in statement.targets:
                clear_target_bindings(target)
            if is_type_alias:
                source_validated_type_nodes.update(ast.walk(statement.value))
                safe_type_names.add(statement.targets[0].id)
        elif isinstance(statement, ast.AnnAssign):
            if target_has_effectful_store(statement.target):
                raise SourceValidationError(
                    "module-level assignment targets must only bind names"
                )
            is_literal = _is_literal(statement.value)
            is_type_alias = (
                not is_literal
                and isinstance(statement.target, ast.Name)
                and statement.value is not None
                and _is_safe_type_alias_value(
                    statement.value,
                    typing_modules,
                    safe_type_names,
                )
            )
            if not is_literal and not is_type_alias:
                raise SourceValidationError(
                    "module-level assignments must contain only literal values or safe type aliases"
                )
            if statement.value is not None:
                clear_target_bindings(statement.target)
            if is_type_alias:
                source_validated_type_nodes.update(ast.walk(statement.value))
                safe_type_names.add(statement.target.id)
        else:
            raise SourceValidationError(
                f"module-level {type(statement).__name__} statements are not allowed"
            )

    if len(run_defs) != 1:
        raise SourceValidationError("run.py must define exactly one async run_tasks function")
    run_def = run_defs[0]
    args = run_def.args
    if (
        args.posonlyargs
        or [arg.arg for arg in args.args] != ["tasks", "max_concurrent"]
        or args.vararg is not None
        or args.kwarg is not None
        or args.kwonlyargs
        or args.defaults
    ):
        raise SourceValidationError(
            "run_tasks must have standard parameters tasks and max_concurrent without positional-only or keyword-only markers"
        )

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            raise SourceValidationError(f"{type(node).__name__} is not allowed")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("__"):
                raise SourceValidationError("dunder function names are not allowed")
            if node.decorator_list:
                raise SourceValidationError("function decorators are not allowed")
        if isinstance(node, ast.Name):
            if node.id == "type":
                call = parents.get(node)
                comparison = (
                    parents.get(call) if isinstance(call, ast.Call) else None
                )
                if not (
                    isinstance(call, ast.Call)
                    and call.func is node
                    and len(call.args) == 1
                    and not isinstance(call.args[0], ast.Starred)
                    and not call.keywords
                    and isinstance(comparison, ast.Compare)
                    and comparison.left is call
                    and len(comparison.ops) == 1
                    and isinstance(comparison.ops[0], (ast.Is, ast.IsNot))
                    and len(comparison.comparators) == 1
                    and isinstance(comparison.comparators[0], ast.Name)
                    and comparison.comparators[0].id == "int"
                ):
                    raise SourceValidationError(
                        "type may only be used in a direct exact-type identity check"
                    )
            if node.id.startswith("__") or node.id in _FORBIDDEN_NAMES:
                raise SourceValidationError(f"name {node.id!r} is not allowed")
            if (
                isinstance(node.ctx, ast.Load)
                and node.id in module_aliases
                and node not in source_validated_type_nodes
            ):
                parent = parents.get(node)
                if not (isinstance(parent, ast.Attribute) and parent.value is node):
                    raise SourceValidationError(
                        f"module {node.id!r} may only be used for approved attributes"
                    )
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRIBUTES:
                raise SourceValidationError(f"attribute {node.attr!r} is not allowed")
            base = node.value
            while isinstance(base, ast.Attribute):
                if base.attr.startswith("_") or base.attr in _FORBIDDEN_ATTRIBUTES:
                    raise SourceValidationError(f"attribute {base.attr!r} is not allowed")
                base = base.value
            if (
                isinstance(base, ast.Name)
                and base.id in module_aliases
                and node not in source_validated_type_nodes
            ):
                if node.value is not base or node.attr not in module_aliases[base.id]:
                    raise SourceValidationError(
                        f"attribute {node.attr!r} is not approved for module {base.id!r}"
                    )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets: list[ast.AST]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            if any(target_mutates_attribute(target) for target in targets):
                raise SourceValidationError("attribute mutation is not allowed")

    return tree


def _load_candidate(run_file: Path) -> Callable[..., Awaitable[None]]:
    source = run_file.read_text(encoding="utf-8")
    tree = _validate_candidate_source(source)
    code = compile(
        tree,
        str(run_file),
        "exec",
        flags=__import__("__future__").annotations.compiler_flag,
        dont_inherit=True,
    )
    module = types.ModuleType("candidate_run")
    module.__dict__["__builtins__"] = builtins.__dict__
    exec(code, module.__dict__)
    fn = module.__dict__.get("run_tasks")
    if not callable(fn):
        raise SourceValidationError("run_tasks is not callable")
    return fn


class _EventClient:
    """Child-side client. Candidate callbacks can only reach this narrow protocol."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._sequence = 0
        self._reader_task = asyncio.create_task(self._read_responses())

    @property
    def reader_task(self) -> asyncio.Task[None]:
        return self._reader_task

    async def _send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            self._writer.write(payload)
            await self._writer.drain()

    async def _read_responses(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    raise RuntimeError("observer service closed unexpectedly")
                message = json.loads(line.decode("utf-8"))
                request_id = message.get("request_id") if isinstance(message, dict) else None
                future = self._pending.pop(str(request_id), None)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(str(exc)))
            self._pending.clear()

    async def invoke(self, task_id: int) -> None:
        self._sequence += 1
        request_id = f"{task_id}-{self._sequence}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        await self._send({"op": "start", "request_id": request_id, "task_id": task_id})
        try:
            response = await asyncio.shield(future)
        except asyncio.CancelledError:
            if not future.done():
                await self._send({"op": "cancel", "request_id": request_id})
                try:
                    await asyncio.shield(future)
                except BaseException:
                    pass
            raise
        status = response.get("status")
        if status == "completed":
            return
        if status == "cancelled":
            raise asyncio.CancelledError
        if status == "error":
            raise RemoteTaskError(str(response.get("error_type") or "observed task failed"))
        raise RuntimeError("observer returned an invalid task status")

    async def close(self) -> None:
        self._reader_task.cancel()
        await asyncio.gather(self._reader_task, return_exceptions=True)
        self._writer.close()
        await self._writer.wait_closed()


class _RemoteTask:
    __slots__ = ("_client", "_task_id")

    def __init__(self, client: _EventClient, task_id: int) -> None:
        self._client = client
        self._task_id = task_id

    async def __call__(self) -> None:
        await self._client.invoke(self._task_id)


def _has_remote_task_error(exc: BaseException) -> bool:
    if type(exc).__name__ == "RemoteTaskError":
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_has_remote_task_error(sub) for sub in exc.exceptions)
    return False


async def _invoke_candidate(
    fn: Callable[..., Awaitable[None]],
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: object,
    cancel_after: float | None = None,
) -> dict[str, Any]:
    candidate_task = asyncio.create_task(fn(tasks, max_concurrent))
    cancel_requested = False
    cancel_acknowledged = False
    try:
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            cancel_requested = True
            cancel_acknowledged = candidate_task.cancel()
        await candidate_task
        return {
            "status": "returned",
            "error_type": None,
            "cancel_requested": cancel_requested,
            "cancel_acknowledged": cancel_acknowledged,
            "contains_remote_task_error": False,
        }
    except asyncio.CancelledError:
        return {
            "status": "cancelled",
            "error_type": "CancelledError",
            "cancel_requested": cancel_requested,
            "cancel_acknowledged": cancel_acknowledged,
            "contains_remote_task_error": False,
        }
    except BaseException as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "cancel_requested": cancel_requested,
            "cancel_acknowledged": cancel_acknowledged,
            "contains_remote_task_error": _has_remote_task_error(exc),
        }


async def _cancel_candidate_leaks(client: _EventClient) -> int:
    current = asyncio.current_task()
    excluded = {current, client.reader_task}
    leaked = [
        task
        for task in asyncio.all_tasks()
        if task not in excluded and not task.done()
    ]
    for task in leaked:
        task.cancel()
    if leaked:
        await asyncio.gather(*leaked, return_exceptions=True)
    return len(leaked)


async def _child_async(event_fd: int, config: dict[str, Any]) -> dict[str, Any]:
    event_socket = socket.socket(fileno=event_fd)
    event_socket.setblocking(False)
    reader, writer = await asyncio.open_connection(sock=event_socket)
    client = _EventClient(reader, writer)
    result: dict[str, Any]
    try:
        fn = _load_candidate(WORKSPACE_ROOT / "run.py")
        kind = config.get("kind")
        if kind == "empty":
            result = await _invoke_candidate(fn, [], 3)
        elif kind == "invalid":
            checks: dict[str, bool] = {}
            callbacks = [_RemoteTask(client, 0)]
            for name, value in (
                ("zero_rejected", 0),
                ("negative_rejected", -2),
                ("string_rejected", "2"),
                ("bool_rejected", True),
                ("float_rejected", 2.5),
                ("float_integral_rejected", 1.0),
                ("nan_rejected", float("nan")),
                ("pos_inf_rejected", float("inf")),
                ("neg_inf_rejected", float("-inf")),
                ("none_rejected", None),
            ):
                call_result = await _invoke_candidate(fn, callbacks, value)
                checks[name] = (
                    call_result.get("status") == "error"
                    and call_result.get("error_type") == "ValueError"
                )
            result = {"status": "returned", "error_type": None, "checks": checks}
        elif kind == "batch":
            task_specs = config.get("tasks")
            if not isinstance(task_specs, list):
                raise ValueError("batch task specs missing")
            callbacks = [_RemoteTask(client, index) for index in range(len(task_specs))]
            result = await _invoke_candidate(
                fn,
                callbacks,
                config.get("max_concurrent"),
                float(config["cancel_after"])
                if config.get("cancel_after") is not None
                else None,
            )
        else:
            raise ValueError("unknown child scenario kind")
        result["leaked_tasks"] = await _cancel_candidate_leaks(client)
        result["candidate_pid"] = os.getpid()
        return result
    finally:
        await client.close()


def _child_main(event_fd: int) -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_CHILD_OUTPUT_BYTES + 1)
        if not raw or len(raw) > MAX_CHILD_OUTPUT_BYTES:
            raise ValueError("invalid child command size")
        config = json.loads(raw.decode("utf-8"))
        if not isinstance(config, dict):
            raise ValueError("child command must be an object")
        result = asyncio.run(_child_async(event_fd, config))
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except BaseException as exc:
        failure = {
            "candidate_pid": os.getpid(),
            "error_type": type(exc).__name__,
            "leaked_tasks": -1,
            "status": "harness-error",
        }
        sys.stdout.write(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 1


class _ObserverService:
    """Trusted parent-side executor and lifecycle recorder."""

    def __init__(self, sock: socket.socket, task_specs: list[dict[str, Any]]) -> None:
        self._sock = sock
        self._specs = task_specs
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self.records: dict[int, dict[str, Any]] = {}
        self.invocations: dict[int, int] = {}
        self.events: list[dict[str, Any]] = []
        self.violations: list[str] = []
        self.active = 0
        self.max_active = 0

    def _record_event(self, event: str, task_id: int) -> None:
        self.events.append({"event": event, "task_id": task_id})

    async def _send(self, message: dict[str, Any]) -> None:
        if self._writer is None:
            return
        data = json.dumps(message, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        try:
            async with self._write_lock:
                self._writer.write(data)
                await self._writer.drain()
        except (BrokenPipeError, ConnectionError):
            self.violations.append("observer response channel closed")

    async def _execute(self, request_id: str, task_id: int) -> None:
        spec = self._specs[task_id]
        now = asyncio.get_running_loop().time()
        record = {
            "started_at": now,
            "finished_at": None,
            "cleaned_at": None,
            "status": "running",
        }
        self.records[task_id] = record
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self._record_event("started", task_id)
        status = "completed"
        error_type: str | None = None
        try:
            await asyncio.sleep(float(spec.get("duration", 0.05)))
            if spec.get("failure") is True:
                raise _InjectedTaskFailure("observer-injected task failure")
            record["finished_at"] = asyncio.get_running_loop().time()
            record["status"] = "completed"
            self._record_event("finished", task_id)
        except asyncio.CancelledError:
            status = "cancelled"
            record["status"] = status
            self._record_event("cancelled", task_id)
        except _InjectedTaskFailure as exc:
            status = "error"
            error_type = type(exc).__name__
            record["status"] = status
            self._record_event("failed", task_id)
        finally:
            await asyncio.sleep(float(spec.get("cleanup", 0.01)))
            record["cleaned_at"] = asyncio.get_running_loop().time()
            self.active -= 1
            self._record_event("cleaned", task_id)
            await self._send(
                {
                    "error_type": error_type,
                    "request_id": request_id,
                    "status": status,
                }
            )

    async def serve(self) -> None:
        reader, writer = await asyncio.open_connection(sock=self._sock)
        self._writer = writer
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    self.violations.append("malformed observer request")
                    continue
                if not isinstance(message, dict):
                    self.violations.append("non-object observer request")
                    continue
                op = message.get("op")
                request_id = message.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    self.violations.append("observer request lacks request_id")
                if op == "start":
                    task_id = message.get("task_id")
                    if (
                        not isinstance(task_id, int)
                        or isinstance(task_id, bool)
                        or task_id < 0
                        or task_id >= len(self._specs)
                    ):
                        self.violations.append("observer start has invalid task_id")
                        continue
                    self.invocations[task_id] = self.invocations.get(task_id, 0) + 1
                    if self.invocations[task_id] > 1 or task_id in self.records or request_id in self._jobs:
                        self.violations.append(f"task callback {task_id} invoked more than once")
                        continue
                    job = asyncio.create_task(self._execute(request_id, task_id))
                    self._jobs[request_id] = job
                elif op == "cancel":
                    job = self._jobs.get(request_id)
                    if job is None or job.done():
                        self.violations.append("observer cancel has no active request")
                    else:
                        job.cancel()
                else:
                    self.violations.append("unknown observer operation")
        finally:
            pending = [job for job in self._jobs.values() if not job.done()]
            for job in pending:
                job.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            writer.close()
            await writer.wait_closed()


def _common_observation(
    *,
    scenario_id: str,
    service: _ObserverService,
    child: dict[str, Any],
) -> dict[str, Any]:
    candidate_pid = child.get("candidate_pid")
    events_digest = hashlib.sha256(
        json.dumps(service.events, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "scenario_id": scenario_id,
        "observation_authority": OBSERVATION_AUTHORITY,
        "observer_pid": os.getpid(),
        "candidate_pid": candidate_pid,
        "event_digest_sha256": events_digest,
        "protocol_violations": list(service.violations),
    }


async def _read_bounded(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError(f"child output exceeded stream limit of {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _run_child_scenario(
    scenario_id: str,
    config: dict[str, Any],
    *,
    timeout: float = 4.0,
) -> tuple[dict[str, Any], _ObserverService, float, str | None]:
    task_specs = config.get("tasks")
    if not isinstance(task_specs, list):
        task_specs = []
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent_sock.setblocking(False)
    child_sock.setblocking(False)
    child_fd = child_sock.fileno()
    service = _ObserverService(parent_sock, task_specs)
    serve_task = asyncio.create_task(service.serve())
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        str(Path(__file__).resolve()),
        "--child",
        str(child_fd),
        cwd=str(WORKSPACE_ROOT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=(child_fd,),
        start_new_session=True,
    )
    child_sock.close()
    payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    error: str | None = None
    child: dict[str, Any]
    try:
        if process.stdin is not None:
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

        stdout_task = asyncio.create_task(_read_bounded(process.stdout, MAX_CHILD_OUTPUT_BYTES))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr, MAX_CHILD_OUTPUT_BYTES))
        stdout, stderr = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task),
            timeout=timeout,
        )
        await asyncio.wait_for(process.wait(), timeout=1.0)
        received_at = asyncio.get_running_loop().time()
        parsed = json.loads(stdout.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("candidate child output is not an object")
        child = parsed
        if process.returncode != 0:
            error = f"candidate child exited {process.returncode}"
        if stderr:
            error = "candidate child wrote to stderr"
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        received_at = asyncio.get_running_loop().time()
        child = {
            "candidate_pid": process.pid,
            "error_type": "TimeoutError",
            "leaked_tasks": -1,
            "status": "harness-timeout",
        }
        error = "candidate child timed out"
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if process.returncode is None:
            process.kill()
            await process.wait()
        received_at = asyncio.get_running_loop().time()
        child = {
            "candidate_pid": process.pid,
            "error_type": type(exc).__name__,
            "leaked_tasks": -1,
            "status": "harness-error",
        }
        error = str(exc)
    try:
        await asyncio.wait_for(serve_task, timeout=1.0)
    except asyncio.TimeoutError:
        serve_task.cancel()
        await asyncio.gather(serve_task, return_exceptions=True)
        error = error or "observer service did not terminate"
    return child, service, received_at, error


def _source_separated(common: dict[str, Any]) -> bool:
    observer_pid = common.get("observer_pid")
    candidate_pid = common.get("candidate_pid")
    return (
        common.get("observation_authority") == OBSERVATION_AUTHORITY
        and isinstance(observer_pid, int)
        and isinstance(candidate_pid, int)
        and observer_pid > 0
        and candidate_pid > 0
        and observer_pid != candidate_pid
        and common.get("protocol_violations") == []
    )


async def _empty_scenario() -> dict[str, Any]:
    child, service, _, error = await _run_child_scenario("empty_tasks", {"kind": "empty"})
    common = _common_observation(scenario_id="empty_tasks", service=service, child=child)
    passed = (
        error is None
        and _source_separated(common)
        and child.get("status") == "returned"
        and child.get("error_type") is None
        and child.get("leaked_tasks") == 0
        and not service.records
    )
    return {
        **common,
        "passed": passed,
        "tasks_executed": len(service.records),
        "error_type": child.get("error_type") if error is None else error,
    }


async def _invalid_scenario() -> dict[str, Any]:
    specs = [{"cleanup": 0.01, "duration": 0.1, "failure": False}]
    child, service, _, error = await _run_child_scenario(
        "invalid_bounds", {"kind": "invalid", "tasks": specs}
    )
    common = _common_observation(scenario_id="invalid_bounds", service=service, child=child)
    checks = child.get("checks") if isinstance(child.get("checks"), dict) else {}
    expected = {
        "zero_rejected",
        "negative_rejected",
        "string_rejected",
        "bool_rejected",
        "float_rejected",
        "float_integral_rejected",
        "nan_rejected",
        "pos_inf_rejected",
        "neg_inf_rejected",
        "none_rejected",
    }
    passed = (
        error is None
        and _source_separated(common)
        and child.get("status") == "returned"
        and child.get("leaked_tasks") == 0
        and set(checks) == expected
        and all(checks.get(name) is True for name in expected)
        and not service.records
    )
    return {**common, "passed": passed, **{name: checks.get(name) for name in sorted(expected)}}


async def _successful_batch_scenario(
    scenario_id: str,
    *,
    task_count: int,
    max_concurrent: int,
    duration: float,
) -> dict[str, Any]:
    specs = [
        {"cleanup": 0.008, "duration": duration, "failure": False}
        for _ in range(task_count)
    ]
    child, service, _, error = await _run_child_scenario(
        scenario_id,
        {"kind": "batch", "max_concurrent": max_concurrent, "tasks": specs},
    )
    common = _common_observation(scenario_id=scenario_id, service=service, child=child)
    started = len(service.records)
    finished = sum(record.get("status") == "completed" for record in service.records.values())
    cleaned = sum(record.get("cleaned_at") is not None for record in service.records.values())
    expected_concurrency = min(task_count, max_concurrent)
    exact_concurrency_matched = service.max_active == expected_concurrency
    exact_invocations_matched = (
        len(service.invocations) == task_count
        and set(service.invocations) == set(range(task_count))
        and all(count == 1 for count in service.invocations.values())
    )
    passed = (
        error is None
        and _source_separated(common)
        and child.get("status") == "returned"
        and child.get("leaked_tasks") == 0
        and started == task_count
        and finished == task_count
        and cleaned == task_count
        and exact_concurrency_matched
        and exact_invocations_matched
    )
    result = {
        **common,
        "passed": passed,
        "tasks_started": started,
        "tasks_finished": finished,
        "tasks_cleaned_up": cleaned,
        "max_observed_concurrency": service.max_active,
    }
    if scenario_id == "concurrency_bound":
        result["concurrency_limit_exceeded"] = service.max_active > max_concurrent
    return result


async def _cancellation_scenario() -> dict[str, Any]:
    specs = [
        {"cleanup": 0.015, "duration": 0.25, "failure": False}
        for _ in range(10)
    ]
    child, service, received_at, error = await _run_child_scenario(
        "cancellation_cleanup",
        {
            "cancel_after": 0.025,
            "kind": "batch",
            "max_concurrent": 3,
            "tasks": specs,
        },
    )
    common = _common_observation(
        scenario_id="cancellation_cleanup", service=service, child=child
    )
    started = len(service.records)
    finished = sum(record.get("status") == "completed" for record in service.records.values())
    cleaned_times = [record.get("cleaned_at") for record in service.records.values()]
    cleaned = sum(value is not None for value in cleaned_times)
    cleanup_before_return = bool(cleaned_times) and all(
        isinstance(value, float) and value <= received_at for value in cleaned_times
    )
    queued_prevented = started == 3
    cancellation_propagated = (
        child.get("status") == "cancelled"
        and child.get("cancel_requested") is True
        and child.get("cancel_acknowledged") is True
    )
    passed = (
        error is None
        and _source_separated(common)
        and child.get("leaked_tasks") == 0
        and started == 3
        and finished == 0
        and cleaned == 3
        and service.max_active <= 3
        and cleanup_before_return
        and queued_prevented
        and cancellation_propagated
    )
    return {
        **common,
        "passed": passed,
        "tasks_started": started,
        "tasks_finished": finished,
        "tasks_cleaned_up": cleaned,
        "cleanup_completed_before_return": cleanup_before_return,
        "queued_tasks_prevented": queued_prevented,
        "cancellation_propagated": cancellation_propagated,
    }


async def _exception_scenario() -> dict[str, Any]:
    specs = [
        {"cleanup": 0.015, "duration": 0.015, "failure": True},
        {"cleanup": 0.015, "duration": 0.25, "failure": False},
        *[
            {"cleanup": 0.015, "duration": 0.25, "failure": False}
            for _ in range(4)
        ],
    ]
    child, service, received_at, error = await _run_child_scenario(
        "exception_propagation",
        {"kind": "batch", "max_concurrent": 2, "tasks": specs},
    )
    common = _common_observation(
        scenario_id="exception_propagation", service=service, child=child
    )
    started = len(service.records)
    cleaned_times = [record.get("cleaned_at") for record in service.records.values()]
    all_cleaned = bool(cleaned_times) and len(cleaned_times) == started and all(
        isinstance(value, float) and value <= received_at for value in cleaned_times
    )
    error_propagated = (
        child.get("status") == "error"
        and child.get("contains_remote_task_error") is True
    )
    queued_prevented = started == 2
    task_0_status = service.records.get(0, {}).get("status")
    task_1_status = service.records.get(1, {}).get("status")
    task_1_cleaned = service.records.get(1, {}).get("cleaned_at")
    peer_cancelled_and_awaited = (
        task_1_status == "cancelled"
        and isinstance(task_1_cleaned, float)
        and task_1_cleaned <= received_at
    )
    passed = (
        error is None
        and _source_separated(common)
        and child.get("leaked_tasks") == 0
        and error_propagated
        and queued_prevented
        and all_cleaned
        and service.max_active <= 2
        and task_0_status == "error"
        and peer_cancelled_and_awaited
    )
    return {
        **common,
        "passed": passed,
        "tasks_started": started,
        "tasks_cleaned_up": sum(value is not None for value in cleaned_times),
        "error_propagated": error_propagated,
        "all_started_cleaned_up": all_cleaned,
        "queued_tasks_prevented": queued_prevented,
    }


async def _wave_scenario() -> dict[str, Any]:
    specs = [
        {"cleanup": 0.005, "duration": 0.025, "failure": False}
        for _ in range(4)
    ]
    child, service, _, error = await _run_child_scenario(
        "wave_timing",
        {"kind": "batch", "max_concurrent": 2, "tasks": specs},
    )
    common = _common_observation(scenario_id="wave_timing", service=service, child=child)
    started = len(service.records)
    finished = sum(record.get("status") == "completed" for record in service.records.values())
    cleaned = sum(record.get("cleaned_at") is not None for record in service.records.values())
    starts = sorted(
        float(record["started_at"])
        for record in service.records.values()
        if isinstance(record.get("started_at"), float)
    )
    first_cleanup = min(
        (
            float(record["cleaned_at"])
            for record in service.records.values()
            if isinstance(record.get("cleaned_at"), float)
        ),
        default=float("inf"),
    )
    timing_valid = len(starts) == 4 and starts[2] >= first_cleanup
    passed = (
        error is None
        and _source_separated(common)
        and child.get("status") == "returned"
        and child.get("leaked_tasks") == 0
        and started == 4
        and finished == 4
        and cleaned == 4
        and service.max_active == 2
        and timing_valid
    )
    return {
        **common,
        "passed": passed,
        "tasks_started": started,
        "tasks_finished": finished,
        "tasks_cleaned_up": cleaned,
        "timing_valid": timing_valid,
        "total_tasks": 4,
        "max_concurrent_limit": 2,
    }


async def _run_diagnostics() -> list[dict[str, Any]]:
    """Run every candidate in a separate process observed by this parent process."""
    scenarios = [
        await _empty_scenario(),
        await _invalid_scenario(),
        await _successful_batch_scenario(
            "concurrency_bound", task_count=8, max_concurrent=3, duration=0.015
        ),
        await _successful_batch_scenario(
            "under_limit_concurrency", task_count=4, max_concurrent=10, duration=0.015
        ),
        await _cancellation_scenario(),
        await _exception_scenario(),
        await _wave_scenario(),
    ]
    return scenarios


def _snapshot(*, status: str, error: str | None, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "status": status,
        "error": error,
        "workspace": "cancel-async-tasks",
        "observation_authority": OBSERVATION_AUTHORITY,
        "observer_pid": os.getpid(),
        "scenario_count": len(scenarios),
        "passed_count": sum(item.get("passed") is True for item in scenarios),
        "scenarios": scenarios,
    }


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        try:
            event_fd = int(sys.argv[2])
        except ValueError:
            return 2
        return _child_main(event_fd)

    script_dir = Path(__file__).resolve().parent
    source_dir = script_dir / "public" / "workspace"
    if not source_dir.is_dir():
        source_dir = DEFAULT_SOURCE if DEFAULT_SOURCE.is_dir() else Path.cwd() / "public" / "workspace"
    _init_workspace(WORKSPACE_ROOT, source_dir)

    raw = sys.stdin.buffer.read(MAX_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_ARTIFACT_BYTES:
        snapshot = _snapshot(status="rejected", error="artifact exceeds 128 KiB", scenarios=[])
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0
    try:
        source = raw.decode("utf-8")
    except UnicodeError:
        source = ""
        error = "artifact is not UTF-8"
    else:
        error = None

    if not source.strip():
        error = "artifact must contain a complete run.py implementation"
    elif source.startswith(("diff --git", "--- ", "@@ ")):
        error = "unified diffs are not accepted; submit the complete run.py source"
    elif "\x00" in source:
        error = "artifact contains a NUL byte"
    else:
        try:
            _validate_candidate_source(source)
        except SourceValidationError as exc:
            error = str(exc)

    if error is not None:
        snapshot = _snapshot(status="rejected", error=error, scenarios=[])
        sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0

    run_file = WORKSPACE_ROOT / "run.py"
    run_file.write_text(source.rstrip() + "\n", encoding="utf-8")
    try:
        scenarios = asyncio.run(_run_diagnostics())
        snapshot = _snapshot(status="applied", error=None, scenarios=scenarios)
    except BaseException as exc:
        snapshot = _snapshot(
            status="error",
            error=f"diagnostic harness failed: {type(exc).__name__}: {exc}",
            scenarios=[],
        )
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
