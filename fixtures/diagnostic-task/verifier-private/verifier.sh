#!/bin/sh
set -eu

expected=$(cat /opt/rolebench/task/verifier-private/expected.txt)
payload=$(dd bs=128 count=1 2>/dev/null) || {
  printf '%s\n' '{"outcome":"rejected","reward":0}'
  exit 0
}

if [ "$payload" = "$expected" ]; then
  printf '%s\n' '{"outcome":"accepted","reward":1}'
else
  printf '%s\n' '{"outcome":"rejected","reward":0}'
fi
