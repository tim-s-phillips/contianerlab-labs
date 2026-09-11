# 02 — Junos access ports over NETCONF, into Grafana

Four users plugged into a Junos switch, a read-only NETCONF account that can
see their port state and nothing else, and a flat XML document that Grafana
parses straight off an HTTP endpoint.

The point of the lab is the read-only telemetry path: how you authorise an
account to poll and only poll, what the Junos RPCs actually return, and how to
get from a 2000-line `<rpc-reply>` to something a dashboard can render without
standing up a whole TSDB first.

## Topology

```
   host :3000 ──▶ ┌─────────┐   HTTP /users.xml   ┌────────┐
                  │ grafana │ ───────────────────▶│ poller │◀── host :8080
                  └─────────┘                     └───┬────┘
                                                      │
                       NETCONF over SSH :830          │  user: telemetry
                       (containerlab mgmt net)        │  class: netconf-ro
                                                      ▼
                  ┌────────────────────────────────────────────┐
                  │                  access1                   │  vJunos-switch
                  │   VLAN users (id 10), irb.0 10.10.10.1/24  │
                  └───┬─────────┬──────────┬──────────┬────────┘
                 ge-0/0/0   ge-0/0/1   ge-0/0/2   ge-0/0/3
                      │         │          │          │
                    user1     user2      user3      user4
                  .11        .12        .13        .14      10.10.10.0/24
```

`grafana` and `poller` have no data-plane links — they reach `access1` over the
containerlab management bridge, which is how you would reach a real switch's
management interface.

## Addressing

| Node    | Address           | Port on access1 | Description          |
|---------|-------------------|-----------------|----------------------|
| access1 | irb.0 10.10.10.1/24 | —             | VLAN `users`, id 10  |
| user1   | 10.10.10.11/24    | ge-0/0/0        | `user1 - desk 101`   |
| user2   | 10.10.10.12/24    | ge-0/0/1        | `user2 - desk 102`   |
| user3   | 10.10.10.13/24    | ge-0/0/2        | `user3 - desk 103`   |
| user4   | 10.10.10.14/24    | ge-0/0/3        | `user4 - meeting room` |

Containerlab maps `eth1..eth4` in the topology file onto `ge-0/0/0..ge-0/0/3`
inside Junos — off by one, and the usual reason a link looks connected in
`containerlab inspect` but dead in `show interfaces`.

## What it demonstrates

- **A genuinely read-only NETCONF account.** Junos authorises NETCONF RPCs
  against the CLI command each one maps to, so a login class with
  `permissions [ view view-configuration ]` is what lets `get-*` through. No
  `configure` permission means the account cannot enter configuration mode at
  all, over CLI or NETCONF.
- **Three RPCs joined into one view.** Link state alone does not tell you a user
  is there. `get-interface-information` gives admin/oper state and counters,
  `get-ethernet-switching-table-information` says which MAC was learned on which
  port, and `get-arp-table-information` turns that MAC into an IP.
- **The difference between "port up" and "user present."** A port with no cable
  and a port with a cable to a powered-off laptop look different in the MAC
  table and identical in `oper-status`. The dashboard shows both columns.
- **Reshaping vendor XML for a dashboard.** Junos XML is namespaced, deeply
  nested and release-versioned. The poller flattens it to one `<user>` element
  per port, which is what makes the Grafana side a three-line selector instead
  of a transformation pipeline.
- **Raw NETCONF framing.** `netconf/manual-poll.sh` runs an RPC with nothing but
  `ssh -s netconf` and the `]]>]]>` delimiter, so you can see what the library
  is doing for you.

## Requirements

This lab needs a **vJunos-switch image, which does not pull**. Unlike the FRR
labs in this repo you have to build it yourself:

1. Download `vJunos-switch-23.2R1.15.qcow2` from Juniper's
   [vJunos download page](https://support.juniper.net/support/downloads/?p=vjunos)
   (free account, no licence needed).
2. Build the container with [vrnetlab](https://github.com/srl-labs/vrnetlab):
   drop the qcow2 into `juniper/vjunosswitch/` and run `make`.
3. You should end up with `vrnetlab/juniper_vjunos-switch:23.2R1.15`. If your
   tag differs, change the `image:` line in the topology file.

Budget **4 vCPU and ~5 GB RAM** for access1 alone, and about **15 minutes** for
it to boot — it is a full VM under the container. The other six nodes are
light.

The `poller` node pip-installs `ncclient` on first start, and Grafana downloads
the Infinity plugin on first start, so the initial deploy needs internet.

## Bring it up

```bash
sudo containerlab deploy -t junos-netconf-grafana.clab.yml
```

Then wait for Junos. `docker logs -f clab-junos-netconf-grafana-access1` will
tell you when it is done; the poller logs `poll failed:` on a loop until then,
which is expected.

Teardown:

```bash
sudo containerlab destroy -t junos-netconf-grafana.clab.yml --cleanup
```

## Verify

**On the switch**, as the `admin` account (password `admin@123`):

```bash
docker exec -it clab-junos-netconf-grafana-access1 cli
```

```
show interfaces descriptions
show ethernet-switching table
show arp no-resolve
```

You should see four access ports up, four MACs learned in VLAN `users`, and
four ARP entries against `irb.0`.

**The read-only account**, over raw NETCONF, from the host:

```bash
cd netconf
./manual-poll.sh rpc/get-arp-table.xml     # password: Telemetry.123
```

That prints the `<rpc-reply>` exactly as it comes off the wire — `<hello>`,
`]]>]]>`, namespaces and all.

**The flattened XML** the poller publishes:

```bash
curl -s localhost:8080/users.xml
curl -s localhost:8080/raw/interfaces.xml   # the untouched reply, for reference
```

**Grafana** is on <http://localhost:3000> with anonymous admin access (lab
only). The *Junos user ports (NETCONF)* dashboard is provisioned already and
refreshes every 15s.

## The XML

`/users.xml` is the contract between the poller and Grafana. One `<user>` per
access port, plus a few poll-level fields:

```xml
<poll>
  <device>clab-junos-netconf-grafana-access1</device>
  <timestamp>2026-09-11T10:14:03Z</timestamp>
  <status>ok</status>
  <ports>4</ports>
  <portsup>4</portsup>
  <user>
    <port>ge-0/0/0</port>
    <name>user1 - desk 101</name>
    <oper>up</oper>
    <up>1</up>
    <online>1</online>
    <mac>aa:c1:ab:11:11:11</mac>
    <ip>10.10.10.11</ip>
    ...
  </user>
</poll>
```

| Element | Type | From |
|---------|------|------|
| `port`, `name`, `admin`, `oper`, `speed`, `mtu`, `lastflap` | string | `get-interface-information` (`name`, `description`, `admin-status`, `oper-status`) |
| `up` | 0/1 | `oper-status` — numeric so a panel can threshold without a value mapping |
| `flapsecs`, `rxpkts`, `txpkts`, `rxbytes`, `txbytes`, `rxerrors`, `txerrors` | number | `get-interface-information` extensive |
| `mac`, `vlan`, `macs` | string/number | `get-ethernet-switching-table-information` |
| `online` | 0/1 | 1 when a MAC is learned on the port |
| `ip` | string | `get-arp-table-information`, joined to `mac` |

Two shapes here are deliberate, and both exist because of how Grafana consumes
XML:

- **No hyphens in element names.** Infinity converts the XML to JSON and runs
  JSONata over it, and JSONata reads `-` as subtraction — `<admin-status>` would
  need backtick quoting in every selector. Hence `<admin>`, not `<admin-status>`.
- **No empty elements.** `<mac/>` becomes `null` or `{}` depending on the
  XML-to-JSON library and shows up as junk in a table cell. Empty values are
  written as `-` instead.

## How Grafana reads it

The Infinity datasource fetches the URL, converts the XML to JSON and applies a
JSONata root selector:

| Panel | `root_selector` | Columns |
|-------|-----------------|---------|
| Connected users | `poll.user` | `port`, `name`, `oper`, `up`, `online`, `mac`, `ip`, … |
| Poll status | `poll` | `device`, `timestamp`, `status`, `error` |

If a panel comes back empty, the root selector is almost always the thing to
check first — the XML-to-JSON step differs slightly between Infinity versions
and parsers:

- backend (JSONata/JQ) parser: `poll.user`, or `user` if the root element is
  unwrapped
- frontend parser: `poll.user`, with column selectors like `port[0]._` because
  every element becomes an array and text lands under `_`

Open the panel, switch the query editor to table view and look at the raw
response — Infinity shows you the converted JSON, which settles it in seconds.

One honest limitation: this is **current state, not history**. Each refresh
re-reads the file, so you get a live table, not a graph over time. If you want
flap history, keep the same RPCs and parsing and feed the rows to Prometheus or
InfluxDB instead of an XML file.

## Break it on purpose

**1. Unplug a user**

```bash
docker exec clab-junos-netconf-grafana-user2 ip link set eth1 down
```

Within one poll cycle `ge-0/0/1` goes `oper: down`, `up: 0`, and once the MAC
ages out `online` drops to `no MAC`. `lastflap` resets. Bring it back up and
watch both recover — the link state immediately, the MAC only once the host
sends traffic again.

**2. Prove the account is actually read-only**

```bash
ssh telemetry@clab-junos-netconf-grafana-access1      # Telemetry.123
```

```
> show interfaces descriptions     # works
> configure                        # "permission denied"
> start shell                      # denied by deny-commands
```

Then try it over NETCONF, which is the path that matters here:

```bash
cd netconf
cat > /tmp/lock.xml <<'EOF'
<lock><target><candidate/></target></lock>
EOF
./manual-poll.sh /tmp/lock.xml
```

You get an `<rpc-error>` with an access-denied `error-tag`, not a lock. That is
the login class doing its job — the same class that lets every `get-*` RPC
through untouched.

**3. Kill the telemetry path without touching the data plane**

On access1 as `admin`:

```
configure
deactivate system services netconf
commit
```

The users stay up and reachable, but the poller fails on its next cycle and
`users.xml` flips to `<status>error</status>` with the exception in `<error>`.
The Grafana *Poll status* panel goes red while *Connected users* empties. This
is worth seeing: a monitoring outage and an outage look completely different,
and a dashboard that cannot tell you which one it is having is a bad dashboard.

`activate system services netconf` and commit to restore.

**4. Break the account instead of the service**

Change `JUNOS_PASSWORD` in the topology file to something wrong and redeploy the
poller. Same red dashboard, different `<error>` — `AuthenticationError` rather
than a connection failure. Being able to tell those two apart from the XML
alone is the reason the error message is published rather than just logged.

**5. Watch a user go quiet without going down**

```bash
docker exec clab-junos-netconf-grafana-user3 pkill -f init.sh
```

The ping loop stops, the port stays `up`, and after the MAC ageing timer
(300s by default) `online` flips to `no MAC` on its own. This is the
distinction access-layer monitoring gets wrong most often.

Note that `ip` keeps showing the last known address for a while after that:
ARP entries age out at 1200s in Junos, not 300s, and the poller falls back to
the ARP table's own `irb.0 [ge-0/0/2.0]` port reference once the MAC is gone.
Two ageing timers, two different ideas of "still here".

## Notes

- vrnetlab builds the boot config by concatenating `configs/access1.cfg` onto
  its own `init.conf`, so the file must be in curly-brace format rather than
  `set` commands, and it is additive — host-name, the `admin` user, fxp0
  addressing and SSH/NETCONF come from the base config. The concatenation
  leaves `juniper.conf` with two top-level `system` and `interfaces` stanzas;
  Junos merges duplicate containers when it parses the file, which is what
  every vJunos containerlab topology relies on to add data ports alongside
  `fxp0`.
- The poller only needs the `view` permission — all three RPCs are
  operational. `view-configuration` is in the class to demonstrate the next
  boundary (config readable, secrets not), not because the polling needs it.
- `permissions [ view view-configuration ]` lets the account read the
  configuration but **not** secrets; that needs the separate `secret`
  permission, which `netconf-ro` does not have. Check with
  `show configuration system login | display set` as `telemetry` — the password
  hashes are elided.
- `idle-timeout 15` on the class is harmless at a 15s poll interval, but it is
  the reason a hand-held NETCONF session goes away while you are reading the
  output.
- The poller opens one NETCONF session and reuses it, reconnecting on any
  failure. Reconnecting per poll also works and is simpler; it just costs an SSH
  handshake every cycle, which a real switch with hundreds of pollers will
  notice.
- `interface-name` in the RPC is `ge-0/0/*` — Junos accepts the same wildcard it
  accepts in `show interfaces`, which keeps the reply to the access ports rather
  than all 56 ports the chassis claims.
- Passwords are in the repo on purpose; this is a lab. Do not carry that pattern
  anywhere else — a real deployment wants SSH keys for the telemetry account and
  the password out of the topology file.
