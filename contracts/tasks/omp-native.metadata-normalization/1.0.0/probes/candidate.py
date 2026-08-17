#!/usr/bin/env python3
"""Synthetic tiny-role candidate admission probe."""

import sys

sys.stdout.write('{"schema_version":"rolebench.metadata-normalization/v1","records":[{"run_id":"rb-041-alpha","outcome":"passed","role":"tiny","retryable":false,"latency_bucket":"under-100ms","labels":["blue","fast"]},{"run_id":"rb-042-edge","outcome":"failed","role":"plan","retryable":true,"latency_bucket":"under-100ms","labels":["triage","urgent","queue-1"]},{"run_id":"rb-043-boundary","outcome":"passed","role":"advisor","retryable":true,"latency_bucket":"100-999ms","labels":["cache","hit"]},{"run_id":"rb-044-high","outcome":"passed","role":"reviewer","retryable":true,"latency_bucket":"100-999ms","labels":["security","audit","p0"]},{"run_id":"rb-045-thousand","outcome":"failed","role":"commit","retryable":false,"latency_bucket":"at-least-1000ms","labels":[]},{"run_id":"unknown-run","outcome":"failed","role":"default","retryable":false,"latency_bucket":"at-least-1000ms","labels":[]},{"run_id":"rb-047-slow-run","outcome":"passed","role":"designer","retryable":false,"latency_bucket":"at-least-1000ms","labels":["ui","frontend","css"]}]}')
