#!/bin/sh
set -eu

case "${ROLEBENCH_PROBE_MODE:-}" in
  baseline)
    printf '%s' 'incorrect-artifact'
    ;;
  reference|production)
    cat /opt/rolebench/task/public/workspace/input.txt
    ;;
  tamper)
    cat /opt/rolebench/task/public/workspace/input.txt
    printf '%s' '-tampered'
    ;;
  *)
    printf '%s\n' 'rolebench-probe: invalid probe mode' >&2
    exit 64
    ;;
esac
