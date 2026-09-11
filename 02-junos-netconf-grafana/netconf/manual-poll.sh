#!/usr/bin/env bash
# Run one Junos RPC over raw NETCONF-over-SSH and print the <rpc-reply>.
#
# No ncclient, no Python - just an SSH subsystem and the ]]>]]> framing from
# RFC 4742. Useful for seeing exactly what the poller container is doing, and
# for checking what the read-only account is allowed to ask for.
#
#   ./manual-poll.sh rpc/get-interface-information.xml
#
# You will be prompted for the telemetry password (lab default: Telemetry.123).
set -euo pipefail

HOST=${JUNOS_HOST:-clab-junos-netconf-grafana-access1}
PORT=${JUNOS_PORT:-830}
USERNAME=${JUNOS_USER:-telemetry}
RPC_FILE=${1:?usage: manual-poll.sh <rpc file>   e.g. rpc/get-arp-table.xml}

here=$(cd "$(dirname "$0")" && pwd)

{
    cat "$here/hello.xml"
    printf '<rpc message-id="1">'
    cat "$RPC_FILE"
    printf '</rpc>\n]]>]]>\n'
    printf '<rpc message-id="2"><close-session/></rpc>\n]]>]]>\n'
} | ssh -p "$PORT" -s \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR \
        "$USERNAME@$HOST" netconf
