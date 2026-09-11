#!/usr/bin/env bash
#
# Applies the kernel-side setup that FRR cannot do for itself:
#   - MPLS label processing (per-namespace and per-interface)
#   - Linux VRF devices and interface enslavement
#   - interface addressing
#
# FRR reads the resulting state from the kernel via netlink, so this must
# run before the routing protocols are expected to converge. `make up`
# calls it automatically.
#
set -euo pipefail

LAB="mpls-l3vpn"
PREFIX="clab-${LAB}"

say () { printf '  %s\n' "$*"; }

# Run a command inside a node, via sh so redirection works.
in_node () {
    local node=$1; shift
    docker exec "${PREFIX}-${node}" sh -c "$*"
}

# Enable MPLS label processing in the node's namespace, then on named
# interfaces. platform_labels must be non-zero or the kernel silently
# drops every labelled packet.
enable_mpls () {
    local node=$1; shift
    in_node "$node" "echo 100000 > /proc/sys/net/mpls/platform_labels"
    for iface in "$@"; do
        in_node "$node" "echo 1 > /proc/sys/net/mpls/conf/${iface}/input"
    done
}

addr () {
    local node=$1 iface=$2 cidr=$3
    in_node "$node" "ip addr replace ${cidr} dev ${iface} && ip link set ${iface} up"
}

# Create a VRF and move an interface into it. Enslaving flushes addresses,
# so this always runs before the address is applied.
vrf_attach () {
    local node=$1 vrf=$2 table=$3 iface=$4
    in_node "$node" "ip link show ${vrf} >/dev/null 2>&1 || ip link add ${vrf} type vrf table ${table}"
    in_node "$node" "ip link set ${vrf} up"
    in_node "$node" "ip link set ${iface} master ${vrf}"
}

echo "==> Core: MPLS forwarding"
enable_mpls pe1 eth1
enable_mpls p1  eth1 eth2
enable_mpls pe2 eth1
say "label processing enabled on core-facing interfaces"

echo "==> Core: loopbacks and links"
addr pe1 lo   10.0.0.1/32
addr p1  lo   10.0.0.2/32
addr pe2 lo   10.0.0.3/32
addr pe1 eth1 10.1.0.1/30
addr p1  eth1 10.1.0.2/30
addr p1  eth2 10.1.0.5/30
addr pe2 eth1 10.1.0.6/30
say "10.0.0.0/24 loopbacks, 10.1.0.0/24 core links"

echo "==> PE: VRFs"
vrf_attach pe1 CUST-A 100 eth2
vrf_attach pe1 CUST-B 200 eth3
vrf_attach pe2 CUST-A 100 eth2
say "CUST-A (table 100), CUST-B (table 200)"

echo "==> PE: attachment circuits"
# 172.20.1.1/30 appears twice on pe1, once per VRF. Overlapping addressing
# in separate VRFs is the behaviour under test, not a mistake.
addr pe1 eth2 172.20.1.1/30
addr pe1 eth3 172.20.1.1/30
addr pe2 eth2 172.20.2.1/30
say "PE-CE links up (172.20.1.1/30 intentionally duplicated across VRFs)"

echo "==> CE: LAN and uplinks"
addr ce-a1 lo   192.168.10.1/24
addr ce-a2 lo   192.168.20.1/24
addr ce-b1 lo   192.168.10.1/24
addr ce-a1 eth1 172.20.1.2/30
addr ce-a2 eth1 172.20.2.2/30
addr ce-b1 eth1 172.20.1.2/30
say "customer LANs on lo, uplinks on eth1"

echo "==> Reloading FRR so it re-reads interfaces in their final state"
for node in pe1 p1 pe2 ce-a1 ce-a2 ce-b1; do
    in_node "$node" "vtysh -c 'clear ip ospf process' >/dev/null 2>&1 || true"
    in_node "$node" "vtysh -b >/dev/null 2>&1 || true"
done

echo
echo "Bootstrap complete. Allow 60-90s for OSPF, LDP and VPNv4 to converge."
echo "Then: make verify"
