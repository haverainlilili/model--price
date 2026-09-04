#!/bin/sh
set -eu

NAMESPACE="mp-limit"
HOST_INTERFACE="mp-limit-host"
NAMESPACE_INTERFACE="mp-limit-ns"
UPLINK_INTERFACE="eth0"
HOST_ADDRESS="10.203.0.1/30"
NAMESPACE_ADDRESS="10.203.0.2/30"
NAMESPACE_GATEWAY="10.203.0.1"
SOURCE_NETWORK="10.203.0.0/30"
NFT_TABLE="model_price_limit"
STATE_DIRECTORY="/run/model-price-network-limit"
FORWARDING_STATE="$STATE_DIRECTORY/ip_forward"
DOWNLOAD_RATE="${MODEL_PRICE_DOWNLOAD_RATE:-2mbit}"
UPLOAD_RATE="${MODEL_PRICE_UPLOAD_RATE:-256kbit}"

cleanup_network() {
    nft delete table ip "$NFT_TABLE" 2>/dev/null || true
    ip link delete "$HOST_INTERFACE" 2>/dev/null || true
    ip netns delete "$NAMESPACE" 2>/dev/null || true

    if [ -f "$FORWARDING_STATE" ]; then
        previous_forwarding=$(cat "$FORWARDING_STATE")
        sysctl -q -w "net.ipv4.ip_forward=$previous_forwarding"
        rm -f "$FORWARDING_STATE"
        rmdir "$STATE_DIRECTORY" 2>/dev/null || true
    fi
}

setup_network() {
    cleanup_network
    mkdir -p "$STATE_DIRECTORY"
    sysctl -n net.ipv4.ip_forward > "$FORWARDING_STATE"
    trap cleanup_network EXIT HUP INT TERM

    sysctl -q -w net.ipv4.ip_forward=1
    ip netns add "$NAMESPACE"
    ip link add "$HOST_INTERFACE" type veth peer name "$NAMESPACE_INTERFACE"
    ip link set "$NAMESPACE_INTERFACE" netns "$NAMESPACE"

    ip address add "$HOST_ADDRESS" dev "$HOST_INTERFACE"
    ip link set "$HOST_INTERFACE" up
    ip netns exec "$NAMESPACE" ip address add \
        "$NAMESPACE_ADDRESS" dev "$NAMESPACE_INTERFACE"
    ip netns exec "$NAMESPACE" ip link set lo up
    ip netns exec "$NAMESPACE" ip link set "$NAMESPACE_INTERFACE" up
    ip netns exec "$NAMESPACE" ip route add default via "$NAMESPACE_GATEWAY"

    nft add table ip "$NFT_TABLE"
    nft "add chain ip $NFT_TABLE postrouting { type nat hook postrouting priority srcnat; policy accept; }"
    nft add rule ip "$NFT_TABLE" postrouting \
        ip saddr "$SOURCE_NETWORK" oifname "$UPLINK_INTERFACE" masquerade

    # host -> namespace 是抓取下载；namespace -> host 是上传。
    tc qdisc replace dev "$HOST_INTERFACE" root tbf \
        rate "$DOWNLOAD_RATE" burst 64kb latency 400ms
    ip netns exec "$NAMESPACE" tc qdisc replace dev "$NAMESPACE_INTERFACE" \
        root tbf rate "$UPLOAD_RATE" burst 16kb latency 400ms

    trap - EXIT HUP INT TERM
}

case "${1:-setup}" in
    setup)
        setup_network
        ;;
    cleanup)
        cleanup_network
        ;;
    status)
        ip netns exec "$NAMESPACE" ip route
        tc -s qdisc show dev "$HOST_INTERFACE"
        ip netns exec "$NAMESPACE" tc -s qdisc show dev "$NAMESPACE_INTERFACE"
        ;;
    *)
        echo "usage: $0 {setup|cleanup|status}" >&2
        exit 2
        ;;
esac
