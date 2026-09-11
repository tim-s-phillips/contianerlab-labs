#!/bin/sh
# Bring up a "user" endpoint on an access port.
#
# containerlab attaches the veth after the container starts, so wait for eth1
# to appear before addressing it. The ping loop exists so the switch keeps a
# MAC table and ARP entry for this host - without traffic both age out and the
# NETCONF poll has nothing to report.
set -u

USER_IP=${USER_IP:-10.10.10.11}
GATEWAY=${GATEWAY:-10.10.10.1}
PREFIX=${PREFIX:-24}

i=0
while [ $i -lt 60 ] && ! ip link show eth1 >/dev/null 2>&1; do
    i=$((i + 1))
    sleep 1
done

ip link set eth1 up
ip addr add "$USER_IP/$PREFIX" dev eth1 2>/dev/null || true
ip route add default via "$GATEWAY" 2>/dev/null || true

echo "user endpoint $USER_IP/$PREFIX up on eth1, gateway $GATEWAY"

while true; do
    ping -c 1 -W 2 "$GATEWAY" >/dev/null 2>&1 || true
    sleep 10
done
