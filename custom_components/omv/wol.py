"""Wake-on-LAN magic packet helper using only the standard library.

Implemented with stdlib ``socket`` instead of a PyPI dependency so the
integration's ``manifest.json`` ``requirements`` stays empty (HACS-friendly,
no version pinning against Home Assistant core). The packet MUST be sent
from the Home Assistant host — the OMV API is down while the NAS is in
standby.
"""

from __future__ import annotations

import re
import socket

from homeassistant.core import HomeAssistant

_MAC_RE = r"(?i)([0-9a-f]{2}[:-]){5}[0-9a-f]{2}"

DEFAULT_BROADCAST = "255.255.255.255"
DEFAULT_PORT = 9


def _build_magic_packet(mac: str) -> bytes:
    """Build a Wake-on-LAN magic packet for one MAC address.

    Args:
        mac: MAC address in ``aa:bb:cc:dd:ee:ff`` or ``AA-BB-CC-DD-EE-FF``
            notation (case-insensitive).

    Returns:
        The 102-byte magic packet (6x ``0xFF`` followed by the MAC 16 times).

    Raises:
        ValueError: When ``mac`` is not a valid MAC address.
    """
    if not re.fullmatch(_MAC_RE, mac):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    return b"\xff" * 6 + raw * 16


def send_magic_packet(
    mac: str,
    *,
    broadcast: str = DEFAULT_BROADCAST,
    port: int = DEFAULT_PORT,
) -> None:
    """Send a Wake-on-LAN magic packet via UDP broadcast (blocking).

    Args:
        mac: Target MAC address (see :func:`_build_magic_packet`).
        broadcast: Broadcast address to send to.
        port: UDP port to send to (9 is the conventional discard port).

    Raises:
        ValueError: When ``mac`` is not a valid MAC address.
        OSError: When the socket operation fails.
    """
    packet = _build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))


async def async_send_magic_packet(hass: HomeAssistant, mac: str) -> None:
    """Send a Wake-on-LAN magic packet without blocking the event loop.

    Args:
        hass: The Home Assistant instance (provides the executor).
        mac: Target MAC address.

    Raises:
        ValueError: When ``mac`` is not a valid MAC address.
        OSError: When the socket operation fails.
    """
    await hass.async_add_executor_job(send_magic_packet, mac)
