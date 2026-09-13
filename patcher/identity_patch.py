from __future__ import annotations

import hashlib
import struct
import zipfile


ORIGINAL_PACKAGE = "com.halfbrick.fruitninja"
ENTRY_HASHES = {
    "AndroidManifest.xml": "ee3c8750633bbb11df406d58505441f06a88692924d09ea11feb168deaa62ebb",
    "resources.arsc": "856b1cd060d58151fe54c6d943c0c47ba75f93a901bfe7083617d50032d22028",
}

# These offsets apply only to the hash-checked, allowlisted APK entries.
PACKAGE_ATTRIBUTE = 0xA5C
LABEL_ATTRIBUTE = 0xA94
RESOURCE_PACKAGE_NAME = 0x1DAD0


def _utf16_string(value: str) -> bytes:
    encoded = value.encode("utf-16-le")
    units = len(encoded) // 2
    if units > 0x7FFFFFFF:
        raise ValueError("manifest string exceeds the binary UTF-16 length limit")
    if units <= 0x7FFF:
        prefix = struct.pack("<H", units)
    else:
        prefix = struct.pack("<HH", 0x8000 | (units >> 16), units & 0xFFFF)
    return prefix + encoded + b"\0\0"


def _patch_manifest(
    data: bytes, package_name: str | None, launcher_name: str | None
) -> bytes:
    # Preserve every existing pool index: class names, attribute names and the
    # resource map depend on them. Only append new strings and retarget values.
    pool_start = 8
    pool_type, header_size, pool_size, count, styles, flags, strings_start, styles_start = (
        struct.unpack_from("<HHIIIIII", data, pool_start)
    )
    if (pool_type, header_size, pool_size, count, styles, flags, strings_start, styles_start) != (
        1, 28, 2456, 53, 0, 0, 240, 0
    ):
        raise ValueError("unexpected manifest string-pool layout")
    pool_end = pool_start + pool_size
    offsets = bytearray(data[pool_start + header_size : pool_start + strings_start])
    strings = bytearray(data[pool_start + strings_start : pool_end])
    tail = bytearray(data[pool_end:])
    added = 0
    for value, attribute, expected in (
        (
            package_name,
            PACKAGE_ATTRIBUTE,
            bytes.fromhex("ffffffff12000000140000000800000314000000"),
        ),
        (
            launcher_name,
            LABEL_ATTRIBUTE,
            bytes.fromhex("1000000003000000ffffffff080000014201077f"),
        ),
    ):
        if value is None:
            continue
        relative = attribute - pool_end
        if tail[relative : relative + 20] != expected:
            raise ValueError("unexpected manifest identity attribute")
        index = count + added
        offsets.extend(struct.pack("<I", len(strings)))
        strings.extend(_utf16_string(value))
        struct.pack_into("<I", tail, relative + 8, index)  # rawValue
        struct.pack_into("<HBBI", tail, relative + 12, 8, 0, 3, index)
        added += 1
    if not added:
        return data
    strings.extend(b"\0" * (-len(strings) % 4))
    new_strings_start = strings_start + 4 * added
    new_pool_size = new_strings_start + len(strings)
    pool_header = struct.pack(
        "<HHIIIIII", 1, header_size, new_pool_size, count + added,
        0, 0, new_strings_start, 0,
    )
    size = pool_start + new_pool_size + len(tail)
    return struct.pack("<HHI", 3, 8, size) + pool_header + offsets + strings + tail


def _patch_resource_package(data: bytes, package_name: str) -> bytes:
    # getIdentifier(..., getPackageName()) must find the renamed table package.
    # Keep package ID 0x7f, resource IDs, class namespaces and resource data intact.
    if not package_name.isascii() or len(package_name) > 127:
        raise ValueError("resource package name must fit 127 ASCII characters")
    encoded = package_name.encode("utf-16-le")
    expected = ORIGINAL_PACKAGE.encode("utf-16-le").ljust(256, b"\0")
    if data[RESOURCE_PACKAGE_NAME : RESOURCE_PACKAGE_NAME + 256] != expected:
        raise ValueError("unexpected resource-table package name")
    result = bytearray(data)
    result[RESOURCE_PACKAGE_NAME : RESOURCE_PACKAGE_NAME + 256] = encoded.ljust(256, b"\0")
    return bytes(result)


def patch_identity(
    archive: zipfile.ZipFile,
    *,
    package_name: str | None,
    launcher_name: str | None,
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Rewrite allowlisted identity entries using already validated CLI/API values."""
    replacements: dict[str, bytes] = {}
    files: list[dict[str, str]] = []
    changed_package = package_name if package_name != ORIGINAL_PACKAGE else None
    entries = []
    if changed_package is not None or launcher_name is not None:
        entries.append("AndroidManifest.xml")
    if changed_package is not None:
        entries.append("resources.arsc")
    for entry in entries:
        original = archive.read(entry)
        original_hash = hashlib.sha256(original).hexdigest()
        if original_hash != ENTRY_HASHES[entry]:
            raise ValueError(f"unsupported identity entry SHA-256 for {entry}: {original_hash}")
        if entry == "AndroidManifest.xml":
            result = _patch_manifest(original, changed_package, launcher_name)
        else:
            result = _patch_resource_package(original, changed_package)
        replacements[entry] = result
        files.append({
            "entry": entry,
            "input_sha256": original_hash,
            "output_sha256": hashlib.sha256(result).hexdigest(),
        })
    return replacements, {
        "package_name": {
            "original": ORIGINAL_PACKAGE,
            "requested": package_name,
            "effective": package_name or ORIGINAL_PACKAGE,
        },
        "launcher_name": {
            "original_resource_id": "0x7f070142",
            "requested": launcher_name,
            "scope": "application_and_inheriting_activities" if launcher_name is not None else "unchanged",
        },
        "files": files,
    }
