#!/bin/sh
set -eu

reject() {
    printf 'rolebench-verifier: %s\n' "$*" >&2
    printf '%s\n' '{"outcome":"rejected","reward":0}'
    exit 0
}

payload=$(dd bs=34 count=1 2>/dev/null) || reject 'cannot read artifact payload'
[ "${#payload}" -eq 33 ] \
    || reject 'artifact payload length is not exactly 33 bytes'
[ "$payload" = 'rolebench-docker-runsc-fixture-v1' ] \
    || reject 'artifact payload is not the expected value'

printf '%s\n' 'rolebench-verifier: artifact accepted' >&2
printf '%s\n' '{"outcome":"accepted","reward":1}'
