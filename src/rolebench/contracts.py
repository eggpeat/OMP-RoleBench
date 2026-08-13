"""Loading, validating, and hashing role contract repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable, Iterator, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from .accounting_rules import ATTEMPT_OUTCOME_RULES


type JSONScalar = None | bool | int | float | str
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]

BUILTIN_ROLES: tuple[str, ...] = (
    "default",
    "smol",
    "slow",
    "vision",
    "plan",
    "designer",
    "commit",
    "tiny",
    "task",
    "advisor",
)

_SCHEMA_DIRECTORY = Path("contracts/schemas")
_REGISTRY_FILE = Path("contracts/role-registry.json")
_ROLE_DIRECTORY = Path("contracts/roles")
_ROLE_SCHEMA = "role-contract.schema.json"
_REGISTRY_SCHEMA = "role-registry.schema.json"
_THRESHOLD_VALUES = (
    "quality_floor",
    "reliability_floor",
    "confidence",
    "latency_slo_seconds",
)

_RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time")
def _is_rfc3339_datetime(value: object) -> bool:
    """Validate the RFC 3339 subset used by RoleBench timestamps."""

    if not isinstance(value, str):
        return True
    if _RFC3339_DATETIME.fullmatch(value) is None:
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, order=True)
class Diagnostic:
    """One deterministic validation diagnostic."""

    file: str
    json_path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """The complete validation outcome."""

    diagnostics: tuple[Diagnostic, ...]

    @property
    def valid(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class Repository:
    """Loaded registry and manifests in canonical role order."""

    root: Path
    registry: JSONObject
    manifests: tuple[tuple[str, JSONObject], ...]


@dataclass(frozen=True)
class ContractError(Exception):
    """A user-facing contract repository error."""

    message: str
    file: str | None = None
    json_path: str = "$"

    def __str__(self) -> str:
        if self.file is None:
            return self.message
        return f"{self.file}:{self.json_path}: {self.message}"


def discover_root(start: Path | None = None) -> Path:
    """Find the nearest ancestor containing the role registry."""

    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / _REGISTRY_FILE).is_file():
            return candidate
    raise ContractError(
        f"could not find {_REGISTRY_FILE.as_posix()} from {current} or its parents"
    )


def resolve_root(root: Path | None) -> Path:
    """Resolve an explicit root or discover one from the current directory."""

    if root is None:
        return discover_root()
    resolved = root.expanduser().resolve()
    if not (resolved / _REGISTRY_FILE).is_file():
        raise ContractError(
            f"repository root does not contain {_REGISTRY_FILE.as_posix()}: {resolved}"
        )
    return resolved


def _as_json(value: object, *, file: str) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_as_json(item, file=file) for item in value]
    if isinstance(value, dict):
        converted: JSONObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("JSON object key is not a string", file)
            converted[key] = _as_json(item, file=file)
        return converted
    raise ContractError(f"unsupported JSON value {type(value).__name__}", file)


def _load_json(root: Path, relative: Path) -> JSONValue:
    path = root / relative
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError(str(error), relative.as_posix()) from error
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            relative.as_posix(),
        ) from error
    return _as_json(decoded, file=relative.as_posix())


def _load_object(root: Path, relative: Path) -> JSONObject:
    value = _load_json(root, relative)
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", relative.as_posix())
    return value


def load_repository(root: Path | None = None) -> Repository:
    """Load the registry and its ten canonical manifests."""

    resolved = resolve_root(root)
    registry = _load_object(resolved, _REGISTRY_FILE)
    contracts = registry.get("contracts")
    if not isinstance(contracts, dict):
        raise ContractError("contracts must be an object", _REGISTRY_FILE.as_posix(), "$.contracts")

    manifests: list[tuple[str, JSONObject]] = []
    for role in BUILTIN_ROLES:
        relative_value = contracts.get(role)
        if not isinstance(relative_value, str):
            raise ContractError(
                f"missing contract path for role {role!r}",
                _REGISTRY_FILE.as_posix(),
                f"$.contracts.{role}",
            )
        relative = Path(relative_value)
        manifests.append((role, _load_object(resolved, relative)))
    return Repository(resolved, registry, tuple(manifests))


def canonical_json(value: JSONValue) -> str:
    """Encode JSON in the stable form used for display and hashing."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(repository: Repository) -> str:
    """Hash the canonical registry followed by manifests in built-in role order."""

    digest = sha256()
    documents: tuple[JSONObject, ...] = (
        repository.registry,
        *(manifest for _, manifest in repository.manifests),
    )
    for document in documents:
        encoded = canonical_json(document).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


def _json_path(parts: Iterable[object]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            name = str(part)
            if name.isidentifier():
                path += f".{name}"
            else:
                path += f"[{json.dumps(name, ensure_ascii=False)}]"
    return path


def _schema_diagnostics(schema: JSONObject, relative: Path) -> tuple[Diagnostic, ...]:
    validator = Draft202012Validator(
        Draft202012Validator.META_SCHEMA,
        format_checker=_FORMAT_CHECKER,
    )
    errors = sorted(
        validator.iter_errors(schema),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            tuple(str(part) for part in item.absolute_schema_path),
            item.message,
        ),
    )
    return tuple(
        Diagnostic(relative.as_posix(), _json_path(error.absolute_path), error.message)
        for error in errors
    )


def _instance_diagnostics(
    instance: JSONObject,
    schema: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            tuple(str(part) for part in item.absolute_schema_path),
            item.message,
        ),
    )
    for error in errors:
        yield Diagnostic(relative.as_posix(), _json_path(error.absolute_path), error.message)


def _duplicate_diagnostics(value: JSONValue, relative: Path, parts: tuple[object, ...] = ()) -> Iterator[Diagnostic]:
    if isinstance(value, list):
        seen: dict[str, int] = {}
        for index, item in enumerate(value):
            fingerprint = canonical_json(item)
            first = seen.get(fingerprint)
            if first is not None:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path((*parts, index)),
                    f"duplicate array item; first appears at index {first}",
                )
            else:
                seen[fingerprint] = index
            yield from _duplicate_diagnostics(item, relative, (*parts, index))
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _duplicate_diagnostics(value[key], relative, (*parts, key))


def _registry_semantics(registry: JSONObject) -> Iterator[Diagnostic]:
    relative = _REGISTRY_FILE
    roles = registry.get("roles")
    if isinstance(roles, list) and roles != list(BUILTIN_ROLES):
        yield Diagnostic(
            relative.as_posix(),
            "$.roles",
            f"roles must exactly equal {list(BUILTIN_ROLES)!r}",
        )
    contracts = registry.get("contracts")
    if isinstance(contracts, dict):
        actual = set(contracts)
        expected = set(BUILTIN_ROLES)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            yield Diagnostic(
                relative.as_posix(),
                "$.contracts",
                f"contract coverage mismatch; missing={missing!r}, extra={extra!r}",
            )
        for role in BUILTIN_ROLES:
            expected_path = (_ROLE_DIRECTORY / f"{role}.json").as_posix()
            actual_path = contracts.get(role)
            if actual_path is not None and actual_path != expected_path:
                yield Diagnostic(
                    relative.as_posix(),
                    f"$.contracts.{role}",
                    f"must be {expected_path!r}",
                )
    yield from _duplicate_diagnostics(registry, relative)


def _manifest_semantics(
    role: str,
    manifest: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    declared_role = manifest.get("role")
    if declared_role != role:
        yield Diagnostic(
            relative.as_posix(),
            "$.role",
            f"role must match filename role {role!r}",
        )

    thresholds = manifest.get("thresholds")
    if isinstance(thresholds, dict):
        status = thresholds.get("status")
        values = [thresholds.get(name) for name in _THRESHOLD_VALUES]
        if status == "calibration-required":
            for name, value in zip(_THRESHOLD_VALUES, values, strict=True):
                if value is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.thresholds.{name}",
                        "must be null while threshold status is calibration-required",
                    )
        elif isinstance(status, str):
            for name, value in zip(_THRESHOLD_VALUES, values, strict=True):
                if value is None:
                    yield Diagnostic(
                        relative.as_posix(),
                        f"$.thresholds.{name}",
                        f"must be set while threshold status is {status!r}",
                    )
    yield from _duplicate_diagnostics(manifest, relative)

def _parse_datetime(value: JSONValue) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _route_policy_semantics(
    policy: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    roles = policy.get("roles")
    if not isinstance(roles, dict):
        return
    if not roles:
        yield Diagnostic(relative.as_posix(), "$.roles", "must contain at least one active role")
    for role in sorted(roles):
        if role not in BUILTIN_ROLES:
            yield Diagnostic(
                relative.as_posix(),
                _json_path(("roles", role)),
                f"unknown role {role!r}",
            )
            continue
        role_policy = roles[role]
        if not isinstance(role_policy, dict):
            continue
        routes = role_policy.get("routes")
        if isinstance(routes, list):
            route_ids: dict[str, int] = {}
            total = 0
            complete_weights = True
            for index, route in enumerate(routes):
                if not isinstance(route, dict):
                    complete_weights = False
                    continue
                route_id = route.get("route_id")
                if isinstance(route_id, str):
                    previous = route_ids.get(route_id)
                    if previous is not None:
                        yield Diagnostic(
                            relative.as_posix(),
                            _json_path(("roles", role, "routes", index, "route_id")),
                            f"duplicate route_id; first appears at index {previous}",
                        )
                    else:
                        route_ids[route_id] = index
                weight = route.get("weight_bps")
                if isinstance(weight, int) and not isinstance(weight, bool):
                    total += weight
                    if weight <= 0:
                        yield Diagnostic(
                            relative.as_posix(),
                            _json_path(("roles", role, "routes", index, "weight_bps")),
                            "weight_bps must be positive",
                        )
                else:
                    complete_weights = False
            if complete_weights and total != 10_000:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("roles", role, "routes")),
                    f"weight_bps values must sum to 10000; got {total}",
                )

        emergency = role_policy.get("emergency_fallback")
        if isinstance(emergency, list):
            seen_fallbacks: dict[str, int] = {}
            for index, route_id in enumerate(emergency):
                if not isinstance(route_id, str):
                    continue
                previous = seen_fallbacks.get(route_id)
                if previous is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("roles", role, "emergency_fallback", index)),
                        f"duplicate emergency fallback; first appears at index {previous}",
                    )
                else:
                    seen_fallbacks[route_id] = index

    created_at = _parse_datetime(policy.get("created_at"))
    valid_from = _parse_datetime(policy.get("valid_from"))
    valid_until = _parse_datetime(policy.get("valid_until"))
    try:
        created_after_start = (
            created_at is not None
            and valid_from is not None
            and created_at > valid_from
        )
        end_not_after_start = (
            valid_from is not None
            and valid_until is not None
            and valid_from >= valid_until
        )
    except TypeError:
        created_after_start = False
        end_not_after_start = False
    if created_after_start:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_from",
            "valid_from must not precede created_at",
        )
    if end_not_after_start:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_until",
            "valid_until must be later than valid_from",
        )


def _routing_decision_semantics(
    decision: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    candidates = decision.get("candidates")
    eligible_route_ids: set[str] = set()
    candidate_route_ids: dict[str, int] = {}
    ranks: dict[int, int] = {}
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            route_id = candidate.get("route_id")
            eligible = candidate.get("eligible")
            rank = candidate.get("rank")
            if isinstance(route_id, str):
                previous_route = candidate_route_ids.get(route_id)
                if previous_route is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("candidates", index, "route_id")),
                        f"duplicate candidate route_id; first appears at index {previous_route}",
                    )
                else:
                    candidate_route_ids[route_id] = index
            if eligible is True and isinstance(route_id, str):
                eligible_route_ids.add(route_id)
            if eligible is True and (
                not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("candidates", index, "rank")),
                    "eligible candidate rank must be a positive integer",
                )
            if isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
                previous = ranks.get(rank)
                if previous is not None:
                    yield Diagnostic(
                        relative.as_posix(),
                        _json_path(("candidates", index, "rank")),
                        f"duplicate candidate rank; first appears at index {previous}",
                    )
                else:
                    ranks[rank] = index

    selected_route = decision.get("selected_route")
    override = decision.get("explicit_override")
    override_active = isinstance(override, dict) and override.get("active") is True
    if override_active:
        override_route = override.get("route_id")
        if selected_route != override_route:
            yield Diagnostic(
                relative.as_posix(),
                "$.selected_route",
                "selected_route must equal explicit_override.route_id while override is active",
            )
    elif isinstance(selected_route, str) and selected_route not in eligible_route_ids:
        yield Diagnostic(
            relative.as_posix(),
            "$.selected_route",
            "selected_route must identify an eligible candidate unless explicit override is active",
        )
    elif selected_route is None and eligible_route_ids:
        yield Diagnostic(
            relative.as_posix(),
            "$.selected_route",
            "selected_route may be null only when no candidate is eligible",
        )

    fallback_ranking = decision.get("fallback_ranking")
    if isinstance(fallback_ranking, list):
        seen: dict[str, int] = {}
        for index, route_id in enumerate(fallback_ranking):
            if not isinstance(route_id, str):
                continue
            previous = seen.get(route_id)
            if previous is not None:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("fallback_ranking", index)),
                    f"duplicate fallback route; first appears at index {previous}",
                )
            else:
                seen[route_id] = index


def _attempt_observation_semantics(
    observation: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    lifecycle = observation.get("lifecycle")
    if isinstance(lifecycle, dict):
        implications = (
            ("agent_started", "environment_started"),
            ("agent_finished", "agent_started"),
            ("artifact_frozen", "agent_finished"),
            ("verifier_started", "artifact_frozen"),
            ("verifier_finished", "verifier_started"),
        )
        for later, earlier in implications:
            if lifecycle.get(later) is True and lifecycle.get(earlier) is not True:
                yield Diagnostic(
                    relative.as_posix(),
                    _json_path(("lifecycle", later)),
                    f"{later} requires {earlier}",
                )

        verifier = observation.get("verifier")
        if isinstance(verifier, dict):
            verifier_outcome = verifier.get("outcome")
            if verifier_outcome in {"accepted", "rejected"} and lifecycle.get(
                "verifier_finished"
            ) is not True:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.verifier.outcome",
                    "a decisive verifier outcome requires a finished verifier",
                )
            if verifier_outcome == "not-run" and (
                lifecycle.get("verifier_started") is True
                or lifecycle.get("verifier_finished") is True
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.verifier.outcome",
                    "not-run requires verifier_started and verifier_finished to be false",
                )

        digests = observation.get("digests")
        if isinstance(digests, dict):
            if lifecycle.get("artifact_frozen") is True and not isinstance(
                digests.get("artifact"), str
            ):
                yield Diagnostic(
                    relative.as_posix(),
                    "$.digests.artifact",
                    "a frozen artifact requires its digest",
                )

    readiness = observation.get("readiness")
    if (
        isinstance(readiness, dict)
        and readiness.get("environment") == "ready"
        and isinstance(lifecycle, dict)
        and lifecycle.get("environment_started") is not True
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.readiness.environment",
            "a ready environment must have started",
        )

    provider = observation.get("provider")
    if isinstance(provider, dict):
        request_started = provider.get("request_started")
        http_status = provider.get("http_status")
        if (
            request_started is True
            and isinstance(lifecycle, dict)
            and lifecycle.get("agent_started") is not True
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.request_started",
                "a provider request requires a started agent",
            )
        if isinstance(http_status, int) and request_started is not True:
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.http_status",
                "an HTTP status requires a started provider request",
            )

    termination = observation.get("termination")
    if isinstance(termination, dict):
        kind = termination.get("kind")
        oom_scope = termination.get("oom_scope")
        if kind == "resource-limit":
            if oom_scope not in {"attempt", "host", "unknown"}:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.termination.oom_scope",
                    "resource-limit termination requires attempt, host, or unknown scope",
                )
        elif oom_scope != "none":
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                "only resource-limit termination may have a non-none OOM scope",
            )
        verifier = observation.get("verifier")
        if (
            kind != "completed"
            and isinstance(verifier, dict)
            and verifier.get("outcome") in {"accepted", "rejected"}
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.verifier.outcome",
                "a decisive verifier outcome requires completed termination",
            )
        if (
            kind == "model-deadline"
            or (kind == "resource-limit" and oom_scope == "attempt")
        ) and (
            not isinstance(provider, dict)
            or provider.get("request_started") is not True
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.request_started",
                "a scored model limit requires a started provider request",
            )

    integrity = observation.get("integrity")
    digests = observation.get("digests")
    if (
        isinstance(integrity, dict)
        and integrity.get("state") == "verified"
        and isinstance(digests, dict)
        and not isinstance(digests.get("trajectory"), str)
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.digests.trajectory",
            "verified integrity requires a trajectory digest",
        )

    issues = observation.get("issues")
    issue_set = set(issues) if isinstance(issues, list) else set()
    if (
        "artifact-tampering" in issue_set
        and isinstance(integrity, dict)
        and integrity.get("state") != "failed"
    ):
        yield Diagnostic(
            relative.as_posix(),
            "$.integrity.state",
            "artifact-tampering requires failed integrity",
        )
    if isinstance(provider, dict):
        http_status = provider.get("http_status")
        expected_statuses: tuple[tuple[str, object], ...] = (
            ("provider-rate-limit", 429),
            ("provider-auth-error", {401, 403}),
        )
        for issue, expected in expected_statuses:
            if issue not in issue_set:
                continue
            matches = (
                http_status in expected
                if isinstance(expected, set)
                else http_status == expected
            )
            if not matches:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.provider.http_status",
                    f"{issue} has an inconsistent HTTP status",
                )
        if "provider-server-error" in issue_set and (
            not isinstance(http_status, int) or not 500 <= http_status <= 599
        ):
            yield Diagnostic(
                relative.as_posix(),
                "$.provider.http_status",
                "provider-server-error requires a 5xx HTTP status",
            )


def _attempt_outcome_semantics(
    outcome: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    reason_code = outcome.get("reason_code")
    if not isinstance(reason_code, str) or reason_code not in ATTEMPT_OUTCOME_RULES:
        yield Diagnostic(
            relative.as_posix(),
            "$.reason_code",
            "reason_code has no accounting rule",
        )
        return

    (
        expected_disposition,
        allowed_domains,
        expected_model_outcome,
        expected_verifier_outcome,
        allowed_terminations,
        allowed_oom_scopes,
    ) = ATTEMPT_OUTCOME_RULES[reason_code]
    expected_fields: tuple[tuple[str, JSONValue, object, str], ...] = (
        (
            "disposition",
            outcome.get("disposition"),
            expected_disposition,
            f"must be {expected_disposition!r} for reason_code {reason_code!r}",
        ),
        (
            "model_outcome",
            outcome.get("model_outcome"),
            expected_model_outcome,
            f"must be {expected_model_outcome!r} for reason_code {reason_code!r}",
        ),
        (
            "verifier_outcome",
            outcome.get("verifier_outcome"),
            expected_verifier_outcome,
            f"must be {expected_verifier_outcome!r} for reason_code {reason_code!r}",
        ),
    )
    for field, actual, expected, message in expected_fields:
        if actual != expected:
            yield Diagnostic(
                relative.as_posix(),
                _json_path((field,)),
                message,
            )

    failure_domain = outcome.get("failure_domain")
    if failure_domain not in allowed_domains:
        yield Diagnostic(
            relative.as_posix(),
            "$.failure_domain",
            f"is incompatible with reason_code {reason_code!r}",
        )

    termination = outcome.get("termination")
    if isinstance(termination, dict):
        kind = termination.get("kind")
        oom_scope = termination.get("oom_scope")
        if kind not in allowed_terminations:
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.kind",
                f"is incompatible with reason_code {reason_code!r}",
            )
        if oom_scope not in allowed_oom_scopes:
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                f"is incompatible with reason_code {reason_code!r}",
            )
        if kind == "resource-limit":
            if oom_scope not in {"attempt", "host", "unknown"}:
                yield Diagnostic(
                    relative.as_posix(),
                    "$.termination.oom_scope",
                    "resource-limit requires attempt, host, or unknown scope",
                )
        elif oom_scope != "none":
            yield Diagnostic(
                relative.as_posix(),
                "$.termination.oom_scope",
                "only resource-limit may have a non-none OOM scope",
            )

    scored = expected_disposition == "scored"
    if outcome.get("valid_attempt") is not scored:
        yield Diagnostic(
            relative.as_posix(),
            "$.valid_attempt",
            f"must be {scored!r} for reason_code {reason_code!r}",
        )
    if outcome.get("counts_toward_quality") is not scored:
        yield Diagnostic(
            relative.as_posix(),
            "$.counts_toward_quality",
            f"must be {scored!r} for reason_code {reason_code!r}",
        )


def _artifact_semantics(
    schema_name: str,
    artifact: JSONObject,
    relative: Path,
) -> Iterator[Diagnostic]:
    if schema_name == "attempt-observation":
        yield from _attempt_observation_semantics(artifact, relative)
    elif schema_name == "attempt-outcome":
        yield from _attempt_outcome_semantics(artifact, relative)
    elif schema_name == "route-policy":
        yield from _route_policy_semantics(artifact, relative)
    elif schema_name == "routing-decision":
        yield from _routing_decision_semantics(artifact, relative)


def _safe_schema(root: Path, relative: Path, diagnostics: list[Diagnostic]) -> JSONObject | None:
    try:
        return _load_object(root, relative)
    except ContractError as error:
        diagnostics.append(Diagnostic(error.file or relative.as_posix(), error.json_path, error.message))
        return None

def _schema_names(root: Path) -> tuple[str, ...]:
    suffix = ".schema.json"
    return tuple(
        path.name.removesuffix(suffix)
        for path in sorted((root / _SCHEMA_DIRECTORY).glob(f"*{suffix}"))
    )


def _load_artifact(path: Path, display_path: Path) -> JSONObject:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError(str(error), display_path.as_posix()) from error
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}",
            display_path.as_posix(),
        ) from error
    value = _as_json(decoded, file=display_path.as_posix())
    if not isinstance(value, dict):
        raise ContractError("document must be a JSON object", display_path.as_posix())
    return value


def validate_artifact(
    root: Path | None,
    schema_name: str,
    artifact_path: Path,
) -> ValidationResult:
    """Validate one JSON artifact against a named repository schema."""

    resolved = resolve_root(root)
    names = _schema_names(resolved)
    if schema_name not in names:
        raise ContractError(
            f"unknown schema {schema_name!r}; expected one of {list(names)!r}"
        )

    schema_relative = _SCHEMA_DIRECTORY / f"{schema_name}.schema.json"
    diagnostics: list[Diagnostic] = []
    schema = _safe_schema(resolved, schema_relative, diagnostics)
    if schema is None:
        return ValidationResult(tuple(sorted(set(diagnostics))))
    diagnostics.extend(_schema_diagnostics(schema, schema_relative))
    if diagnostics:
        return ValidationResult(tuple(sorted(set(diagnostics))))

    path = artifact_path.expanduser()
    if not path.is_absolute():
        path = resolved / path
    path = path.resolve()
    try:
        display_path = path.relative_to(resolved)
    except ValueError:
        display_path = path
    try:
        artifact = _load_artifact(path, display_path)
    except ContractError as error:
        diagnostics.append(
            Diagnostic(error.file or display_path.as_posix(), error.json_path, error.message)
        )
        return ValidationResult(tuple(sorted(set(diagnostics))))

    diagnostics.extend(_instance_diagnostics(artifact, schema, display_path))
    diagnostics.extend(_artifact_semantics(schema_name, artifact, display_path))
    return ValidationResult(tuple(sorted(set(diagnostics))))


def validate_repository(root: Path | None = None) -> ValidationResult:
    """Validate schemas, instances, and cross-document semantic invariants."""

    resolved = resolve_root(root)
    diagnostics: list[Diagnostic] = []

    schema_files = sorted((resolved / _SCHEMA_DIRECTORY).glob("*.json"))
    schemas: dict[str, JSONObject] = {}
    for schema_path in schema_files:
        relative = schema_path.relative_to(resolved)
        schema = _safe_schema(resolved, relative, diagnostics)
        if schema is not None:
            schema_errors = _schema_diagnostics(schema, relative)
            diagnostics.extend(schema_errors)
            if not schema_errors:
                schemas[schema_path.name] = schema

    for required_schema in (_REGISTRY_SCHEMA, _ROLE_SCHEMA):
        if required_schema not in schemas:
            diagnostics.append(
                Diagnostic(
                    (_SCHEMA_DIRECTORY / required_schema).as_posix(),
                    "$",
                    "required schema file is missing or invalid",
                )
            )

    registry = _safe_schema(resolved, _REGISTRY_FILE, diagnostics)
    if registry is None:
        return ValidationResult(tuple(sorted(set(diagnostics))))

    registry_schema = schemas.get(_REGISTRY_SCHEMA)
    if registry_schema is not None:
        diagnostics.extend(_instance_diagnostics(registry, registry_schema, _REGISTRY_FILE))
    diagnostics.extend(_registry_semantics(registry))

    contracts = registry.get("contracts")
    expected_files = {f"{role}.json" for role in BUILTIN_ROLES}
    role_directory = resolved / _ROLE_DIRECTORY
    actual_files = (
        {path.relative_to(role_directory).as_posix() for path in role_directory.rglob("*.json")}
        if role_directory.is_dir()
        else set()
    )
    for name in sorted(expected_files - actual_files):
        diagnostics.append(Diagnostic((_ROLE_DIRECTORY / name).as_posix(), "$", "required manifest file is missing"))
    for name in sorted(actual_files - expected_files):
        diagnostics.append(Diagnostic((_ROLE_DIRECTORY / name).as_posix(), "$", "unexpected manifest file"))

    role_schema = schemas.get(_ROLE_SCHEMA)
    contract_ids: dict[str, str] = {}
    for role in BUILTIN_ROLES:
        relative = _ROLE_DIRECTORY / f"{role}.json"
        if not (resolved / relative).is_file():
            continue
        manifest = _safe_schema(resolved, relative, diagnostics)
        if manifest is None:
            continue
        if role_schema is not None:
            diagnostics.extend(_instance_diagnostics(manifest, role_schema, relative))
        diagnostics.extend(_manifest_semantics(role, manifest, relative))
        contract_id = manifest.get("contract_id")
        if isinstance(contract_id, str):
            previous = contract_ids.get(contract_id)
            if previous is not None:
                diagnostics.append(
                    Diagnostic(
                        relative.as_posix(),
                        "$.contract_id",
                        f"contract_id duplicates {previous}",
                    )
                )
            else:
                contract_ids[contract_id] = relative.as_posix()
        if isinstance(contracts, dict):
            mapped = contracts.get(role)
            if isinstance(mapped, str) and mapped != relative.as_posix():
                diagnostics.append(
                    Diagnostic(relative.as_posix(), "$", f"registry maps role {role!r} to {mapped!r}")
                )

    return ValidationResult(tuple(sorted(set(diagnostics))))
