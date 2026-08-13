#!/bin/sh
set -eu

member_fail() {
    printf 'rolebench-verifier member: %s\n' "$*" >&2
    exit 1
}

if [ "${ROLEBENCH_TAR_MEMBER_CHECK:-}" = 1 ]; then
    [ "${TAR_FILENAME:-}" = result.txt ] || member_fail "unexpected member: ${TAR_FILENAME:-missing}"
    [ "${TAR_SIZE:-}" = 33 ] || member_fail "result.txt size is ${TAR_SIZE:-missing}, expected 33"
    payload=$(cat) || member_fail 'cannot read result.txt'
    [ "$payload" = 'rolebench-docker-runsc-fixture-v1' ] || member_fail 'result.txt payload mismatch'
    printf '%s\n' result.txt
    exit 0
fi

reject() {
    printf 'rolebench-verifier: %s\n' "$*" >&2
    printf '%s\n' '{"outcome":"rejected","reward":0}'
    exit 0
}

# BusyBox tar invokes the fixed command once per regular archive member and streams
# that member on stdin. The captured marker must therefore be exactly one result.txt.
# Non-regular extra members require filesystem extraction, which fails on the
# verifier container's read-only root filesystem.
if markers=$(ROLEBENCH_TAR_MEMBER_CHECK=1 tar -xf - --to-command "$0"); then
    :
else
    reject 'artifact is not a readable single-member tar'
fi
[ "$markers" = result.txt ] || reject 'artifact member set is not exactly result.txt'

printf '%s\n' 'rolebench-verifier: artifact accepted' >&2
printf '%s\n' '{"outcome":"accepted","reward":1}'
