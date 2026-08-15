from __future__ import annotations

import io
from hashlib import sha256
import json
import os
import shutil
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest
from rolebench.contracts import (
    canonical_sha256,
    file_sha256,
    task_execution_sha256,
    tree_sha256,
    validate_value,
)
from rolebench.accounting import classify_attempt
from rolebench import task_workflow
from rolebench.task_workflow import (
    TaskAdmissionError,
    TaskWorkflowError,
    check_task_qualification,
    generate_task_qualification,
    import_omp_gym_task,
    prepare_admission_worker_manifest,
    prepare_worker_manifest,
    scan_session_candidates,
)


class _Valid:
    valid = True
    diagnostics: tuple[object, ...] = ()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def test_session_scan_is_non_linkable_and_only_emits_deterministic_signals(tmp_path: Path) -> None:
    private = "provider=secret-model account=customer-42 /home/alice/private prompt text"
    rows = [
        {"type": "metadata", "version": 3, "id": private},
        {"type": "message", "version": 3, "id": "entry-secret", "message": {"content": "Tests passed. " + private}},
        {"type": "message", "version": 3, "message": {"content": "Versioned correction v2. " + private}},
        {"type": "message", "version": 3, "message": {"content": "A late test failure appeared. " + private}},
    ]
    session = tmp_path / "sensitive-name.jsonl"
    session.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    first = scan_session_candidates(session, role_hint="task")
    second = scan_session_candidates(session, role_hint="task")

    assert first["signals"] == [
        {"ordinal": 2, "kind": "versioned-correction"},
        {"ordinal": 3, "kind": "late-test-failure"},
    ]
    assert first["entry_count"] == 3
    assert first["origin"]["candidate_ref"] != second["origin"]["candidate_ref"]
    encoded = json.dumps(first)
    for forbidden in (private, str(session), "entry-secret", "secret-model", "customer-42", sha256(session.read_bytes()).hexdigest()):
        assert forbidden not in encoded


def test_session_scan_rejects_symlink_without_disclosing_source_path(tmp_path: Path) -> None:
    source = tmp_path / "private-session-name"
    source.write_text('{}\n')
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(TaskWorkflowError) as caught:
        scan_session_candidates(link)
    assert str(source) not in str(caught.value)
    assert str(link) not in str(caught.value)


def test_session_scan_rejects_excessive_json_depth(
    tmp_path: Path,
) -> None:
    content: object = "tests passed"
    for _ in range(140):
        content = {"content": content}
    session = tmp_path / "deep.jsonl"
    session.write_text(
        json.dumps(
            {
                "type": "message",
                "version": 3,
                "message": content,
            }
        )
        + "\n"
    )

    with pytest.raises(
        TaskWorkflowError,
        match="JSON depth limit",
    ):
        scan_session_candidates(session)


def _gym_source(path: Path, *, license_text: str = "NOASSERTION") -> None:
    path.mkdir(parents=True)
    (path / "task.toml").write_text(
        'prompt = "repair the fixture"\n'
        'test_command = ["python", "-m", "pytest"]\n'
        'tools = "read,bash,edit"\n'
        'max_time = "30"\n'
        'fidelity = "legacy"\n'
    )
    workspace = path / "workspace"
    workspace.mkdir()
    executable = workspace / "run"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (workspace / "data.txt").write_text("workspace")


def test_omp_gym_import_is_private_preserves_only_executable_bit_and_does_not_infer_license(
    tmp_path: Path,
) -> None:
    source = tmp_path / "upstream"
    _gym_source(source)
    root = tmp_path / "repository"
    root.mkdir()
    destination = root / ".rolebench/candidates/example"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "contracts",
        root / "contracts",
    )

    candidate = import_omp_gym_task(
        root, source, destination, source_version="abc123", license_expression="NOASSERTION",
        role_hint="task", capability_hints=("debugging",),
    )
    result = validate_value(
        root,
        "task-candidate",
        candidate,
        Path(".rolebench/candidates/example/candidate.json"),
    )

    assert result.valid
    assert candidate["assets"]["workspace"]["digest_sha256"] == (  # type: ignore[index]
        tree_sha256(destination / "workspace")
    )

    assert candidate["review"] == {
        "privacy": "required", "license": "required",
        "license_expression": "NOASSERTION", "publication": "prohibited",
    }
    assert candidate["legacy"] == {
        "test_command": ["python", "-m", "pytest"],
        "tools": "read,bash,edit",
        "max_time_seconds": 30,
        "fidelity": "legacy",
    }
    assert stat_mode(destination) == 0o700
    assert stat_mode(destination / "prompt.txt") == 0o600
    assert stat_mode(destination / "candidate.json") == 0o600
    assert stat_mode(destination / "workspace/run") == 0o700
    assert stat_mode(destination / "workspace/data.txt") == 0o600
    assert not (destination / "workspace/task.toml").exists()


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


def test_omp_gym_import_rejects_symlink_and_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "upstream"
    _gym_source(source)
    (source / "workspace/escape").symlink_to("data.txt")
    root = tmp_path / "repository"
    root.mkdir()
    destination = root / ".rolebench/candidates/example"

    with pytest.raises(TaskWorkflowError):
        import_omp_gym_task(root, source, destination, source_version="v1", license_expression="MIT")

    assert not destination.exists()
    parent = destination.parent
    assert not parent.exists() or not any(item.name.startswith(".example.tmp-") for item in parent.iterdir())


def test_omp_gym_import_rejects_deep_empty_directory_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "upstream"
    _gym_source(source)
    nested = source / "workspace"
    for index in range(129):
        nested /= f"d{index}"
        nested.mkdir()
    root = tmp_path / "repository"
    root.mkdir()
    destination = root / ".rolebench/candidates/deep"

    with pytest.raises(
        TaskWorkflowError,
        match="structural limits",
    ):
        import_omp_gym_task(
            root,
            source,
            destination,
            source_version="v1",
            license_expression="MIT",
        )

    assert not destination.exists()


def test_omp_gym_import_refuses_overwrite_and_destination_recursion(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    source = root / ".rolebench/candidates/source"
    _gym_source(source)
    existing = root / ".rolebench/candidates/existing"
    existing.mkdir()
    with pytest.raises(TaskWorkflowError):
        import_omp_gym_task(root, source, existing, source_version="v1", license_expression="MIT")
    with pytest.raises(TaskWorkflowError):
        import_omp_gym_task(root, source, source / "nested", source_version="v1", license_expression="MIT")


def _admission_report(
    root: Path,
    task: dict[str, object],
    probe: str,
    trial: int,
    *,
    verifier_outcome: str,
    command_digest: str,
    artifact_digest: str,
) -> Path:
    run_id = f"{probe}-{trial}"
    reward = 1 if verifier_outcome == "accepted" else 0
    observation = {
        "schema_version": "omp.attempt-observation/v2",
        "observation_id": f"{run_id}-observation",
        "observed_at": "2026-08-13T00:00:00Z",
        "attempt": {
            "attempt_id": run_id,
            "number": 1,
            "previous_attempt_id": None,
        },
        "stage": "complete",
        "lifecycle": {
            "environment_started": True,
            "agent_started": True,
            "agent_finished": True,
            "artifact_frozen": True,
            "runner_started": True,
            "runner_finished": True,
            "runner_evidence_frozen": True,
            "verifier_started": True,
            "verifier_finished": True,
        },
        "readiness": {
            "environment": "ready",
            "runner": "healthy",
            "provider": "failed",
        },
        "issues": [],
        "provider": {
            "request_started": False,
            "http_status": None,
        },
        "termination": {
            "kind": "completed",
            "exit_code": 0,
            "signal": None,
            "oom_scope": "none",
        },
        "verifier": {
            "outcome": verifier_outcome,
            "result_valid": True,
            "reward": reward,
        },
        "integrity": {"state": "verified"},
        "evidence_use": "admission-only",
        "digests": {
            "task": task_execution_sha256(task),
            "config": "2" * 64,
            "agent_image": str(task["admission_agents"][probe]["image"]).rpartition("@sha256:")[2],
            "runner_image": str(task["runner"]["image"]).rpartition("@sha256:")[2],
            "verifier_image": str(task["verifier"]["image"]).rpartition("@sha256:")[2],
            "runtime_policy": "2" * 64,
            "artifact": artifact_digest,
            "runner_evidence": "e" * 64,
            "trajectory": sha256(run_id.encode()).hexdigest(),
            "task_public_tree": "3" * 64,
            "verifier_private_tree": "4" * 64,
            "agent_image_config": command_digest,
            "runner_image_config": task["runner"]["config_digest_sha256"],
            "verifier_image_config": task["verifier"]["config_digest_sha256"],
        },
    }
    report = {
        "schema_version": "omp.worker-run-report/v1",
        "run_id": run_id,
        "passed": True,
        "external_provider_calls": 0,
        "policy_digest_sha256": "2" * 64,
        "artifact_digest_sha256": artifact_digest,
        "runner_evidence_digest_sha256": "e" * 64,
        "observation": observation,
        "outcome": classify_attempt(observation),
        "doctor": {"ready": True},
        "isolation": {
            "agent": {"verified": True},
            "runner": {"verified": True},
            "verifier": {"verified": True},
            "distinct_images": True,
            "resource_enforcement": True,
            "image_binding": {"agent": True, "runner": True, "verifier": True},
            "image_binding_verified": True,
            "artifact_frozen_after_agent_exit": True,
            "runner_evidence_frozen_after_runner_exit": True,
            "immutable_agent_runner_handoff": True,
            "immutable_runner_verifier_handoff": True,
        },
        "diagnostics": [],
    }
    path = root / f"evidence/{run_id}.json"
    _write_json(path, report)
    return path

def _bind_verifier_review_reports(
    root: Path,
    task: dict[str, object],
    report_paths: list[Path],
) -> None:
    evidence_path = root / "reviews/verifier.json"
    _write_json(
        evidence_path,
        {
            "report_digests_sha256": [
                file_sha256(path)
                for path in report_paths
            ]
        },
    )
    verifier_review = task["reviews"]["verifier"]  # type: ignore[index]
    verifier_review["evidence_path"] = "reviews/verifier.json"  # type: ignore[index]
    verifier_review["evidence_digest_sha256"] = file_sha256(  # type: ignore[index]
        evidence_path
    )
    _write_json(root / "tasks/task.json", task)


def _qualification_pair(
    root: Path,
    *,
    observation_authority: str = "host-process",
) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    task_path = root / "tasks/task.json"
    task = {
        "schema_version": "omp.diagnostic-task/v2",
        "task_id": "fixture", "task_version": "1", "content_digest_sha256": "1" * 64,
        "role": "task", "partition": "anchor", "routing_eligible": False,
        "authorship": {"author": "author"},
        "reviews": {
            "privacy": {"decision": "approved", "reviewer": "reviewer", "reviewed_at": "2026-01-01T00:00:00Z", "evidence_digest_sha256": "c" * 64},
            "license": {"decision": "approved", "reviewer": "reviewer", "reviewed_at": "2026-01-01T00:00:00Z", "evidence_digest_sha256": "d" * 64},
            "verifier": {
                "decision": "approved",
                "reviewer": "reviewer",
                "reviewed_at": "2026-01-01T00:00:00Z",
                "evidence_digest_sha256": "e" * 64,
                "evidence_path": "reviews/verifier.json",
                "runner_image": "runner.example/task@sha256:" + "f" * 64,
                "runner_config_digest_sha256": "d" * 64,
                "runner_platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "verifier_image": "verifier.example/task@sha256:" + "7" * 64,
                "verifier_config_digest_sha256": "8" * 64,
                "verifier_platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "private_tree_digest_sha256": "4" * 64,
            },
            "split": {"decision": "approved", "reviewer": "reviewer", "reviewed_at": "2026-01-01T00:00:00Z", "evidence_digest_sha256": "f" * 64},
        },
        "policy": {"path": "contracts/policy.json", "digest_sha256": "2" * 64},
        "assets": {
            "public": {"digest_sha256": "3" * 64},
            "verifier_private": {"digest_sha256": "4" * 64},
        },
        "agent": {
            "image": "agent.example/task@sha256:" + "5" * 64,
            "config_digest_sha256": "6" * 64,
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": "3" * 64, "argv": ["agent"],
        },
        "admission_agents": {
            "baseline": {
                "image": "agent.example/baseline@sha256:" + "9" * 64,
                "config_digest_sha256": "a" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "3" * 64,
                "argv": ["baseline"],
            },
            "reference": {
                "image": "agent.example/reference@sha256:" + "b" * 64,
                "config_digest_sha256": "c" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "3" * 64,
                "argv": ["reference"],
            },
            "tamper": {
                "image": "agent.example/tamper@sha256:" + "d" * 64,
                "config_digest_sha256": "e" * 64,
                "platform": {"os": "linux", "architecture": "amd64", "variant": None},
                "asset_tree_digest_sha256": "3" * 64,
                "argv": ["tamper"],
            },
        },
        "runner": {
            "image": "runner.example/task@sha256:" + "f" * 64,
            "config_digest_sha256": "d" * 64,
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": "3" * 64,
            "argv": ["runner"],
        },
        "verifier": {
            "image": "verifier.example/task@sha256:" + "7" * 64,
            "config_digest_sha256": "8" * 64,
            "platform": {"os": "linux", "architecture": "amd64", "variant": None},
            "asset_tree_digest_sha256": "4" * 64, "argv": ["verify"],
        },
        "objective": {
            "mode": "file",
            "scoring": "binary",
            "criteria": ["return exact contents of workspace/input.txt"],
            "observation": {
                "artifact_kind": "data-only",
                "authority": observation_authority,
                "runner_output_trust": "untrusted",
            },
        },
    }
    _write_json(task_path, task)
    baseline = _admission_report(
        root,
        task,
        "baseline",
        1,
        verifier_outcome="rejected",
        command_digest="a" * 64,
        artifact_digest="1" * 64,
    )
    baseline_repeat = _admission_report(
        root,
        task,
        "baseline",
        2,
        verifier_outcome="rejected",
        command_digest="a" * 64,
        artifact_digest="1" * 64,
    )
    reference = _admission_report(
        root,
        task,
        "reference",
        1,
        verifier_outcome="accepted",
        command_digest="c" * 64,
        artifact_digest="5" * 64,
    )
    reference_repeat = _admission_report(
        root,
        task,
        "reference",
        2,
        verifier_outcome="accepted",
        command_digest="c" * 64,
        artifact_digest="5" * 64,
    )
    tamper = _admission_report(
        root,
        task,
        "tamper",
        1,
        verifier_outcome="rejected",
        command_digest="e" * 64,
        artifact_digest="9" * 64,
    )
    tamper_repeat = _admission_report(
        root,
        task,
        "tamper",
        2,
        verifier_outcome="rejected",
        command_digest="e" * 64,
        artifact_digest="9" * 64,
    )
    _bind_verifier_review_reports(
        root,
        task,
        [
            baseline,
            baseline_repeat,
            reference,
            reference_repeat,
            tamper,
            tamper_repeat,
        ],
    )
    qualification_path = root / "qualifications/task.json"
    qualification = {
        "schema_version": "omp.task-qualification/v2",
        "task_id": "fixture", "task_version": "1", "content_digest_sha256": "1" * 64,
        "source_task": {"path": "tasks/task.json", "digest_sha256": canonical_sha256(task)},
        "reviews": task["reviews"],
        "evaluation_provenance": {
            "reviewer_id": "reviewer",
            "runner_image": task["runner"]["image"],
            "runner_config_digest_sha256": task["runner"]["config_digest_sha256"],
            "runner_platform": task["runner"]["platform"],
            "verifier_image": task["verifier"]["image"],
            "verifier_config_digest_sha256": task["verifier"]["config_digest_sha256"],
            "verifier_platform": task["verifier"]["platform"],
            "review_digest_sha256": task["reviews"]["verifier"]["evidence_digest_sha256"],
        },
        "observed_mapping": {
            "task_digest_sha256": task_execution_sha256(task),
            "public_tree_digest_sha256": "3" * 64,
            "verifier_private_tree_digest_sha256": "4" * 64,
            "agent_config_digest_sha256": "6" * 64,
            "runner_config_digest_sha256": "d" * 64,
            "verifier_config_digest_sha256": "8" * 64,
        },
        "checks": {
            "privacy": "pass", "license": "pass", "runner_isolation": "pass", "verifier_isolation": "pass",
            "observation_authority": "pass",
            "tamper_resistance": {
                "result": "pass",
                "command_digest_sha256": "e" * 64,
                "evidence": [
                    {"path": "evidence/tamper-1.json", "digest_sha256": file_sha256(tamper)},
                    {"path": "evidence/tamper-2.json", "digest_sha256": file_sha256(tamper_repeat)},
                ],
            },
            "determinism": "pass",
            "infrastructure_classification": "pass",
            "discrimination": "calibration-required",
            "baseline_fails": {
                "result": "pass",
                "command_digest_sha256": "a" * 64,
                "evidence": [
                    {"path": "evidence/baseline-1.json", "digest_sha256": file_sha256(baseline)},
                    {"path": "evidence/baseline-2.json", "digest_sha256": file_sha256(baseline_repeat)},
                ],
            },
            "reference_passes": {
                "result": "pass",
                "command_digest_sha256": "c" * 64,
                "evidence": [
                    {"path": "evidence/reference-1.json", "digest_sha256": file_sha256(reference)},
                    {"path": "evidence/reference-2.json", "digest_sha256": file_sha256(reference_repeat)},
                ],
            },
        },
        "decision": "calibration-required",
    }
    _write_json(qualification_path, qualification)
    return task_path, qualification_path, task, qualification

def test_qualification_requires_exact_links_evidence_and_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_path, qualification_path, _, qualification = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    assert check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    ) == {
        "valid": True,
        "decision": "calibration-required",
        "diagnostics": [],
    }

    qualification["checks"]["baseline_fails"]["result"] = "fail"
    qualification["decision"] = "calibration-required"
    _write_json(qualification_path, qualification)
    result = check_task_qualification(tmp_path, task_path, qualification_path)
    assert result["valid"] is False
    assert "qualification decision must be rejected" in result["diagnostics"]


def test_qualification_rejects_hashed_but_forged_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, _, qualification = (
        _qualification_pair(tmp_path)
    )
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    forged = tmp_path / "evidence/baseline-1.json"
    _write_json(forged, {"self_declared": "pass"})
    qualification["checks"]["baseline_fails"]["evidence"][0][  # type: ignore[index]
        "digest_sha256"
    ] = file_sha256(forged)
    _write_json(qualification_path, qualification)

    result = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )

    assert result["valid"] is False
    assert any(
        "baseline_fails evidence is invalid" in diagnostic
        for diagnostic in result["diagnostics"]
    )


def test_qualification_revalidation_rejects_copied_probe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, _, qualification = (
        _qualification_pair(tmp_path)
    )
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    original = tmp_path / "evidence/baseline-1.json"
    copied = tmp_path / "evidence/baseline-copy.json"
    copied.write_bytes(original.read_bytes())
    qualification["checks"]["baseline_fails"]["evidence"][1] = {  # type: ignore[index]
        "path": "evidence/baseline-copy.json",
        "digest_sha256": file_sha256(copied),
    }
    _write_json(qualification_path, qualification)

    result = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )

    assert result["valid"] is False
    assert any(
        "distinct reports and run ids" in diagnostic
        for diagnostic in result["diagnostics"]
    )



def test_qualification_revalidation_rejects_cross_probe_run_id_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, _, qualification = (
        _qualification_pair(tmp_path)
    )
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    reference = tmp_path / "evidence/reference-1.json"
    report = json.loads(reference.read_text())
    report["run_id"] = "baseline-1"
    report["observation"]["attempt"]["attempt_id"] = "baseline-1"
    report["outcome"] = classify_attempt(report["observation"])
    _write_json(reference, report)
    qualification["checks"]["reference_passes"]["evidence"][0][  # type: ignore[index]
        "digest_sha256"
    ] = file_sha256(reference)
    _write_json(qualification_path, qualification)

    result = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )

    assert result["valid"] is False
    assert (
        "qualification report run ids must be distinct"
        in result["diagnostics"]
    )

def test_generate_qualification_uses_repeated_distinct_bound_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, _, task, _ = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    reports = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }
    output = tmp_path / "qualifications/generated.json"
    qualification = generate_task_qualification(
        tmp_path,
        task_path,
        reports["baseline"],
        reports["reference"],
        reports["tamper"],
        output,
        reviewer="reviewer",
    )
    assert qualification["decision"] == "calibration-required"
    assert (
        qualification["checks"]["baseline_fails"]["evidence"][0][
            "digest_sha256"
        ]
        == file_sha256(reports["baseline"][0])
    )
    assert (
        qualification["checks"]["reference_passes"]["evidence"][1][
            "digest_sha256"
        ]
        == file_sha256(reports["reference"][1])
    )
    with pytest.raises(TaskWorkflowError):
        generate_task_qualification(
            tmp_path,
            task_path,
            reports["baseline"],
            reports["reference"],
            reports["tamper"],
            output,
            reviewer="reviewer",
        )


def test_host_filesystem_observation_authority_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, _, _ = _qualification_pair(
        tmp_path,
        observation_authority="host-filesystem",
    )
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )

    checked = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )

    assert checked["valid"] is False
    assert (
        "qualification decision must be rejected"
        in checked["diagnostics"]
    )
    reports = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }
    generated = generate_task_qualification(
        tmp_path,
        task_path,
        reports["baseline"],
        reports["reference"],
        reports["tamper"],
        tmp_path / "qualifications/host-filesystem.json",
        reviewer="reviewer",
    )
    assert generated["checks"]["observation_authority"] == "fail"
    assert generated["decision"] == "rejected"


def test_generate_qualification_rejects_extra_attested_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, _, task, _ = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    reports = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }
    extra = tmp_path / "evidence/baseline-3.json"
    extra_report = json.loads(reports["baseline"][1].read_text())
    extra_report["run_id"] = "baseline-run-3"
    _write_json(extra, extra_report)
    reports["baseline"].append(extra)
    _bind_verifier_review_reports(
        tmp_path,
        task,
        [
            *reports["baseline"],
            *reports["reference"],
            *reports["tamper"],
        ],
    )

    with pytest.raises(
        TaskAdmissionError,
        match="each qualification probe requires exactly two runs",
    ):
        generate_task_qualification(
            tmp_path,
            task_path,
            reports["baseline"],
            reports["reference"],
            reports["tamper"],
            tmp_path / "qualifications/extra-report.json",
            reviewer="reviewer",
        )


def test_generate_qualification_rejects_scalar_probe_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, _, _, _ = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    report = tmp_path / "evidence/baseline-1.json"

    with pytest.raises(
        TaskAdmissionError,
        match="each qualification probe requires exactly two runs",
    ):
        generate_task_qualification(
            tmp_path,
            task_path,
            report,  # type: ignore[arg-type]
            [report, report],
            [report, report],
            tmp_path / "qualifications/scalar-probe.json",
            reviewer="reviewer",
        )


def test_generate_qualification_rejects_nondeterministic_probe_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, _, _, _ = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    changed_path = tmp_path / "evidence/reference-2.json"
    changed = json.loads(changed_path.read_text())
    changed["artifact_digest_sha256"] = "6" * 64
    changed["observation"]["digests"]["artifact"] = "6" * 64
    changed["outcome"] = classify_attempt(
        changed["observation"]
    )
    _write_json(changed_path, changed)
    reports = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }

    with pytest.raises(
        TaskAdmissionError,
        match="artifacts and rewards must be deterministic",
    ):
        generate_task_qualification(
            tmp_path,
            task_path,
            reports["baseline"],
            reports["reference"],
            reports["tamper"],
            tmp_path / "qualifications/nondeterministic.json",
            reviewer="reviewer",
        )


def test_qualification_revalidation_rejects_mismatched_runner_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, task, qualification = _qualification_pair(tmp_path)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    task["runner"]["config_digest_sha256"] = "9" * 64
    _write_json(task_path, task)
    result = check_task_qualification(tmp_path, task_path, qualification_path)
    assert result["valid"] is False
    assert any(
        "evidence is invalid" in diag
        or "runner config provenance does not match" in diag
        or "observed mapping does not match" in diag
        for diag in result["diagnostics"]
    )


def test_generate_qualification_executable_task_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, _, task, _ = _qualification_pair(tmp_path)
    task["objective"]["observation"] = {
        "artifact_kind": "executable",
        "authority": "source-separated-service",
        "runner_output_trust": "untrusted",
    }
    _write_json(task_path, task)
    new_task_digest = task_execution_sha256(task)
    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        lambda *args: _Valid(),
    )
    reports = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }
    for report_list in reports.values():
        for rpath in report_list:
            rep = json.loads(rpath.read_text())
            rep["observation"]["digests"]["task"] = new_task_digest
            rep["outcome"] = classify_attempt(rep["observation"])
            _write_json(rpath, rep)
    _bind_verifier_review_reports(
        tmp_path,
        task,
        [
            *reports["baseline"],
            *reports["reference"],
            *reports["tamper"],
        ],
    )
    output = tmp_path / "qualifications/executable_rejected.json"
    qualification = generate_task_qualification(
        tmp_path,
        task_path,
        reports["baseline"],
        reports["reference"],
        reports["tamper"],
        output,
        reviewer="reviewer",
    )
    assert qualification["checks"]["observation_authority"] == "fail"
    assert qualification["decision"] == "rejected"


def test_image_inspection_binds_exact_stage_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = "example.invalid/runner@sha256:" + "a" * 64
    container = {
        "image": image,
        "config_digest_sha256": "b" * 64,
        "platform": {
            "os": "linux",
            "architecture": "amd64",
            "variant": None,
        },
        "argv": ["/runner.py"],
    }
    labels = {
        "org.omp.rolebench.task.role": "task",
        "org.omp.rolebench.task.stage": "runner",
        "org.omp.rolebench.task.public-tree-sha256": "c" * 64,
    }
    image_data = {
        "Id": "sha256:" + "b" * 64,
        "RepoDigests": [image],
        "Descriptor": {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "a" * 64,
        },
        "Os": "linux",
        "Architecture": "amd64",
        "Variant": None,
        "Config": {"Labels": labels},
    }
    monkeypatch.setattr(
        task_workflow,
        "_docker_json",
        lambda *args: image_data,
    )

    observed = task_workflow._inspect_image(
        "docker",
        container,
        "task",
        "c" * 64,
        "d" * 64,
        stage="runner",
        include_stage_label=True,
    )
    assert observed["image"] == image

    labels["org.omp.rolebench.task.stage"] = "agent"
    with pytest.raises(
        TaskAdmissionError,
        match="labels do not match",
    ):
        task_workflow._inspect_image(
            "docker",
            container,
            "task",
            "c" * 64,
            "d" * 64,
            stage="runner",
            include_stage_label=True,
        )


def test_image_inspection_extracts_distinct_oci_config_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_data = b'{"architecture":"amd64","os":"linux"}'
    config_digest = sha256(config_data).hexdigest()
    manifest_data = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": f"sha256:{config_digest}",
                "size": len(config_data),
            },
            "layers": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_digest = sha256(manifest_data).hexdigest()
    index_data = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest_data),
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    archive_path = tmp_path / "image.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, data in (
            ("index.json", index_data),
            (
                f"blobs/sha256/{manifest_digest}",
                manifest_data,
            ),
            (f"blobs/sha256/{config_digest}", config_data),
        ):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))

    image = f"example.invalid/runner@sha256:{manifest_digest}"
    container = {
        "image": image,
        "config_digest_sha256": config_digest,
        "argv": ["--rolebench-run"],
        "platform": {
            "os": "linux",
            "architecture": "amd64",
            "variant": None,
        },
    }
    image_data = {
        "Id": f"sha256:{manifest_digest}",
        "RepoDigests": [image],
        "Descriptor": {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": f"sha256:{manifest_digest}",
        },
        "Os": "linux",
        "Architecture": "amd64",
        "Variant": None,
        "Config": {
            "Labels": {
                "org.omp.rolebench.task.role": "task",
                "org.omp.rolebench.task.stage": "runner",
                "org.omp.rolebench.task.public-tree-sha256": "c" * 64,
            }
        },
    }

    monkeypatch.setattr(
        task_workflow,
        "_docker_json",
        lambda *_args: image_data,
    )

    def export_image(
        _docker: str,
        arguments: tuple[str, ...],
        **_kwargs: object,
    ) -> SimpleNamespace:
        output = Path(arguments[arguments.index("--output") + 1])
        shutil.copyfile(archive_path, output)
        return SimpleNamespace(
            returncode=0,
            timed_out=False,
            overflowed=False,
            stdout=b"",
        )

    monkeypatch.setattr(task_workflow, "_docker_result", export_image)
    inspected = task_workflow._inspect_image(
        "docker",
        container,
        "task",
        "c" * 64,
        "d" * 64,
        stage="runner",
        include_stage_label=True,
    )

    assert inspected["config_digest_sha256"] == config_digest


def test_container_tree_capture_hashes_archive_in_fresh_child_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"bound content"
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        entry = tarfile.TarInfo("public/input.txt")
        entry.mode = 0o644
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "input.txt").write_bytes(payload)
    state: dict[str, str] = {}
    image = "example.invalid/agent@sha256:" + "a" * 64
    container_id = "b" * 64

    def result(
        docker: str,
        arguments: tuple[str, ...],
        **kwargs: object,
    ) -> SimpleNamespace:
        del docker, kwargs
        if arguments[0] == "create":
            state["name"] = arguments[arguments.index("--name") + 1]
            return SimpleNamespace(
                returncode=0,
                stdout=(container_id + "\n").encode(),
                stderr=b"",
                timed_out=False,
                overflowed=False,
            )
        if arguments[0] == "cp":
            return SimpleNamespace(
                returncode=0,
                stdout=archive_bytes.getvalue(),
                stderr=b"",
                timed_out=False,
                overflowed=False,
            )
        assert arguments[0] == "rm"
        return SimpleNamespace(
            returncode=0,
            stdout=b"",
            stderr=b"",
            timed_out=False,
            overflowed=False,
        )

    def inspect(docker: str, arguments: tuple[str, ...]) -> object:
        del docker, arguments
        capture_name = state["name"]
        return [
            {
                "Id": container_id,
                "Mounts": [],
                "Config": {
                    "Image": image,
                    "Labels": {
                        "org.omp.rolebench.capture": capture_name,
                    },
                },
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "Binds": [],
                    "Devices": [],
                },
            }
        ]

    monkeypatch.setattr(task_workflow, "_docker_result", result)
    monkeypatch.setattr(task_workflow, "_docker_json", inspect)

    observed = task_workflow._container_tree_digest(
        "docker",
        image,
        "/opt/rolebench/task/public",
        must_exist=True,
    )

    assert observed == task_workflow.tree_sha256(expected)


def test_prepare_manifest_exact_mapping_holdout_and_no_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_path, qualification_path, task, qualification = _qualification_pair(tmp_path)
    monkeypatch.setattr(task_workflow, "validate_value", lambda *args: _Valid())
    monkeypatch.setattr(
        task_workflow, "_inspect_image",
        lambda docker, container, *args, **kwargs: {
            "image": container["image"], "config_digest_sha256": container["config_digest_sha256"],
            "platform": container["platform"], "argv": container["argv"],
        },
    )
    monkeypatch.setattr(
        task_workflow, "_container_tree_digest",
        lambda docker, image, path, must_exist: (
            None if not must_exist else ("4" * 64 if "verifier-private" in path else "3" * 64)
        ),
    )
    admission_output = tmp_path / ".rolebench/runs/baseline.json"
    admission_manifest = prepare_admission_worker_manifest(
        tmp_path,
        task_path,
        "baseline",
        "baseline-1",
        admission_output,
    )
    assert admission_manifest["task"] == {
        "digest_sha256": task_execution_sha256(task),
        "public_tree_digest_sha256": "3" * 64,
        "verifier_private_tree_digest_sha256": "4" * 64,
        "evidence_use": "admission-only",
    }
    assert (
        admission_manifest["agent"]["config_digest_sha256"]
        == "a" * 64
    )

    output = tmp_path / ".rolebench/runs/run.json"
    manifest = prepare_worker_manifest(tmp_path, task_path, qualification_path, "run-1", output)
    assert manifest == {
        "schema_version": "omp.worker-run-manifest/v2", "run_id": "run-1", "role": "task",
        "task": {
            "digest_sha256": task_execution_sha256(task),
            "qualification_digest_sha256": canonical_sha256(qualification),
            "public_tree_digest_sha256": "3" * 64,
            "verifier_private_tree_digest_sha256": "4" * 64,
            "evidence_use": "calibration-only",
        },
        "policy": task["policy"], "provider": {"enabled": False},
        "agent": {key: task["agent"][key] for key in ("image", "config_digest_sha256", "platform", "argv")},
        "runner": {key: task["runner"][key] for key in ("image", "config_digest_sha256", "platform", "argv")},
        "verifier": {key: task["verifier"][key] for key in ("image", "config_digest_sha256", "platform", "argv")},
    }
    with pytest.raises(TaskWorkflowError):
        prepare_worker_manifest(tmp_path, task_path, qualification_path, "run-1", output)

    task["partition"] = "holdout"
    _write_json(task_path, task)
    qualification["source_task"]["digest_sha256"] = canonical_sha256(task)
    qualification["observed_mapping"]["task_digest_sha256"] = task_execution_sha256(task)
    _write_json(qualification_path, qualification)
    with pytest.raises(TaskAdmissionError):
        prepare_worker_manifest(tmp_path, task_path, qualification_path, "run-2", tmp_path / ".rolebench/runs/holdout.json")


def test_v1_qualification_check_and_manifest_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_path, qualification_path, task, qualification = (
        _qualification_pair(tmp_path)
    )
    task["schema_version"] = "omp.diagnostic-task/v1"
    task.pop("runner")
    objective = task["objective"]  # type: ignore[assignment]
    objective.pop("observation")  # type: ignore[union-attr]
    reviews = task["reviews"]
    for review in reviews.values():  # type: ignore[union-attr]
        review.pop("evidence_path", None)
    verifier_review = reviews["verifier"]  # type: ignore[index]
    verifier_review["image"] = verifier_review.pop("verifier_image")  # type: ignore[union-attr]
    verifier_review["config_digest_sha256"] = verifier_review.pop(  # type: ignore[union-attr]
        "verifier_config_digest_sha256"
    )
    verifier_review["platform"] = verifier_review.pop("verifier_platform")  # type: ignore[union-attr]
    for field in (
        "runner_image",
        "runner_config_digest_sha256",
        "runner_platform",
    ):
        verifier_review.pop(field)  # type: ignore[union-attr]
    _write_json(task_path, task)

    report_paths = {
        name: [
            tmp_path / f"evidence/{name}-1.json",
            tmp_path / f"evidence/{name}-2.json",
        ]
        for name in ("baseline", "reference", "tamper")
    }
    for paths in report_paths.values():
        for path in paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            observation = report["observation"]
            observation["schema_version"] = (
                "omp.attempt-observation/v1"
            )
            for field in (
                "runner_started",
                "runner_finished",
                "runner_evidence_frozen",
            ):
                observation["lifecycle"].pop(field)
            for field in (
                "runner_image",
                "runner_evidence",
                "runner_image_config",
            ):
                observation["digests"].pop(field)
            observation["digests"]["task"] = canonical_sha256(task)
            report.pop("runner_evidence_digest_sha256")
            report["outcome"] = classify_attempt(observation)
            _write_json(path, report)

    qualification["schema_version"] = "omp.task-qualification/v1"
    qualification["source_task"]["digest_sha256"] = canonical_sha256(  # type: ignore[index]
        task
    )
    qualification["reviews"] = task["reviews"]
    qualification["verifier_provenance"] = {
        "reviewer_id": verifier_review["reviewer"],  # type: ignore[index]
        "verifier_image": task["verifier"]["image"],  # type: ignore[index]
        "verifier_config_digest_sha256": task["verifier"][  # type: ignore[index]
            "config_digest_sha256"
        ],
        "verifier_platform": task["verifier"]["platform"],  # type: ignore[index]
        "review_digest_sha256": verifier_review[  # type: ignore[index]
            "evidence_digest_sha256"
        ],
    }
    qualification.pop("evaluation_provenance")
    observed = qualification["observed_mapping"]  # type: ignore[assignment]
    observed["task_digest_sha256"] = canonical_sha256(task)  # type: ignore[index]
    observed.pop("runner_config_digest_sha256")  # type: ignore[union-attr]
    checks = qualification["checks"]  # type: ignore[assignment]
    checks.pop("runner_isolation")  # type: ignore[union-attr]
    checks.pop("observation_authority")  # type: ignore[union-attr]
    for probe, check_name in (
        ("baseline", "baseline_fails"),
        ("reference", "reference_passes"),
        ("tamper", "tamper_resistance"),
    ):
        evidence = checks[check_name]["evidence"]  # type: ignore[index]
        for reference, path in zip(
            evidence,
            report_paths[probe],
            strict=True,
        ):
            reference["digest_sha256"] = file_sha256(path)
    _write_json(qualification_path, qualification)

    def validate_legacy_report_artifacts(
        root: Path,
        schema_name: str,
        value: object,
        relative: Path,
    ) -> object:
        if schema_name in {"attempt-observation", "attempt-outcome"}:
            return validate_value(
                Path(__file__).parents[1],
                schema_name,
                value,
                relative,
            )
        return _Valid()

    monkeypatch.setattr(
        task_workflow,
        "validate_value",
        validate_legacy_report_artifacts,
    )
    checked = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )
    assert checked == {
        "valid": True,
        "decision": "calibration-required",
        "diagnostics": [],
    }

    inspected: list[tuple[str, bool]] = []

    def inspect(
        docker: str,
        container: dict[str, object],
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        del docker, args
        inspected.append(
            (
                str(kwargs["stage"]),
                bool(kwargs["include_stage_label"]),
            )
        )
        return {
            key: container[key]
            for key in (
                "image",
                "config_digest_sha256",
                "platform",
                "argv",
            )
        }

    monkeypatch.setattr(task_workflow, "_inspect_image", inspect)
    monkeypatch.setattr(
        task_workflow,
        "_container_tree_digest",
        lambda docker, image, path, must_exist: (
            None
            if not must_exist
            else (
                "4" * 64
                if "verifier-private" in path
                else "3" * 64
            )
        ),
    )
    manifest = prepare_worker_manifest(
        tmp_path,
        task_path,
        qualification_path,
        "legacy-run",
        tmp_path / ".rolebench/runs/legacy.json",
    )

    assert manifest["schema_version"] == "omp.worker-run-manifest/v1"
    assert manifest["task"]["digest_sha256"] == canonical_sha256(task)  # type: ignore[index]
    assert "runner" not in manifest
    assert inspected == [
        ("agent", False),
        ("verifier", False),
    ]

    mismatched_report_path = report_paths["baseline"][0]
    mismatched_report = json.loads(
        mismatched_report_path.read_text(encoding="utf-8")
    )
    mismatched_observation = mismatched_report["observation"]
    mismatched_observation["schema_version"] = (
        "omp.attempt-observation/v2"
    )
    mismatched_observation["lifecycle"].update(
        {
            "runner_started": True,
            "runner_finished": True,
            "runner_evidence_frozen": True,
        }
    )
    mismatched_observation["digests"].update(
        {
            "runner_image": "f" * 64,
            "runner_evidence": "e" * 64,
            "runner_image_config": "d" * 64,
        }
    )
    mismatched_report["runner_evidence_digest_sha256"] = "e" * 64
    mismatched_report["outcome"] = classify_attempt(
        mismatched_observation
    )
    _write_json(mismatched_report_path, mismatched_report)
    baseline_evidence = qualification["checks"]["baseline_fails"][  # type: ignore[index]
        "evidence"
    ]
    baseline_evidence[0]["digest_sha256"] = file_sha256(  # type: ignore[index]
        mismatched_report_path
    )
    _write_json(qualification_path, qualification)

    mismatched = check_task_qualification(
        tmp_path,
        task_path,
        qualification_path,
    )

    assert mismatched["valid"] is False
    assert any(
        "observation schema version does not match" in diagnostic
        for diagnostic in mismatched["diagnostics"]
    )
