# containerlab-labs

Reproducible network labs built with [Containerlab](https://containerlab.dev/).
Each lab is self-contained: a topology file, per-node configs, and a README
covering what it demonstrates, how to verify it, and how to break it.

Built for study and for keeping hands-on with protocols that don't come up
every day at work.

## Labs

| # | Lab | Covers |
|---|-----|--------|
| 01 | [Dual-homed customer BGP](01-bgp-dual-homed/) | eBGP, iBGP, `next-hop-self`, local-preference, AS-path prepending, OSPF underlay, link failover |
| 02 | [Junos NETCONF to Grafana](02-junos-netconf-grafana/) | Junos access ports, read-only NETCONF login class, operational RPCs, XML reshaping, Grafana Infinity datasource |

## Requirements

- Docker
- [Containerlab](https://containerlab.dev/install/)
- ~1GB RAM for the FRR labs; lab 02 wants ~5GB and a locally built vJunos image

FRR, Grafana and Python images pull automatically on first deploy. Vendor
images do not — lab 02's README covers building it with vrnetlab.

## Layout

```
NN-lab-name/
├── README.md              # what it shows, how to verify, how to break it
├── lab-name.clab.yml      # topology
└── configs/
    ├── daemons            # FRR daemon enablement
    └── <node>.conf        # per-node startup config
```

Labs that carry tooling alongside the topology keep it in its own directory —
`netconf/` and `grafana/` in lab 02 — rather than in `configs/`.

## Why this exists

Reading about BGP path selection and watching a route move because you changed
one local-pref are different kinds of knowing. These labs are the second kind.
