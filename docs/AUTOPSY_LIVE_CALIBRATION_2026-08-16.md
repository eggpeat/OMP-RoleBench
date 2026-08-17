# Live Calibration Autopsy — 2026-08-16

## Executive Summary

The 7-model × 16-task live calibration exposed **systemic harness defects**, not just model quality gaps. Of 112 evaluations:
- **32 (29%)** were excluded from scoring (infra/timeout/malformed)
- **68 (61%)** scored-fail
- **12 (11%)** scored-pass

The high exclusion rate and brittle verifiers make the current leaderboard unreliable for routing decisions.

---

## Failure Classification

### 1. Timeouts (14 evals, 13%)

| Route | Count | Root Cause |
|-------|------:|------------|
| Alibaba qwen3.8-max:max | 10 | Thinking tokens + Singapore gateway latency exceed 180s budget |
| Grok 4.6:max | 2 | Deep reasoning on scheduler + text-editing |
| Gemini 3.7-flash:high | 1 | WAL recovery reasoning |
| Kimi k3:max | 1 | Empty response at 129s (token exhaustion) |

**Harness defect**: 180s non-streaming timeout is too short for thinking models. Retry doubles latency (362s) without helping deterministic overruns.

**Fix**: Per-provider timeouts (Alibaba: 360s, Grok/Kimi/Gemini: 300s, DeepSeek/Devin: 120s). Fix retry accounting to accumulate all attempts. Long-term: SSE streaming with per-chunk timeout.

### 2. Malformed Output (14 evals, 13%)

| Route | Count | Root Cause |
|-------|------:|------------|
| ZAI glm-5.2 | 10 | Thinking tokens exhaust 4096 max_tokens before text block |
| Kimi k3:max | 4 | Same + binary PNG injection into prompt |

**Harness defect**: 
- `build_task_prompt` reads binary files (`.png`, `.db`, `.wal`) as UTF-8 with `errors='replace'`, injecting thousands of `\ufffd` characters.
- ZAI/Kimi return empty `content` when `reasoning_content` consumes budget. We don't read `reasoning_content` fallback.
- Empty responses not retried.

**Fix**: Skip binary extensions in prompt builder. Read `reasoning_content` as fallback. Retry once on empty response.

### 3. Infra Error (7 evals, 6%)

All 7 models: `terminal-bench.memcached-backdoor` — "container image labels do not match task"

**Root cause**: `prompt.txt` modified after Docker image build. Public tree SHA256 changed from `6c1ab605...` to `da316270...` but image label is stale.

**Fix**: Rebuild candidate image with updated build-args, or revert prompt.txt to match image. Exclude task until reconciled.

### 4. Reviewer Tasks — Too Easy (12/14 pass)

| Task | Pass Rate | Problem |
|------|----------:|---------|
| defect-recall | 6/7 | `# Bug 1:`, `# Bug 2:`, `# Bug 3:` comments in source code |
| precision-control | 7/7 | Rubber-stamp: `verdict=approved, findings=[]` scores 1.0 |

**Root cause**: Prompt tells model exactly what to look for. Patch contains literal bug labels. No distractors in precision control.

**Fix**: Remove in-code comments. Generalize prompt. Add subtle multi-file defects. Add realistic distractors (lock-free patterns, double-checked locking) to precision control. Tighten line tolerance to ±2. Use F1 scoring.

### 5. Commit Task — Phrase-Locked (1/7 pass)

Only Grok passed. Failures:
- ZAI: `"entries"` (plural) vs regex expecting singular
- DeepSeek/Devin: `"L1"` uppercase, 79-char subject (limit 72)
- Gemini: Missing exact token `"backing store"`
- Kimi: Evidence range included line 7

**Root cause**: Verifier uses exact regex templates and hardcoded evidence ranges `["change.patch:8-9", "change.patch:10-11"]`. Tests puzzle compliance, not commit quality.

**Fix**: Accept plural nouns. Case-fold before regex. Allow evidence range overlap (lines 6-9, 9-12). Move purpose clause guidance to body.

### 6. Zero-Pass Tasks (11 tasks)

| Task | Category | Issue |
|------|----------|-------|
| fix-code-vulnerability | Broken verifier | Over-constrained expectations |
| llm-inference-batching-scheduler | Broken verifier | Hidden graph invariants |
| cad-model | Harness gap | Vision model can't see image |
| code-from-image | Harness gap | PNG injected as text |
| cancel-async-tasks | Capability | Dynamic IPC torture — legitimately hard |
| sanitize-git-repo | Capability | Byte-exact SHA256 — very strict |
| multi-source-data-merger | Capability | Exact JSON equality |
| db-wal-recovery | Capability | SQLite binary forensics |
| large-scale-text-editing | Capability | Vim macro generation |
| responsive-incident-console | Capability | Headless Chromium + WCAG |
| custom-memory-heap-crash | Capability | C++ release crash diagnosis |

**Broken verifiers**: 2 tasks need verifier fixes.
**Harness gaps**: 2 tasks need image/binary handling.
**Legitimate difficulty**: 7 tasks are hard but fair — keep as capability measures.

---

## Dirty Repository State

```
Modified:
  contracts/tasks/terminal-bench.cancel-async-tasks/2.1-r6/probes/candidate.py  (gutted 78→4 lines)
  src/rolebench/worker.py  (inject fix — keep)
  tests/test_worker_runtime.py  (inject test — keep)

Untracked:
  contracts/tasks/terminal-bench.sanitize-git-repo/2.1-r6/workspace/  (40K scratch)
  scripts/live_model_calibration.py  (new live runner — keep)
  scripts/run_model_calibration.py  (old calibration — review)
  scripts/test_*.py  (4 test harnesses — review or delete)
  tests/test_live_model_calibration.py  (new test — keep)
  workspace/  (132K reports — gitignore)
```

**Cleanup**: Revert candidate.py. Add workspace/ to .gitignore. Review scripts/test_*.py and run_model_calibration.py for deletion. Keep live_model_calibration.py and its test.

---

## Priority Fixes

### P0 — Harness (unblocks valid scoring)
1. Per-provider timeout config
2. Binary file exclusion in prompt builder
3. `reasoning_content` fallback for ZAI/Kimi
4. Retry on empty response
5. Fix retry latency accounting

### P1 — Task Repairs (restores discrimination)
6. Rebuild memcached-backdoor image or revert prompt.txt
7. Remove bug comments from reviewer defect-recall
8. Add distractors to reviewer precision-control
9. Relax commit verifier (plurals, case, evidence ranges)

### P2 — Verifier Fixes (fair scoring)
10. fix-code-vulnerability: relax over-constrained expectations
11. llm-inference-batching-scheduler: document hidden invariants

### P3 — Exclude or Accept
12. Vision tasks: accept as capability ceiling (models can't see images in this harness)
13. Keep 7 legitimately hard tasks as capability measures

---

## Re-run Criteria

Do not re-run 7×16 until:
- [ ] P0 harness fixes landed
- [ ] P1 task repairs landed
- [ ] memcached-backdoor image reconciled
- [ ] Expected exclusion rate < 10%
