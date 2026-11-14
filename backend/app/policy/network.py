"""Blocked internal IP ranges for crawlers (SSRF protection, the specification §5.3)."""

from __future__ import annotations

import ipaddress
from typing import Final

BLOCKED_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        # Additional non-public ranges
        "0.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "192.0.0.0/24",
        "192.0.2.0/24",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "fe80::/10",
        "ff00::/8",
        "2001:db8::/32",
        "64:ff9b::/96",  # NAT64 can map to internal v4
    )
)

ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
ALLOWED_PORTS: Final = frozenset({80, 443})


def is_blocked_ip(ip: str) -> bool:
    """True if the address is internal/non-public and must never be fetched."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv6Address) and addr.sixtofour is not None:
        addr = addr.sixtofour
    return any(addr in net for net in BLOCKED_NETWORKS if addr.version == net.version)
