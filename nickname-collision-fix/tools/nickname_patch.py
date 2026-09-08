from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
from patcher import patch_apk as base


@dataclass(frozen=True)
class NicknamePatch:
    abi: str
    payload_vaddr: int
    payload_sha256: str
    hook_offset: int
    hook_before: bytes
    callback_address_base: int
    original_callback: int
    peer_get_nick: int
    configured_nick: int
    set_nick: int
    peer_connect: int
    request_hook_offset: int
    request_hook_before: bytes
    request_payload_offset: int
    clang_args: tuple[str, ...]
    linker_machine: str

    @property
    def payload_path(self) -> Path:
        return ROOT / "native" / "payloads" / f"{self.abi}.bin"

    @property
    def source_path(self) -> Path:
        architecture = "x86" if self.abi == "x86" else "arm"
        return ROOT / "native" / f"accepted_nick_{architecture}.S"

    @property
    def hook_after(self) -> bytes:
        displacement = (self.payload_vaddr - self.callback_address_base) & 0xFFFFFFFF
        prefix = b"\x8d\x83" if self.abi == "x86" else b""
        return prefix + struct.pack("<I", displacement)

    @property
    def request_hook_after(self) -> bytes:
        target = self.payload_vaddr + self.request_payload_offset
        if self.abi == "x86":
            return b"\xe8" + struct.pack("<i", target - self.request_hook_offset - 5)
        displacement = target - self.request_hook_offset - 8
        if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
            raise ValueError(f"{self.abi}: connection helper is outside ARM BL range")
        return struct.pack("<I", 0xEB000000 | ((displacement >> 2) & 0xFFFFFF))


PATCHES = {
    patch.abi: patch
    for patch in (
        NicknamePatch(
            "armeabi", 0x3A2D4C,
            "c5f2b4fad243b04fc6283e76bd8119131dd2c1e9812abaa6a690ccbb67082b2f",
            0x19D964, bytes.fromhex("88efffff"), 0x19D6F4,
            0x19C67C, 0x1A95EC,
            0x3B1CC8, 0x19C820, 0x1AC338,
            0x19D704, bytes.fromhex("0b3b00eb"), 92,
            ("--target=armv5te-linux-androideabi19", "-march=armv5te"),
            "armelf_linux_eabi",
        ),
        NicknamePatch(
            "armeabi-v7a", 0x3A5994,
            "9b09d3c7e9a19839044f113dbad7c2ef0b38c7d57dda607e9d3d8d66c469e864",
            0x19D934, bytes.fromhex("88efffff"), 0x19D6C4,
            0x19C64C, 0x1A95BC,
            0x3B55F8, 0x19C7F0, 0x1AC308,
            0x19D6D4, bytes.fromhex("0b3b00eb"), 92,
            ("--target=armv7a-linux-androideabi19", "-march=armv7-a"),
            "armelf_linux_eabi",
        ),
        NicknamePatch(
            "x86", 0x3A63E8,
            "3b264fff9c5c74698419a2cbafa1c0bc6e3c9adb7cbe17939d933a3920d7b855",
            0x188D19, bytes.fromhex("8d83d852ddff"), 0x3B22C8,
            0x1875A0, 0x195500,
            0x3B6FA4, 0x187910, 0x198690,
            0x188D72, bytes.fromhex("e819f90000"), 80,
            ("--target=i686-linux-android19", "-m32"), "elf_i386",
        ),
    )
}


def patch_library(data: bytes, abi: str) -> tuple[bytes, dict[str, object]]:
    """Apply after the repository's validated clock and endpoint transformations."""
    patch = PATCHES[abi]
    base.verify_elf32(data, abi)
    payload = patch.payload_path.read_bytes()
    if base.sha256_bytes(payload) != patch.payload_sha256:
        raise ValueError(f"{abi}: nickname payload hash mismatch")

    clock = base.CLOCK_PATCHES[abi]
    clock_payload = clock.payload_path.read_bytes()
    if base.sha256_bytes(clock_payload) != clock.payload_sha256:
        raise ValueError(f"{abi}: clock payload hash mismatch")
    if data[clock.payload_file_offset:clock.payload_file_offset + len(clock_payload)] != clock_payload:
        raise ValueError(f"{abi}: apply the repository clock patch before the nickname patch")

    # Reuse the existing ELF injector's placement contract. Its other ClockPatch
    # fields are not consumed by inject_payload; the clock patch itself is unchanged.
    placement = replace(
        clock,
        payload_file_offset=clock.payload_file_offset + len(clock_payload),
        payload_vaddr=clock.payload_vaddr + len(clock_payload),
    )
    padding = patch.payload_vaddr - placement.payload_vaddr
    if not 0 <= padding < 4:
        raise ValueError(f"{abi}: unexpected executable payload placement")
    injected, shift = base.inject_payload(data, placement, b"\0" * padding + payload)
    result = bytearray(injected)
    base.replace_exact(result, patch.hook_offset, patch.hook_before, patch.hook_after, abi)
    base.replace_exact(
        result, patch.request_hook_offset, patch.request_hook_before, patch.request_hook_after, abi,
    )
    output = bytes(result)
    return output, {
        "input_sha256": base.sha256_bytes(data),
        "output_sha256": base.sha256_bytes(output),
        "hook_file_offset": patch.hook_offset,
        "hook_before": patch.hook_before.hex(),
        "hook_after": patch.hook_after.hex(),
        "payload_file_offset": placement.payload_file_offset + padding,
        "payload_virtual_address": patch.payload_vaddr,
        "payload_size": len(payload),
        "payload_sha256": patch.payload_sha256,
        "file_offset_shift": shift,
        "original_callback": patch.original_callback,
        "peer_get_nick": patch.peer_get_nick,
        "configured_nickname": patch.configured_nick,
        "set_nickname": patch.set_nick,
        "peer_connect": patch.peer_connect,
        "request_hook_file_offset": patch.request_hook_offset,
        "request_hook_before": patch.request_hook_before.hex(),
        "request_hook_after": patch.request_hook_after.hex(),
        "request_helper_virtual_address": patch.payload_vaddr + patch.request_payload_offset,
        "cached_nickname_offset": 0xA4,
        "cached_nickname_capacity": 64,
    }
