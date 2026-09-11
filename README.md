# containerlab-labs

Reproducible network labs built with [Containerlab](https://containerlab.dev/).
Each lab ships a topology, per-node configs, and a README covering what it
demonstrates, how to verify it, and how to break it on purpose.

Built for study and for staying hands-on with protocols that do not come up
every day at work.

## Labs

| # | Lab | Covers |
|---|-----|--------|
| 01 | [Dual-homed customer BGP](01-bgp-dual-homed/) | eBGP, iBGP, `next-hop-self`, local-preference, AS-path prepending, OSPF underlay, link failover |
| 02 | [Junos NETCONF to Grafana](02-junos-netconf-grafana/) | Junos access ports, read-only NETCONF login class, operational RPCs, XML reshaping, Grafana Infinity datasource |
| 03 | [MPLS L3VPN](03-mpls-l3vpn/) | VRFs, LDP, VPNv4 iBGP, RD/RT, `as-override`, overlapping customer addressing, label-switched forwarding |

## Install

```bash
sudo ./install.sh
```

Installs Docker and containerlab, loads the MPLS kernel modules lab 03 needs,
and persists them across reboot. Re-running is safe; anything already present
is left alone. Supports Arch, Debian/Ubuntu and Fedora.

Lab 02 additionally needs a vJunos-switch image, which does not pull and is not
something the installer can fetch — its README covers building one with
vrnetlab.

## Run

```bash
cd 03-mpls-l3vpn
make up        # deploy, bootstrap the nodes, then verify
make down
```

Labs 01 and 02 have no bootstrap step:

```bash
cd 01-bgp-dual-homed
sudo containerlab deploy -t bgp-dual-homed.clab.yml
```

## Layout

```
NN-lab-name/
├── README.md              # what it shows, how to verify it, how to break it
├── lab-name.clab.yml      # topology
├── Makefile               # up / verify / shell / down, where a lab needs one
└── configs/
    ├── daemons            # FRR daemon enablement
    └── <node>.conf        # per-node startup config
```

Labs that carry tooling beyond configs keep it in its own directory rather than
in `configs/` — `scripts/` in lab 03, `netconf/` and `grafana/` in lab 02.

## Verification

Lab 03 asserts its own correctness rather than asking you to eyeball command
output. `make verify` checks the control plane, the data plane, and VRF
isolation separately, because passing one does not imply the others. A VPN can
have every session established and still forward nothing.

CI validates syntax and topology consistency on every push. It does not deploy
the labs, because GitHub runners lack reliable MPLS kernel support and a green
tick that does not mean anything is worse than no tick.

## Requirements

- Docker
- [Containerlab](https://containerlab.dev/install/)
- ~1GB RAM for the FRR labs (01 and 03); lab 02 wants ~5GB
- MPLS kernel modules for lab 03 — `install.sh` handles these
- A locally built vJunos-switch image for lab 02

FRR, Grafana and Python images pull automatically on first deploy. Vendor
images do not.

## Why this exists

Reading about BGP path selection and watching a route move because you changed
one local-pref are different kinds of knowing. These labs are the second kind.
