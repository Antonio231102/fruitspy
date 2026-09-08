# Fruit Ninja 1.7.6 nickname-collision correction

Standalone native correction for `armeabi`, `armeabi-v7a`, and `x86`. After PeerChat accepts a connection, the game now copies the SDK's accepted nickname into its cached identity **before** publishing connection success. This prevents a renamed host from waiting indefinitely in matchmaking and a renamed joiner from being mistaken for another player who owns its original nickname.

## Status and boundary

- All three ABI payloads are implemented, reproducible, and hash-checked.
- Native execution reproduces the uncorrected renamed-host failure in every ABI and passes 18 corrected scenarios per ABI at two load addresses (54 total).
- A signed APK was built with the existing FruitSpy certificate; v1/v2/v3 signatures and APK alignment passed verification.
- Update installation succeeded on the Android x86 emulator and Galaxy S20 without uninstalling or clearing data.
- **Post-fix live matchmaking is not yet qualified.** Both devices became unreachable through ADB after an interruption before that check could be completed. Native execution is not evidence of an end-to-end network match, and there was no physical legacy `armeabi` device qualification.

This subfolder owns the nickname patch, payload source/binaries, build entry point, regression harness, and findings. It reuses `patcher/patch_apk.py` for the allowlisted clean APK, existing clock/endpoint transformations, ELF insertion, alignment, and signing. The shared patcher, server, and existing Unified APK are not modified. Its installable output includes the existing clock and FruitSpy endpoint fixes rather than returning to the defunct GameSpy endpoints.

See [FINDINGS.md](FINDINGS.md) for the diagnosis, ABI addresses, implementation invariants, and validation evidence.

## Build a corrected APK

Run from the repository root with Python 3.10+, a JDK (`keytool`), and Android SDK build-tools (`zipalign`, `apksigner`):

```text
python nickname-collision-fix/tools/build_apk.py --source "Fruit Ninja 1.7.6.apk" --output nickname-collision-fix/build/FruitSpy-Nickname-Fix.apk --server-host 217.154.27.122
```

Use your FruitSpy IPv4 address or short DNS hostname for `--server-host`. The source must be the clean Fruit Ninja 1.7.6 APK with SHA-256:

```text
5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650
```

The command writes the APK and `<output>.manifest.json`. Existing outputs are refused; choose a fresh filename for another build. The manifest records the base transformations, exact nickname hook bytes, payload and library hashes, and signing verification.

- Signing uses the shared patcher's persistent local key directory. Reuse the key that signed the installed app to preserve update compatibility.
- `--signing-directory`, `--android-sdk`, `--keytool`, `--zipalign`, and `--apksigner` override discovery.
- `--unsigned` builds an inspection artifact only; it is not installable as-is.
- The standalone builder intentionally takes the **clean** APK, not an already patched Unified APK, so its entire transformation chain is validated.

## Reproduce payloads

The checked-in payloads permit APK builds without a native compiler. To reproduce them, put LLVM `clang`, `ld.lld`, and `llvm-objcopy` on `PATH`:

```text
python nickname-collision-fix/tools/build_native_payloads.py
```

A mismatched build is rejected before replacing a checked-in payload. ARM is compiled for ARMv5TE and ARMv7-A respectively; x86 uses the i686 Android calling convention. All calls and callback references remain position-independent.

## Run the native regression harness

Install the optional pinned dependency in your Python environment:

```text
python -m pip install -r nickname-collision-fix/requirements-validation.txt
python nickname-collision-fix/tools/verify_native.py --source "Fruit Ninja 1.7.6.apk" --report nickname-collision-fix/build/native-validation.json
```

The harness extracts the libraries from the allowlisted APK, applies the existing clock correction and then this patch, and executes the selected callback, SDK nickname getter, game host predicate, and SDK player lookup in Unicorn. It supplies only libc string imports and a valid one-bucket fixture hash callback. It does not replace the host decision with a Python mock. Failed assertions terminate the command; do not run Python with `-O`.

Proprietary APKs, extracted libraries, disassemblies, local dependencies, generated install artifacts, and signing material are not committed. Generated files belong under ignored `build/` or `input/` directories.
