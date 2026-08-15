#!/usr/bin/env python3
"""Deterministic candidate probe for the multi-source merger task."""

from __future__ import annotations

import sys

PAYLOAD = '{"conflict_report":{"conflicts":[{"field":"name","selected":"Example Alpha","user_id":101,"values":{"source_a":"Example Alpha","source_b":"Example Alpha Secondary","source_c":"Example A"}},{"field":"email","selected":"alpha-primary@example.test","user_id":101,"values":{"source_a":"alpha-primary@example.test","source_b":"alpha-secondary@example.test","source_c":"alpha-tertiary@example.test"}},{"field":"created_date","selected":"2024-01-15","user_id":101,"values":{"source_a":"2024-01-15","source_b":"2024-01-10","source_c":"2024-01-20"}},{"field":"name","selected":"Example Beta","user_id":102,"values":{"source_a":"Example Beta","source_b":"Example Beta","source_c":"Example Beta Tertiary"}},{"field":"email","selected":"beta-secondary@example.test","user_id":102,"values":{"source_b":"beta-secondary@example.test","source_c":"beta-tertiary@example.test"}},{"field":"created_date","selected":"2024-02-20","user_id":102,"values":{"source_a":"2024-02-20","source_b":"2024-02-19","source_c":"2024-02-18"}},{"field":"status","selected":"inactive","user_id":102,"values":{"source_a":"inactive","source_b":"inactive","source_c":"active"}},{"field":"email","selected":"epsilon-primary@example.test","user_id":105,"values":{"source_a":"epsilon-primary@example.test","source_b":"epsilon-primary@example.test","source_c":"epsilon-tertiary@example.test"}},{"field":"created_date","selected":"2024-05-01","user_id":105,"values":{"source_a":"2024-05-01","source_b":"2024-05-01","source_c":"2024-05-02"}},{"field":"status","selected":"active","user_id":105,"values":{"source_a":"active","source_b":"active","source_c":"inactive"}}],"total_conflicts":10},"merged_users":[{"created_date":"2024-01-15","email":"alpha-primary@example.test","name":"Example Alpha","status":"active","user_id":101},{"created_date":"2024-02-20","email":"beta-secondary@example.test","name":"Example Beta","status":"inactive","user_id":102},{"created_date":"2024-03-01","email":"gamma-secondary@example.test","name":"Example Gamma","status":"active","user_id":103},{"created_date":"2024-04-01","email":"delta-tertiary@example.test","name":"Example Delta","status":"active","user_id":104},{"created_date":"2024-05-01","email":"epsilon-primary@example.test","name":"Example Epsilon","status":"active","user_id":105}],"schema_version":"rolebench.multi-source-submission/v1"}'

def main() -> None:
    sys.stdout.write(PAYLOAD)

if __name__ == "__main__":
    main()
