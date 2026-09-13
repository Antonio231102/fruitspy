from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from . import patch_apk as base


@dataclass(frozen=True)
class LvlPatch:
    function_offset: int
    function_size: int
    function_sha256: str
    hook_offset: int
    hook_before: bytes
    hook_after: bytes


# The native implementation called by native_GetIsAppLicensed already returns
# true when licensing is disabled. Select that existing path without changing
# JNI setup/teardown, persisted license state, or the provider's other methods.
# Whole-function hashes guard the branch target and surrounding instructions,
# not just the small replacement. Earlier native stages leave these ranges intact.
PATCHES = {
    "armeabi": LvlPatch(
        0x1E5754, 88,
        "81623bb15748cde931fcfc56ef1e09b1daa04430ff128037743888c3e8ae878d",
        0x1E5760, bytes.fromhex("0830d4e5"), bytes.fromhex("0030a0e3"),
    ),
    "armeabi-v7a": LvlPatch(
        0x1E4440, 88,
        "21e48ca00ef74eb641c0e78687cef4f797ffd8b64e51acdb5ef3778bf9393d10",
        0x1E444C, bytes.fromhex("0830d4e5"), bytes.fromhex("0030a0e3"),
    ),
    "x86": LvlPatch(
        0x1D5870, 80,
        "6d5fac5c7c69912836001baf37a2b39e3954045268fb29cec302c453c51a6049",
        0x1D588B, bytes.fromhex("7418"), bytes.fromhex("eb18"),
    ),
}


def patch_library(data: bytes, abi: str) -> tuple[bytes, dict[str, object]]:
    """Apply only for a changed package, after the validated native stages."""
    patch = PATCHES[abi]
    base.verify_elf32(data, abi)
    function = data[patch.function_offset:patch.function_offset + patch.function_size]
    if base.sha256_bytes(function) != patch.function_sha256:
        raise ValueError(f"{abi}: unsupported LVL query function SHA-256")
    result = bytearray(data)
    base.replace_exact(
        result, patch.hook_offset, patch.hook_before, patch.hook_after, abi,
    )
    output = bytes(result)
    return output, {
        "input_sha256": base.sha256_bytes(data),
        "output_sha256": base.sha256_bytes(output),
        "function_file_offset": patch.function_offset,
        "function_size": patch.function_size,
        "function_input_sha256": patch.function_sha256,
        "hook_file_offset": patch.hook_offset,
        "hook_before": patch.hook_before.hex(),
        "hook_after": patch.hook_after.hex(),
    }


DEX_SHA256 = "081d366831b1e93e3f7d8f8061ded63ad3034379fefa3904dc92a191126f620e"
DEX_HOOK_OFFSET = 0x07A174


def patch_dex(data: bytes) -> tuple[bytes, dict[str, object]]:
    """Disable only MortarLVLGameActivity.doLicenseCheck's automatic request."""
    input_hash = base.sha256_bytes(data)
    if input_hash != DEX_SHA256:
        raise ValueError("unsupported LVL classes.dex SHA-256")
    result = bytearray(data)
    # const/4 v0, 1 -> return-void. Keep the code item size, registers, exception
    # metadata and all other methods intact. In particular, initialization still
    # creates the checker that CleanupLicense later destroys. Native success
    # alone cannot stop Java denial callbacks from showing the forced-quit dialog.
    base.replace_exact(result, DEX_HOOK_OFFSET, b"\x12\x10", b"\x0e\x00", "classes.dex")
    result[12:32] = hashlib.sha1(memoryview(result)[32:], usedforsecurity=False).digest()
    struct.pack_into("<I", result, 8, zlib.adler32(memoryview(result)[12:]) & 0xFFFFFFFF)
    output = bytes(result)
    return output, {
        "input_sha256": input_hash,
        "output_sha256": base.sha256_bytes(output),
        "method": "com.halfbrick.mortar.MortarLVLGameActivity.doLicenseCheck",
        "hook_file_offset": DEX_HOOK_OFFSET,
        "hook_before": "1210",
        "hook_after": "0e00",
    }
