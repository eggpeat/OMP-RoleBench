#!/bin/sh
set -eu

fail() {
    printf 'rolebench-agent: %s\n' "$*" >&2
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
no_new_privs=$(awk '$1 == "NoNewPrivs:" { value=$2 } END { if (value == "") exit 1; print value }' /proc/self/status) || fail 'NoNewPrivs is unavailable'
[ "$no_new_privs" = 1 ] || fail "NoNewPrivs is $no_new_privs, expected 1"
cap_eff=$(awk '$1 == "CapEff:" { value=$2 } END { if (value == "") exit 1; print value }' /proc/self/status) || fail 'CapEff is unavailable'
case "$cap_eff" in
    '' | *[!0]*) fail "CapEff is nonzero: $cap_eff" ;;
esac

[ -d /sys/class/net ] || fail 'network interface inventory is unavailable'
found_loopback=false
for interface_path in /sys/class/net/*; do
    [ -e "$interface_path" ] || continue
    interface_name=${interface_path##*/}
    if [ "$interface_name" = lo ]; then
        found_loopback=true
    else
        fail "non-loopback network interface is present: $interface_name"
    fi
done
[ "$found_loopback" = true ] || fail 'loopback network interface is missing'

[ -r /proc/net/route ] || fail 'IPv4 route inventory is unavailable'
if awk 'NR > 1 && $2 == "00000000" { found=1 } END { exit(found ? 0 : 1) }' /proc/net/route; then
    fail 'IPv4 default route is present'
fi
[ -r /proc/net/ipv6_route ] || fail 'IPv6 route inventory is unavailable'
if awk '$1 == "00000000000000000000000000000000" && $2 == "00" { found=1 } END { exit(found ? 0 : 1) }' /proc/net/ipv6_route; then
    fail 'IPv6 default route is present'
fi

artifact=/workspace/result.txt
umask 077
printf '%s' 'rolebench-docker-runsc-fixture-v1' > "$artifact" || fail 'cannot create artifact payload'
chmod 0600 "$artifact" || fail 'cannot normalize artifact mode'
TZ=UTC0
export TZ
touch -t 197001010000.00 "$artifact" || fail 'cannot normalize artifact timestamp'
printf '%s\n' 'rolebench-agent: isolation probes passed; emitting deterministic tar' >&2
exec tar -cf - -C /workspace result.txt
