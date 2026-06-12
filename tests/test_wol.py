"""Tests for the stdlib Wake-on-LAN helper module."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from custom_components.omv.wol import (
    DEFAULT_BROADCAST,
    DEFAULT_PORT,
    _build_magic_packet,
    send_magic_packet,
)

MAC = "aa:bb:cc:dd:ee:ff"
MAC_BYTES = bytes.fromhex("aabbccddeeff")


def test_build_magic_packet_layout() -> None:
    """Test the magic packet is 6x 0xFF followed by 16 MAC repetitions."""
    packet = _build_magic_packet(MAC)

    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == MAC_BYTES * 16


def test_build_magic_packet_accepts_dash_and_uppercase() -> None:
    """Test dash-separated and uppercase MAC formats are accepted."""
    assert _build_magic_packet("AA-BB-CC-DD-EE-FF") == _build_magic_packet(MAC)


@pytest.mark.parametrize(
    "mac",
    ["", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:gg", "aabbccddeeff", "aa:bb:cc:dd:ee:ff:00"],
)
def test_build_magic_packet_rejects_invalid_mac(mac: str) -> None:
    """Test invalid MAC formats raise ValueError."""
    with pytest.raises(ValueError, match="Invalid MAC"):
        _build_magic_packet(mac)


def test_send_magic_packet_broadcasts_udp() -> None:
    """Test the packet is sent as UDP broadcast to 255.255.255.255:9."""
    sock = MagicMock()
    with patch("custom_components.omv.wol.socket.socket") as socket_cls:
        socket_cls.return_value.__enter__.return_value = sock
        send_magic_packet(MAC)

    socket_cls.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto.assert_called_once_with(_build_magic_packet(MAC), (DEFAULT_BROADCAST, DEFAULT_PORT))


def test_send_magic_packet_custom_target() -> None:
    """Test broadcast address and port overrides are passed through."""
    sock = MagicMock()
    with patch("custom_components.omv.wol.socket.socket") as socket_cls:
        socket_cls.return_value.__enter__.return_value = sock
        send_magic_packet(MAC, broadcast="192.168.1.255", port=7)

    sock.sendto.assert_called_once_with(_build_magic_packet(MAC), ("192.168.1.255", 7))
