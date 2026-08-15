# LLM Inference Batching Scheduler: Architectural Specification

## 1. Domain Background & Hardware Constraints
Static-graph LLM inference accelerators (TPUs, RDUs, and custom ASICs) execute pre-compiled tensor graphs requiring fixed dimensions. Requests arrive with arbitrary prompt lengths (`prompt_len`) and generation lengths (`gen_len`). At runtime, token dimensions must be padded and aligned to hardware granularities (64-token multiples).

Uncoordinated batching causes catastrophic padding inflation, excessive compilation overhead, and SLA breaches. The batching scheduler architecture must ingest input workloads, assign requests into batches with concrete tensor shapes, and satisfy strict global resource budgets and quality gates.

## 2. Ingest Feeds & Workload Partitions
The scheduler ingests two distinct workload categories:
1. **Bucket 1 (Interactive Workload)**: `/app/task_file/input_data/requests_bucket_1.jsonl`
   - Latency-sensitive interactive requests with shorter prompt and generation requirements.
2. **Bucket 2 (Throughput Workload)**: `/app/task_file/input_data/requests_bucket_2.jsonl`
   - High-throughput background/batch requests with larger sequence lengths.

### Synthetic Data Notice
All request records (`request_id`, `prompt_len`, `gen_len`) in `requests_bucket_1.jsonl` and `requests_bucket_2.jsonl` are synthetic fixtures authored for RoleBench evaluation, containing no real user, customer, production, model, or session data.

## 3. Deliverables & Output Contract
The execution of the batching plan produces two output manifests:
1. `/app/task_file/output_data/plan_b1.jsonl` (for Bucket 1)
2. `/app/task_file/output_data/plan_b2.jsonl` (for Bucket 2)

Each output line maps a single request to a `batch_id` and tensor shape:
```json
{
  "request_id": "r-001001",
  "batch_id": "b-0001",
  "shape": {
    "seq_align": 192,
    "heads_align": 32,
    "hidden_align": 4096
  }
}
```

## 4. Structured Invariant Categories
The scheduler architecture must define and enforce seven explicit invariant categories:
1. `EXACT_ONCE_ASSIGNMENT`: Every input request across both buckets is assigned exactly once with no omitted or duplicate request IDs.
2. `TENSOR_SHAPE_ALIGNMENT`: All batches use valid hardware shapes where `seq_align` is a multiple of 64 tokens with `seq_align >= ceil(prompt_len / 64) * 64`, `heads_align=32`, `hidden_align=4096`. All requests in a batch share identical shape.
3. `GLOBAL_SHAPE_BUDGET_LE_8`: The union of all unique tensor shapes used across both buckets must not exceed 8 shapes ($|\text{UniqueShapes}(\text{Bucket 1}) \cup \text{UniqueShapes}(\text{Bucket 2})| \le 8$).
4. `COST_AND_LATENCY_GATES`: Generated plans strictly satisfy analytical cost model gates for pad ratio, latency SLAs, sequential timecost, and compilation overhead.
5. `IMMUTABLE_INPUTS`: Input files are read-only and preserved untouched across execution and abort.
6. `ATOMIC_TRANSACTION_OR_ROLLBACK`: Plans are staged to isolated ephemeral files and committed atomically via rename/swap only after all gates pass, with clean abort rollback preventing partial writes.
7. `DETERMINISTIC_TIE_BREAKING`: Deterministic tie-breaking on sequence length and request IDs ensures reproducible plan generation.

## 5. Structured Quality Gate Categories
The scheduler must evaluate four explicit blocking quality gate categories:
1. `GLOBAL_SHAPE_BUDGET`: Verifies unique shape count $\le 8$ across both buckets.
2. `REQUEST_INTEGRITY`: Verifies exact-once assignment and absence of duplicate or dropped requests.
3. `SLA_AND_COST_BOUNDS`: Verifies pad ratio, latency, cost, and timecost thresholds per `cost_model_spec.md`.
4. `ATOMIC_WRITE_VERIFICATION`: Verifies staging integrity and confirms input files remain unmodified before commit.

All gates must be `blocking: true` with `fallback_action: "ABORT_AND_ROLLBACK"` (or `"FAIL_CLOSED"`), and each gate must be bound to a pre-commit evaluation step.
