from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"
OUTPUT = NATIVE / "payloads"


@dataclass(frozen=True)
class PayloadBuild:
    abi: str
    source: Path
    clang_args: tuple[str, ...]
    linker_args: tuple[str, ...]
    expected_sha256: str


BUILDS = (
    PayloadBuild(
        abi="armeabi",
        source=NATIVE / "monotonic_arm.S",
        clang_args=("--target=armv5te-linux-androideabi19", "-march=armv5te"),
        linker_args=(
            "-m",
            "armelf_linux_eabi",
            "-Ttext=0x3a2ce4",
            "--defsym=fruit_floatsisf=0x2fc0dc",
        ),
        expected_sha256="af424f843eb6d16e18c03b49ce1076fb7b283a01482ec84e4ceedd142c88f9af",
    ),
    PayloadBuild(
        abi="armeabi-v7a",
        source=NATIVE / "monotonic_arm.S",
        clang_args=("--target=armv7a-linux-androideabi19", "-march=armv7-a"),
        linker_args=(
            "-m",
            "armelf_linux_eabi",
            "-Ttext=0x3a592c",
            "--defsym=fruit_floatsisf=0x2fbf30",
        ),
        expected_sha256="16297923317059a10dfeb3b709ad9155278bcf85e7622b0b67e6a5b4d36af4aa",
    ),
    PayloadBuild(
        abi="x86",
        source=NATIVE / "monotonic_x86.S",
        clang_args=("--target=i686-linux-android19", "-m32"),
        linker_args=(
            "-m",
            "elf_i386",
            "--image-base=0",
            "-Ttext=0x3a639c",
            "--defsym=fruit_conversion_resume=0x1d4efc",
        ),
        expected_sha256="2f281cc847d8fca9362fd53558a8713289a4d4cec653d6ec3a9565ae9b26ced8",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    tool_paths: dict[str, str] = {}
    for tool in ("clang", "ld.lld", "llvm-objcopy"):
        path = shutil.which(tool)
        if path is None:
            raise SystemExit(f"missing required LLVM tool: {tool}")
        tool_paths[tool] = path

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fruitspy-payloads-") as temporary:
        work = Path(temporary)
        for build in BUILDS:
            object_path = work / f"{build.abi}.o"
            elf_path = work / f"{build.abi}.elf"
            binary_path = OUTPUT / f"{build.abi}.bin"

            subprocess.run(
                [
                    tool_paths["clang"],
                    *build.clang_args,
                    "-c",
                    str(build.source),
                    "-o",
                    str(object_path),
                ],
                check=True,
            )
            subprocess.run(
                [
                    tool_paths["ld.lld"],
                    *build.linker_args,
                    "--entry=fruit_monotonic_us32",
                    "-o",
                    str(elf_path),
                    str(object_path),
                ],
                check=True,
            )
            subprocess.run(
                [
                    tool_paths["llvm-objcopy"],
                    "-O",
                    "binary",
                    "--only-section=.text",
                    str(elf_path),
                    str(binary_path),
                ],
                check=True,
            )

            actual = sha256(binary_path)
            if actual != build.expected_sha256:
                binary_path.unlink(missing_ok=True)
                raise SystemExit(
                    f"{build.abi}: payload hash mismatch; expected "
                    f"{build.expected_sha256}, got {actual}"
                )
            print(f"{build.abi}: {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
