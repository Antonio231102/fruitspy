from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import zipfile
from pathlib import Path

from nickname_patch import PATCHES, base, patch_library


def build_output(
    source: Path,
    output: Path,
    server_host: str,
    unsigned: bool = False,
    signing_directory: Path | None = None,
    android_sdk: Path | None = None,
    keytool: Path | None = None,
    zipalign: Path | None = None,
    apksigner: Path | None = None,
) -> dict[str, object]:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    report = output.with_suffix(output.suffix + ".manifest.json")
    if not source.is_file():
        raise ValueError(f"source APK was not found at {source}")
    if source in (output, report):
        raise ValueError("source, output, and manifest paths must differ")
    if output.exists() or report.exists():
        raise ValueError("output APK or manifest already exists; choose a new output path")
    tools = None if unsigned else base.discover_android_tools(android_sdk, keytool, zipalign, apksigner)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".fruitspy-nickname-", dir=output.parent) as temporary:
        work = Path(temporary)
        baseline = work / "baseline.apk"
        # The shared builder allowlists the complete clean APK and all three native
        # inputs, removes old signatures, and applies the clock/endpoint patches.
        manifest = base.patch_apk(source, baseline, server_host)
        manifest["base_tool"] = manifest["tool"]
        manifest["tool"] = {"name": "fruitspy-nickname-collision-fix", "version": "1"}
        records = {record["abi"]: record for record in manifest["libraries"]}
        targets = {f"lib/{abi}/{base.LIBRARY}": abi for abi in PATCHES}
        seen = set()
        final_apk = work / "unsigned.apk"
        with zipfile.ZipFile(baseline) as incoming, zipfile.ZipFile(final_apk, "w") as outgoing:
            outgoing.comment = incoming.comment
            for entry in incoming.infolist():
                data = incoming.read(entry)
                abi = targets.get(entry.filename)
                if abi is not None:
                    if abi in seen:
                        raise ValueError(f"duplicate native library for {abi}")
                    seen.add(abi)
                    data, record = patch_library(data, abi)
                    records[abi]["nickname"] = record
                    records[abi]["output_sha256"] = record["output_sha256"]
                outgoing.writestr(entry, data)
        if seen != set(PATCHES):
            raise ValueError(f"missing nickname patch targets: {sorted(set(PATCHES) - seen)}")

        if tools is not None:
            material = base.ensure_signing_material(
                signing_directory or base.default_signing_directory(), tools.keytool
            )
            signed = work / "signed.apk"
            manifest["signing"] = base.align_sign_and_verify(final_apk, signed, tools, material, work)
            final_apk = signed
        manifest["output"] = {
            "apk_sha256": base.sha256_file(final_apk),
            "signed": tools is not None,
            "zipaligned": tools is not None,
        }
        temporary_report = report.with_name(f".{report.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary_report.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            final_apk.replace(output)
            temporary_report.replace(report)
        except Exception:
            output.unlink(missing_ok=True)
            temporary_report.unlink(missing_ok=True)
            raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Fruit Ninja 1.7.6 with accepted-nickname, clock, and FruitSpy endpoint corrections."
    )
    parser.add_argument("--source", type=Path, required=True, help="Allowlisted clean Fruit Ninja 1.7.6 APK")
    parser.add_argument("--output", type=Path, required=True, help="New output APK; also writes .manifest.json")
    parser.add_argument("--server-host", required=True, help="FruitSpy IPv4 address or short DNS hostname")
    parser.add_argument("--unsigned", action="store_true", help="Skip signing; output is not installable as-is")
    parser.add_argument("--signing-directory", type=Path)
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--keytool", type=Path)
    parser.add_argument("--zipalign", type=Path)
    parser.add_argument("--apksigner", type=Path)
    args = parser.parse_args()
    try:
        manifest = build_output(**vars(args))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"error: {error}\n")
    print(f"Built {args.output}: all three ABIs patched")
    print(f"APK SHA-256: {manifest['output']['apk_sha256']}")
    print(f"Manifest: {args.output}.manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
