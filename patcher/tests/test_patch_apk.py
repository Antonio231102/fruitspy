from argparse import ArgumentParser, ArgumentTypeError, Namespace
from dataclasses import replace
from pathlib import Path
from unittest import mock
import io
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

from patcher import nickname_patch
from patcher.identity_patch import ORIGINAL_PACKAGE, _utf16_string, patch_identity
from patcher.patch_apk import (
    CLEAN_APK_SHA256,
    CLOCK_PATCHES,
    GAMESPY_HOSTS,
    NATNEG_RESOLVER_PATCHES,
    TARGET_ABIS,
    AndroidTools,
    SigningMaterial,
    align_sign_and_verify,
    MAX_SERVER_HOST_BYTES,
    ensure_signing_material,
    collect_arguments,
    inject_payload,
    is_signature_entry,
    parse_launcher_name,
    parse_package_name,
    parse_server_host,
    patch_apk,
    patch_clock_library,
    patch_endpoint_library,
    patch_library,
    replace_exact,
    sha256_bytes,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLEAN_APK = PROJECT_ROOT / "Fruit Ninja 1.7.6.apk"


def binary_chunks(data: bytes):
    _, start, size = struct.unpack_from("<HHI", data)
    assert size == len(data)
    while start < size:
        kind, header, length = struct.unpack_from("<HHI", data, start)
        assert 8 <= header <= length and length % 4 == 0
        assert start + length <= size
        yield kind, start, header, length
        start += length


def manifest_elements(data: bytes) -> list[tuple[str, dict]]:
    strings = []
    elements = []
    for kind, start, header, _ in binary_chunks(data):
        if kind == 1:
            count, _, flags, strings_start = struct.unpack_from("<IIII", data, start + 8)
            assert not flags & 0x100
            for index in range(count):
                offset = start + strings_start + struct.unpack_from(
                    "<I", data, start + header + index * 4
                )[0]
                units = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                if units & 0x8000:
                    units = ((units & 0x7FFF) << 16) | struct.unpack_from("<H", data, offset)[0]
                    offset += 2
                end = offset + units * 2
                assert data[end : end + 2] == b"\0\0"
                strings.append(data[offset:end].decode("utf-16-le"))
        elif kind == 0x102:
            extension = start + header
            _, name, attributes_start, attributes_size, count = struct.unpack_from(
                "<IIHHH", data, extension
            )
            attributes = {}
            for index in range(count):
                attribute = extension + attributes_start + index * attributes_size
                _, attribute_name, raw, _, _, value_type, value = struct.unpack_from(
                    "<IIIHBBI", data, attribute
                )
                attributes[strings[attribute_name]] = (
                    value_type,
                    strings[value] if value_type == 3 else value,
                    None if raw == 0xFFFFFFFF else strings[raw],
                )
            elements.append((strings[name], attributes))
    return elements


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
    nickname = nickname_patch.PATCHES[abi]
    library[
        nickname.hook_offset : nickname.hook_offset + len(nickname.hook_before)
    ] = nickname.hook_before
    library[
        nickname.request_hook_offset :
        nickname.request_hook_offset + len(nickname.request_hook_before)
    ] = nickname.request_hook_before

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
    def assert_combined_transformation(
        self, source: bytes, result: bytes, record: dict, host: str, abi: str
    ) -> None:
        clock = CLOCK_PATCHES[abi]
        nickname = nickname_patch.PATCHES[abi]
        clock_payload = clock.payload_path.read_bytes()
        nickname_payload = nickname.payload_path.read_bytes()
        nickname_offset = (
            clock.payload_file_offset + nickname.payload_vaddr - clock.payload_vaddr
        )
        for offset, expected in (
            (clock.clock_offset, clock.clock_after),
            (clock.conversion_offset, clock.conversion_after),
            (clock.payload_file_offset, clock_payload),
            (nickname.hook_offset, nickname.hook_after),
            (nickname.request_hook_offset, nickname.request_hook_after),
            (nickname_offset, nickname_payload),
        ):
            self.assertEqual(result[offset : offset + len(expected)], expected)
        for offset, _, replacement_bytes, _ in NATNEG_RESOLVER_PATCHES[abi]:
            self.assertEqual(
                result[offset : offset + len(replacement_bytes)], replacement_bytes
            )
        self.assertEqual(result.count(host.encode("ascii")), len(GAMESPY_HOSTS))
        self.assertNotIn(b"gamespy.com", result)

        clock_result, _ = patch_clock_library(source, clock)
        nickname_result, _ = nickname_patch.patch_library(clock_result, abi)
        self.assertEqual(record["abi"], abi)
        self.assertEqual(record["input_sha256"], sha256_bytes(source))
        self.assertEqual(record["clock"]["output_sha256"], sha256_bytes(clock_result))
        self.assertEqual(
            record["nickname"]["input_sha256"], record["clock"]["output_sha256"]
        )
        self.assertEqual(
            record["nickname"]["output_sha256"], sha256_bytes(nickname_result)
        )
        self.assertEqual(record["endpoint"]["server_host"], host)
        self.assertEqual(record["output_sha256"], sha256_bytes(result))
        self.assertNotEqual(record["output_sha256"], record["nickname"]["output_sha256"])

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
        self.assertEqual(MAX_SERVER_HOST_BYTES, 18)

    def test_noninteractive_mode_requires_explicit_server_host(self) -> None:
        parser = ArgumentParser()
        args = Namespace(
            source=Path("clean.apk"),
            output=Path("patched.apk"),
            server_host=None,
            non_interactive=True,
            report=None,
            package_name=None,
            launcher_name=None,
        )
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                collect_arguments(parser, args)
        self.assertEqual(error.exception.code, 2)

    def test_interactive_optional_names_are_independent_and_follow_endpoint(self) -> None:
        parser = ArgumentParser()
        args = Namespace(
            source=None,
            output=None,
            server_host=None,
            non_interactive=False,
            report=None,
            package_name=None,
            launcher_name=None,
        )
        for package, launcher in (
            ("", ""),
            ("org.example.fruitspy", ""),
            ("", "FruitSpy Local"),
            ("org.example.fruitspy", "FruitSpy Local"),
        ):
            with self.subTest(package=package, launcher=launcher):
                with (
                    mock.patch("patcher.patch_apk.sys.stdin.isatty", return_value=True),
                    mock.patch(
                        "builtins.input",
                        side_effect=["clean.apk", "fn.example.net", package, launcher],
                    ),
                    mock.patch("sys.stdout", new_callable=io.StringIO),
                ):
                    source, output, host, chosen_package, chosen_launcher, report = (
                        collect_arguments(parser, args)
                    )
                self.assertEqual(source, Path("clean.apk"))
                self.assertEqual(host, "fn.example.net")
                self.assertEqual(chosen_package, package or None)
                self.assertEqual(chosen_launcher, launcher or None)
                self.assertIsNone(report)

    def test_noninteractive_omitted_names_do_not_prompt(self) -> None:
        args = Namespace(
            source=Path("clean.apk"), output=None, server_host="fn.example.net",
            non_interactive=True, report=None, package_name=None, launcher_name=None,
        )
        with (
            mock.patch("patcher.patch_apk.sys.stdin.isatty", return_value=True),
            mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
        ):
            result = collect_arguments(ArgumentParser(), args)
        self.assertEqual(result[3:5], (None, None))

    def test_default_filename_uses_launcher_and_avoids_duplicate_fruitspy(self) -> None:
        for launcher, expected in (
            (None, "Fruit Ninja FruitSpy - fn.example.net.apk"),
            ("FruitSpy", "FruitSpy - fn.example.net.apk"),
            ("fruitspy", "fruitspy FruitSpy - fn.example.net.apk"),
            ("FruitSpy Local", "FruitSpy Local FruitSpy - fn.example.net.apk"),
        ):
            with self.subTest(launcher=launcher):
                args = Namespace(
                    source=Path("inputs/clean.apk"), output=None, server_host="fn.example.net",
                    non_interactive=True, report=None, package_name=None, launcher_name=launcher,
                )
                result = collect_arguments(ArgumentParser(), args)
                self.assertEqual(result[1], Path("inputs") / expected)

    def test_unusable_default_filename_requires_explicit_output_without_changing_label(self) -> None:
        for launcher in ("../FruitSpy", "Fruit:Spy", "a" * 256, "\u5fcd" * 100):
            with self.subTest(launcher=launcher):
                args = Namespace(
                    source=Path("inputs/clean.apk"), output=None, server_host="192.0.2.1",
                    non_interactive=True, report=None, package_name=None, launcher_name=launcher,
                )
                with self.assertRaises(ValueError):
                    collect_arguments(ArgumentParser(), args)
                args.output = Path("chosen.apk")
                result = collect_arguments(ArgumentParser(), args)
                self.assertEqual(result[1], args.output)
                self.assertEqual(result[4], launcher)

    def test_package_syntax_and_resource_field_capacity(self) -> None:
        maximum = "org." + "a" * 123
        self.assertEqual(parse_package_name(maximum), maximum)
        for invalid in (
            maximum + "a", "single", "org..app", "org.2app",
            "org._app", "org.app-name", "org.\u00e1pp",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ArgumentTypeError):
                    parse_package_name(invalid)

    def test_launcher_name_rejects_controls_and_unpaired_surrogates(self) -> None:
        for invalid in ("", "   ", "A\nB", "A\x00B", "A\x1bB", "A\ud800B"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ArgumentTypeError):
                    parse_launcher_name(invalid)

    def test_binary_strings_round_trip_utf16_units_and_extended_lengths(self) -> None:
        for value in (
            "Caf\u00e9 \u5fcd\u8005 \U0001d11e",
            "a" * 0x7FFF,
            "a" * 0x8000,
            "a" * 0x7FFE + "\U0001d11e",
        ):
            with self.subTest(scalars=len(value)):
                encoded = _utf16_string(parse_launcher_name(value))
                length = struct.unpack_from("<H", encoded)[0]
                start = 2
                if length & 0x8000:
                    length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", encoded, 2)[0]
                    start = 4
                end = start + length * 2
                self.assertEqual(encoded[start:end].decode("utf-16-le"), value)
                self.assertEqual(encoded[end:], b"\0\0")

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

    def test_clock_then_nickname_then_endpoint_composition_for_ipv4_and_dns(self) -> None:
        for abi in TARGET_ABIS:
            source, synthetic_patch = combined_fixture(abi)
            for host in ("192.168.100.2", "games.example.net"):
                with self.subTest(abi=abi, host=host):
                    with mock.patch.dict(
                        CLOCK_PATCHES,
                        {abi: synthetic_patch},
                    ):
                        result, record = patch_library(source, host, abi)
                        self.assert_combined_transformation(
                            source, result, record, host, abi
                        )

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
            with mock.patch("patcher.patch_apk.run_checked", side_effect=fake_run):
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
            with mock.patch("patcher.patch_apk.run_checked", side_effect=fake_run):
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
                        ["monotonic_clock", "nickname_collision", "gamespy_endpoint"],
                    )
                    self.assertEqual(
                        {record["abi"] for record in manifest["libraries"]},
                        set(TARGET_ABIS),
                    )
                    records = {
                        record["abi"]: record for record in manifest["libraries"]
                    }
                    with zipfile.ZipFile(CLEAN_APK) as source_archive, zipfile.ZipFile(
                        output
                    ) as archive:
                        self.assertFalse(
                            any(
                                name.upper().startswith("META-INF/")
                                and name.upper().endswith((".MF", ".SF", ".RSA"))
                                for name in archive.namelist()
                            )
                        )
                        for abi in TARGET_ABIS:
                            library = archive.read(f"lib/{abi}/libmortargame.so")
                            source = source_archive.read(f"lib/{abi}/libmortargame.so")
                            self.assert_combined_transformation(
                                source, library, records[abi], host, abi
                            )


    @unittest.skipUnless(
        CLEAN_APK.is_file(),
        "requires a locally supplied clean Fruit Ninja 1.7.6 APK",
    )
    def test_real_apk_removes_lvl_only_for_a_changed_package(self) -> None:
        from patcher.lvl_patch import patch_dex, patch_library as patch_lvl_library

        host = "games.example.net"
        native_paths = {f"lib/{abi}/libmortargame.so": abi for abi in TARGET_ABIS}
        with zipfile.ZipFile(CLEAN_APK) as source, tempfile.TemporaryDirectory() as directory:
            original_dex = source.read("classes.dex")
            lvl_dex, _ = patch_dex(original_dex)
            base_libraries = {}
            lvl_libraries = {}
            base_records = {}
            for path, abi in native_paths.items():
                base_libraries[path], base_records[abi] = patch_library(source.read(path), host, abi)
                lvl_libraries[path], _ = patch_lvl_library(base_libraries[path], abi)
                self.assertNotEqual(lvl_libraries[path], base_libraries[path])

            for name, options, remove_lvl in (
                ("default", {}, False),
                ("original-package", {"package_name": ORIGINAL_PACKAGE}, False),
                ("launcher-only", {"launcher_name": "FruitSpy Local"}, False),
                (
                    "original-and-launcher",
                    {"package_name": ORIGINAL_PACKAGE, "launcher_name": "FruitSpy Local"},
                    False,
                ),
                ("custom-package", {"package_name": "org.example.fruitspy"}, True),
                (
                    "custom-and-launcher",
                    {"package_name": "org.example.fruitspy", "launcher_name": "FruitSpy Local"},
                    True,
                ),
            ):
                with self.subTest(identity=name):
                    output = Path(directory) / f"{name}.apk"
                    manifest = patch_apk(CLEAN_APK, output, host, **options)
                    expected_order = ["monotonic_clock", "nickname_collision", "gamespy_endpoint"]
                    if remove_lvl:
                        expected_order.append("lvl_removal")
                    expected_order.extend(
                        key for key in ("package_name", "launcher_name") if options.get(key) is not None
                    )
                    self.assertEqual(manifest["patch_order"], expected_order)
                    records = {record["abi"]: record for record in manifest["libraries"]}
                    self.assertEqual(set(records), set(TARGET_ABIS))
                    with zipfile.ZipFile(output) as patched:
                        self.assertEqual(
                            patched.namelist(),
                            [entry for entry in source.namelist() if not is_signature_entry(entry)],
                        )
                        self.assertEqual(patched.comment, source.comment)
                        self.assertEqual(
                            patched.read("classes.dex"), lvl_dex if remove_lvl else original_dex
                        )
                        for path, abi in native_paths.items():
                            library = patched.read(path)
                            record = records[abi]
                            expected = lvl_libraries[path] if remove_lvl else base_libraries[path]
                            self.assertEqual(library, expected)
                            self.assertEqual(record["output_sha256"], sha256_bytes(library))
                            for key in ("input_sha256", "clock", "nickname", "endpoint"):
                                self.assertEqual(record[key], base_records[abi][key])
                            if remove_lvl:
                                self.assertEqual(
                                    record["lvl"]["input_sha256"], sha256_bytes(base_libraries[path])
                                )
                                self.assertEqual(record["lvl"]["output_sha256"], sha256_bytes(library))
                            else:
                                self.assertNotIn("lvl", record)

                        elements = manifest_elements(patched.read("AndroidManifest.xml"))
                        self.assertEqual(
                            elements[0][1]["package"][1], options.get("package_name") or ORIGINAL_PACKAGE
                        )
                        if options.get("launcher_name") is not None:
                            application = next(attrs for tag, attrs in elements if tag == "application")
                            self.assertEqual(application["label"][1], options["launcher_name"])
                        identity_entries = set()
                        if options.get("package_name") is not None:
                            identity_entries.update(("AndroidManifest.xml", "resources.arsc"))
                        if options.get("launcher_name") is not None:
                            identity_entries.add("AndroidManifest.xml")
                        if remove_lvl:
                            identity_entries.add("classes.dex")
                        for entry in patched.namelist():
                            if entry not in native_paths and entry not in identity_entries:
                                self.assertEqual(patched.read(entry), source.read(entry), entry)
    @unittest.skipUnless(
        CLEAN_APK.is_file(),
        "requires a locally supplied clean Fruit Ninja 1.7.6 APK",
    )
    def test_identity_keeps_resource_lookup_namespace_and_components_consistent(self) -> None:
        with zipfile.ZipFile(CLEAN_APK) as archive:
            original_xml = archive.read("AndroidManifest.xml")
            original_resources = archive.read("resources.arsc")
            original_elements = manifest_elements(original_xml)
            for package, label in (
                (None, None),
                ("org.example.fruitspy", None),
                (None, "Caf\u00e9 \u5fcd\u8005 \U0001d11e"),
                ("org." + "a" * 123, "FruitSpy Local"),
            ):
                with self.subTest(package=package, label=label):
                    entries, _ = patch_identity(
                        archive, package_name=package, launcher_name=label
                    )
                    xml = entries.get("AndroidManifest.xml", original_xml)
                    resources = entries.get("resources.arsc", original_resources)
                    elements = manifest_elements(xml)
                    expected = [(name, dict(attributes)) for name, attributes in original_elements]
                    expected_package = package or ORIGINAL_PACKAGE
                    if package is not None:
                        expected[0][1]["package"] = (3, package, package)
                    if label is not None:
                        next(attrs for name, attrs in expected if name == "application")["label"] = (
                            3, label, label
                        )
                    self.assertEqual(elements, expected)
                    packages = [
                        start for kind, start, _, _ in binary_chunks(resources) if kind == 0x200
                    ]
                    self.assertEqual(len(packages), 1)
                    name_start = packages[0] + 12
                    resource_package = resources[name_start : name_start + 256].decode(
                        "utf-16-le"
                    ).split("\0", 1)[0]
                    self.assertEqual(resource_package, expected_package)
                    self.assertEqual(
                        resources[:name_start] + resources[name_start + 256:],
                        original_resources[:name_start] + original_resources[name_start + 256:],
                    )
                    if package is None:
                        self.assertEqual(resources, original_resources)
                    if package is None and label is None:
                        self.assertEqual(xml, original_xml)


if __name__ == "__main__":
    unittest.main()
