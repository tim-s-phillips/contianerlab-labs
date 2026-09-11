# 03 — MPLS L3VPN with overlapping customer addressing

Two customers over one provider core. Customer A has two sites and needs them
joined. Customer B has one site. Both customers use 192.168.10.0/24, and both
PE-CE links use 172.20.1.0/30, so the lab only works if VRF separation is real
rather than decorative.

The transit router in the middle carries customer traffic without holding a
single customer route. That is the part worth understanding, and the
verification script asserts it.

## Topology

```
        AS 65100                AS 65000                     AS 65100
      ┌─────────┐    ┌──────┐   ┌────┐   ┌──────┐          ┌─────────┐
      │  ce-a1  ├────┤ pe1  ├───┤ p1 ├───┤ pe2  ├──────────┤  ce-a2  │
      │192.168. │eth2│      │   │    │   │      │eth2      │192.168. │
      │  10.0/24│    │VRF A │   │ no │   │VRF A │          │  20.0/24│
      └─────────┘    │VRF B │   │ BGP│   └──────┘          └─────────┘
                     └───┬──┘   └────┘
                     eth3│
                  ┌──────┴──┐
      AS 65200    │  ce-b1  │   192.168.10.0/24  ← same prefix as ce-a1
                  └─────────┘
```

## Addressing

| Node  | Loopback / LAN   | Links                          | VRF    |
|-------|------------------|--------------------------------|--------|
| pe1   | 10.0.0.1/32      | 10.1.0.1/30 (core)             | —      |
|       |                  | 172.20.1.1/30 (to ce-a1)       | CUST-A |
|       |                  | 172.20.1.1/30 (to ce-b1)       | CUST-B |
| p1    | 10.0.0.2/32      | 10.1.0.2/30, 10.1.0.5/30       | —      |
| pe2   | 10.0.0.3/32      | 10.1.0.6/30 (core)             | —      |
|       |                  | 172.20.2.1/30 (to ce-a2)       | CUST-A |
| ce-a1 | 192.168.10.1/24  | 172.20.1.2/30                  | —      |
| ce-a2 | 192.168.20.1/24  | 172.20.2.2/30                  | —      |
| ce-b1 | 192.168.10.1/24  | 172.20.1.2/30                  | —      |

`172.20.1.1/30` appearing twice on pe1 is deliberate. Different VRFs, different
routing tables, no conflict.

RD/RT: CUST-A is `65000:100`, CUST-B is `65000:200`. pe2 never imports
`65000:200`, so customer B's prefix does not exist there at all.

## Run it

From a clean machine:

```bash
sudo ../install.sh     # Docker, containerlab, MPLS kernel modules
make up                # deploy, bootstrap, verify
```

`make up` runs three stages. `deploy` starts the containers, `bootstrap` applies
the kernel-side setup FRR cannot do for itself, and `verify` asserts the result.

## Why bootstrap-nodes.sh exists

FRR configures routing protocols. It does not create Linux VRF devices, enslave
interfaces to them, or switch on MPLS label processing in the kernel. Those are
netlink operations that have to happen first, which is what the bootstrap script
does:

- `net.mpls.platform_labels` per namespace, or the kernel drops labelled packets
  with no log entry
- `net.mpls.conf.<iface>.input=1` on every core-facing interface
- `ip link add CUST-A type vrf table 100`, then enslave, then address

Order matters in the last one. Enslaving an interface to a VRF flushes its
addresses, so addressing has to come after.

## Verify

`make verify` runs twelve assertions across three categories, because passing
one does not imply the others:

**Control plane** — OSPF adjacencies, LDP sessions operational, VPNv4 session
established between the PEs.

**Routes** — the far site's prefix present in `pe2`'s VRF CUST-A, `ce-a2`
learning it over eBGP, and no customer prefix anywhere on `p1`.

**Data plane** — `ce-a1` pinging `ce-a2` across the core, and `pe1` holding
actual label bindings.

**Isolation** — customer A's remote prefix absent from VRF CUST-B, `ce-b1`
failing to reach customer A, and both copies of 192.168.10.0/24 present in
their own VRFs.

Manual poke around:

```bash
make shell N=pe1
```

```
show bgp ipv4 vpn                  # both RDs, both customers
show ip route vrf CUST-A
show ip route vrf CUST-B           # same prefix, different table
show mpls table                    # LDP transport + BGP VPN labels
```

On p1, `show ip route` should contain nothing from 192.168.0.0/16. If it does,
something has leaked.

## Break it on purpose

**1. Turn off MPLS label input on the core**

```bash
docker exec clab-mpls-l3vpn-p1 sh -c "echo 0 > /proc/sys/net/mpls/conf/eth1/input"
```

Every protocol stays up. OSPF is fine, LDP is fine, VPNv4 is fine, and customer
traffic stops dead. This is the L3VPN fault that wastes the most time, because
the control plane gives no hint. `make verify` catches it as a data plane
failure with the control plane checks all passing.

**2. Remove `as-override` on pe2**

```
configure terminal
router bgp 65000 vrf CUST-A
 address-family ipv4 unicast
  no neighbor 172.20.2.2 as-override
```

ce-a2 stops accepting the far site's prefix. Both sites are AS 65100, so the
route arrives with 65100 already in the path and gets dropped as a loop. The
alternative fix lives on the customer side (`allowas-in`), which is worth
knowing when the customer owns the CE and you do not.

**3. Break the RT import**

Change `rt vpn both 65000:100` to `65000:999` on pe2. The VPNv4 session stays
up and the routes still arrive, they just do not get imported into the VRF.
`show bgp ipv4 vpn` shows the prefix, `show ip route vrf CUST-A` does not.

**4. Drop the IGP under LDP**

```bash
docker exec clab-mpls-l3vpn-p1 ip link set eth1 down
```

Watch the order things fail in: OSPF adjacency, then LDP session, then the
label-switched path, then the VPNv4 next-hop becomes unreachable. Four layers,
one cause.

## Notes and caveats

- **Requires MPLS kernel modules on the host.** `install.sh` loads
  `mpls_router` and `mpls_iptunnel` and persists them. Some distro kernels ship
  without MPLS support entirely; `zgrep MPLS /proc/config.gz` tells you.
- Containers run privileged (containerlab's default for `linux` nodes) because
  writing to `/proc/sys/net/mpls/` needs it.
- `label vpn export auto` is required in FRR for VPN label allocation. Without
  it the routes get exported with no label and forwarding fails silently.
- `no bgp default ipv4-unicast` on the core BGP instance stops the PEs forming
  an unwanted global IPv4 session alongside the VPNv4 one.
- Convergence takes 60-90s from a cold start. LDP waits on OSPF, VPNv4 waits on
  loopback reachability.
