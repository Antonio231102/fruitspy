from __future__ import annotations

import hashlib
import struct
import unittest
import zipfile
import zlib
from pathlib import Path

from patcher import lvl_patch
from patcher import patch_apk as base

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, UC_MODE_ARM
    from unicorn import arm_const as arm, x86_const as x86
except ImportError:
    Uc = None


CLEAN_APK = Path(__file__).resolve().parents[2] / "Fruit Ninja 1.7.6.apk"
HEAP = 0x70000000
STACK = 0x71000000
STOP = 0x72000000
QUERY = STOP + 0x100

# Independently traced JNI entries and the native provider's enable/cache fields.
RUNTIME = {
    "armeabi": (0x2F3F8, 0x43D434, 0x43D438),
    "armeabi-v7a": (0x2F3C8, 0x43DDE0, 0x43DDE4),
    "x86": (0x309F0, 0x43FF34, 0x43FF30),
}


class LicenseMachine:
    """Run the original JNI bridge and native query; supply only provider status."""

    def __init__(self, data: bytes, abi: str, load_base: int) -> None:
        self.is_x86 = abi == "x86"
        self.load_base = load_base
        self.entry, enabled, provider = RUNTIME[abi]
        self.uc = Uc(
            UC_ARCH_X86 if self.is_x86 else UC_ARCH_ARM,
            UC_MODE_32 if self.is_x86 else UC_MODE_ARM,
        )
        self.uc.mem_map(load_base, 0x500000)
        for header in base.program_headers(data):
            if header[0] == base.PT_LOAD:
                self.uc.mem_write(load_base + header[2], data[header[1]:header[1] + header[4]])
        for address, size in ((HEAP, 0x10000), (STACK, 0x20000), (STOP, 0x1000)):
            self.uc.mem_map(address, size)
        self.enabled = load_base + enabled
        self.provider = load_base + provider
        self.word(self.provider, HEAP)
        self.word(HEAP, HEAP + 0x100)
        self.word(HEAP + 0x100 + 0x5C, QUERY)
        if self.is_x86:
            self.pc, self.sp, self.result = x86.UC_X86_REG_EIP, x86.UC_X86_REG_ESP, x86.UC_X86_REG_EAX
            self.saved = (x86.UC_X86_REG_EBX, x86.UC_X86_REG_ESI, x86.UC_X86_REG_EDI, x86.UC_X86_REG_EBP)
            got = 0x3B22C8
            slots = (got - 0x168, got - 0x148, got - 0x280)
        else:
            self.pc, self.sp, self.result = arm.UC_ARM_REG_PC, arm.UC_ARM_REG_SP, arm.UC_ARM_REG_R0
            self.saved = tuple(getattr(arm, f"UC_ARM_REG_R{n}") for n in range(4, 12))
            got = self.entry + 24 + base.read_u32(data, self.entry + 128)
            slots = tuple(got + base.read_u32(data, self.entry + offset) for offset in (132, 136, 140))
        # Resolve only the three JNI environment/scope GOT entries used here.
        for index, slot in enumerate(slots):
            self.word(load_base + slot, HEAP + 0x200 + 4 * index)
        self.scope = HEAP + 0x204
        self.status = 2
        self.queries = 0
        self.uc.hook_add(UC_HOOK_CODE, self.query, begin=QUERY, end=QUERY)

    def word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value))

    def query(self, uc, address, size, user_data) -> None:
        self.queries += 1
        uc.reg_write(self.result, self.status)
        if self.is_x86:
            sp = uc.reg_read(self.sp)
            uc.reg_write(self.pc, struct.unpack("<I", uc.mem_read(sp, 4))[0])
            uc.reg_write(self.sp, sp + 4)
        else:
            uc.reg_write(self.pc, uc.reg_read(arm.UC_ARM_REG_LR))

    def licensed(self, *, enabled: bool, status: int) -> bool:
        self.status = status
        self.uc.mem_write(self.enabled, bytes((enabled,)))
        saved_values = tuple(0x11110000 + index for index in range(len(self.saved)))
        for reg, value in zip(self.saved, saved_values, strict=True):
            self.uc.reg_write(reg, value)
        sp = STACK + 0x10000 - (4 if self.is_x86 else 0)
        self.uc.reg_write(self.sp, sp)
        environment = HEAP + 0x1000
        if self.is_x86:
            self.uc.mem_write(sp, struct.pack("<III", STOP, environment, 0))
        else:
            self.uc.reg_write(arm.UC_ARM_REG_R0, environment)
            self.uc.reg_write(arm.UC_ARM_REG_R1, 0)
            self.uc.reg_write(arm.UC_ARM_REG_LR, STOP)
        self.uc.emu_start(self.load_base + self.entry, STOP, count=10000)
        assert self.uc.reg_read(self.pc) == STOP, "JNI query did not return"
        assert self.uc.reg_read(self.sp) == sp + (4 if self.is_x86 else 0), "JNI stack imbalance"
        assert tuple(self.uc.reg_read(reg) for reg in self.saved) == saved_values, "callee-saved register corruption"
        assert self.uc.mem_read(self.scope, 8) == bytes(8), "JNI scope did not unwind"
        assert self.uc.mem_read(self.enabled, 1) == bytes((enabled,)), "persistent licensing flag changed"
        return bool(self.uc.reg_read(self.result) & 0xFF)


@unittest.skipUnless(CLEAN_APK.is_file(), "requires the privately supplied original APK")
class LvlPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if base.sha256_file(CLEAN_APK) != base.CLEAN_APK_SHA256:
            raise ValueError("unsupported private APK fixture")
        with zipfile.ZipFile(CLEAN_APK) as source:
            cls.libraries = {
                abi: base.patch_library(source.read(f"lib/{abi}/{base.LIBRARY}"), "games.example.net", abi)[0]
                for abi in base.TARGET_ABIS
            }
            cls.dex = source.read("classes.dex")

    def test_rejects_changed_function_outside_replacement(self) -> None:
        for abi, data in self.libraries.items():
            with self.subTest(abi=abi):
                altered = bytearray(data)
                altered[lvl_patch.PATCHES[abi].function_offset] ^= 1
                with self.assertRaises(ValueError):
                    lvl_patch.patch_library(bytes(altered), abi)

    def test_rejects_duplicate_application(self) -> None:
        for abi, data in self.libraries.items():
            with self.subTest(abi=abi):
                patched, _ = lvl_patch.patch_library(data, abi)
                with self.assertRaises(ValueError):
                    lvl_patch.patch_library(patched, abi)

    def test_dex_patch_preserves_other_code_and_valid_checksums(self) -> None:
        patched, _ = lvl_patch.patch_dex(self.dex)
        offset = lvl_patch.DEX_HOOK_OFFSET
        self.assertEqual(len(patched), len(self.dex))
        self.assertEqual(patched[:8], self.dex[:8])
        self.assertEqual(patched[32:offset], self.dex[32:offset])
        self.assertEqual(patched[offset + 2:], self.dex[offset + 2:])
        self.assertEqual(patched[offset:offset + 2], b"\x0e\x00")  # Dalvik return-void.
        self.assertEqual(
            patched[12:32], hashlib.sha1(patched[32:], usedforsecurity=False).digest()
        )
        self.assertEqual(
            struct.unpack_from("<I", patched, 8)[0], zlib.adler32(patched[12:]) & 0xFFFFFFFF
        )

    def test_rejects_changed_or_already_patched_dex(self) -> None:
        patched, _ = lvl_patch.patch_dex(self.dex)
        changed = bytearray(self.dex)
        changed[lvl_patch.DEX_HOOK_OFFSET + 2] ^= 1
        for data in (bytes(changed), patched):
            with self.subTest(already_patched=data is patched):
                with self.assertRaises(ValueError):
                    lvl_patch.patch_dex(data)

    @unittest.skipIf(Uc is None, "requires the optional Unicorn native-validation dependency")
    def test_denial_and_later_license_changes_cannot_fail_patched_jni_query(self) -> None:
        for abi, data in self.libraries.items():
            patched, _ = lvl_patch.patch_library(data, abi)
            for load_base in (0x10000000, 0x38000000):
                with self.subTest(abi=abi, load_base=hex(load_base)):
                    original = LicenseMachine(data, abi, load_base)
                    fixed = LicenseMachine(patched, abi, load_base)
                    for enabled, status, original_result in (
                        (True, 2, False),
                        (True, 1, True),
                        (True, 2, False),
                        (False, 2, True),
                    ):
                        self.assertEqual(original.licensed(enabled=enabled, status=status), original_result)
                        self.assertTrue(fixed.licensed(enabled=enabled, status=status))
                    self.assertGreater(original.queries, 0)
                    self.assertEqual(fixed.queries, 0)
                    # The fixed query also needs no provider initialization.
                    fixed.word(fixed.provider, 0)
                    self.assertTrue(fixed.licensed(enabled=True, status=2))


if __name__ == "__main__":
    unittest.main()
