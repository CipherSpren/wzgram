import asyncio
import socket

import pytest

from pyrogram.connection.proxy import HTTPProxy, SOCKS5Proxy
from pyrogram.connection.transport.tcp import TCP, TCPAbridged


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch):
    monkeypatch.setattr(TCP, "TIMEOUT", 2)
    monkeypatch.setattr(TCP, "CONNECT_TIMEOUT", 2)


async def _start_echo_server():
    async def serve(reader, writer):
        try:
            # The transport tag the framing class sends once, which a real server
            #  consumes and never echoes back.
            await reader.readexactly(1)

            while True:
                chunk = await reader.read(4096)

                if not chunk:
                    return

                writer.write(chunk)
                await writer.drain()
        except asyncio.IncompleteReadError:
            return
        finally:
            writer.close()

    server = await asyncio.start_server(serve, host="127.0.0.1", port=0)

    return server, server.sockets[0].getsockname()[1]


async def _pipe(reader, writer):
    try:
        while True:
            chunk = await reader.read(4096)

            if not chunk:
                return

            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        return


async def _relay(client_reader, client_writer, upstream_reader, upstream_writer):
    # Both halves stop as soon as either does: leaving one waiting would keep the
    #  upstream connection open, and its own server would then never close.
    tasks = [
        asyncio.create_task(_pipe(client_reader, upstream_writer)),
        asyncio.create_task(_pipe(upstream_reader, client_writer)),
    ]

    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()

        upstream_writer.close()


async def _start_socks5_server(target_port: int, *, username=None, password=None):
    seen = {}

    async def serve(reader, writer):
        try:
            version, count = await reader.readexactly(2)
            methods = await reader.readexactly(count)
            seen["methods"] = methods

            assert version == 5

            if username is not None:
                assert 0x02 in methods
                writer.write(b"\x05\x02")
                await writer.drain()

                assert await reader.readexactly(1) == b"\x01"
                user = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
                secret = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
                seen["credentials"] = (user, secret)

                writer.write(b"\x01\x00")
                await writer.drain()
            else:
                writer.write(b"\x05\x00")
                await writer.drain()

            head = await reader.readexactly(4)
            assert head[:2] == b"\x05\x01"

            address_type = head[3]

            if address_type == 0x01:
                host = socket.inet_ntoa(await reader.readexactly(4))
            elif address_type == 0x03:
                host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            else:
                raise AssertionError(f"unexpected address type {address_type}")

            port = int.from_bytes(await reader.readexactly(2), "big")
            seen["destination"] = (host, port)

            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", target_port
            )

            writer.write(b"\x05\x00\x00\x01" + b"\x00" * 4 + (0).to_bytes(2, "big"))
            await writer.drain()

            await _relay(reader, writer, upstream_reader, upstream_writer)
        finally:
            writer.close()

    server = await asyncio.start_server(serve, host="127.0.0.1", port=0)

    return server, server.sockets[0].getsockname()[1], seen


async def _start_http_connect_server(target_port: int):
    seen = {}

    async def serve(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            seen["request"] = request

            assert request.startswith(b"CONNECT ")

            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", target_port
            )

            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()

            await _relay(reader, writer, upstream_reader, upstream_writer)
        finally:
            writer.close()

    server = await asyncio.start_server(serve, host="127.0.0.1", port=0)

    return server, server.sockets[0].getsockname()[1], seen


async def _round_trip(transport, destination):
    payload = b"A" * 64

    await transport.connect(destination)

    try:
        await transport.send(payload)

        return await transport.recv()
    finally:
        await transport.close()


async def _close(*servers):
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_a_direct_connection_round_trips_an_abridged_frame():
    echo, echo_port = await _start_echo_server()
    transport = TCPAbridged()

    try:
        assert await _round_trip(transport, ("127.0.0.1", echo_port)) == b"A" * 64
    finally:
        await _close(echo)


async def test_a_socks5_proxy_carries_the_connection_to_the_destination():
    echo, echo_port = await _start_echo_server()
    proxy_server, proxy_port, seen = await _start_socks5_server(echo_port)

    transport = TCPAbridged(proxy=SOCKS5Proxy(hostname="127.0.0.1", port=proxy_port))

    try:
        assert await _round_trip(transport, ("127.0.0.1", echo_port)) == b"A" * 64
    finally:
        await _close(proxy_server, echo)

    assert seen["destination"] == ("127.0.0.1", echo_port), (
        "the proxy must be asked for the DC address, not for its own"
    )


async def test_a_socks5_proxy_is_given_credentials_that_a_url_would_mangle():
    echo, echo_port = await _start_echo_server()
    proxy_server, proxy_port, seen = await _start_socks5_server(
        echo_port, username="us:er", password="p@ss%1"
    )

    transport = TCPAbridged(
        proxy=SOCKS5Proxy(
            hostname="127.0.0.1",
            port=proxy_port,
            username="us:er",
            password="p@ss%1"
        )
    )

    try:
        assert await _round_trip(transport, ("127.0.0.1", echo_port)) == b"A" * 64
    finally:
        await _close(proxy_server, echo)

    assert seen["credentials"] == ("us:er", "p@ss%1")


async def test_an_http_proxy_tunnels_the_connection_with_connect():
    echo, echo_port = await _start_echo_server()
    proxy_server, proxy_port, seen = await _start_http_connect_server(echo_port)

    transport = TCPAbridged(proxy=HTTPProxy(hostname="127.0.0.1", port=proxy_port))

    try:
        assert await _round_trip(transport, ("127.0.0.1", echo_port)) == b"A" * 64
    finally:
        await _close(proxy_server, echo)

    assert f"127.0.0.1:{echo_port}".encode() in seen["request"]


async def test_a_refused_proxy_is_reported_as_an_os_error():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    transport = TCPAbridged(proxy=SOCKS5Proxy(hostname="127.0.0.1", port=dead_port))

    with pytest.raises(OSError):
        await transport.connect(("127.0.0.1", 443))

    await transport.close()
