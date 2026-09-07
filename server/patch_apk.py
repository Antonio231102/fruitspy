from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

TOOL_NAME = "fruitspy-apk-patcher"
TOOL_VERSION = "1"
CLEAN_APK_SHA256 = "5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650"
TARGET_ABIS = ("armeabi", "armeabi-v7a", "x86")
LIBRARY = "libmortargame.so"
NATIVE_ROOT = Path(__file__).resolve().parent / "patcher_native"
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
    "armeabi": (
        (0x1A7F9C, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg2 literal"),
        (0x1A7FA0, bytes.fromhex("491afaeb"), bytes.fromhex("d7ffffea"), "skip natneg2 hostname formatting"),
        (0x1A7FCC, bytes.fromhex("00c08de5"), bytes.fromhex("0c70a0e1"), "load natneg3 literal"),
        (0x1A7FD0, bytes.fromhex("3d1afaeb"), bytes.fromhex("b2ffffea"), "skip natneg3 hostname formatting"),
        (0x1A7FFC, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg1 literal"),
        (0x1A8000, bytes.fromhex("311afaeb"), bytes.fromhex("b3ffffea"), "skip natneg1 hostname formatting"),
    ),
    "armeabi-v7a": (
        (0x1A7F6C, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg2 literal"),
        (0x1A7F70, bytes.fromhex("4c1afaeb"), bytes.fromhex("d7ffffea"), "skip natneg2 hostname formatting"),
        (0x1A7F9C, bytes.fromhex("00c08de5"), bytes.fromhex("0c70a0e1"), "load natneg3 literal"),
        (0x1A7FA0, bytes.fromhex("401afaeb"), bytes.fromhex("b2ffffea"), "skip natneg3 hostname formatting"),
        (0x1A7FCC, bytes.fromhex("00c08de5"), bytes.fromhex("0c60a0e1"), "load natneg1 literal"),
        (0x1A7FD0, bytes.fromhex("341afaeb"), bytes.fromhex("b3ffffea"), "skip natneg1 hostname formatting"),
    ),
    "x86": (
        (0x1938FF, b"\x93", b"\x83", "natneg1 literal as hostname override"),
        (0x19391F, b"\x93", b"\x83", "natneg2 literal as hostname override"),
        (0x19393F, b"\x93", b"\x83", "natneg3 literal as hostname override"),
    ),
}
SIGNATURE_SUFFIXES = (".MF", ".SF", ".RSA", ".DSA", ".EC")

ELF32_HEADER_SIZE = 52
ELF32_PROGRAM_HEADER_SIZE = 32
ELF32_SECTION_HEADER_SIZE = 40
PT_LOAD = 1
PF_X = 1


@dataclass(frozen=True)
class ClockPatch:
    abi: str
    input_sha256: str
    output_sha256: str
    clock_offset: int
    clock_before: bytes
    clock_after: bytes
    conversion_offset: int
    conversion_before: bytes
    conversion_after: bytes
    payload_file_offset: int
    payload_vaddr: int
    payload_sha256: str

    @property
    def payload_path(self) -> Path:
        return NATIVE_ROOT / "payloads" / f"{self.abi}.bin"


CLOCK_PATCHES = {
    patch.abi: patch
    for patch in (
        ClockPatch(
            abi="armeabi",
            input_sha256="f3504049d4c89a60260ed12bfddb36520db1d92db8532983344e88efd5714d29",
            output_sha256="5ceba5b4f6aa1091ec91af14df9708bfeccaa7630585120f1bce813f05b5c4f1",
            clock_offset=0x1E4518,
            clock_before=bytes.fromhex("2828f9eb"),
            clock_after=bytes.fromhex("f1f906eb"),
            conversion_offset=0x1E4544,
            conversion_before=bytes.fromhex("e45e04eb"),
            conversion_after=bytes.fromhex("f9f906eb"),
            payload_file_offset=0x3A2CE4,
            payload_vaddr=0x3A2CE4,
            payload_sha256="af424f843eb6d16e18c03b49ce1076fb7b283a01482ec84e4ceedd142c88f9af",
        ),
        ClockPatch(
            abi="armeabi-v7a",
            input_sha256="8ec12dea59ac4a160078b19637b137bada21869184117a80d095908921cc4507",
            output_sha256="7325dedde22c58e75c790db70900661b524ac2c728e233a4e40edf36377114aa",
            clock_offset=0x1E3204,
            clock_before=bytes.fromhex("e42cf9eb"),
            clock_after=bytes.fromhex("c80907eb"),
            conversion_offset=0x1E3230,
            conversion_before=bytes.fromhex("3e6304eb"),
            conversion_after=bytes.fromhex("d00907eb"),
            payload_file_offset=0x3A592C,
            payload_vaddr=0x3A592C,
            payload_sha256="16297923317059a10dfeb3b709ad9155278bcf85e7622b0b67e6a5b4d36af4aa",
        ),
        ClockPatch(
            abi="x86",
            input_sha256="223203cd256ef6e2471a817ef35bdb38bf6b3e9db45288a73d90794cc3d7232c",
            output_sha256="91867749b72054ee1362d91ed87a3dbfdc1ec2e53a62c8961da20fc7aadd5c31",
            clock_offset=0x1D4ED6,
            clock_before=bytes.fromhex("e8d1a8e5ff"),
            clock_after=bytes.fromhex("e8c1141d00"),
            conversion_offset=0x1D4EF4,
            conversion_before=bytes.fromhex("f30f2ac08b442434"),
            conversion_after=bytes.fromhex("e9d3141d00909090"),
            payload_file_offset=0x3A639C,
            payload_vaddr=0x3A639C,
            payload_sha256="2f281cc847d8fca9362fd53558a8713289a4d4cec653d6ec3a9565ae9b26ced8",
        ),
    )
}


@dataclass(frozen=True)
class AndroidTools:
    keytool: Path
    zipalign: Path
    apksigner: Path


@dataclass(frozen=True)
class SigningMaterial:
    keystore: Path
    alias: str
    store_password: str
    key_password: str
    created: bool


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def read_u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def read_u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def write_u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


def verify_elf32(data: bytes, abi: str) -> None:
    if len(data) < ELF32_HEADER_SIZE or data[:4] != b"\x7fELF":
        raise ValueError(f"{abi}: {LIBRARY} is not ELF")
    if data[4] != 1 or data[5] != 1:
        raise ValueError(f"{abi}: expected a 32-bit little-endian ELF")
    if read_u16(data, 40) != ELF32_HEADER_SIZE:
        raise ValueError(f"{abi}: unexpected ELF header size")
    if read_u16(data, 42) != ELF32_PROGRAM_HEADER_SIZE:
        raise ValueError(f"{abi}: unexpected ELF program header size")
    if read_u16(data, 46) != ELF32_SECTION_HEADER_SIZE:
        raise ValueError(f"{abi}: unexpected ELF section header size")


def program_headers(data: bytes | bytearray) -> list[tuple[int, ...]]:
    phoff = read_u32(data, 28)
    phentsize = read_u16(data, 42)
    phnum = read_u16(data, 44)
    end = phoff + phentsize * phnum
    if end > len(data):
        raise ValueError("ELF program header table lies outside the file")
    return [
        struct.unpack_from("<IIIIIIII", data, phoff + index * phentsize)
        for index in range(phnum)
    ]


def inject_payload(data: bytes, patch: ClockPatch, payload: bytes) -> tuple[bytes, int]:
    headers = program_headers(data)
    executable_index = next(
        (
            index
            for index, header in enumerate(headers)
            if header[0] == PT_LOAD
            and header[6] & PF_X
            and header[2] + header[5] == patch.payload_vaddr
            and header[1] + header[4] == patch.payload_file_offset
        ),
        None,
    )
    if executable_index is None:
        raise ValueError(f"{patch.abi}: executable segment does not end at payload cave")

    executable = headers[executable_index]
    next_load = min(
        (
            header
            for header in headers
            if header[0] == PT_LOAD and header[2] >= patch.payload_vaddr + len(payload)
        ),
        key=lambda header: header[2],
        default=None,
    )
    if next_load is None:
        raise ValueError(f"{patch.abi}: no load segment follows payload cave")
    if patch.payload_vaddr + len(payload) > next_load[2]:
        raise ValueError(f"{patch.abi}: payload overlaps the writable segment")

    insertion_offset = next_load[1]
    if patch.payload_file_offset > insertion_offset:
        raise ValueError(f"{patch.abi}: invalid payload file offset")
    file_gap = insertion_offset - patch.payload_file_offset
    if any(data[patch.payload_file_offset:insertion_offset]):
        raise ValueError(f"{patch.abi}: payload file cave is not zero-filled")

    required = max(0, len(payload) - file_gap)
    alignment = next_load[7]
    if required and (alignment == 0 or alignment & (alignment - 1)):
        raise ValueError(f"{patch.abi}: unsupported load-segment alignment")
    shift = 0 if required == 0 else (required + alignment - 1) & -alignment

    original_shoff = read_u32(data, 32)
    shentsize = read_u16(data, 46)
    shnum = read_u16(data, 48)
    if original_shoff + shentsize * shnum > len(data):
        raise ValueError(f"{patch.abi}: ELF section header table lies outside the file")
    original_section_offsets = [
        read_u32(data, original_shoff + index * shentsize + 16)
        for index in range(shnum)
    ]

    result = bytearray(data)
    if shift:
        result[insertion_offset:insertion_offset] = b"\0" * shift

    new_shoff = original_shoff + (shift if original_shoff >= insertion_offset else 0)
    write_u32(result, 32, new_shoff)

    phoff = read_u32(result, 28)
    phentsize = read_u16(result, 42)
    for index, header in enumerate(headers):
        if shift and header[1] and header[1] >= insertion_offset:
            write_u32(result, phoff + index * phentsize + 4, header[1] + shift)

    executable_header = phoff + executable_index * phentsize
    write_u32(result, executable_header + 16, executable[4] + len(payload))
    write_u32(result, executable_header + 20, executable[5] + len(payload))

    for index, section_offset in enumerate(original_section_offsets):
        if shift and section_offset and section_offset >= insertion_offset:
            write_u32(
                result,
                new_shoff + index * shentsize + 16,
                section_offset + shift,
            )

    result[
        patch.payload_file_offset : patch.payload_file_offset + len(payload)
    ] = payload

    for header in program_headers(result):
        if header[0] == PT_LOAD and header[7]:
            if header[1] % header[7] != header[2] % header[7]:
                raise ValueError(f"{patch.abi}: load segment lost file/address congruence")
    return bytes(result), shift


def replace_exact(
    data: bytearray,
    offset: int,
    before: bytes,
    after: bytes,
    abi: str,
) -> None:
    if len(before) != len(after):
        raise ValueError(f"{abi}: replacement changes instruction range length")
    end = offset + len(before)
    if end > len(data) or data[offset:end] != before:
        found = data[offset:end].hex() if end <= len(data) else "<outside file>"
        raise ValueError(
            f"{abi}: unsupported instruction range at 0x{offset:x}; "
            f"expected {before.hex()}, found {found}"
        )
    data[offset:end] = after


def patch_clock_library(data: bytes, patch: ClockPatch) -> tuple[bytes, dict[str, object]]:
    verify_elf32(data, patch.abi)
    input_hash = sha256_bytes(data)
    if input_hash != patch.input_sha256:
        raise ValueError(f"{patch.abi}: unsupported clean {LIBRARY} SHA-256 {input_hash}")

    payload = patch.payload_path.read_bytes()
    if sha256_bytes(payload) != patch.payload_sha256:
        raise ValueError(f"{patch.abi}: native payload hash mismatch")

    injected, file_shift = inject_payload(data, patch, payload)
    mutable = bytearray(injected)
    replace_exact(
        mutable,
        patch.clock_offset,
        patch.clock_before,
        patch.clock_after,
        patch.abi,
    )
    replace_exact(
        mutable,
        patch.conversion_offset,
        patch.conversion_before,
        patch.conversion_after,
        patch.abi,
    )
    result = bytes(mutable)
    output_hash = sha256_bytes(result)
    if output_hash != patch.output_sha256:
        raise ValueError(
            f"{patch.abi}: clock-patched library hash mismatch; got {output_hash}"
        )

    return result, {
        "clock_call_file_offset": patch.clock_offset,
        "clock_call_before": patch.clock_before.hex(),
        "clock_call_after": patch.clock_after.hex(),
        "conversion_file_offset": patch.conversion_offset,
        "conversion_before": patch.conversion_before.hex(),
        "conversion_after": patch.conversion_after.hex(),
        "payload_file_offset": patch.payload_file_offset,
        "payload_virtual_address": patch.payload_vaddr,
        "payload_size": len(payload),
        "payload_sha256": patch.payload_sha256,
        "file_offset_shift": file_shift,
        "output_sha256": output_hash,
    }


def patch_endpoint_library(
    data: bytes,
    server_host: str,
    abi: str,
) -> tuple[bytes, list[dict[str, object]]]:
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
                "kind": "hostname",
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
                "kind": "natneg_resolver",
                "offset": offset,
                "original": expected.hex(),
                "replacement": replacement_bytes.hex(),
                "description": description,
            }
        )

    result = bytes(patched)
    if b"gamespy.com" in result:
        raise ValueError(f"{abi}: an unpatched gamespy.com hostname remains")
    if result.count(replacement) != len(GAMESPY_HOSTS):
        raise ValueError(f"{abi}: endpoint replacement count is not {len(GAMESPY_HOSTS)}")
    return result, records


def patch_library(data: bytes, server_host: str, abi: str) -> tuple[bytes, dict[str, object]]:
    clock_result, clock_record = patch_clock_library(data, CLOCK_PATCHES[abi])
    result, endpoint_records = patch_endpoint_library(clock_result, server_host, abi)
    return result, {
        "abi": abi,
        "library": f"lib/{abi}/{LIBRARY}",
        "input_sha256": sha256_bytes(data),
        "clock": clock_record,
        "endpoint": {
            "server_host": server_host,
            "patch_count": len(endpoint_records),
            "patches": endpoint_records,
        },
        "output_sha256": sha256_bytes(result),
    }


def patch_apk(source: Path, output: Path, server_host: str) -> dict[str, object]:
    server_host = parse_server_host(server_host)
    if source.resolve() == output.resolve():
        raise ValueError("source and output APK paths must differ")
    source_hash = sha256_file(source)
    if source_hash != CLEAN_APK_SHA256:
        raise ValueError(
            f"unsupported source APK SHA-256 {source_hash}; expected {CLEAN_APK_SHA256}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(
        f".{output.name}.{secrets.token_hex(8)}.tmp"
    )
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    removed_signatures: list[str] = []
    patches_by_path = {
        f"lib/{abi}/{LIBRARY}": abi
        for abi in TARGET_ABIS
    }

    try:
        with zipfile.ZipFile(source, "r") as source_zip, zipfile.ZipFile(
            temporary_output, "w"
        ) as output_zip:
            output_zip.comment = source_zip.comment
            for info in source_zip.infolist():
                if is_signature_entry(info.filename):
                    removed_signatures.append(info.filename)
                    continue

                data = source_zip.read(info.filename)
                abi = patches_by_path.get(info.filename)
                if abi is not None:
                    data, record = patch_library(data, server_host, abi)
                    records.append(record)
                    seen.add(info.filename)
                output_zip.writestr(info, data)

        missing = sorted(set(patches_by_path) - seen)
        if missing:
            raise ValueError(f"missing native libraries: {', '.join(missing)}")
        temporary_output.replace(output)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise

    return {
        "manifest_version": 1,
        "tool": {
            "name": TOOL_NAME,
            "version": TOOL_VERSION,
            "sha256": sha256_file(Path(__file__)),
        },
        "input": {
            "apk_sha256": source_hash,
            "allowlisted_apk_sha256": CLEAN_APK_SHA256,
        },
        "patch_order": ["monotonic_clock", "gamespy_endpoint"],
        "server_host": server_host,
        "removed_signature_entries": sorted(removed_signatures),
        "libraries": records,
        "unsigned_apk_sha256": sha256_file(output),
        "output": {
            "apk_sha256": sha256_file(output),
            "signed": False,
            "zipaligned": False,
        },
    }


def default_signing_directory() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "FruitSpy"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FruitSpy"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "fruitspy"


def version_key(path: Path) -> tuple[int, ...]:
    numbers = tuple(int(value) for value in re.findall(r"\d+", path.parent.name))
    return numbers or (0,)


def require_executable(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{description} was not found at {resolved}")
    return resolved


def discover_keytool(explicit: Path | None) -> Path:
    if explicit is not None:
        return require_executable(explicit, "keytool")
    found = shutil.which("keytool")
    if found:
        return Path(found).resolve()
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("keytool was not found; install a JDK or pass --keytool")


def android_sdk_roots(explicit: Path | None) -> list[Path]:
    roots: list[Path] = []
    if explicit is not None:
        roots.append(explicit)
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Android" / "Sdk")
    else:
        roots.extend((Path.home() / "Android" / "Sdk", Path.home() / "Library" / "Android" / "sdk"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def discover_android_tool(name: str, explicit: Path | None, sdk: Path | None) -> Path:
    if explicit is not None:
        return require_executable(explicit, name)
    found = shutil.which(name)
    if found:
        return Path(found).resolve()

    filenames = (name, f"{name}.exe", f"{name}.bat")
    candidates: list[Path] = []
    for root in android_sdk_roots(sdk):
        build_tools = root / "build-tools"
        if not build_tools.is_dir():
            continue
        for version in build_tools.iterdir():
            if version.is_dir():
                for filename in filenames:
                    candidate = version / filename
                    if candidate.is_file():
                        candidates.append(candidate)
    if candidates:
        return max(candidates, key=version_key).resolve()
    raise ValueError(
        f"{name} was not found; install Android SDK Build Tools, set "
        "ANDROID_SDK_ROOT, or pass its explicit path"
    )


def discover_android_tools(
    android_sdk: Path | None = None,
    keytool: Path | None = None,
    zipalign: Path | None = None,
    apksigner: Path | None = None,
) -> AndroidTools:
    return AndroidTools(
        keytool=discover_keytool(keytool),
        zipalign=discover_android_tool("zipalign", zipalign, android_sdk),
        apksigner=discover_android_tool("apksigner", apksigner, android_sdk),
    )


def run_checked(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        if details:
            raise ValueError(f"{description} failed: {details}") from exc
        raise ValueError(f"{description} failed with exit code {exc.returncode}") from exc
    except OSError as exc:
        raise ValueError(f"{description} could not start: {exc}") from exc


def restrict_permissions(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError as exc:
        raise ValueError(f"could not restrict permissions on {path}: {exc}") from exc


@contextmanager
def password_file(directory: Path, password: str) -> Iterator[Path]:
    path = directory / f".password-{secrets.token_hex(8)}.txt"
    try:
        path.write_text(password + "\n", encoding="utf-8")
        restrict_permissions(path, 0o600)
        yield path
    finally:
        path.unlink(missing_ok=True)


def load_signing_material(directory: Path) -> SigningMaterial | None:
    keystore = directory / "signing.p12"
    configuration = directory / "signing.json"
    if not keystore.exists() and not configuration.exists():
        return None
    if not keystore.is_file() or not configuration.is_file():
        raise ValueError(
            f"incomplete signing material in {directory}; both signing.p12 and signing.json are required"
        )
    try:
        data = json.loads(configuration.read_text(encoding="utf-8"))
        alias = data["alias"]
        store_password = data["store_password"]
        key_password = data["key_password"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"invalid signing configuration in {configuration}") from exc
    if (
        data.get("version") != 1
        or not all(isinstance(value, str) and value for value in (alias, store_password, key_password))
    ):
        raise ValueError(f"invalid signing configuration in {configuration}")
    return SigningMaterial(
        keystore=keystore,
        alias=alias,
        store_password=store_password,
        key_password=key_password,
        created=False,
    )


def ensure_signing_material(directory: Path, keytool: Path) -> SigningMaterial:
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    restrict_permissions(directory, 0o700)
    existing = load_signing_material(directory)
    if existing is not None:
        return existing

    alias = "fruitspy"
    password = secrets.token_urlsafe(32)
    keystore = directory / "signing.p12"
    configuration = directory / "signing.json"
    temporary_keystore = directory / f".signing-{secrets.token_hex(8)}.p12"
    temporary_configuration = directory / f".signing-{secrets.token_hex(8)}.json"
    try:
        with password_file(directory, password) as secret:
            run_checked(
                [
                    str(keytool),
                    "-genkeypair",
                    "-noprompt",
                    "-keystore",
                    str(temporary_keystore),
                    "-storetype",
                    "PKCS12",
                    "-storepass:file",
                    str(secret),
                    "-keypass:file",
                    str(secret),
                    "-alias",
                    alias,
                    "-keyalg",
                    "RSA",
                    "-keysize",
                    "2048",
                    "-sigalg",
                    "SHA256withRSA",
                    "-validity",
                    "36500",
                    "-dname",
                    "CN=FruitSpy Local Signing Key, OU=FruitSpy, O=FruitSpy, C=XX",
                ],
                "local signing-key generation",
            )
        restrict_permissions(temporary_keystore, 0o600)
        temporary_configuration.write_text(
            json.dumps(
                {
                    "version": 1,
                    "alias": alias,
                    "store_password": password,
                    "key_password": password,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        restrict_permissions(temporary_configuration, 0o600)
        temporary_keystore.replace(keystore)
        temporary_configuration.replace(configuration)
    except Exception:
        temporary_keystore.unlink(missing_ok=True)
        temporary_configuration.unlink(missing_ok=True)
        if keystore.exists() != configuration.exists():
            keystore.unlink(missing_ok=True)
            configuration.unlink(missing_ok=True)
        raise

    return SigningMaterial(
        keystore=keystore,
        alias=alias,
        store_password=password,
        key_password=password,
        created=True,
    )


def align_sign_and_verify(
    unsigned_apk: Path,
    output: Path,
    tools: AndroidTools,
    material: SigningMaterial,
    work: Path,
) -> dict[str, object]:
    aligned = work / "aligned.apk"
    run_checked(
        [
            str(tools.zipalign),
            "-f",
            "-p",
            "4",
            str(unsigned_apk),
            str(aligned),
        ],
        "zipalign",
    )

    with password_file(work, material.store_password) as store_secret:
        with password_file(work, material.key_password) as key_secret:
            run_checked(
                [
                    str(tools.apksigner),
                    "sign",
                    "--ks",
                    str(material.keystore),
                    "--ks-key-alias",
                    material.alias,
                    "--ks-pass",
                    f"file:{store_secret}",
                    "--key-pass",
                    f"file:{key_secret}",
                    "--v1-signing-enabled",
                    "true",
                    "--v2-signing-enabled",
                    "true",
                    "--v3-signing-enabled",
                    "true",
                    "--v4-signing-enabled",
                    "false",
                    "--out",
                    str(output),
                    str(aligned),
                ],
                "APK signing",
            )

    verification = run_checked(
        [
            str(tools.apksigner),
            "verify",
            "--verbose",
            "--print-certs",
            str(output),
        ],
        "APK signature verification",
    )
    run_checked(
        [str(tools.zipalign), "-c", "-p", "4", str(output)],
        "APK alignment verification",
    )
    verification_text = verification.stdout + "\n" + verification.stderr
    certificate_match = re.search(
        r"certificate SHA-256 digest:\s*([0-9a-fA-F]{64})",
        verification_text,
    )
    if certificate_match is None:
        raise ValueError("APK signature verification did not report a signer certificate")
    schemes = {
        match.group(1): match.group(2).lower() == "true"
        for match in re.finditer(
            r"Verified using (v[1-4]) scheme[^:]*:\s*(true|false)",
            verification_text,
            re.IGNORECASE,
        )
    }
    if not schemes.get("v1", False):
        raise ValueError("APK lacks the required v1 signature for older Android versions")
    return {
        "mode": "persistent_local_key",
        "key_created": material.created,
        "certificate_sha256": certificate_match.group(1).lower(),
        "schemes": schemes,
    }




def build_output(
    source: Path,
    output: Path,
    server_host: str,
    report: Path | None,
    unsigned: bool,
    signing_directory: Path | None = None,
    android_sdk: Path | None = None,
    keytool: Path | None = None,
    zipalign: Path | None = None,
    apksigner: Path | None = None,
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    report = report.expanduser().resolve() if report is not None else None
    if not source.is_file():
        raise ValueError(f"source APK was not found at {source}")
    if source == output:
        raise ValueError("source and output APK paths must differ")
    if report is not None and report in (source, output):
        raise ValueError("manifest path must differ from source and output APK paths")
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    if report is not None and report.exists():
        raise ValueError(f"manifest already exists: {report}")

    tools = None
    if not unsigned:
        tools = discover_android_tools(android_sdk, keytool, zipalign, apksigner)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".fruitspy-patch-", dir=output.parent) as temporary:
        work = Path(temporary)
        unsigned_apk = work / "unsigned.apk"
        manifest = patch_apk(source, unsigned_apk, server_host)
        final_apk = unsigned_apk
        if tools is not None:
            signing_root = signing_directory or default_signing_directory()
            material = ensure_signing_material(signing_root, tools.keytool)
            signed_apk = work / "signed.apk"
            signing_record = align_sign_and_verify(
                unsigned_apk,
                signed_apk,
                tools,
                material,
                work,
            )
            final_apk = signed_apk
            manifest["signing"] = signing_record
            manifest["output"] = {
                "apk_sha256": sha256_file(signed_apk),
                "signed": True,
                "zipaligned": True,
            }

        report_temporary: Path | None = None
        try:
            if report is not None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report_temporary = report.with_name(
                    f".{report.name}.{secrets.token_hex(8)}.tmp"
                )
                report_temporary.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            final_apk.replace(output)
            if report_temporary is not None:
                report_temporary.replace(report)
        except Exception:
            output.unlink(missing_ok=True)
            if report_temporary is not None:
                report_temporary.unlink(missing_ok=True)
            raise
    return manifest


def prompt_value(label: str) -> str:
    value = input(label).strip().strip('"')
    if not value:
        raise ValueError("a value is required")
    return value


def collect_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[Path, Path, str, Path | None]:
    interactive = not args.non_interactive and sys.stdin.isatty()
    if args.source is None:
        if not interactive:
            parser.error("source is required in non-interactive mode")
        source = Path(prompt_value("Clean Fruit Ninja 1.7.6 APK: "))
    else:
        source = args.source

    if args.server_host is None:
        if not interactive:
            parser.error("--server-host is required in non-interactive mode")
        while True:
            try:
                server_host = parse_server_host(
                    prompt_value("FruitSpy server IPv4 address or short DNS name: ")
                )
                break
            except argparse.ArgumentTypeError as exc:
                print(f"Invalid server address: {exc}", file=sys.stderr)
    else:
        server_host = args.server_host

    output = args.output
    if output is None:
        output = source.with_name(f"{source.stem} - FruitSpy.apk")
    if args.no_report:
        report = None
    elif args.report is not None:
        report = args.report
    else:
        report = output.with_suffix(output.suffix + ".manifest.json")
    return source, output, server_host, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a Fruit Ninja 1.7.6 APK with the monotonic-clock fix and "
            "FruitSpy endpoint patch for all three packaged ABIs."
        )
    )
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--server-host", type=parse_server_host)
    parser.add_argument(
        "--unsigned",
        action="store_true",
        help="produce an unsigned, unaligned APK without requiring Android Build Tools",
    )
    parser.add_argument("--signing-dir", type=Path)
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--keytool", type=Path)
    parser.add_argument("--zipalign", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()

    try:
        source, output, server_host, report = collect_arguments(parser, args)
        manifest = build_output(
            source=source,
            output=output,
            server_host=server_host,
            report=report,
            unsigned=args.unsigned,
            signing_directory=args.signing_dir,
            android_sdk=args.android_sdk,
            keytool=args.keytool,
            zipalign=args.zipalign,
            apksigner=args.apksigner,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"patch_apk: {exc}", file=sys.stderr)
        return 1

    mode = "unsigned" if args.unsigned else "signed and verified"
    print(f"Created {mode} APK: {output}")
    print(f"Patched {len(manifest['libraries'])} ABI libraries.")
    if report is not None:
        print(f"Manifest: {report}")
    signing = manifest.get("signing")
    if isinstance(signing, dict):
        print(f"Signer certificate SHA-256: {signing['certificate_sha256']}")
        if signing["key_created"]:
            print(
                "Created a persistent local signing key. Back up the signing "
                "directory to preserve APK update compatibility."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
