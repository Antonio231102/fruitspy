from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import zipfile
from pathlib import Path

TARGET_ABIS = ("armeabi-v7a", "x86")
LIBRARY = "libmortargame.so"
GAMESPY_HOSTS = (
    b"%s.available.gamespy.com",
    b"peerchat.gamespy.com",
    b"%s.master.gamespy.com",
    b"%s.ms%d.gamespy.com",
    b"natneg1.gamespy.com",
    b"natneg2.gamespy.com",
    b"natneg3.gamespy.com",
)
NATNEG_RESOLVER_PATCHES = {
    "x86": (
        (0x1938FF, b"\x93", b"\x83", "natneg1 literal as hostname override"),
        (0x19391F, b"\x93", b"\x83", "natneg2 literal as hostname override"),
        (0x19393F, b"\x93", b"\x83", "natneg3 literal as hostname override"),
    ),
    "armeabi-v7a": (
        (0x1A7F6C, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg2 literal"),
        (0x1A7F70, bytes.fromhex("4c1afaeb"), bytes.fromhex("d7ffffea"), "skip natneg2 hostname formatting"),
        (0x1A7F9C, bytes.fromhex("00c08de5"), bytes.fromhex("0c70a0e1"), "load natneg3 literal"),
        (0x1A7FA0, bytes.fromhex("401afaeb"), bytes.fromhex("b2ffffea"), "skip natneg3 hostname formatting"),
        (0x1A7FCC, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg1 literal"),
        (0x1A7FD0, bytes.fromhex("341afaeb"), bytes.fromhex("b3ffffea"), "skip natneg1 hostname formatting"),
    ),
}
SIGNATURE_SUFFIXES = (".MF", ".SF", ".RSA", ".DSA", ".EC")


def parse_server_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is not None:
        if address.version != 4:
            raise argparse.ArgumentTypeError("the native patch requires an IPv4 address or DNS name")
        return str(address)

    hostname = value.rstrip(".").lower()
    allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
    labels = hostname.split(".")
    if (
        not hostname
        or not hostname.isascii()
        or any(
            not label
            or len(label) > 63
            or label[0] == "-"
            or label[-1] == "-"
            or any(character not in allowed for character in label)
            for label in labels
        )
    ):
        raise argparse.ArgumentTypeError("invalid server DNS name")
    shortest_literal = min(len(original) for original in GAMESPY_HOSTS)
    if len(hostname.encode("ascii")) >= shortest_literal:
        raise argparse.ArgumentTypeError(
            f"server DNS name must be shorter than {shortest_literal} ASCII bytes"
        )
    return hostname


def is_signature_entry(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("META-INF/") and upper.endswith(SIGNATURE_SUFFIXES)


def patch_library(data: bytes, server_host: str, abi: str) -> tuple[bytes, list[dict[str, object]]]:
    replacement = server_host.encode("ascii")
    patched = bytearray(data)
    records: list[dict[str, object]] = []

    for original in GAMESPY_HOSTS:
        if len(replacement) >= len(original):
            raise ValueError(
                f"server host {server_host!r} does not fit in {original.decode('ascii')!r}"
            )
        count = data.count(original)
        if count != 1:
            raise ValueError(
                f"{abi}: expected one {original.decode('ascii')!r} literal, found {count}"
            )
        offset = data.index(original)
        padded = replacement + b"\0" * (len(original) - len(replacement))
        patched[offset : offset + len(original)] = padded
        records.append(
            {
                "abi": abi,
                "offset": offset,
                "original": original.decode("ascii"),
                "replacement": server_host,
            }
        )

    for offset, expected, replacement_bytes, description in NATNEG_RESOLVER_PATCHES[abi]:
        actual = data[offset : offset + len(expected)]
        if actual != expected:
            raise ValueError(
                f"{abi}: unexpected NatNeg resolver bytes at 0x{offset:x}: "
                f"expected {expected.hex()}, found {actual.hex()}"
            )
        patched[offset : offset + len(expected)] = replacement_bytes
        records.append(
            {
                "abi": abi,
                "offset": offset,
                "original": expected.hex(),
                "replacement": replacement_bytes.hex(),
                "description": description,
            }
        )

    result = bytes(patched)
    if b"gamespy.com" in result:
        raise ValueError(f"{abi}: an unpatched gamespy.com hostname remains")
    return result, records


def patch_apk(source: Path, output: Path, server_host: str) -> list[dict[str, object]]:
    if source.resolve() == output.resolve():
        raise ValueError("source and output APK paths must differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    seen_abis: set[str] = set()

    with zipfile.ZipFile(source, "r") as source_zip, zipfile.ZipFile(output, "w") as output_zip:
        for info in source_zip.infolist():
            if is_signature_entry(info.filename):
                continue

            data = source_zip.read(info.filename)
            for abi in TARGET_ABIS:
                target = f"lib/{abi}/{LIBRARY}"
                if info.filename == target:
                    data, library_records = patch_library(data, server_host, abi)
                    records.extend(library_records)
                    seen_abis.add(abi)
                    break

            output_zip.writestr(info, data)

    missing = set(TARGET_ABIS) - seen_abis
    if missing:
        output.unlink(missing_ok=True)
        raise ValueError(f"missing target ABI libraries: {', '.join(sorted(missing))}")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch Fruit Ninja 1.7.6 GameSpy hostnames for a LAN or Internet server."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--server-host", required=True, type=parse_server_host)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    try:
        records = patch_apk(args.source, args.output, args.server_host)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"patch_apk: {exc}", file=sys.stderr)
        return 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    print(f"Patched {len(records)} native locations in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
