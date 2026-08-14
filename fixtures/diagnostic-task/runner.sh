#!/bin/sh
set -eu

expected=$(cat /opt/rolebench/task/public/workspace/input.txt)
payload=$(cat)

if [ "$payload" = "$expected" ]; then
    printf '%s' 'rolebench-diagnostic-runner-pass'
    printf 'rolebench-task-runner: candidate artifact matched public input\n' >&2
    exit 0
else
    printf '%s' 'rolebench-diagnostic-runner-fail'
    printf 'rolebench-task-runner: candidate artifact did not match public input\n' >&2
    exit 0
fi
