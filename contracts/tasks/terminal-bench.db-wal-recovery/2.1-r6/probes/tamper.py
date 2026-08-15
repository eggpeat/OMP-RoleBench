#!/usr/bin/env python3
"""Deterministic tamper admission probe for db-wal-recovery: emits forged hypothesis evidence."""

from __future__ import annotations

import sys

SUBMISSION = '{\n  "schema_version": "rolebench.db-wal-recovery-submission/v1",\n  "diagnosis": {\n    "sidecar_file": "main.db-wal",\n    "observed_magic_hex": "753d44c0",\n    "expected_magic_hex": "377f0682",\n    "corruption_mechanism": "bit-inversion"\n  },\n  "hypothesis_testing": [\n    {\n      "hypothesis_id": "hyp-invert",\n      "transform_kind": "invert",\n      "parameter_int": 0,\n      "resulting_magic_hex": "377f0682",\n      "wal_magic_valid": true\n    },\n    {\n      "hypothesis_id": "hyp-xor-0x20",\n      "transform_kind": "xor",\n      "parameter_int": 32,\n      "resulting_magic_hex": "551d64e0",\n      "wal_magic_valid": false\n    }\n  ],\n  "selected_transform": {\n    "transform_kind": "invert",\n    "parameter_int": 0,\n    "target_magic_hex": "377f0682"\n  },\n  "recovery_verification": {\n    "base_record_count": 5,\n    "recovered_record_count": 11,\n    "checkpoint_applied": true\n  },\n  "recovered_records": [\n    {\n      "id": 1,\n      "name": "apple",\n      "value": 150\n    },\n    {\n      "id": 2,\n      "name": "banana",\n      "value": 250\n    },\n    {\n      "id": 3,\n      "name": "cherry",\n      "value": 300\n    },\n    {\n      "id": 4,\n      "name": "date",\n      "value": 400\n    },\n    {\n      "id": 5,\n      "name": "elderberry",\n      "value": 500\n    },\n    {\n      "id": 6,\n      "name": "fig",\n      "value": 600\n    },\n    {\n      "id": 7,\n      "name": "grape",\n      "value": 700\n    },\n    {\n      "id": 8,\n      "name": "honeydew",\n      "value": 800\n    },\n    {\n      "id": 9,\n      "name": "kiwi",\n      "value": 900\n    },\n    {\n      "id": 10,\n      "name": "lemon",\n      "value": 1000\n    },\n    {\n      "id": 11,\n      "name": "mango",\n      "value": 1100\n    }\n  ]\n}'

def main() -> None:
    sys.stdout.write(SUBMISSION)

if __name__ == "__main__":
    main()
