from __future__ import annotations

import argparse
import asyncio
import secrets
import socket
import struct
import time

from fruitspy.natneg import (
    MAGIC,
    NN_CONNECT,
    NN_CONNECT_PING,
    NN_INIT,
    NN_INIT_ACK,
    NN_REPORT,
    NN_REPORT_ACK,
)

Address = tuple[str, int]


def natneg_init(cookie: bytes, client_index: int) -> bytes:
    return (
        MAGIC
        + bytes((3, NN_INIT))
        + cookie
        + bytes((0, client_index, 0))
        + b"\x00" * 6
    )


def natneg_report(cookie: bytes, client_index: int) -> bytes:
    return (
        MAGIC
        + bytes((3, NN_REPORT))
        + cookie
        + bytes((0, client_index, 1))
        + struct.pack("<II", 2, 2)
    )


async def receive_type(
    loop: asyncio.AbstractEventLoop,
    sock: socket.socket,
    packet_type: int,
    timeout: float,
) -> bytes:
    deadline = loop.time() + timeout
    while True:
        packet, _ = await asyncio.wait_for(
            loop.sock_recvfrom(sock, 4096),
            deadline - loop.time(),
        )
        if len(packet) > 7 and packet[7] == packet_type:
            return packet


async def establish_relay(
    loop: asyncio.AbstractEventLoop,
    destination: Address,
    cookie: bytes,
    timeout: float,
) -> tuple[socket.socket, socket.socket]:
    clients = tuple(socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2))
    for client in clients:
        client.bind(("127.0.0.1", 0))
        client.setblocking(False)
    first, second = clients
    try:
        await loop.sock_sendto(first, natneg_init(cookie, 0), destination)
        await receive_type(loop, first, NN_INIT_ACK, timeout)
        await loop.sock_sendto(second, natneg_init(cookie, 1), destination)
        await receive_type(loop, first, NN_CONNECT, timeout)
        await receive_type(loop, second, NN_INIT_ACK, timeout)
        await receive_type(loop, second, NN_CONNECT, timeout)
        first_ping, second_ping = await asyncio.gather(
            receive_type(loop, first, NN_CONNECT_PING, timeout),
            receive_type(loop, second, NN_CONNECT_PING, timeout),
        )
        await loop.sock_sendto(first, first_ping, destination)
        await loop.sock_sendto(second, second_ping, destination)
        await asyncio.sleep(0.1)
        await loop.sock_sendto(first, natneg_report(cookie, 0), destination)
        await receive_type(loop, first, NN_REPORT_ACK, timeout)
        await loop.sock_sendto(second, natneg_report(cookie, 1), destination)
        await receive_type(loop, second, NN_REPORT_ACK, timeout)
        return first, second
    except BaseException:
        first.close()
        second.close()
        raise


async def assert_forwarded(
    loop: asyncio.AbstractEventLoop,
    sender: socket.socket,
    receiver: socket.socket,
    destination: Address,
    payload: bytes,
    timeout: float,
) -> None:
    await loop.sock_sendto(sender, payload, destination)
    received, _ = await asyncio.wait_for(loop.sock_recvfrom(receiver, 4096), timeout)
    if received != payload:
        raise RuntimeError(
            f"relay payload mismatch: expected {payload!r}, received {received!r}"
        )


async def run_probe(args: argparse.Namespace) -> None:
    if args.duration_seconds <= 900:
        raise ValueError("duration_seconds must exceed the production 900-second TTL")
    if not 0 < args.interval_seconds < 900:
        raise ValueError("interval_seconds must be between 0 and 900")
    destination = (socket.gethostbyname(args.host), args.port)
    cookie = secrets.token_bytes(4)
    loop = asyncio.get_running_loop()
    first, second = await establish_relay(
        loop,
        destination,
        cookie,
        args.fallback_timeout_seconds,
    )
    started = time.monotonic()
    deadline = started + args.duration_seconds
    rounds = 0
    try:
        while True:
            sequence = struct.pack(">I", rounds)
            await assert_forwarded(
                loop,
                first,
                second,
                destination,
                b"ttl-first-" + sequence,
                args.packet_timeout_seconds,
            )
            await assert_forwarded(
                loop,
                second,
                first,
                destination,
                b"ttl-second-" + sequence,
                args.packet_timeout_seconds,
            )
            rounds += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(args.interval_seconds, remaining))
    finally:
        first.close()
        second.close()
    elapsed = time.monotonic() - started
    print(
        "PASS "
        f"cookie={cookie.hex()} elapsed_seconds={elapsed:.3f} "
        f"rounds={rounds} packets={rounds * 2}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep a FruitSpy relay active beyond its 15-minute idle TTL."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27901)
    parser.add_argument("--duration-seconds", type=float, default=905)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--fallback-timeout-seconds", type=float, default=10)
    parser.add_argument("--packet-timeout-seconds", type=float, default=2)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run_probe(parse_args()))
