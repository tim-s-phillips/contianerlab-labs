#!/usr/bin/env python3
"""
Asserts that the MPLS L3VPN lab actually works, rather than merely coming up.

Checks three separate things, because passing one does not imply the others:
  control plane  - OSPF, LDP and VPNv4 sessions established
  data plane     - labelled forwarding across the core, end to end
  isolation      - customer B cannot see or reach customer A

Exit code 0 means every check passed. Intended for `make verify` and for CI.
"""

import json
import subprocess
import sys
import time

LAB = "mpls-l3vpn"
PREFIX = f"clab-{LAB}"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

results = []


def node(name: str) -> str:
    return f"{PREFIX}-{name}"


def run(container: str, cmd: list[str], timeout: int = 20) -> str:
    proc = subprocess.run(
        ["docker", "exec", node(container), *cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.stdout


def vtysh(container: str, command: str) -> str:
    return run(container, ["vtysh", "-c", command])


def vtysh_json(container: str, command: str):
    raw = vtysh(container, command).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}")
    if detail and not ok:
        print(f"{DIM}         {detail}{RESET}")
    results.append((name, ok))
    return ok


def wait_for_convergence(attempts: int = 12, delay: int = 10) -> None:
    """VPNv4 is the last thing to come up, so poll on that."""
    print(f"{DIM}Waiting for the control plane to converge...{RESET}")
    for i in range(attempts):
        data = vtysh_json("pe1", "show bgp ipv4 vpn summary json")
        if data:
            peers = data.get("peers", {})
            peer = peers.get("10.0.0.3", {})
            if peer.get("state") == "Established":
                print(f"{DIM}Converged after ~{i * delay}s{RESET}\n")
                return
        time.sleep(delay)
    print(f"{YELLOW}Convergence timed out; running checks anyway.{RESET}\n")


# --------------------------------------------------------------------------
# Control plane
# --------------------------------------------------------------------------
def check_control_plane() -> None:
    print("Control plane")

    ospf = vtysh_json("p1", "show ip ospf neighbor json")
    neighbours = list((ospf or {}).get("neighbors", {}).keys())
    check(
        "p1 has OSPF adjacency with both PEs",
        len(neighbours) >= 2,
        f"expected 2 neighbours, saw {neighbours}",
    )

    ldp = vtysh_json("p1", "show mpls ldp neighbor json")
    sessions = (ldp or {}).get("neighbors", [])
    operational = [n for n in sessions if n.get("state", "").upper() == "OPERATIONAL"]
    check(
        "p1 has LDP sessions with both PEs",
        len(operational) >= 2,
        f"operational sessions: {len(operational)} of {len(sessions)}",
    )

    vpn = vtysh_json("pe1", "show bgp ipv4 vpn summary json")
    peer = (vpn or {}).get("peers", {}).get("10.0.0.3", {})
    check(
        "pe1-pe2 VPNv4 session established",
        peer.get("state") == "Established",
        f"state: {peer.get('state', 'no session')}",
    )


# --------------------------------------------------------------------------
# VPN route propagation
# --------------------------------------------------------------------------
def check_routes() -> None:
    print("\nVPN routes")

    routes = vtysh_json("pe2", "show ip route vrf CUST-A json")
    remote = (routes or {}).get("192.168.10.0/24", [])
    check(
        "pe2 has customer A site 1 prefix in VRF CUST-A",
        bool(remote),
        "192.168.10.0/24 missing from pe2 VRF CUST-A",
    )

    ce_routes = vtysh_json("ce-a2", "show ip route json")
    check(
        "ce-a2 learned the far site over eBGP",
        bool((ce_routes or {}).get("192.168.10.0/24")),
        "as-override may not be applied on pe2",
    )

    # p1 is a pure LSR. Customer prefixes must not appear anywhere on it.
    p1_routes = vtysh("p1", "show ip route")
    check(
        "p1 holds no customer routes",
        "192.168." not in p1_routes,
        "a customer prefix leaked into the P router's global table",
    )


# --------------------------------------------------------------------------
# Data plane
# --------------------------------------------------------------------------
def ping(container: str, source: str, dest: str, count: int = 3) -> bool:
    out = run(container, ["ping", "-I", source, "-c", str(count), "-W", "2", dest], timeout=30)
    return " 0% packet loss" in out or "3 received" in out


def check_data_plane() -> None:
    print("\nData plane")

    check(
        "ce-a1 reaches ce-a2 across the MPLS core",
        ping("ce-a1", "192.168.10.1", "192.168.20.1"),
        "end-to-end VPN forwarding failed; check MPLS label processing",
    )

    labels = vtysh("pe1", "show mpls table")
    check(
        "pe1 has an MPLS forwarding table",
        "LDP" in labels or "BGP" in labels,
        "no label bindings installed",
    )


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------
def check_isolation() -> None:
    print("\nVRF isolation")

    b_routes = vtysh_json("pe1", "show ip route vrf CUST-B json")
    check(
        "customer A's remote prefix absent from VRF CUST-B",
        not (b_routes or {}).get("192.168.20.0/24"),
        "route leaked across VRFs; check RT import on pe1",
    )

    check(
        "ce-b1 cannot reach customer A site 2",
        not ping("ce-b1", "192.168.10.1", "192.168.20.1", count=2),
        "customer B reached customer A - isolation is broken",
    )

    # Both customers use 192.168.10.0/24. pe1 must hold both, separately.
    a_local = vtysh_json("pe1", "show ip route vrf CUST-A json")
    b_local = vtysh_json("pe1", "show ip route vrf CUST-B json")
    check(
        "overlapping 192.168.10.0/24 present in both VRFs independently",
        bool((a_local or {}).get("192.168.10.0/24")) and bool((b_local or {}).get("192.168.10.0/24")),
        "one of the overlapping prefixes is missing",
    )


def main() -> int:
    print(f"\nVerifying lab: {LAB}\n")

    if not run("pe1", ["true"]) and subprocess.run(
        ["docker", "inspect", node("pe1")], capture_output=True
    ).returncode != 0:
        print(f"{RED}Lab is not running. Try: make up{RESET}")
        return 2

    wait_for_convergence()
    check_control_plane()
    check_routes()
    check_data_plane()
    check_isolation()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")

    if passed != total:
        print(f"\n{YELLOW}Failed checks and where to look:{RESET}")
        for name, ok in results:
            if not ok:
                print(f"  - {name}")
        print(f"{DIM}  docker logs {node('pe1')}{RESET}")
        print(f"{DIM}  docker exec -it {node('pe1')} vtysh{RESET}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
