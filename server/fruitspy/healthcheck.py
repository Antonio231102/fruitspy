from __future__ import annotations

import argparse
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from .availability_qr import PACKET_AVAILABLE, availability_response
from .config import ServerConfig, load_config
from .natneg import MAGIC, NN_ERT_TEST, NN_NATIFY_REQUEST


@dataclass(frozen=True, slots=True)
class CheckResult:
    service: str
    healthy: bool
    detail: str


def _udp_exchange(host: str, port: int, payload: bytes, timeout: float) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, (host, port))
        response, _ = sock.recvfrom(4096)
        return response


def _check_availability(config: ServerConfig, host: str, timeout: float) -> None:
    request = (
        bytes((PACKET_AVAILABLE,))
        + b"\x00\x00\x00\x00"
        + config.game.name.encode("ascii")
        + b"\x00"
    )
    response = _udp_exchange(
        host,
        config.ports.availability_qr_udp,
        request,
        timeout,
    )
    if response != availability_response():
        raise RuntimeError("unexpected availability response")


def _check_tcp(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return


def _check_natneg(config: ServerConfig, host: str, timeout: float) -> None:
    cookie = b"HLTH"
    request = MAGIC + bytes((3, NN_NATIFY_REQUEST)) + cookie
    response = _udp_exchange(host, config.ports.natneg_udp, request, timeout)
    if len(response) < 12 or response[:6] != MAGIC:
        raise RuntimeError("invalid NatNeg response header")
    if response[7] != NN_ERT_TEST or response[8:12] != cookie:
        raise RuntimeError("unexpected NatNeg response")


def check_server(
    config: ServerConfig,
    host: str = "127.0.0.1",
    timeout: float = 2.0,
) -> list[CheckResult]:
    checks = (
        (
            "availability_qr_udp",
            lambda: _check_availability(config, host, timeout),
        ),
        (
            "peerchat_tcp",
            lambda: _check_tcp(host, config.ports.peerchat_tcp, timeout),
        ),
        (
            "server_browser_tcp",
            lambda: _check_tcp(host, config.ports.server_browser_tcp, timeout),
        ),
        (
            "natneg_udp",
            lambda: _check_natneg(config, host, timeout),
        ),
    )
    results: list[CheckResult] = []
    for service, check in checks:
        try:
            check()
        except (OSError, RuntimeError) as error:
            results.append(CheckResult(service, False, str(error)))
        else:
            results.append(CheckResult(service, True, "ok"))
    return results


def wait_for_server(
    config: ServerConfig,
    host: str,
    timeout: float,
    wait_seconds: float,
) -> list[CheckResult]:
    deadline = time.monotonic() + wait_seconds
    while True:
        results = check_server(config, host, timeout)
        if all(result.healthy for result in results):
            return results
        if time.monotonic() >= deadline:
            return results
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fruitspy-healthcheck",
        description="Check all four FruitSpy protocol listeners",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config.json",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="retry until healthy for up to this many seconds",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.wait < 0:
        parser.error("--wait must not be negative")

    config = load_config(args.config)
    results = wait_for_server(
        config,
        args.host,
        args.timeout,
        args.wait,
    )
    for result in results:
        status = "PASS" if result.healthy else "FAIL"
        print(f"{status} service={result.service} detail={result.detail}")
    if not all(result.healthy for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
