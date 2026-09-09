"""Execute real nickname request/callback/host paths in Unicorn.

Only strncpy/strcasecmp libc imports, a one-bucket fixture hash callback, and
observation at the SDK connection boundary are supplied by the harness. Preferred
name restoration, the native setter/string routines, callback publication, and
host selection execute the APK's native machine code.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_ARCH_X86, UC_MODE_ARM, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn import arm_const as arm, x86_const as x86

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from patcher import patch_apk as base
from patcher.nickname_patch import PATCHES, patch_library

HEAP = 0x70000000
STACK = 0x71000000
STOP = 0x72000000
PEER = HEAP
PROVIDER = HEAP + 0x3000
TABLE = HEAP + 0x4000
BUCKETS = HEAP + 0x4100
ARRAY = HEAP + 0x4200
PLAYERS = HEAP + 0x5000
HASH = STOP + 0x100

# Addresses independently checked against each allowlisted library.
RUNTIME = {
    "armeabi": (0x19C900, 0x2EB18, 0x2E788, 0x3AD140),
    "armeabi-v7a": (0x19C8D0, 0x2EAE8, 0x2E764, 0x3B0A6C),
    "x86": (0x187B10, 0x2FEAC, 0x2FA0C, 0x3B1E6C),
}


class Machine:
    def __init__(self, data: bytes, abi: str, load_base: int):
        self.patch = PATCHES[abi]
        self.load_base = load_base
        self.is_x86 = abi == "x86"
        self.host, self.strncpy, self.strcasecmp, canary_slot = RUNTIME[abi]
        self.uc = Uc(UC_ARCH_X86 if self.is_x86 else UC_ARCH_ARM, UC_MODE_32 if self.is_x86 else UC_MODE_ARM)
        self.uc.mem_map(load_base, 0x500000)
        for header in base.program_headers(data):
            if header[0] == base.PT_LOAD:
                self.uc.mem_write(load_base + header[2], data[header[1]:header[1] + header[4]])
        for start, size in ((HEAP, 0x10000), (STACK, 0x20000), (STOP, 0x1000)):
            self.uc.mem_map(start, size)
        self.word(load_base + canary_slot, HEAP + 0x8000)
        self.word(HEAP + 0x8000, 0x12345678)
        if self.is_x86:
            self.pc, self.sp, self.result = x86.UC_X86_REG_EIP, x86.UC_X86_REG_ESP, x86.UC_X86_REG_EAX
            self.saved = (x86.UC_X86_REG_EBX, x86.UC_X86_REG_ESI, x86.UC_X86_REG_EDI, x86.UC_X86_REG_EBP)
            self.success = load_base + 0x42E1A8
            self.connecting = load_base + 0x42E1A0
        else:
            self.pc, self.sp, self.result = arm.UC_ARM_REG_PC, arm.UC_ARM_REG_SP, arm.UC_ARM_REG_R0
            self.saved = tuple(getattr(arm, f"UC_ARM_REG_R{n}") for n in range(4, 12))
            callback = self.patch.original_callback
            globals_address = callback + 16 + base.read_u32(data, callback + 24)
            self.connecting = load_base + globals_address
            self.success = self.connecting + 4
        displacement_offset = self.patch.hook_offset + (2 if self.is_x86 else 0)
        target = (base.read_u32(data, displacement_offset) + self.patch.callback_address_base) & 0xFFFFFFFF
        self.callback = load_base + target
        self.published_names = []
        offset = self.patch.request_hook_offset
        if self.is_x86:
            assert data[offset] == 0xE8
            target = offset + 5 + struct.unpack_from("<i", data, offset + 1)[0]
        else:
            instruction = base.read_u32(data, offset)
            assert instruction >> 24 == 0xEB
            displacement = instruction & 0xFFFFFF
            if displacement & 0x800000:
                displacement -= 1 << 24
            target = offset + 8 + displacement * 4
        self.request_entry = load_base + target
        self.requested_names = []
        self.uc.hook_add(
            UC_HOOK_CODE, self.external,
            begin=load_base + self.patch.peer_connect, end=load_base + self.patch.peer_connect,
        )
        self.uc.hook_add(UC_HOOK_CODE, self.external, begin=load_base + self.strncpy, end=load_base + self.strncpy)
        self.uc.hook_add(UC_HOOK_CODE, self.external, begin=load_base + self.strcasecmp, end=load_base + self.strcasecmp)
        self.uc.hook_add(UC_HOOK_CODE, self.external, begin=HASH, end=HASH)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.publish, begin=self.success, end=self.success + 3)

    def word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def read_word(self, address: int) -> int:
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def string(self, address: int) -> bytes:
        result = bytearray()
        for offset in range(256):
            byte = self.uc.mem_read(address + offset, 1)[0]
            if not byte:
                return bytes(result)
            result.append(byte)
        raise AssertionError("unterminated native string")

    def argument(self, index: int) -> int:
        if self.is_x86:
            return self.read_word(self.uc.reg_read(self.sp) + 4 * (index + 1))
        if index < 4:
            return self.uc.reg_read(getattr(arm, f"UC_ARM_REG_R{index}"))
        return self.read_word(self.uc.reg_read(self.sp) + 4 * (index - 4))

    def external(self, uc, address, size, user_data) -> None:
        if address == HASH:
            result = 0  # Valid hash for this deliberately single-bucket table.
        elif address == self.load_base + self.patch.peer_connect:
            self.requested_names.append(self.string(self.argument(1)))
            result = 0
        elif address == self.load_base + self.strncpy:
            destination, source, count = (self.argument(n) for n in range(3))
            text = self.string(source)
            uc.mem_write(destination, text[:count].ljust(count, b"\0"))
            result = destination
        else:
            left, right = (self.string(self.argument(n)).lower() for n in range(2))
            result = (left > right) - (left < right)
        uc.reg_write(self.result, result & 0xFFFFFFFF)
        if self.is_x86:
            sp = uc.reg_read(self.sp)
            uc.reg_write(self.pc, self.read_word(sp))
            uc.reg_write(self.sp, sp + 4)
        else:
            uc.reg_write(self.pc, uc.reg_read(arm.UC_ARM_REG_LR))

    def publish(self, uc, access, address, size, value, user_data) -> None:
        if value:
            self.published_names.append(self.string(PROVIDER + 0xA4))

    def call(self, address: int, *args: int) -> int:
        for index, reg in enumerate(self.saved):
            self.uc.reg_write(reg, 0x11110000 + index)
        entry_sp = STACK + 0x10000 - (4 if self.is_x86 else 0)
        self.uc.reg_write(self.sp, entry_sp)
        if self.is_x86:
            self.uc.mem_write(entry_sp, struct.pack("<" + "I" * (len(args) + 1), STOP, *args))
        else:
            for index, value in enumerate(args[:4]):
                self.uc.reg_write(getattr(arm, f"UC_ARM_REG_R{index}"), value)
            for index, value in enumerate(args[4:]):
                self.word(entry_sp + 4 * index, value)
            self.uc.reg_write(arm.UC_ARM_REG_LR, STOP)
        self.uc.emu_start(address, STOP, count=100000)
        assert self.uc.reg_read(self.pc) == STOP, "native execution did not return"
        assert self.uc.reg_read(self.sp) == entry_sp + (4 if self.is_x86 else 0), "stack imbalance"
        for index, reg in enumerate(self.saved):
            assert self.uc.reg_read(reg) == 0x11110000 + index, "callee-saved register corrupted"
        return self.uc.reg_read(self.result)

    def prepare(
        self, requested: bytes, accepted: bytes, hosting: bool,
        other_is_host: bool = False, *, reset_provider: bool = True,
    ) -> None:
        assert len(requested) <= 63 and len(accepted) <= 63
        if reset_provider:
            self.uc.mem_write(PROVIDER, b"\xa5" * 0x200)
            self.uc.mem_write(PROVIDER + 0xA4, requested + b"\0")
            self.configure_nickname(requested)
        self.word(PROVIDER + 0x88, PEER)
        self.uc.mem_write(PEER + 4, accepted.ljust(64, b"\0"))
        self.word(PEER + 0x48, 1)  # Connected.
        self.word(PEER + 0x398, 1)  # In StagingRoom.
        self.word(PEER + 0xB40, int(hosting))
        self.word(PEER + 0xAB4, TABLE)
        # HashTable and DArray layouts consumed by the original SDK lookup code.
        for offset, value in ((0, BUCKETS), (4, 1), (12, HASH), (16, self.load_base + self.strcasecmp)):
            self.word(TABLE + offset, value)
        self.word(BUCKETS, ARRAY)
        records = [(accepted, True, True)]
        if requested != accepted:
            records.insert(0, (requested, False, other_is_host))
        for offset, value in ((0, len(records)), (4, len(records)), (8, 0xB0), (20, PLAYERS)):
            self.word(ARRAY + offset, value)
        for index, (nick, local, in_stage) in enumerate(records):
            player = PLAYERS + index * 0xB0
            self.uc.mem_write(player, bytes(0xB0))
            self.uc.mem_write(player, nick + b"\0")
            self.word(player + 0x48, int(in_stage))
            self.word(player + 0x4C, int(local))
            self.word(player + 0x64, 0x33 if in_stage else 0)
        self.word(self.connecting, 1)
        self.word(self.success, 0)
        self.published_names.clear()

    def is_host(self) -> bool:
        return bool(self.call(self.load_base + self.host, PROVIDER, 2))

    def connect(self, success: int = 1, provider: int = PROVIDER, peer: int = PEER) -> None:
        before = bytes(self.uc.mem_read(PROVIDER, 0x200))
        self.call(self.callback, peer, success, 17, provider)
        after = bytes(self.uc.mem_read(PROVIDER, 0x200))
        assert before[:0xA4] == after[:0xA4] and before[0xE4:] == after[0xE4:], "nickname copy overflow"
        assert self.read_word(self.success) == success, "original callback result changed"
        assert self.read_word(self.connecting) == 0, "original callback did not finish"

    def configure_nickname(self, nickname: bytes) -> None:
        assert len(nickname) <= 16
        self.uc.mem_write(self.load_base + self.patch.configured_nick, nickname.ljust(17, b"\0"))

    def request(self) -> bytes:
        before = bytes(self.uc.mem_read(PROVIDER, 0x200))
        preferred = self.string(self.load_base + self.patch.configured_nick)
        self.call(self.request_entry, PEER, PROVIDER + 0xA4, 0, STOP, self.callback, PROVIDER, 0)
        after = bytes(self.uc.mem_read(PROVIDER, 0x200))
        assert before[:0xA4] == after[:0xA4] and before[0xE4:] == after[0xE4:]
        assert self.string(self.load_base + self.patch.configured_nick) == preferred
        return self.requested_names[-1]


def verify_abi(clean: bytes, abi: str) -> list[str]:
    baseline, _ = base.patch_clock_library(clean, base.CLOCK_PATCHES[abi])
    corrected, _ = patch_library(baseline, abi)
    passed = []
    for load_base in (0x10000000, 0x38000000):
        original = Machine(baseline, abi, load_base)
        original.prepare(b"Alex", b"Alex.42", True)
        original.connect()
        assert not original.is_host(), "baseline no longer reproduces renamed-host stall"
        # The previous callback-only patch fixed hosting but reused the accepted
        # alias as the next requested name. Keep that distinct baseline visible.
        callback_only = bytearray(corrected)
        patch = PATCHES[abi]
        callback_only[patch.request_hook_offset:patch.request_hook_offset + len(patch.request_hook_before)] = patch.request_hook_before
        sticky = Machine(bytes(callback_only), abi, load_base)
        sticky.prepare(b"Alex", b"Alex.42", True)
        sticky.connect()
        assert sticky.request() == b"Alex.42", "previous patch no longer reproduces sticky nickname"
        fixed = Machine(corrected, abi, load_base)
        for name, requested, accepted, hosting, other_host in (
            ("renamed host", b"Alex", b"Alex.42", True, False),
            ("same-name live joiner", b"Alex", b"Alex.42", False, True),
            ("ordinary host", b"Alex", b"Alex", True, False),
            ("SDK nickname capacity", b"Alex", b"A" * 63, True, False),
        ):
            fixed.prepare(requested, accepted, hosting, other_host)
            fixed.connect()
            assert fixed.is_host() == hosting, name
            assert fixed.published_names == [accepted], "success published before identity synchronization"
            passed.append(f"{load_base:#x}: {name}")
        fixed.prepare(b"Alex", b"Alex.42", True)
        assert fixed.request() == b"Alex"
        fixed.connect()
        assert fixed.is_host()
        # Keep this provider alive through successive collision and free-name
        # connections: do not manufacture the requested name from the SDK result.
        for accepted in (b"Alex.7", b"Alex"):
            assert fixed.request() == b"Alex", "reconnect requested a cached suffix"
            fixed.prepare(b"Alex", accepted, True, reset_provider=False)
            fixed.connect()
            assert fixed.is_host() and fixed.published_names == [accepted]
            assert fixed.string(load_base + patch.configured_nick) == b"Alex"
        passed.append(f"{load_base:#x}: preferred-name reconnect and suffix removal")
        fixed.configure_nickname(b"Beth.12")
        assert fixed.request() == b"Beth.12", "configured numeric suffix was stripped"
        fixed.prepare(b"Beth.12", b"Beth.12.8", True, reset_provider=False)
        fixed.connect()
        assert fixed.is_host() and fixed.request() == b"Beth.12"
        passed.append(f"{load_base:#x}: edited preferred name with intentional suffix")
        fixed.prepare(b"nick123", b"nick123", True)
        fixed.configure_nickname(b"")
        assert fixed.request() == b"nick123", "empty setting replaced native-generated name"
        passed.append(f"{load_base:#x}: native-generated name with empty setting")
        for name, success, provider, peer, connected in (
            ("failed connection", 0, PROVIDER, PEER, 1),
            ("null provider", 1, 0, PEER, 1),
            ("null peer", 1, PROVIDER, 0, 1),
            ("SDK getter unavailable", 1, PROVIDER, PEER, 0),
        ):
            fixed.prepare(b"Alex", b"Alex.42", True)
            fixed.word(PEER + 0x48, connected)
            fixed.connect(success, provider, peer)
            assert fixed.string(PROVIDER + 0xA4) == b"Alex", name
            passed.append(f"{load_base:#x}: {name}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if base.sha256_file(args.source) != base.CLEAN_APK_SHA256:
        parser.error("requires the allowlisted clean Fruit Ninja 1.7.6 APK")
    results = {}
    with zipfile.ZipFile(args.source) as apk:
        for abi in PATCHES:
            results[abi] = verify_abi(apk.read(f"lib/{abi}/{base.LIBRARY}"), abi)
            print(f"{abi}: host stall and sticky reconnect reproduced; {len(results[abi])} corrected native scenarios passed")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
