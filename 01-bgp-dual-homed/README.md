# 01 — Dual-homed customer with BGP path preference

A customer (AS 65100) dual-homed into a two-router provider core (AS 65001),
which in turn takes transit from an upstream (AS 65002).

The point of the lab is path selection: the primary link is preferred through
two independent mechanisms, and the backup only takes over when the primary
fails. Both mechanisms are visible in the BGP table, and both can be broken on
purpose.

## Topology

```
                          AS 65002
                     ┌──────────────────┐
                     │     transit1     │  lo 198.51.100.1/24
                     └───┬──────────┬───┘
           203.0.113.0/30│          │203.0.113.4/30
                     eth3│          │eth3
                 ┌───────┴──┐    ┌──┴───────┐
     AS 65001    │   pe1    ├────┤   pe2    │   10.1.12.0/30
                 │10.0.0.1  │eth1│  10.0.0.2│   OSPF area 0
                 └───┬──────┘    └──────┬───┘   + iBGP
          172.16.1.0/30│                │172.16.2.0/30
              (primary)│                │(backup)
                   eth1│                │eth2
                     ┌─┴────────────────┴─┐
     AS 65100        │        ce1         │  lo 192.168.100.1/24
                     └────────────────────┘
```

## Addressing

| Node     | Loopback         | Links                                          |
|----------|------------------|------------------------------------------------|
| pe1      | 10.0.0.1/32      | 10.1.12.1/30, 172.16.1.2/30, 203.0.113.1/30    |
| pe2      | 10.0.0.2/32      | 10.1.12.2/30, 172.16.2.2/30, 203.0.113.5/30    |
| ce1      | 192.168.100.1/24 | 172.16.1.1/30, 172.16.2.1/30                   |
| transit1 | 198.51.100.1/24  | 203.0.113.2/30, 203.0.113.6/30                 |

## What it demonstrates

- **OSPF as the IGP underlay** — loopback reachability inside AS 65001 so the
  iBGP session has something to ride on.
- **iBGP with `next-hop-self`** — without it, pe2 learns the customer prefix
  with an unreachable next-hop and never installs it.
- **eBGP local-preference** — pe1 sets local-pref 200 on customer routes, so
  every router inside AS 65001 prefers the pe1 path regardless of where the
  traffic entered.
- **AS-path prepending** — the customer's own lever. ce1 prepends its ASN twice
  on the pe2 link, influencing inbound traffic from the provider side.
- **Failover** — pull the primary link and watch the backup path get selected.

## Bring it up

```bash
sudo containerlab deploy -t bgp-dual-homed.clab.yml
```

Teardown:

```bash
sudo containerlab destroy -t bgp-dual-homed.clab.yml --cleanup
```

## Verify

Drop into a router:

```bash
docker exec -it clab-bgp-dual-homed-pe1 vtysh
```

Sessions should all be Established (give it ~60s for OSPF to converge first):

```
show ip bgp summary
show ip ospf neighbor
```

On **pe2**, the customer prefix should be learned via iBGP from pe1 (next-hop
10.0.0.1) rather than over its own directly-connected eBGP session — that is
local-pref doing its job:

```
show ip bgp 192.168.100.0/24
```

You should see two paths, with the iBGP one marked best.

End-to-end reachability from the customer to the stand-in internet prefix:

```bash
docker exec -it clab-bgp-dual-homed-ce1 ping -I 192.168.100.1 -c 3 198.51.100.1
```

## Break it on purpose

These are the interesting bit — each one has a specific observable outcome.

**1. Fail the primary customer link**

```bash
docker exec clab-bgp-dual-homed-pe1 ip link set eth2 down
```

On pe2, the prefix should reconverge onto its own eBGP session within a few
seconds. `show ip bgp 192.168.100.0/24` now shows one path, next-hop
172.16.2.1. Bring it back up and it should revert.

**2. Remove `next-hop-self` on pe1**

In vtysh on pe1:

```
configure terminal
router bgp 65001
 address-family ipv4 unicast
  no neighbor 10.0.0.2 next-hop-self
```

pe2 still *receives* the customer prefix but marks it inaccessible — the
next-hop is now 172.16.1.1, which lives in AS 65100 and isn't in pe2's IGP.
`show ip bgp 192.168.100.0/24` shows the path without a `>` best marker. This
is the classic iBGP next-hop failure and worth being able to recognise on
sight.

**3. Break the IGP underneath iBGP**

```bash
docker exec clab-bgp-dual-homed-pe1 ip link set eth1 down
```

The iBGP session drops once the loopback becomes unreachable — a useful
reminder that iBGP over loopbacks is only as good as the IGP carrying it.
Note the hold timer: the session doesn't drop instantly.

**4. Flip the local-pref**

Change `set local-preference 200` to `50` on pe1 and watch the whole AS prefer
the pe2 path instead, even though the pe1 link is up and healthy.

## Notes

- FRR needs `no bgp ebgp-requires-policy` for eBGP sessions to exchange routes
  without an explicit inbound/outbound policy. That default changed in FRR 7.4
  and it's the usual reason a lab like this comes up with sessions Established
  but no prefixes.
- The `network` statement only advertises a prefix that already exists in the
  routing table, which is why the customer and transit prefixes sit on
  loopbacks rather than being conjured from nothing.
- Addressing uses documentation ranges (RFC 5737) for the public-facing bits.
