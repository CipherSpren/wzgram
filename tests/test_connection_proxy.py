#  Pyrogram - Telegram MTProto API Client Library for Python
#  Copyright (C) 2017-present Dan <https://github.com/delivrance>
#
#  This file is part of Pyrogram.
#
#  Pyrogram is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Pyrogram is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with Pyrogram.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations as _annotations

from typing import Final

import pytest

from pyrogram.connection.connection import Connection, protocol_dc_id, transport_class_for
from pyrogram.connection.proxy import MTProxy, Proxy, SOCKS5Proxy, WebProxy
from pyrogram.connection.transport import TCP, TCPAbridged, TCPFull, TCPPaddedIntermediate

from tests.proxy_values import DD_SECRET_HEX, PLAIN_SECRET_HEX, SNI_DOMAIN

_PLAIN_MTPROXY: Final[MTProxy] = MTProxy(
    hostname="11.22.33.44",
    port=443,
    secret=bytes.fromhex(PLAIN_SECRET_HEX),
)
_DD_MTPROXY: Final[MTProxy] = MTProxy(
    hostname="11.22.33.44",
    port=443,
    secret=bytes.fromhex(DD_SECRET_HEX),
)
# An ee secret keeps a bare 16-byte key: its marker and domain came off in
#  `normalize_proxy`, and `sni_hostname` is what records that it was one.
_EE_MTPROXY: Final[MTProxy] = MTProxy(
    hostname="11.22.33.44",
    port=443,
    secret=bytes.fromhex(PLAIN_SECRET_HEX),
    sni_hostname=SNI_DOMAIN,
)


def testprotocol_dc_id_plain() -> None:
    assert protocol_dc_id(2, test_mode=False, media=False) == 2


def testprotocol_dc_id_media_is_negated() -> None:
    assert protocol_dc_id(2, test_mode=False, media=True) == -2


def testprotocol_dc_id_test_mode_is_shifted() -> None:
    assert protocol_dc_id(2, test_mode=True, media=False) == 10002


def testprotocol_dc_id_test_mode_media_shifts_then_negates() -> None:
    assert protocol_dc_id(2, test_mode=True, media=True) == -10002


async def test_connection_computesprotocol_dc_id_from_media_and_test_mode() -> None:
    connection = Connection(dc_id=5, test_mode=True, ipv6=False, media=True, server_address="unused", port=443)
    assert connection.protocol_dc_id == -10005


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        pytest.param(None, TCPAbridged, id="no-proxy"),
        pytest.param(SOCKS5Proxy(hostname="11.22.33.44", port=1234), TCPAbridged, id="socks5"),
        pytest.param(_PLAIN_MTPROXY, TCPAbridged, id="mtproxy-plain"),
        pytest.param(_DD_MTPROXY, TCPPaddedIntermediate, id="mtproxy-dd"),
        pytest.param(_EE_MTPROXY, TCPPaddedIntermediate, id="mtproxy-ee"),
        pytest.param(
            WebProxy(hostname="relay.example.com", secret=bytes.fromhex(PLAIN_SECRET_HEX)),
            TCPAbridged,
            id="web-plain",
        ),
        pytest.param(
            WebProxy(hostname="relay.example.com", secret=bytes.fromhex(DD_SECRET_HEX)),
            TCPPaddedIntermediate,
            id="web-dd",
        ),
    ],
)
def test_transport_class_for_reads_the_framing_off_the_secret(
    proxy: Proxy | None,
    expected: type[TCP],
) -> None:
    assert transport_class_for(proxy) is expected


def test_transport_class_for_keeps_the_default_when_the_secret_asks_for_nothing() -> None:
    # A plain secret pads nothing, so whatever the caller picked still stands.
    assert transport_class_for(_PLAIN_MTPROXY, default=TCPFull) is TCPFull


def test_transport_class_for_overrides_a_default_the_secret_contradicts() -> None:
    assert transport_class_for(_EE_MTPROXY, default=TCPFull) is TCPPaddedIntermediate


async def test_connection_takes_its_transport_from_the_proxy_secret() -> None:
    connection = Connection(
        dc_id=2,
        test_mode=False,
        ipv6=False,
        proxy=_EE_MTPROXY,
        server_address="unused",
        port=443,
    )

    assert connection.protocol_factory is TCPPaddedIntermediate


async def test_connection_keeps_the_requested_transport_without_a_padded_secret() -> None:
    connection = Connection(
        dc_id=2,
        test_mode=False,
        ipv6=False,
        proxy=_PLAIN_MTPROXY,
        protocol_factory=TCPFull,
        server_address="unused",
        port=443,
    )

    assert connection.protocol_factory is TCPFull


async def test_a_failure_names_the_proxy_that_was_dialed_not_only_the_dc() -> None:
    class _Refusing(TCPAbridged):
        async def connect(self, address) -> None:
            raise OSError("getaddrinfo failed")

    connection = Connection(
        dc_id=4,
        test_mode=False,
        ipv6=False,
        proxy=_DD_MTPROXY,
        protocol_factory=_Refusing,
        server_address="149.154.167.92",
        port=443,
    )

    with pytest.raises(ConnectionError) as exc:
        await connection.connect()

    assert _DD_MTPROXY.hostname in str(exc.value), (
        "an obfuscated2 proxy is dialed instead of the DC address, so naming only "
        "the DC sends the reader after an address the failure never touched"
    )


async def test_a_failure_without_a_proxy_names_only_the_dc() -> None:
    class _Refusing(TCPAbridged):
        async def connect(self, address) -> None:
            raise OSError("refused")

    connection = Connection(
        dc_id=4,
        test_mode=False,
        ipv6=False,
        protocol_factory=_Refusing,
        server_address="149.154.167.92",
        port=443,
    )

    with pytest.raises(ConnectionError) as exc:
        await connection.connect()

    assert "via" not in str(exc.value)
