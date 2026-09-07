from argparse import ArgumentTypeError
from dataclasses import replace
from pathlib import Path
from unittest import mock
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

from patch_apk import (
    CLEAN_APK_SHA256,
    CLOCK_PATCHES,
    GAMESPY_HOSTS,
    NATNEG_RESOLVER_PATCHES,
    TARGET_ABIS,
    AndroidTools,
    SigningMaterial,
    align_sign_and_verify,
    ensure_signing_material,
    inject_payload,
    parse_server_host,
    patch_apk,
    patch_endpoint_library,
    patch_library,
    replace_exact,
    sha256_bytes,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLEAN_APK = PROJECT_ROOT / "Fruit Ninja 1.7.6.apk"


def endpoint_fixture(abi: str) -> bytes:
    patches = NATNEG_RESOLVER_PATCHES[abi]
    size = max(offset + len(expected) for offset, expected, _, _ in patches) + 16
    library = bytearray(size)
    cursor = 16
    for hostname in GAMESPY_HOSTS:
        library[cursor : cursor + len(hostname)] = hostname
        cursor += len(hostname) + 8
    for offset, expected, _, _ in patches:
        library[offset : offset + len(expected)] = expected
    return bytes(library)


def combined_fixture(abi: str) -> tuple[bytes, object]:
    production = CLOCK_PATCHES[abi]
    payload = production.payload_path.read_bytes()
    next_load = (production.payload_file_offset + len(payload) + 0xFFF) & ~0xFFF
    size = next_load + 0x1000
    library = bytearray(size)
    library[:4] = b"\x7fELF"
    library[4] = 1
    library[5] = 1
    struct.pack_into("<I", library, 28, 52)
    struct.pack_into("<I", library, 32, 128)
    struct.pack_into("<H", library, 40, 52)
    struct.pack_into("<H", library, 42, 32)
    struct.pack_into("<H", library, 44, 2)
    struct.pack_into("<H", library, 46, 40)
    struct.pack_into("<H", library, 48, 1)
    struct.pack_into(
        "<IIIIIIII",
        library,
        52,
        1,
        0,
        0,
        0,
        production.payload_file_offset,
        production.payload_vaddr,
        1,
        0x1000,
    )
    struct.pack_into(
        "<IIIIIIII",
        library,
        84,
        1,
        next_load,
        next_load,
        next_load,
        size - next_load,
        size - next_load,
        6,
        0x1000,
    )

    cursor = 0x1000
    for hostname in GAMESPY_HOSTS:
        library[cursor : cursor + len(hostname)] = hostname
        cursor += len(hostname) + 8
    for offset, expected, _, _ in NATNEG_RESOLVER_PATCHES[abi]:
        library[offset : offset + len(expected)] = expected
    library[
        production.clock_offset : production.clock_offset + len(production.clock_before)
    ] = production.clock_before
    library[
        production.conversion_offset : production.conversion_offset
        + len(production.conversion_before)
    ] = production.conversion_before

    source = bytes(library)
    prototype = replace(
        production,
        input_sha256=sha256_bytes(source),
        output_sha256="",
    )
    injected, _ = inject_payload(source, prototype, payload)
    mutable = bytearray(injected)
    replace_exact(
        mutable,
        prototype.clock_offset,
        prototype.clock_before,
        prototype.clock_after,
        abi,
    )
    replace_exact(
        mutable,
        prototype.conversion_offset,
        prototype.conversion_before,
        prototype.conversion_after,
        abi,
    )
    synthetic_patch = replace(prototype, output_sha256=sha256_bytes(bytes(mutable)))
    return source, synthetic_patch


class ApkPatchTests(unittest.TestCase):
    def test_server_host_accepts_ipv4_and_short_dns_names(self) -> None:
        self.assertEqual(parse_server_host("192.168.100.2"), "192.168.100.2")
        self.assertEqual(parse_server_host("Games.Example.Net."), "games.example.net")
        self.assertEqual(parse_server_host("123456789012345678"), "123456789012345678")
        with self.assertRaises(ArgumentTypeError):
            parse_server_host("1234567890123456789")
        with self.assertRaises(ArgumentTypeError):
            parse_server_host("bad_name.example")
        with self.assertRaises(ArgumentTypeError):
            parse_server_host("2001:db8::1")

    def test_endpoint_patch_covers_every_abi(self) -> None:
        for abi in TARGET_ABIS:
            with self.subTest(abi=abi):
                library = endpoint_fixture(abi)
                patched, records = patch_endpoint_library(
                    library,
                    "192.168.100.2",
                    abi,
                )
                self.assertEqual(patched.count(b"192.168.100.2"), 7)
                self.assertNotIn(b"gamespy.com", patched)
                self.assertEqual(len(patched), len(library))
                self.assertEqual(
                    len(records),
                    len(GAMESPY_HOSTS) + len(NATNEG_RESOLVER_PATCHES[abi]),
                )
                for offset, _, replacement_bytes, _ in NATNEG_RESOLVER_PATCHES[abi]:
                    self.assertEqual(
                        patched[offset : offset + len(replacement_bytes)],
                        replacement_bytes,
                    )

    def test_clock_then_endpoint_composition_for_ipv4_and_dns(self) -> None:
        for abi in TARGET_ABIS:
            source, synthetic_patch = combined_fixture(abi)
            for host in ("192.168.100.2", "games.example.net"):
                with self.subTest(abi=abi, host=host):
                    with mock.patch.dict(
                        CLOCK_PATCHES,
                        {abi: synthetic_patch},
                    ):
                        result, record = patch_library(source, host, abi)
                    payload = synthetic_patch.payload_path.read_bytes()
                    self.assertEqual(result.count(host.encode("ascii")), 7)
                    self.assertNotIn(b"gamespy.com", result)
                    self.assertEqual(
                        result[
                            synthetic_patch.clock_offset :
                            synthetic_patch.clock_offset + len(synthetic_patch.clock_after)
                        ],
                        synthetic_patch.clock_after,
                    )
                    self.assertEqual(
                        result[
                            synthetic_patch.payload_file_offset :
                            synthetic_patch.payload_file_offset + len(payload)
                        ],
                        payload,
                    )
                    self.assertEqual(record["abi"], abi)
                    self.assertEqual(record["endpoint"]["server_host"], host)

    def test_unsupported_apk_is_rejected_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.apk"
            output = root / "patched.apk"
            source.write_bytes(b"not the allowlisted APK")
            with self.assertRaisesRegex(ValueError, "unsupported source APK SHA-256"):
                patch_apk(source, output, "192.168.100.2")
            self.assertFalse(output.exists())
            self.assertEqual(
                CLEAN_APK_SHA256,
                "5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650",
            )

    def test_local_signing_material_is_generated_once_and_reused(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            self.assertEqual(description, "local signing-key generation")
            keystore = Path(command[command.index("-keystore") + 1])
            keystore.write_bytes(b"synthetic PKCS12")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("patch_apk.run_checked", side_effect=fake_run):
                created = ensure_signing_material(root, Path("keytool"))
                reused = ensure_signing_material(root, Path("keytool"))
            self.assertTrue(created.created)
            self.assertFalse(reused.created)
            self.assertEqual(created.store_password, reused.store_password)
            self.assertEqual(created.key_password, reused.key_password)
            self.assertEqual(len(commands), 1)
            command_text = " ".join(commands[0])
            self.assertNotIn(created.store_password, command_text)
            self.assertTrue((root / "signing.p12").is_file())
            self.assertTrue((root / "signing.json").is_file())

    def test_alignment_signing_and_signature_verification(self) -> None:
        commands: list[tuple[str, list[str]]] = []

        def fake_run(command: list[str], description: str) -> subprocess.CompletedProcess[str]:
            commands.append((description, command))
            if description == "zipalign":
                shutil.copyfile(command[-2], command[-1])
            elif description == "APK signing":
                output = Path(command[command.index("--out") + 1])
                shutil.copyfile(command[-1], output)
            elif description == "APK signature verification":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    (
                        "Verified using v1 scheme (JAR signing): true\n"
                        "Verified using v2 scheme (APK Signature Scheme v2): true\n"
                        "Verified using v3 scheme (APK Signature Scheme v3): true\n"
                        "Verified using v4 scheme (APK Signature Scheme v4): false\n"
                        "Signer #1 certificate SHA-256 digest: "
                        "0123456789abcdef0123456789abcdef"
                        "0123456789abcdef0123456789abcdef\n"
                    ),
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsigned = root / "unsigned.apk"
            signed = root / "signed.apk"
            keystore = root / "signing.p12"
            unsigned.write_bytes(b"synthetic APK")
            keystore.write_bytes(b"synthetic key")
            tools = AndroidTools(
                keytool=Path("keytool"),
                zipalign=Path("zipalign"),
                apksigner=Path("apksigner"),
            )
            material = SigningMaterial(
                keystore=keystore,
                alias="fruitspy",
                store_password="store-secret",
                key_password="key-secret",
                created=True,
            )
            with mock.patch("patch_apk.run_checked", side_effect=fake_run):
                record = align_sign_and_verify(
                    unsigned,
                    signed,
                    tools,
                    material,
                    root,
                )
            self.assertTrue(signed.is_file())
            self.assertEqual(record["mode"], "persistent_local_key")
            self.assertTrue(record["schemes"]["v1"])
            self.assertEqual(
                record["certificate_sha256"],
                "0123456789abcdef" * 4,
            )
            all_arguments = " ".join(
                argument
                for _, command in commands
                for argument in command
            )
            self.assertNotIn("store-secret", all_arguments)
            self.assertNotIn("key-secret", all_arguments)
            signing_command = next(
                command for description, command in commands
                if description == "APK signing"
            )
            self.assertIn("--v1-signing-enabled", signing_command)
            self.assertEqual(
                signing_command[signing_command.index("--v4-signing-enabled") + 1],
                "false",
            )

    @unittest.skipUnless(
        CLEAN_APK.is_file(),
        "requires a locally supplied clean Fruit Ninja 1.7.6 APK",
    )
    def test_real_clean_apk_composes_ipv4_and_dns_across_all_abis(self) -> None:
        for host in ("192.168.100.2", "games.example.net"):
            with self.subTest(host=host):
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory) / "patched.apk"
                    manifest = patch_apk(CLEAN_APK, output, host)
                    self.assertEqual(
                        manifest["patch_order"],
                        ["monotonic_clock", "gamespy_endpoint"],
                    )
                    self.assertEqual(
                        {record["abi"] for record in manifest["libraries"]},
                        set(TARGET_ABIS),
                    )
                    with zipfile.ZipFile(output) as archive:
                        self.assertFalse(
                            any(
                                name.upper().startswith("META-INF/")
                                and name.upper().endswith((".MF", ".SF", ".RSA"))
                                for name in archive.namelist()
                            )
                        )
                        for abi in TARGET_ABIS:
                            library = archive.read(f"lib/{abi}/libmortargame.so")
                            self.assertEqual(library.count(host.encode("ascii")), 7)
                            self.assertNotIn(b"gamespy.com", library)
                            patch = CLOCK_PATCHES[abi]
                            self.assertEqual(
                                library[
                                    patch.clock_offset :
                                    patch.clock_offset + len(patch.clock_after)
                                ],
                                patch.clock_after,
                            )


if __name__ == "__main__":
    unittest.main()
