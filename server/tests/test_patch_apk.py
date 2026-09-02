import tempfile
import unittest
import zipfile
from pathlib import Path

from patch_apk import GAMESPY_HOSTS, NATNEG_RESOLVER_PATCHES, patch_apk


def library_fixture(abi: str) -> bytes:
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


class ApkPatchTests(unittest.TestCase):
    def test_only_requested_abis_and_signatures_change(self) -> None:
        libraries = {abi: library_fixture(abi) for abi in ("armeabi-v7a", "x86")}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.apk"
            output = root / "patched.apk"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
                archive.writestr("META-INF/MANIFEST.MF", b"old manifest")
                archive.writestr("META-INF/GAME.SF", b"old signature")
                archive.writestr("META-INF/GAME.RSA", b"old certificate")
                archive.writestr("META-INF/NOTICE", b"keep this")
                archive.writestr("lib/armeabi/libmortargame.so", b"unpatched armeabi")
                for abi, library in libraries.items():
                    archive.writestr(f"lib/{abi}/libmortargame.so", library)

            records = patch_apk(source, output, "192.168.100.2")

            self.assertEqual(len(records), 23)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read("lib/armeabi/libmortargame.so"), b"unpatched armeabi")
                for abi, library in libraries.items():
                    patched = archive.read(f"lib/{abi}/libmortargame.so")
                    self.assertEqual(patched.count(b"192.168.100.2"), 7)
                    self.assertNotIn(b"gamespy.com", patched)
                    self.assertEqual(len(patched), len(library))
                    for offset, _, replacement, _ in NATNEG_RESOLVER_PATCHES[abi]:
                        self.assertEqual(patched[offset : offset + len(replacement)], replacement)
                self.assertEqual(archive.read("META-INF/NOTICE"), b"keep this")
                self.assertNotIn("META-INF/MANIFEST.MF", archive.namelist())
                self.assertNotIn("META-INF/GAME.SF", archive.namelist())
                self.assertNotIn("META-INF/GAME.RSA", archive.namelist())


if __name__ == "__main__":
    unittest.main()
