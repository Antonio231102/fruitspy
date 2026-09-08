from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from nickname_patch import PATCHES, base


def main() -> int:
    tools = {name: shutil.which(name) for name in ("clang", "ld.lld", "llvm-objcopy")}
    for name, path in tools.items():
        if path is None:
            raise SystemExit(f"missing required LLVM tool: {name}")

    with tempfile.TemporaryDirectory(prefix="fruitspy-nickname-payloads-") as temporary:
        work = Path(temporary)
        for patch in PATCHES.values():
            obj = work / f"{patch.abi}.o"
            elf = work / f"{patch.abi}.elf"
            binary = work / f"{patch.abi}.bin"
            subprocess.run(
                [tools["clang"], *patch.clang_args, "-c", str(patch.source_path), "-o", str(obj)],
                check=True,
            )
            subprocess.run(
                [
                    tools["ld.lld"], "-m", patch.linker_machine,
                    *(["--image-base=0"] if patch.abi == "x86" else []),
                    f"-Ttext={patch.payload_vaddr:#x}",
                    f"--defsym=fruit_peer_get_nick={patch.peer_get_nick:#x}",
                    f"--defsym=fruit_original_connected={patch.original_callback:#x}",
                    "--entry=fruit_connected_with_accepted_nick", "-o", str(elf), str(obj),
                ],
                check=True,
            )
            subprocess.run(
                [tools["llvm-objcopy"], "-O", "binary", "--only-section=.text", str(elf), str(binary)],
                check=True,
            )
            payload = binary.read_bytes()
            digest = base.sha256_bytes(payload)
            if digest != patch.payload_sha256:
                raise SystemExit(f"{patch.abi}: payload hash mismatch: {digest}")
            patch.payload_path.parent.mkdir(parents=True, exist_ok=True)
            patch.payload_path.write_bytes(payload)
            print(f"{patch.abi}: {len(payload)} bytes, SHA-256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
