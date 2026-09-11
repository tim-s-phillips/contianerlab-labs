#!/usr/bin/env python3
"""Poll a Junos switch over NETCONF with a read-only account and publish the
result as a flat XML document Grafana can parse.

Junos RPC replies are verbose, deeply nested and namespaced - fine for a human
reading `show interfaces extensive`, painful for a dashboard. This joins three
RPCs into one row per access port and serves the result over HTTP:

    /users.xml          one <user> element per access port
    /raw/<name>.xml     the untouched <rpc-reply> for each RPC

Two deliberate choices in the output schema:

  * No hyphens in element names. Grafana's Infinity datasource converts XML to
    JSON and runs JSONata over it, and JSONata reads a hyphen as subtraction -
    <admin-status> would need backtick quoting in every single selector.
  * A numeric <up> alongside the textual <oper>, so a panel can threshold on it
    without a value mapping.
  * No empty elements. <mac/> converts to null or {} depending on the XML-to-JSON
    library, which surfaces in a table cell as junk; an explicit "-" does not.
"""

import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from ncclient import manager
from ncclient.xml_ import to_ele

HOST = os.environ.get("JUNOS_HOST", "clab-junos-netconf-grafana-access1")
PORT = int(os.environ.get("JUNOS_PORT", "830"))
USERNAME = os.environ.get("JUNOS_USER", "telemetry")
PASSWORD = os.environ.get("JUNOS_PASSWORD", "Telemetry.123")
INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))
RPC_DIR = os.environ.get("RPC_DIR", "/opt/netconf/rpc")
OUT_DIR = os.environ.get("OUT_DIR", "/opt/out")
ACCESS_PORTS = [
    p.strip()
    for p in os.environ.get(
        "ACCESS_PORTS", "ge-0/0/0,ge-0/0/1,ge-0/0/2,ge-0/0/3"
    ).split(",")
    if p.strip()
]

EMPTY = "-"

RPCS = {
    "interfaces": "get-interface-information.xml",
    "macs": "get-ethernet-switching-table.xml",
    "arp": "get-arp-table.xml",
}


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}", flush=True)


# --- namespace-agnostic XML helpers ------------------------------------------
# Junos tags carry a release-specific namespace (.../junos/23.2R0/junos-interface),
# so matching on the local name keeps the parser working across Junos versions.


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else tag


def iter_local(elem, *names):
    """Every descendant whose local tag name is one of names."""
    wanted = set(names)
    for child in elem.iter():
        if local(child.tag) in wanted:
            yield child


def text_of(elem, *names, default=""):
    """Text of the first descendant matching one of names."""
    for child in iter_local(elem, *names):
        if child.text:
            return child.text.strip()
    return default


def attr_of(elem, name, default=""):
    """Attribute by local name, ignoring the junos: namespace prefix."""
    for key, value in elem.attrib.items():
        if local(key) == name:
            return value
    return default


def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# --- RPC reply parsing --------------------------------------------------------


def parse_interfaces(root):
    """{'ge-0/0/0': {...}} from a get-interface-information extensive reply."""
    out = {}
    for phy in iter_local(root, "physical-interface"):
        name = text_of(phy, "name")
        if not name:
            continue
        stats = next(iter_local(phy, "traffic-statistics"), None)
        flapped = next(iter_local(phy, "interface-flapped"), None)
        out[name] = {
            "description": text_of(phy, "description"),
            "admin": text_of(phy, "admin-status", default="unknown"),
            "oper": text_of(phy, "oper-status", default="unknown"),
            "speed": text_of(phy, "speed"),
            "mtu": text_of(phy, "mtu"),
            "lastflap": (flapped.text or "").strip() if flapped is not None else "",
            "flapsecs": to_int(attr_of(flapped, "seconds")) if flapped is not None else 0,
            "rxbytes": to_int(text_of(stats, "input-bytes")) if stats is not None else 0,
            "rxpkts": to_int(text_of(stats, "input-packets")) if stats is not None else 0,
            "txbytes": to_int(text_of(stats, "output-bytes")) if stats is not None else 0,
            "txpkts": to_int(text_of(stats, "output-packets")) if stats is not None else 0,
            "rxerrors": to_int(text_of(phy, "input-errors")),
            "txerrors": to_int(text_of(phy, "output-errors")),
        }
    return out


MAC_TAGS = ("l2ng-l2-mac-address", "mac-address")
MAC_IFACE_TAGS = (
    "l2ng-l2-mac-logical-interface",
    "mac-interfaces",
    "mac-interface",
    "mac-interfaces-list",
)
VLAN_TAGS = ("l2ng-l2-mac-vlan-name", "mac-vlan", "mac-vlan-name")


def parse_macs(root):
    """{'ge-0/0/0': [{'mac':..., 'vlan':...}]} from the ethernet-switching table.

    ELS and pre-ELS Junos use completely different element names for the same
    table, and the entry element has been spelled both l2ng-mac-entry and
    l2ng-l2-mac-entry across releases. Rather than pinning one, treat any
    element holding a MAC address as an entry and look inside it for the port.
    """
    by_port = {}
    for entry in root.iter():
        mac = ""
        for child in entry:
            if local(child.tag) in MAC_TAGS and child.text:
                mac = child.text.strip()
                break
        if not mac:
            continue
        iface = text_of(entry, *MAC_IFACE_TAGS)
        if not iface:
            continue
        by_port.setdefault(iface.split(".")[0], []).append(
            {"mac": mac, "vlan": text_of(entry, *VLAN_TAGS)}
        )
    return by_port


def parse_arp(root):
    """({mac: ip}, {port: [ip]}) from a get-arp-table-information reply.

    An IRB entry reports its interface as "irb.0 [ge-0/0/0.0]", so the physical
    port is recoverable from ARP alone - handy when the MAC table has aged out.
    """
    by_mac = {}
    by_port = {}
    for entry in iter_local(root, "arp-table-entry"):
        mac = text_of(entry, "mac-address").lower()
        ip = text_of(entry, "ip-address")
        if not ip:
            continue
        if mac:
            by_mac[mac] = ip
        iface = text_of(entry, "interface-name")
        if "[" in iface:
            phy = iface.split("[", 1)[1].rstrip("]").strip().split(".")[0]
            by_port.setdefault(phy, []).append(ip)
    return by_mac, by_port


def build_rows(interfaces, macs, arp_by_mac, arp_by_port):
    rows = []
    for port in ACCESS_PORTS:
        info = interfaces.get(port, {})
        learned = macs.get(port, [])
        mac = learned[0]["mac"] if learned else ""
        vlan = learned[0]["vlan"] if learned else ""
        ip = arp_by_mac.get(mac.lower(), "")
        if not ip:
            fallback = arp_by_port.get(port, [])
            ip = fallback[0] if fallback else ""
        oper = info.get("oper", "unknown")
        rows.append(
            {
                "port": port,
                "name": info.get("description") or port,
                "admin": info.get("admin", "unknown"),
                "oper": oper,
                "up": 1 if oper.lower() == "up" else 0,
                "online": 1 if mac else 0,
                "mac": mac,
                "ip": ip,
                "vlan": vlan,
                "macs": len(learned),
                "speed": info.get("speed", ""),
                "mtu": info.get("mtu", ""),
                "lastflap": info.get("lastflap", ""),
                "flapsecs": info.get("flapsecs", 0),
                "rxpkts": info.get("rxpkts", 0),
                "txpkts": info.get("txpkts", 0),
                "rxbytes": info.get("rxbytes", 0),
                "txbytes": info.get("txbytes", 0),
                "rxerrors": info.get("rxerrors", 0),
                "txerrors": info.get("txerrors", 0),
            }
        )
    return rows


# --- output -------------------------------------------------------------------


def render(rows, status, error=""):
    now = datetime.now(timezone.utc)
    root = ET.Element("poll")
    ET.SubElement(root, "device").text = HOST
    ET.SubElement(root, "timestamp").text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    ET.SubElement(root, "epoch").text = str(int(now.timestamp() * 1000))
    ET.SubElement(root, "status").text = status
    ET.SubElement(root, "error").text = error or EMPTY
    ET.SubElement(root, "ports").text = str(len(rows))
    ET.SubElement(root, "portsup").text = str(sum(r["up"] for r in rows))
    for row in rows:
        user = ET.SubElement(root, "user")
        for key, value in row.items():
            ET.SubElement(user, key).text = str(value) if str(value) else EMPTY
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_atomic(path, content):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.write("\n")
    os.replace(tmp, path)


# --- HTTP ---------------------------------------------------------------------


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve():
    handler = partial(QuietHandler, directory=OUT_DIR)
    ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), handler).serve_forever()


# --- main loop ----------------------------------------------------------------


def load_rpcs():
    """Read the RPC payloads from netconf/rpc/, dropping the XML comments so
    what goes on the wire is exactly the RPC element."""
    payloads = {}
    for key, filename in RPCS.items():
        root = ET.parse(os.path.join(RPC_DIR, filename)).getroot()
        payloads[key] = ET.tostring(root, encoding="unicode")
    return payloads


def connect():
    return manager.connect(
        host=HOST,
        port=PORT,
        username=USERNAME,
        password=PASSWORD,
        hostkey_verify=False,
        allow_agent=False,
        look_for_keys=False,
        device_params={"name": "junos"},
        timeout=30,
    )


def poll_once(session, payloads):
    """Dispatch every RPC, archive the raw reply, return the joined rows."""
    trees = {}
    for key, payload in payloads.items():
        raw = session.dispatch(to_ele(payload)).xml
        write_atomic(os.path.join(OUT_DIR, "raw", f"{key}.xml"), raw)
        # Parse from bytes: the reply carries an encoding declaration.
        trees[key] = ET.fromstring(raw.encode("utf-8"))

    arp_by_mac, arp_by_port = parse_arp(trees["arp"])
    return build_rows(
        parse_interfaces(trees["interfaces"]),
        parse_macs(trees["macs"]),
        arp_by_mac,
        arp_by_port,
    )


def main():
    os.makedirs(os.path.join(OUT_DIR, "raw"), exist_ok=True)
    payloads = load_rpcs()
    write_atomic(os.path.join(OUT_DIR, "users.xml"), render([], "starting"))

    threading.Thread(target=serve, daemon=True).start()
    log(f"serving {OUT_DIR} on :{HTTP_PORT}, polling {USERNAME}@{HOST}:{PORT} every {INTERVAL}s")

    session = None
    while True:
        try:
            if session is None or not session.connected:
                log(f"opening NETCONF session to {HOST}:{PORT}")
                session = connect()
            rows = poll_once(session, payloads)
            write_atomic(os.path.join(OUT_DIR, "users.xml"), render(rows, "ok"))
            log(f"polled {len(rows)} ports, {sum(r['up'] for r in rows)} up")
        except Exception as exc:  # keep polling through switch reboots
            log(f"poll failed: {exc.__class__.__name__}: {exc}")
            write_atomic(
                os.path.join(OUT_DIR, "users.xml"),
                render([], "error", f"{exc.__class__.__name__}: {exc}"),
            )
            try:
                if session is not None:
                    session.close_session()
            except Exception:
                pass
            session = None
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
