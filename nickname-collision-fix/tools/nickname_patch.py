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


PATCHES = {
    patch.abi: patch
    for patch in (
        NicknamePatch(
            "armeabi", 0x3A2D4C,
            "31c23e6720fba00a1e3979acdc2cd3aae926cf1c83360634edcd2ca3d3dc8764",
            0x19D964, bytes.fromhex("88efffff"), 0x19D6F4,
            0x19C67C, 0x1A95EC,
            ("--target=armv5te-linux-androideabi19", "-march=armv5te"),
            "armelf_linux_eabi",
        ),
        NicknamePatch(
            "armeabi-v7a", 0x3A5994,
            "06b294599fb8e106b9de7dee8714682f61353d111748550ee50e952d023d9426",
            0x19D934, bytes.fromhex("88efffff"), 0x19D6C4,
            0x19C64C, 0x1A95BC,
            ("--target=armv7a-linux-androideabi19", "-march=armv7-a"),
            "armelf_linux_eabi",
        ),
        NicknamePatch(
            "x86", 0x3A63E8,
            "474318440740292ca43a11b7b84a1740fc22472723634eec9273446a92246b13",
            0x188D19, bytes.fromhex("8d83d852ddff"), 0x3B22C8,
            0x1875A0, 0x195500,
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
        "cached_nickname_offset": 0xA4,
        "cached_nickname_capacity": 64,
    }
