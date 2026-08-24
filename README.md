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

## Requirements

- Docker
- [Containerlab](https://containerlab.dev/install/)
- ~1GB RAM for the labs here (FRR containers are light; vendor images are not)

Images pull automatically on first deploy.

## Layout

```
NN-lab-name/
├── README.md              # what it shows, how to verify, how to break it
├── lab-name.clab.yml      # topology
└── configs/
    ├── daemons            # FRR daemon enablement
    └── <node>.conf        # per-node startup config
```

## Why this exists

Reading about BGP path selection and watching a route move because you changed
one local-pref are different kinds of knowing. These labs are the second kind.
