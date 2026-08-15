#!/usr/bin/env python3
"""Deterministic tamper probe: emits a commit disconnected from staged plans and gate verdicts."""

from __future__ import annotations

import json
import sys

TAMPER_PLAN = json.loads(r'''{
  "schema_version": "rolebench.scheduler-plan/v1",
  "plan_id": "plan-tamper-disconnected-commit-v1",
  "task_domain": "llm-inference-batching-scheduler",
  "architecture": {
    "components": [
      {
        "component_id": "COMP_INGEST_VALIDATOR",
        "name": "Workload Ingest & Schema Validator",
        "role": "Ingest and validate raw request feeds from bucket files",
        "responsibilities": [
          "Read requests_bucket_1.jsonl and requests_bucket_2.jsonl immutably",
          "Validate request schema and verify prompt_len and gen_len bounds",
          "Emit verified request manifests with sorted deterministic ordering"
        ],
        "state_scope": "read-only",
        "failure_domain": "ingest-stream-validation"
      },
      {
        "component_id": "COMP_GLOBAL_SHAPE_OPTIMIZER",
        "name": "Global Tensor Shape Catalog Optimizer",
        "role": "Synthesize global compilation shape catalog across all workload buckets",
        "responsibilities": [
          "Aggregate prompt length distributions across both bucket 1 and bucket 2",
          "Solve global discrete shape optimization bounded by MAX_GLOBAL_SHAPES=8",
          "Enforce 64-token multiple alignment (seq_align % 64 == 0, heads=32, hidden=4096)"
        ],
        "state_scope": "stateless",
        "failure_domain": "cross-bucket-optimization"
      },
      {
        "component_id": "COMP_BUCKET_BATCH_PACKER",
        "name": "Shape-Aware Bucket Batch Packer",
        "role": "Assign verified requests to batches using the global shape catalog",
        "responsibilities": [
          "Pack bucket 1 requests to meet latency SLAs and pad ratio limits",
          "Pack bucket 2 requests to maximize throughput density",
          "Guarantee exact-once request assignment without duplicates or dropped IDs"
        ],
        "state_scope": "stateless",
        "failure_domain": "per-bucket-packing"
      },
      {
        "component_id": "COMP_SLA_COST_EVALUATOR",
        "name": "Analytical Cost & SLA Quality Gate Evaluator",
        "role": "Evaluate batching plans against analytical cost model and quality gates",
        "responsibilities": [
          "Compute prefill, decode, batch overhead, and shape compilation costs",
          "Calculate pad_ratio, p95 latency, and sequential timecost metrics",
          "Evaluate blocking quality gates and signal commit or abort"
        ],
        "state_scope": "stateless",
        "failure_domain": "quality-verification"
      },
      {
        "component_id": "COMP_TRANSACTION_STORAGE_MANAGER",
        "name": "Atomic Transaction & Rollback Storage Manager",
        "role": "Perform atomic staged commits and crash-safe rollback",
        "responsibilities": [
          "Write uncommitted batch plans to isolated generation directory",
          "Execute atomic manifest pointer swap upon all gates passing",
          "Clean up staging files and preserve immutable inputs upon abort"
        ],
        "state_scope": "ephemeral-staging",
        "failure_domain": "storage-io"
      }
    ],
    "interfaces": [
      {
        "interface_id": "IFACE_RAW_REQUEST_STREAMS",
        "interface_kind": "RAW_REQUEST_FEEDS",
        "name": "Raw Input Request Streams",
        "producer_component_id": "COMP_INGEST_VALIDATOR",
        "consumer_component_ids": [
          "COMP_GLOBAL_SHAPE_OPTIMIZER",
          "COMP_BUCKET_BATCH_PACKER"
        ],
        "data_contract": "Structured manifest of verified requests for bucket 1 and bucket 2 with immutable checksums",
        "immutability": "immutable"
      },
      {
        "interface_id": "IFACE_GLOBAL_SHAPE_CATALOG",
        "interface_kind": "GLOBAL_SHAPE_CATALOG",
        "name": "Global Tensor Shape Catalog (<=8 Shapes)",
        "producer_component_id": "COMP_GLOBAL_SHAPE_OPTIMIZER",
        "consumer_component_ids": [
          "COMP_BUCKET_BATCH_PACKER",
          "COMP_SLA_COST_EVALUATOR"
        ],
        "data_contract": "Set of at most 8 unique hardware-aligned tensor shapes (seq_align % 64 == 0, heads=32, hidden=4096)",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_STAGED_BATCH_PLANS_B1",
        "interface_kind": "STAGED_BATCH_PLANS_B1",
        "name": "Staged Batch Assignment Plans for Bucket 1",
        "producer_component_id": "COMP_BUCKET_BATCH_PACKER",
        "consumer_component_ids": [
          "COMP_SLA_COST_EVALUATOR",
          "COMP_TRANSACTION_STORAGE_MANAGER"
        ],
        "data_contract": "In-memory or staged batch assignments mapping bucket 1 requests to batch_ids and assigned shapes",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_STAGED_BATCH_PLANS_B2",
        "interface_kind": "STAGED_BATCH_PLANS_B2",
        "name": "Staged Batch Assignment Plans for Bucket 2",
        "producer_component_id": "COMP_BUCKET_BATCH_PACKER",
        "consumer_component_ids": [
          "COMP_SLA_COST_EVALUATOR",
          "COMP_TRANSACTION_STORAGE_MANAGER"
        ],
        "data_contract": "In-memory or staged batch assignments mapping bucket 2 requests to batch_ids and assigned shapes",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_GATE_VERDICT_REPORT",
        "interface_kind": "GATE_VERDICT_REPORT",
        "name": "Cost & SLA Quality Gate Evaluation Report",
        "producer_component_id": "COMP_SLA_COST_EVALUATOR",
        "consumer_component_ids": [
          "COMP_TRANSACTION_STORAGE_MANAGER"
        ],
        "data_contract": "Quality gate pass/fail signals, cost breakdown, latency percentiles, and pad ratios",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_COMMITTED_PLAN_MANIFESTS",
        "interface_kind": "COMMITTED_PLAN_MANIFESTS",
        "name": "Committed Final Plan Files",
        "producer_component_id": "COMP_TRANSACTION_STORAGE_MANAGER",
        "consumer_component_ids": [
          "COMP_INGEST_VALIDATOR"
        ],
        "data_contract": "Atomically committed output files plan_b1.jsonl and plan_b2.jsonl on disk",
        "immutability": "single-write-sealed"
      }
    ]
  },
  "execution_graph": {
    "steps": [
      {
        "step_id": "STEP_01_READ_AND_VALIDATE_INPUTS",
        "name": "Read and Validate Request Feeds",
        "component_id": "COMP_INGEST_VALIDATOR",
        "phase": "INGEST_AND_VALIDATE",
        "operation_kind": "READ_INPUTS",
        "depends_on": [],
        "inputs": [],
        "outputs": [
          "IFACE_RAW_REQUEST_STREAMS"
        ],
        "enforced_invariants": [
          "INV_IMMUTABLE_INPUTS",
          "INV_DETERMINISTIC_TIE_BREAKING"
        ],
        "required_capabilities": [
          "system-architecture",
          "task-decomposition"
        ]
      },
      {
        "step_id": "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG",
        "name": "Synthesize Global Tensor Shape Catalog",
        "component_id": "COMP_GLOBAL_SHAPE_OPTIMIZER",
        "phase": "GLOBAL_SHAPE_ALLOCATION",
        "operation_kind": "SYNTHESIZE_GLOBAL_SHAPES",
        "depends_on": [
          "STEP_01_READ_AND_VALIDATE_INPUTS"
        ],
        "inputs": [
          "IFACE_RAW_REQUEST_STREAMS"
        ],
        "outputs": [
          "IFACE_GLOBAL_SHAPE_CATALOG"
        ],
        "enforced_invariants": [
          "INV_TENSOR_SHAPE_ALIGNMENT",
          "INV_GLOBAL_SHAPE_BUDGET_LE_8"
        ],
        "required_capabilities": [
          "system-architecture",
          "interface-design"
        ]
      },
      {
        "step_id": "STEP_03_PACK_BUCKET_1_BATCHES",
        "name": "Pack Interactive Workload Batches (Bucket 1)",
        "component_id": "COMP_BUCKET_BATCH_PACKER",
        "phase": "BUCKET_BATCH_PACKING",
        "operation_kind": "ASSIGN_BATCHES",
        "depends_on": [
          "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG"
        ],
        "inputs": [
          "IFACE_RAW_REQUEST_STREAMS",
          "IFACE_GLOBAL_SHAPE_CATALOG"
        ],
        "outputs": [
          "IFACE_STAGED_BATCH_PLANS_B1"
        ],
        "enforced_invariants": [
          "INV_EXACT_ONCE_ASSIGNMENT",
          "INV_TENSOR_SHAPE_ALIGNMENT",
          "INV_DETERMINISTIC_TIE_BREAKING"
        ],
        "required_capabilities": [
          "task-decomposition",
          "dependency-sequencing"
        ]
      },
      {
        "step_id": "STEP_04_PACK_BUCKET_2_BATCHES",
        "name": "Pack Throughput Workload Batches (Bucket 2)",
        "component_id": "COMP_BUCKET_BATCH_PACKER",
        "phase": "BUCKET_BATCH_PACKING",
        "operation_kind": "ASSIGN_BATCHES",
        "depends_on": [
          "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG"
        ],
        "inputs": [
          "IFACE_RAW_REQUEST_STREAMS",
          "IFACE_GLOBAL_SHAPE_CATALOG"
        ],
        "outputs": [
          "IFACE_STAGED_BATCH_PLANS_B2"
        ],
        "enforced_invariants": [
          "INV_EXACT_ONCE_ASSIGNMENT",
          "INV_TENSOR_SHAPE_ALIGNMENT",
          "INV_DETERMINISTIC_TIE_BREAKING"
        ],
        "required_capabilities": [
          "task-decomposition",
          "dependency-sequencing"
        ]
      },
      {
        "step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "name": "Evaluate Analytical Cost & SLA Quality Gates",
        "component_id": "COMP_SLA_COST_EVALUATOR",
        "phase": "COST_AND_SLA_VALIDATION",
        "operation_kind": "EVALUATE_GATES",
        "depends_on": [
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "inputs": [
          "IFACE_GLOBAL_SHAPE_CATALOG",
          "IFACE_STAGED_BATCH_PLANS_B1",
          "IFACE_STAGED_BATCH_PLANS_B2"
        ],
        "outputs": [
          "IFACE_GATE_VERDICT_REPORT"
        ],
        "enforced_invariants": [
          "INV_COST_AND_LATENCY_GATES",
          "INV_GLOBAL_SHAPE_BUDGET_LE_8",
          "INV_EXACT_ONCE_ASSIGNMENT"
        ],
        "required_capabilities": [
          "risk-identification",
          "interface-design"
        ]
      },
      {
        "step_id": "STEP_06_ATOMIC_COMMIT_PLANS",
        "name": "Execute Staged Atomic Commit",
        "component_id": "COMP_TRANSACTION_STORAGE_MANAGER",
        "phase": "ATOMIC_COMMIT_AND_ROLLBACK",
        "operation_kind": "COMMIT_OUTPUTS",
        "depends_on": [
          "STEP_05_EVALUATE_SLA_AND_COST_GATES"
        ],
        "inputs": [],
        "outputs": [
          "IFACE_COMMITTED_PLAN_MANIFESTS"
        ],
        "enforced_invariants": [
          "INV_ATOMIC_TRANSACTION_OR_ROLLBACK"
        ],
        "required_capabilities": [
          "system-architecture",
          "dependency-sequencing"
        ]
      },
      {
        "step_id": "STEP_07_ABORT_AND_CLEANUP_ROLLBACK",
        "name": "Rollback and Staging Cleanup on Failure",
        "component_id": "COMP_TRANSACTION_STORAGE_MANAGER",
        "phase": "ATOMIC_COMMIT_AND_ROLLBACK",
        "operation_kind": "ROLLBACK_ON_FAILURE",
        "depends_on": [
          "STEP_05_EVALUATE_SLA_AND_COST_GATES"
        ],
        "inputs": [
          "IFACE_STAGED_BATCH_PLANS_B1",
          "IFACE_STAGED_BATCH_PLANS_B2",
          "IFACE_GATE_VERDICT_REPORT"
        ],
        "outputs": [],
        "enforced_invariants": [
          "INV_ATOMIC_TRANSACTION_OR_ROLLBACK",
          "INV_IMMUTABLE_INPUTS"
        ],
        "required_capabilities": [
          "risk-identification",
          "system-architecture"
        ]
      }
    ]
  },
  "service_level_model": {
    "compiled_shape_metric_id": "UNIQUE_COMPILED_GRAPHS",
    "compiled_shape_operator": "LESS_THAN_OR_EQUAL",
    "compiled_shape_limit": 8,
    "latency_metric_id": "REQUEST_LATENCY_MS",
    "latency_aggregation": "P95",
    "latency_operator": "LESS_THAN_OR_EQUAL",
    "latency_limit_parameter_id": "REQUEST_SLA_MAX_LATENCY_MS",
    "padding_metric_id": "PADDED_TOKEN_WORK",
    "padding_operator": "LESS_THAN_OR_EQUAL",
    "padding_limit_parameter_id": "REQUEST_SLA_MAX_PADDED_TOKENS",
    "cost_metric_id": "TOTAL_COST",
    "cost_equation_id": "GPU_TIME_COST_PLUS_QUEUE_OVERHEAD",
    "gpu_time_metric_id": "TOTAL_ACTIVE_GPU_SECONDS",
    "gpu_time_coefficient_parameter_id": "GPU_RATE_PER_SECOND",
    "queue_overhead_metric_id": "QUEUE_OVERHEAD_COST",
    "queue_overhead_coefficient": 1,
    "cost_operator": "LESS_THAN_OR_EQUAL",
    "cost_limit_parameter_id": "REQUEST_SLA_MAX_COST"
  },
  "invariants": [
    {
      "invariant_id": "INV_EXACT_ONCE_ASSIGNMENT",
      "invariant_category": "EXACT_ONCE_ASSIGNMENT",
      "name": "Exact-Once Request Assignment",
      "description": "Every request in requests_bucket_1.jsonl and requests_bucket_2.jsonl is assigned exactly once with no duplicates or dropped records",
      "scope": "CROSS_BUCKET",
      "enforcing_step_ids": [
        "STEP_03_PACK_BUCKET_1_BATCHES",
        "STEP_04_PACK_BUCKET_2_BATCHES",
        "STEP_05_EVALUATE_SLA_AND_COST_GATES"
      ],
      "verification_gate_ids": [
        "GATE_EXACT_ONCE_INTEGRITY"
      ]
    },
    {
      "invariant_id": "INV_TENSOR_SHAPE_ALIGNMENT",
      "invariant_category": "TENSOR_SHAPE_ALIGNMENT",
      "name": "Hardware Tensor Shape Alignment",
      "description": "All batches use valid hardware shapes where seq_align is a multiple of 64 tokens with seq_align >= ceil(prompt_len/64)*64, heads_align=32, hidden_align=4096",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG",
        "STEP_03_PACK_BUCKET_1_BATCHES",
        "STEP_04_PACK_BUCKET_2_BATCHES"
      ],
      "verification_gate_ids": [
        "GATE_TENSOR_SHAPE_ALIGNMENT"
      ]
    },
    {
      "invariant_id": "INV_GLOBAL_SHAPE_BUDGET_LE_8",
      "invariant_category": "GLOBAL_SHAPE_BUDGET_LE_8",
      "name": "Global Shape Budget Constraint",
      "description": "Total count of unique tensor shapes across both bucket 1 and bucket 2 must not exceed 8 shapes",
      "scope": "CROSS_BUCKET",
      "enforcing_step_ids": [
        "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG",
        "STEP_05_EVALUATE_SLA_AND_COST_GATES"
      ],
      "verification_gate_ids": [
        "GATE_GLOBAL_SHAPE_LIMIT"
      ]
    },
    {
      "invariant_id": "INV_COST_AND_LATENCY_GATES",
      "invariant_category": "COST_AND_LATENCY_GATES",
      "name": "Cost and Latency SLA Compliance",
      "description": "Plans must strictly satisfy cost, pad_ratio, p95 latency, and sequential timecost thresholds",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STEP_05_EVALUATE_SLA_AND_COST_GATES"
      ],
      "verification_gate_ids": [
        "GATE_SLA_AND_COST_BOUNDS"
      ]
    },
    {
      "invariant_id": "INV_IMMUTABLE_INPUTS",
      "invariant_category": "IMMUTABLE_INPUTS",
      "name": "Input Files Immutability",
      "description": "Original input request files remain unmodified and read-only during execution and rollback",
      "scope": "STORAGE",
      "enforcing_step_ids": [
        "STEP_01_READ_AND_VALIDATE_INPUTS",
        "STEP_07_ABORT_AND_CLEANUP_ROLLBACK"
      ],
      "verification_gate_ids": [
        "GATE_IMMUTABLE_INPUTS_VERIFICATION"
      ]
    },
    {
      "invariant_id": "INV_ATOMIC_TRANSACTION_OR_ROLLBACK",
      "invariant_category": "ATOMIC_TRANSACTION_OR_ROLLBACK",
      "name": "Atomic Transaction Commit and Rollback",
      "description": "Plan emission writes to isolated staging directory and commits via atomic manifest swap only after all gates pass, with clean abort cleanup",
      "scope": "STORAGE",
      "enforcing_step_ids": [
        "STEP_06_ATOMIC_COMMIT_PLANS",
        "STEP_07_ABORT_AND_CLEANUP_ROLLBACK"
      ],
      "verification_gate_ids": [
        "GATE_ATOMIC_WRITE_VERIFICATION"
      ]
    },
    {
      "invariant_id": "INV_DETERMINISTIC_TIE_BREAKING",
      "invariant_category": "DETERMINISTIC_TIE_BREAKING",
      "name": "Deterministic Execution and Tie-Breaking",
      "description": "Deterministic tie-breaking rules on sequence length and request IDs guarantee reproducible batch assignments",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STEP_01_READ_AND_VALIDATE_INPUTS",
        "STEP_03_PACK_BUCKET_1_BATCHES",
        "STEP_04_PACK_BUCKET_2_BATCHES"
      ],
      "verification_gate_ids": [
        "GATE_DETERMINISTIC_ORDERING"
      ]
    }
  ],
  "risks_and_mitigations": {
    "risks": [
      {
        "risk_id": "RISK_GLOBAL_SHAPE_BUDGET_OVERRUN",
        "description": "Independent bucket packing could allocate conflicting shapes exceeding the hardware limit of 8 unique compiled graphs",
        "severity": "CRITICAL",
        "mitigation_step_ids": [
          "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG"
        ]
      },
      {
        "risk_id": "RISK_DUPLICATE_OR_DROPPED_REQUESTS",
        "description": "Partitioning or greedy packing bugs could omit input requests or double-assign requests to multiple batches",
        "severity": "CRITICAL",
        "mitigation_step_ids": [
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES",
          "STEP_05_EVALUATE_SLA_AND_COST_GATES"
        ]
      },
      {
        "risk_id": "RISK_PARTIAL_FILE_CORRUPTION",
        "description": "Crash or validation abort during file write could leave partially written plan files in destination directory",
        "severity": "HIGH",
        "mitigation_step_ids": [
          "STEP_06_ATOMIC_COMMIT_PLANS",
          "STEP_07_ABORT_AND_CLEANUP_ROLLBACK"
        ]
      },
      {
        "risk_id": "RISK_LATENCY_SLA_VIOLATION",
        "description": "Suboptimal batch size or excessive padding could breach latency or cost thresholds",
        "severity": "HIGH",
        "mitigation_step_ids": [
          "STEP_05_EVALUATE_SLA_AND_COST_GATES"
        ]
      }
    ],
    "gates": [
      {
        "gate_id": "GATE_EXACT_ONCE_INTEGRITY",
        "gate_category": "REQUEST_INTEGRITY",
        "name": "Request Assignment Exact-Once Integrity Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_EXACT_ONCE_ASSIGNMENT",
        "precondition_step_ids": [
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_GLOBAL_SHAPE_LIMIT",
        "gate_category": "GLOBAL_SHAPE_BUDGET",
        "name": "Global Shape Limit Quality Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_GLOBAL_SHAPE_BUDGET_LE_8",
        "precondition_step_ids": [
          "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_TENSOR_SHAPE_ALIGNMENT",
        "gate_category": "GLOBAL_SHAPE_BUDGET",
        "name": "Hardware Shape Alignment Quality Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_TENSOR_SHAPE_ALIGNMENT",
        "precondition_step_ids": [
          "STEP_02_OPTIMIZE_GLOBAL_SHAPE_CATALOG",
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_SLA_AND_COST_BOUNDS",
        "gate_category": "SLA_AND_COST_BOUNDS",
        "name": "SLA and Cost Quality Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_COST_AND_LATENCY_GATES",
        "precondition_step_ids": [
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_IMMUTABLE_INPUTS_VERIFICATION",
        "gate_category": "ATOMIC_WRITE_VERIFICATION",
        "name": "Immutable Input Storage Verification Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_IMMUTABLE_INPUTS",
        "precondition_step_ids": [
          "STEP_01_READ_AND_VALIDATE_INPUTS",
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_ATOMIC_WRITE_VERIFICATION",
        "gate_category": "ATOMIC_WRITE_VERIFICATION",
        "name": "Atomic Write Verification Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_ATOMIC_TRANSACTION_OR_ROLLBACK",
        "precondition_step_ids": [
          "STEP_01_READ_AND_VALIDATE_INPUTS",
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_DETERMINISTIC_ORDERING",
        "gate_category": "REQUEST_INTEGRITY",
        "name": "Deterministic Ordering Verification Gate",
        "evaluation_step_id": "STEP_05_EVALUATE_SLA_AND_COST_GATES",
        "target_metric_or_invariant": "INV_DETERMINISTIC_TIE_BREAKING",
        "precondition_step_ids": [
          "STEP_01_READ_AND_VALIDATE_INPUTS",
          "STEP_03_PACK_BUCKET_1_BATCHES",
          "STEP_04_PACK_BUCKET_2_BATCHES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      }
    ]
  },
  "rollout_and_recovery": {
    "atomic_commit_strategy": {
      "strategy_kind": "MANIFEST_POINTER_SWAP",
      "staging_directory": "/app/task_file/output_data/.staging_gen_1",
      "atomic_publication_unit": "/app/task_file/output_data/.staging_gen_1 -> /app/task_file/output_data/current_gen",
      "target_paths": [
        "/app/task_file/output_data/plan_b1.jsonl",
        "/app/task_file/output_data/plan_b2.jsonl"
      ],
      "cleanup_on_abort": true
    },
    "input_preservation": {
      "immutable_paths": [
        "/app/task_file/input_data/requests_bucket_1.jsonl",
        "/app/task_file/input_data/requests_bucket_2.jsonl"
      ],
      "verification_method": "CHECKSUM_VERIFICATION"
    },
    "rollback_guarantees": {
      "preserves_inputs": true,
      "preserves_prior_outputs": true,
      "prevents_partial_writes": true,
      "rollback_step_ids": [
        "STEP_07_ABORT_AND_CLEANUP_ROLLBACK"
      ]
    }
  }
}''')


def main() -> None:
    sys.stdout.write(json.dumps(TAMPER_PLAN, indent=2) + "\n")


if __name__ == "__main__":
    main()
