#!/usr/bin/env bash
#
# Installs everything the labs in this repo need:
#   - Docker (started and enabled)
#   - containerlab
#   - MPLS kernel modules, loaded now and persisted across reboot
#
# Safe to re-run. Anything already present is left alone.
#
set -euo pipefail

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; DIM='\033[2m'; RESET='\033[0m'

info () { printf "${DIM}  %s${RESET}\n" "$*"; }
ok   () { printf "${GREEN}  ok${RESET} %s\n" "$*"; }
warn () { printf "${YELLOW}  !${RESET}  %s\n" "$*"; }
die  () { printf "${RED}  x${RESET}  %s\n" "$*"; exit 1; }

need_root () {
    if [ "$(id -u)" -ne 0 ]; then
        die "Run with sudo: sudo ./install.sh"
    fi
}

detect_distro () {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${ID:-unknown}"
    else
        echo unknown
    fi
}

install_docker () {
    echo "==> Docker"
    if command -v docker >/dev/null 2>&1; then
        ok "already installed ($(docker --version | cut -d' ' -f3 | tr -d ,))"
    else
        case "$DISTRO" in
            arch|manjaro|endeavouros)
                pacman -S --needed --noconfirm docker
                ;;
            ubuntu|debian)
                apt-get update -qq && apt-get install -y docker.io
                ;;
            fedora|rhel|centos)
                dnf install -y docker
                ;;
            *)
                die "Unsupported distro '$DISTRO'. Install Docker manually, then re-run."
                ;;
        esac
        ok "installed"
    fi

    systemctl enable --now docker >/dev/null 2>&1 || true
    if docker info >/dev/null 2>&1; then
        ok "daemon running"
    else
        die "Docker installed but the daemon will not start. Check: systemctl status docker"
    fi
}

install_containerlab () {
    echo "==> containerlab"
    if command -v containerlab >/dev/null 2>&1; then
        ok "already installed ($(containerlab version 2>/dev/null | grep -oP 'version:\s*\K\S+' || echo present))"
        return
    fi

    if [ "$DISTRO" = "arch" ] || [ "$DISTRO" = "manjaro" ] || [ "$DISTRO" = "endeavouros" ]; then
        warn "AUR package containerlab-bin is preferred on Arch"
        info "as your normal user: yay -S containerlab-bin"
        info "falling back to the upstream installer"
    fi

    if command -v curl >/dev/null 2>&1; then
        bash -c "$(curl -sL https://get.containerlab.dev)"
    else
        die "curl not found. Install curl, or install containerlab manually."
    fi
    command -v containerlab >/dev/null 2>&1 && ok "installed" || die "containerlab install failed"
}

enable_mpls () {
    echo "==> MPLS kernel support"
    # 03-mpls-l3vpn needs these on the host. Container namespaces inherit
    # the modules; the sysctls are set per-namespace by the lab bootstrap.
    local loaded=1
    for mod in mpls_router mpls_iptunnel; do
        if lsmod | grep -q "^${mod}"; then
            ok "$mod already loaded"
        elif modprobe "$mod" 2>/dev/null; then
            ok "$mod loaded"
        else
            warn "could not load $mod"
            loaded=0
        fi
    done

    if [ "$loaded" -eq 0 ]; then
        warn "Labs 01 and 02 will still work. Lab 03 (MPLS L3VPN) will not."
        info "Your kernel may lack MPLS support; check: zgrep MPLS /proc/config.gz"
        return
    fi

    mkdir -p /etc/modules-load.d
    cat > /etc/modules-load.d/mpls.conf <<'EOF'
# Required by containerlab-labs/03-mpls-l3vpn
mpls_router
mpls_iptunnel
EOF
    ok "persisted across reboot (/etc/modules-load.d/mpls.conf)"
}

check_resources () {
    echo "==> Resources"
    local mb
    mb=$(free -m | awk '/^Mem:/{print $2}')
    if [ "$mb" -lt 2048 ]; then
        warn "${mb}MB RAM detected; the labs want ~1GB free"
    else
        ok "${mb}MB RAM"
    fi
}

DISTRO=$(detect_distro)
echo
echo "containerlab-labs installer  (detected: $DISTRO)"
echo

need_root
install_docker
install_containerlab
enable_mpls
check_resources

echo
printf "${GREEN}Done.${RESET} Bring up a lab with:\n"
echo "  cd 01-bgp-dual-homed && sudo containerlab deploy -t bgp-dual-homed.clab.yml"
echo "  cd 03-mpls-l3vpn     && make up && make verify"
echo
echo "Lab 02 (Junos NETCONF) additionally needs a vJunos-switch image built"
echo "with vrnetlab, which this script cannot fetch. See 02-junos-netconf-grafana/README.md."
echo
