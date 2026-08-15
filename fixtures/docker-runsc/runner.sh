#!/bin/sh
set -eu

fail() {
    printf 'rolebench-runner: %s\n' "$*" >&2
    exit 1
}

EXPECTED_UID=${ROLEBENCH_EXPECTED_UID:-1000}
EXPECTED_GID=${ROLEBENCH_EXPECTED_GID:-1000}
case "$EXPECTED_UID:$EXPECTED_GID" in
    *[!0-9:]* | :* | *:)
        fail 'expected uid and gid must be decimal integers'
        ;;
esac

actual_uid=$(id -u) || fail 'cannot read uid'
actual_gid=$(id -g) || fail 'cannot read gid'
[ "$actual_uid" = "$EXPECTED_UID" ] || fail "uid is $actual_uid, expected $EXPECTED_UID"
[ "$actual_gid" = "$EXPECTED_GID" ] || fail "gid is $actual_gid, expected $EXPECTED_GID"

root_probe="/rolebench-root-write-probe.$$"
if (umask 077; : > "$root_probe") 2>/dev/null; then
    rm -f "$root_probe"
    fail 'root filesystem is writable'
fi

[ -d /workspace ] || fail '/workspace is missing'
workspace_probe="/workspace/.rolebench-write-probe.$$"
(umask 077; printf '%s' probe > "$workspace_probe") 2>/dev/null || fail '/workspace is not writable'
rm -f "$workspace_probe" || fail 'cannot remove workspace probe'

[ -r /proc/self/status ] || fail 'cannot read process status'
no_new_privs=$(awk '$1 == "NoNewPrivs:" { value=$2 } END { print value }' /proc/self/status) || fail 'cannot inspect NoNewPrivs'
if [ -n "$no_new_privs" ]; then
    [ "$no_new_privs" = 1 ] || fail "NoNewPrivs is $no_new_privs, expected 1"
else
    [ "$(cat /proc/gvisor/kernel_is_gvisor 2>/dev/null || true)" = gvisor ] \
        || fail 'NoNewPrivs is unavailable outside a marked gVisor sandbox'
fi
cap_eff=$(awk '$1 == "CapEff:" { value=$2 } END { if (value == "") exit 1; print value }' /proc/self/status) || fail 'CapEff is unavailable'
case "$cap_eff" in
    '' | *[!0]*) fail "CapEff is nonzero: $cap_eff" ;;
esac

[ -r /proc/net/dev ] || fail 'network interface inventory is unavailable'
interfaces=$(awk 'NR > 2 { name=$1; sub(/:$/, "", name); print name }' /proc/net/dev) \
    || fail 'cannot inspect network interfaces'
[ "$interfaces" = lo ] || fail "network interfaces are not loopback-only: $interfaces"

[ -r /proc/net/route ] || fail 'IPv4 route inventory is unavailable'
if awk 'NR > 1 && $2 == "00000000" { found=1 } END { exit(found ? 0 : 1) }' /proc/net/route; then
    fail 'IPv4 default route is present'
fi
[ -r /proc/net/ipv6_route ] || fail 'IPv6 route inventory is unavailable'
if awk '$1 == "00000000000000000000000000000000" && $2 == "00" { found=1 } END { exit(found ? 0 : 1) }' /proc/net/ipv6_route; then
    fail 'IPv6 default route is present'
fi

payload=$(dd bs=34 count=1 2>/dev/null) || fail 'cannot read artifact payload'
[ "${#payload}" -eq 33 ] || fail 'artifact payload length is not exactly 33 bytes'
[ "$payload" = 'rolebench-docker-runsc-fixture-v1' ] || fail 'artifact payload is not the expected value'

umask 077
printf '%s' 'rolebench-docker-runsc-runner-pass'
printf '%s\n' 'rolebench-runner: isolation probes passed; candidate artifact verified; emitting deterministic payload' >&2
