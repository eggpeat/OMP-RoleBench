#!/usr/bin/env python3
"""Provider-disabled reference candidate for llm-inference-batching-scheduler: emits alternative valid plan."""

from __future__ import annotations

import json
import sys

CANDIDATE_PLAN = json.loads(r'''{
  "schema_version": "rolebench.scheduler-plan/v1",
  "plan_id": "plan-candidate-static-graph-scheduler-v2",
  "task_domain": "llm-inference-batching-scheduler",
  "architecture": {
    "components": [
      {
        "component_id": "COMP_WORKLOAD_INGEST_CONTROLLER",
        "name": "Workload Ingest & Normalization Controller",
        "role": "Immutable ingestion and sorting of incoming request streams",
        "responsibilities": [
          "Read requests_bucket_1.jsonl and requests_bucket_2.jsonl with immutable read locks",
          "Sort requests deterministically by sequence length and request ID",
          "Construct canonical request inventory manifests"
        ],
        "state_scope": "read-only",
        "failure_domain": "workload-ingestion"
      },
      {
        "component_id": "COMP_CROSS_WORKLOAD_SHAPE_SYNTHESIZER",
        "name": "Cross-Workload Tensor Geometry Synthesizer",
        "role": "Global tensor shape budget allocation across all workloads",
        "responsibilities": [
          "Jointly analyze sequence length quantiles from both buckets",
          "Synthesize a shared global catalog of at most 8 compilation shapes",
          "Enforce 64-token granularity constraints for static graph compilation"
        ],
        "state_scope": "stateless",
        "failure_domain": "tensor-geometry-synthesis"
      },
      {
        "component_id": "COMP_CONSTRAINED_BATCH_ENGINE",
        "name": "Constrained Batch Assembly Engine",
        "role": "Pack requests into shaped batches satisfying latency and throughput constraints",
        "responsibilities": [
          "Partition interactive and throughput workloads into discrete batches",
          "Assign each batch a concrete shape from the global shape catalog",
          "Guarantee complete, bijective mapping of every input request"
        ],
        "state_scope": "stateless",
        "failure_domain": "batch-assembly"
      },
      {
        "component_id": "COMP_ANALYTICAL_VALIDATION_GUARD",
        "name": "Analytical SLA & Integrity Guard",
        "role": "Validate plans against performance thresholds and system invariants",
        "responsibilities": [
          "Evaluate cost model equations for prefill, decode, and shape compilation",
          "Enforce hard thresholds for pad ratio, latency, and timecost",
          "Verify exact-once integrity and absence of duplicate assignments"
        ],
        "state_scope": "stateless",
        "failure_domain": "invariant-guard"
      },
      {
        "component_id": "COMP_CRASH_SAFE_STAGE_COMMISSIONER",
        "name": "Crash-Safe Staged File Commissioner",
        "role": "Manage two-phase file commitment and clean abort rollback",
        "responsibilities": [
          "Serialize batch assignments to ephemeral hidden staging files",
          "Perform atomic generation directory swap to destination plan files",
          "Purge staging artifacts and ensure zero side effects upon abort"
        ],
        "state_scope": "ephemeral-staging",
        "failure_domain": "transaction-storage"
      }
    ],
    "interfaces": [
      {
        "interface_id": "IFACE_CANONICAL_REQUEST_FEEDS",
        "interface_kind": "RAW_REQUEST_FEEDS",
        "name": "Canonical Request Feeds",
        "producer_component_id": "COMP_WORKLOAD_INGEST_CONTROLLER",
        "consumer_component_ids": [
          "COMP_CROSS_WORKLOAD_SHAPE_SYNTHESIZER",
          "COMP_CONSTRAINED_BATCH_ENGINE"
        ],
        "data_contract": "Deterministic request manifests for interactive and throughput workloads",
        "immutability": "immutable"
      },
      {
        "interface_id": "IFACE_SHARED_SHAPE_CATALOG_8",
        "interface_kind": "GLOBAL_SHAPE_CATALOG",
        "name": "Shared Global Shape Catalog (Budget <= 8)",
        "producer_component_id": "COMP_CROSS_WORKLOAD_SHAPE_SYNTHESIZER",
        "consumer_component_ids": [
          "COMP_CONSTRAINED_BATCH_ENGINE",
          "COMP_ANALYTICAL_VALIDATION_GUARD"
        ],
        "data_contract": "Array of <= 8 unique aligned tensor shapes (seq_align % 64 == 0, heads=32, hidden=4096)",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_STAGED_BATCH_SPECIFICATIONS_B1",
        "interface_kind": "STAGED_BATCH_PLANS_B1",
        "name": "Staged Batch Assignment Specifications for Bucket 1",
        "producer_component_id": "COMP_CONSTRAINED_BATCH_ENGINE",
        "consumer_component_ids": [
          "COMP_ANALYTICAL_VALIDATION_GUARD",
          "COMP_CRASH_SAFE_STAGE_COMMISSIONER"
        ],
        "data_contract": "Candidate batching records mapping bucket 1 requests to batch_ids and assigned shapes",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_STAGED_BATCH_SPECIFICATIONS_B2",
        "interface_kind": "STAGED_BATCH_PLANS_B2",
        "name": "Staged Batch Assignment Specifications for Bucket 2",
        "producer_component_id": "COMP_CONSTRAINED_BATCH_ENGINE",
        "consumer_component_ids": [
          "COMP_ANALYTICAL_VALIDATION_GUARD",
          "COMP_CRASH_SAFE_STAGE_COMMISSIONER"
        ],
        "data_contract": "Candidate batching records mapping bucket 2 requests to batch_ids and assigned shapes",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_VALIDATION_AUDIT_VERDICT",
        "interface_kind": "GATE_VERDICT_REPORT",
        "name": "Validation Audit Verdict",
        "producer_component_id": "COMP_ANALYTICAL_VALIDATION_GUARD",
        "consumer_component_ids": [
          "COMP_CRASH_SAFE_STAGE_COMMISSIONER"
        ],
        "data_contract": "Boolean gate satisfaction decision with cost and latency metric summaries",
        "immutability": "single-write-sealed"
      },
      {
        "interface_id": "IFACE_PERMANENT_OUTPUT_PLANS",
        "interface_kind": "COMMITTED_PLAN_MANIFESTS",
        "name": "Permanent Output Plan Files",
        "producer_component_id": "COMP_CRASH_SAFE_STAGE_COMMISSIONER",
        "consumer_component_ids": [
          "COMP_WORKLOAD_INGEST_CONTROLLER"
        ],
        "data_contract": "Persisted plan_b1.jsonl and plan_b2.jsonl files on the filesystem",
        "immutability": "single-write-sealed"
      }
    ]
  },
  "execution_graph": {
    "steps": [
      {
        "step_id": "STAGE_01_INGEST_WORKLOADS",
        "name": "Ingest and Sort Workload Requests",
        "component_id": "COMP_WORKLOAD_INGEST_CONTROLLER",
        "phase": "INGEST_AND_VALIDATE",
        "operation_kind": "READ_INPUTS",
        "depends_on": [],
        "inputs": [],
        "outputs": [
          "IFACE_CANONICAL_REQUEST_FEEDS"
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
        "step_id": "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES",
        "name": "Synthesize Shared 8-Shape Geometry Catalog",
        "component_id": "COMP_CROSS_WORKLOAD_SHAPE_SYNTHESIZER",
        "phase": "GLOBAL_SHAPE_ALLOCATION",
        "operation_kind": "SYNTHESIZE_GLOBAL_SHAPES",
        "depends_on": [
          "STAGE_01_INGEST_WORKLOADS"
        ],
        "inputs": [
          "IFACE_CANONICAL_REQUEST_FEEDS"
        ],
        "outputs": [
          "IFACE_SHARED_SHAPE_CATALOG_8"
        ],
        "enforced_invariants": [
          "INV_GLOBAL_SHAPE_BUDGET_LE_8",
          "INV_TENSOR_SHAPE_ALIGNMENT"
        ],
        "required_capabilities": [
          "system-architecture",
          "interface-design"
        ]
      },
      {
        "step_id": "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
        "name": "Assemble Batches for Interactive Feed",
        "component_id": "COMP_CONSTRAINED_BATCH_ENGINE",
        "phase": "BUCKET_BATCH_PACKING",
        "operation_kind": "ASSIGN_BATCHES",
        "depends_on": [
          "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES"
        ],
        "inputs": [
          "IFACE_CANONICAL_REQUEST_FEEDS",
          "IFACE_SHARED_SHAPE_CATALOG_8"
        ],
        "outputs": [
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B1"
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
        "step_id": "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2",
        "name": "Assemble Batches for Throughput Feed",
        "component_id": "COMP_CONSTRAINED_BATCH_ENGINE",
        "phase": "BUCKET_BATCH_PACKING",
        "operation_kind": "ASSIGN_BATCHES",
        "depends_on": [
          "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES"
        ],
        "inputs": [
          "IFACE_CANONICAL_REQUEST_FEEDS",
          "IFACE_SHARED_SHAPE_CATALOG_8"
        ],
        "outputs": [
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B2"
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
        "step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "name": "Audit Cost, SLA, and Exact-Once Constraints",
        "component_id": "COMP_ANALYTICAL_VALIDATION_GUARD",
        "phase": "COST_AND_SLA_VALIDATION",
        "operation_kind": "EVALUATE_GATES",
        "depends_on": [
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "inputs": [
          "IFACE_SHARED_SHAPE_CATALOG_8",
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B1",
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B2"
        ],
        "outputs": [
          "IFACE_VALIDATION_AUDIT_VERDICT"
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
        "step_id": "STAGE_06_ATOMIC_STAGE_RENAME_COMMIT",
        "name": "Execute Generation Directory Swap Commit",
        "component_id": "COMP_CRASH_SAFE_STAGE_COMMISSIONER",
        "phase": "ATOMIC_COMMIT_AND_ROLLBACK",
        "operation_kind": "COMMIT_OUTPUTS",
        "depends_on": [
          "STAGE_05_VERIFY_COST_AND_SLA_GATES"
        ],
        "inputs": [
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B1",
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B2",
          "IFACE_VALIDATION_AUDIT_VERDICT"
        ],
        "outputs": [
          "IFACE_PERMANENT_OUTPUT_PLANS"
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
        "step_id": "STAGE_07_PURGE_STAGING_ON_ABORT",
        "name": "Purge Staging and Restore Baseline State on Abort",
        "component_id": "COMP_CRASH_SAFE_STAGE_COMMISSIONER",
        "phase": "ATOMIC_COMMIT_AND_ROLLBACK",
        "operation_kind": "ROLLBACK_ON_FAILURE",
        "depends_on": [
          "STAGE_05_VERIFY_COST_AND_SLA_GATES"
        ],
        "inputs": [
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B1",
          "IFACE_STAGED_BATCH_SPECIFICATIONS_B2",
          "IFACE_VALIDATION_AUDIT_VERDICT"
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
  "invariants": [
    {
      "invariant_id": "INV_EXACT_ONCE_ASSIGNMENT",
      "invariant_category": "EXACT_ONCE_ASSIGNMENT",
      "name": "Exact-Once Request Completeness",
      "description": "Every incoming inference request across both buckets is assigned to a batch exactly once without duplicates, omissions, or unassigned requests",
      "scope": "CROSS_BUCKET",
      "enforcing_step_ids": [
        "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
        "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2",
        "STAGE_05_VERIFY_COST_AND_SLA_GATES"
      ],
      "verification_gate_ids": [
        "GATE_EXACT_ONCE_INTEGRITY"
      ]
    },
    {
      "invariant_id": "INV_TENSOR_SHAPE_ALIGNMENT",
      "invariant_category": "TENSOR_SHAPE_ALIGNMENT",
      "name": "Static Tensor Shape Alignment Granularity",
      "description": "All assigned batches use static hardware shapes with seq_align as a multiple of 64 tokens, heads_align=32, and hidden_align=4096",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES",
        "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
        "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
      ],
      "verification_gate_ids": [
        "GATE_TENSOR_SHAPE_ALIGNMENT"
      ]
    },
    {
      "invariant_id": "INV_GLOBAL_SHAPE_BUDGET_LE_8",
      "invariant_category": "GLOBAL_SHAPE_BUDGET_LE_8",
      "name": "Global 8-Shape Compilation Ceiling",
      "description": "The union of distinct compiled shapes across both interactive and throughput workloads is bounded by <= 8 unique shapes",
      "scope": "CROSS_BUCKET",
      "enforcing_step_ids": [
        "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES",
        "STAGE_05_VERIFY_COST_AND_SLA_GATES"
      ],
      "verification_gate_ids": [
        "GATE_GLOBAL_SHAPE_LIMIT"
      ]
    },
    {
      "invariant_id": "INV_COST_AND_LATENCY_GATES",
      "invariant_category": "COST_AND_LATENCY_GATES",
      "name": "Cost Model and SLA Compliance",
      "description": "Generated plans satisfy mathematical bounds for padding waste, p95 latency, sequential timecost, and compilation overhead",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STAGE_05_VERIFY_COST_AND_SLA_GATES"
      ],
      "verification_gate_ids": [
        "GATE_SLA_AND_COST_BOUNDS"
      ]
    },
    {
      "invariant_id": "INV_IMMUTABLE_INPUTS",
      "invariant_category": "IMMUTABLE_INPUTS",
      "name": "Immutable Input Storage Guarantee",
      "description": "Input requests files remain strictly unmodified and read-only during execution and recovery",
      "scope": "STORAGE",
      "enforcing_step_ids": [
        "STAGE_01_INGEST_WORKLOADS",
        "STAGE_07_PURGE_STAGING_ON_ABORT"
      ],
      "verification_gate_ids": [
        "GATE_IMMUTABLE_INPUTS_VERIFICATION"
      ]
    },
    {
      "invariant_id": "INV_ATOMIC_TRANSACTION_OR_ROLLBACK",
      "invariant_category": "ATOMIC_TRANSACTION_OR_ROLLBACK",
      "name": "Atomic Transaction Commit and Rollback Safety",
      "description": "File commits utilize isolated staging generation directory with atomic swap, ensuring failure preserves prior state without partial writes",
      "scope": "STORAGE",
      "enforcing_step_ids": [
        "STAGE_06_ATOMIC_STAGE_RENAME_COMMIT",
        "STAGE_07_PURGE_STAGING_ON_ABORT"
      ],
      "verification_gate_ids": [
        "GATE_ATOMIC_WRITE_VERIFICATION"
      ]
    },
    {
      "invariant_id": "INV_DETERMINISTIC_TIE_BREAKING",
      "invariant_category": "DETERMINISTIC_TIE_BREAKING",
      "name": "Deterministic Sorting and Tie-Breaking",
      "description": "Deterministic tie-breaking on sequence lengths and request IDs guarantees reproducible plan generation",
      "scope": "GLOBAL",
      "enforcing_step_ids": [
        "STAGE_01_INGEST_WORKLOADS",
        "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
        "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
      ],
      "verification_gate_ids": [
        "GATE_DETERMINISTIC_ORDERING"
      ]
    }
  ],
  "risks_and_mitigations": {
    "risks": [
      {
        "risk_id": "RISK_CROSS_BUCKET_SHAPE_COLLISION",
        "description": "Uncoordinated shape selection across buckets exceeding the 8 compiled shapes limit",
        "severity": "CRITICAL",
        "mitigation_step_ids": [
          "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES"
        ]
      },
      {
        "risk_id": "RISK_ORPHANED_OR_DUPLICATE_REQUEST",
        "description": "Request omission or duplicate assignment violating exact-once invariant",
        "severity": "CRITICAL",
        "mitigation_step_ids": [
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2",
          "STAGE_05_VERIFY_COST_AND_SLA_GATES"
        ]
      },
      {
        "risk_id": "RISK_INCOMPLETE_OUTPUT_WRITE",
        "description": "Interrupted execution causing truncated or corrupt destination plan files",
        "severity": "HIGH",
        "mitigation_step_ids": [
          "STAGE_06_ATOMIC_STAGE_RENAME_COMMIT",
          "STAGE_07_PURGE_STAGING_ON_ABORT"
        ]
      },
      {
        "risk_id": "RISK_EXCESSIVE_PADDING_OVERHEAD",
        "description": "Loose alignment causing excessive padding waste and SLA failure",
        "severity": "HIGH",
        "mitigation_step_ids": [
          "STAGE_05_VERIFY_COST_AND_SLA_GATES"
        ]
      }
    ],
    "gates": [
      {
        "gate_id": "GATE_EXACT_ONCE_INTEGRITY",
        "gate_category": "REQUEST_INTEGRITY",
        "name": "Request Exact-Once Integrity Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_EXACT_ONCE_ASSIGNMENT",
        "precondition_step_ids": [
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_GLOBAL_SHAPE_LIMIT",
        "gate_category": "GLOBAL_SHAPE_BUDGET",
        "name": "Global 8-Shape Compilation Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_GLOBAL_SHAPE_BUDGET_LE_8",
        "precondition_step_ids": [
          "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_TENSOR_SHAPE_ALIGNMENT",
        "gate_category": "GLOBAL_SHAPE_BUDGET",
        "name": "Hardware Alignment Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_TENSOR_SHAPE_ALIGNMENT",
        "precondition_step_ids": [
          "STAGE_02_SYNTHESIZE_GLOBAL_SHAPES",
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_SLA_AND_COST_BOUNDS",
        "gate_category": "SLA_AND_COST_BOUNDS",
        "name": "SLA and Cost Analytical Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_COST_AND_LATENCY_GATES",
        "precondition_step_ids": [
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_IMMUTABLE_INPUTS_VERIFICATION",
        "gate_category": "ATOMIC_WRITE_VERIFICATION",
        "name": "Immutable Input Storage Verification Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_IMMUTABLE_INPUTS",
        "precondition_step_ids": [
          "STAGE_01_INGEST_WORKLOADS",
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_ATOMIC_WRITE_VERIFICATION",
        "gate_category": "ATOMIC_WRITE_VERIFICATION",
        "name": "Atomic Write Verification Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_ATOMIC_TRANSACTION_OR_ROLLBACK",
        "precondition_step_ids": [
          "STAGE_01_INGEST_WORKLOADS",
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      },
      {
        "gate_id": "GATE_DETERMINISTIC_ORDERING",
        "gate_category": "REQUEST_INTEGRITY",
        "name": "Deterministic Ordering Verification Gate",
        "evaluation_step_id": "STAGE_05_VERIFY_COST_AND_SLA_GATES",
        "target_metric_or_invariant": "INV_DETERMINISTIC_TIE_BREAKING",
        "precondition_step_ids": [
          "STAGE_01_INGEST_WORKLOADS",
          "STAGE_03_ASSEMBLE_JOINT_BATCHES_B1",
          "STAGE_04_ASSEMBLE_JOINT_BATCHES_B2"
        ],
        "blocking": true,
        "fallback_action": "ABORT_AND_ROLLBACK"
      }
    ]
  },
  "rollout_and_recovery": {
    "atomic_commit_strategy": {
      "strategy_kind": "GENERATION_DIRECTORY_SWAP",
      "staging_directory": "/app/task_file/output_data/.staging_gen_2",
      "atomic_publication_unit": "/app/task_file/output_data/.staging_gen_2 -> /app/task_file/output_data/current_gen",
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
      "verification_method": "READ_ONLY_ACCESS"
    },
    "rollback_guarantees": {
      "preserves_inputs": true,
      "preserves_prior_outputs": true,
      "prevents_partial_writes": true,
      "rollback_step_ids": [
        "STAGE_07_PURGE_STAGING_ON_ABORT"
      ]
    }
  }
}''')


def main() -> None:
    sys.stdout.write(json.dumps(CANDIDATE_PLAN, indent=2) + "\n")


if __name__ == "__main__":
    main()
