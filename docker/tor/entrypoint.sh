#!/bin/sh
set -eu

runtime_config=/tmp/torrc
cp /etc/tor/torrc "$runtime_config"

upstream_proxy=${TOR_UPSTREAM_PROXY:-}
if [ -z "$upstream_proxy" ] && [ -n "${TOR_UPSTREAM_SOCKS:-}" ]; then
    upstream_proxy="socks5://${TOR_UPSTREAM_SOCKS}"
fi

if [ -n "$upstream_proxy" ]; then
    case "$upstream_proxy" in
        http://*) directive=HTTPSProxy; endpoint=${upstream_proxy#http://} ;;
        socks5://*) directive=Socks5Proxy; endpoint=${upstream_proxy#socks5://} ;;
        *)
            echo "TOR_UPSTREAM_PROXY must start with http:// or socks5://" >&2
            exit 2
            ;;
    esac
    case "$endpoint" in
        "")
            echo "TOR_UPSTREAM_PROXY endpoint is empty" >&2
            exit 2
            ;;
        *[!A-Za-z0-9._:-]*)
            echo "TOR_UPSTREAM_PROXY endpoint contains unsupported characters" >&2
            exit 2
            ;;
    esac
    printf '\n%s %s\n' "$directive" "$endpoint" >> "$runtime_config"
fi

exec tor -f "$runtime_config"
