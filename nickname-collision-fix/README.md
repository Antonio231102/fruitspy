# Fruit Ninja 1.7.6 nickname-collision correction

Standalone native correction for `armeabi`, `armeabi-v7a`, and `x86`. Before each new PeerChat connection, the game refreshes its network nickname from the configured nickname. After acceptance, it copies the SDK's actual nickname into its cached session identity **before** publishing connection success. A collision suffix therefore remains authoritative during the match without becoming the preferred name for the next connection.

## Status and boundary

- All three ABI payloads are implemented, reproducible, and hash-checked.
- Native execution reproduces both the original renamed-host failure and the previous patch's sticky reconnect name in every ABI, then passes 22 corrected scenarios per ABI at two load addresses (66 total).
- `build/FruitSpy-Nickname-Fix-Preferred.apk` is signed with the existing FruitSpy certificate; v1/v2/v3 signatures and APK alignment passed verification. Its actual libraries also passed a collision-host/free-name-reconnect smoke check under Unicorn.
- **The preferred-name restoration revision passed four physical S20/S4 matches on 2026-09-09:** a forced-collision match followed by a free-name reconnect with each phone hosting. Both phones execute `armeabi-v7a`; neither game process restarted across the four matches. Captured registration and host-launch messages confirm suffixed identities during collisions and original configured identities afterward. The earlier five x86-emulator/S20 matches belong to the callback-only APK, not this revision.
- **The legacy `armeabi` binary also passed four physical S20/S4 matches on 2026-09-09**, using a separately signed test APK containing only that ABI. Both hosting roles recovered from forced collisions and restored the original names on reconnect without game restarts. The installed legacy library exactly matches the normal revised APK; this qualifies the binary on these phones, not actual ARMv5/ARMv6 hardware.
- **No further x86 device testing will be performed on this workstation.** The user reported a PC softlock from known, unrelated emulator instability and elected to handle remaining x86 issues through public-release issue tracking and user feedback. Existing native checks and historical live results are retained; the interrupted revised-build test is not counted as a pass.
- The configured nickname is not edited or suffix-stripped. Each connection requests it exactly, including an intentional numeric suffix; an empty setting leaves the game's existing generated candidate unchanged.
- Actual ARMv5/ARMv6 hardware and direct peer-to-peer gameplay remain unqualified. All game UI actions remain user-only. The normal three-ABI release APK is unchanged; the legacy-only APK is a qualification artifact.

This subfolder owns the nickname patch, payload source/binaries, build entry point, regression harness, and findings. It reuses `patcher/patch_apk.py` for the allowlisted clean APK, existing clock/endpoint transformations, ELF insertion, alignment, and signing. The shared patcher, server, and existing Unified APK are not modified. Its installable output includes the existing clock and FruitSpy endpoint fixes rather than returning to the defunct GameSpy endpoints.

See [FINDINGS.md](FINDINGS.md) for the diagnosis, ABI addresses, implementation invariants, and validation evidence.

## Build a corrected APK

Run from the repository root with Python 3.10+, a JDK (`keytool`), and Android SDK build-tools (`zipalign`, `apksigner`):

```text
python nickname-collision-fix/tools/build_apk.py --source "Fruit Ninja 1.7.6.apk" --output nickname-collision-fix/build/FruitSpy-Nickname-Fix-Preferred.apk --server-host 217.154.27.122
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

The harness extracts the libraries from the allowlisted APK, applies the existing clock correction and then this patch, and executes both helpers, the native nickname setter/string routines, SDK nickname getter, game host predicate, and SDK player lookup in Unicorn. It supplies libc imports, a one-bucket fixture hash callback, and an observer at the SDK connection boundary to inspect the requested nickname. A reconnect test keeps the provider alive instead of replacing its state with a synthetic accepted name. Failed assertions terminate the command; do not run Python with `-O`.

Proprietary APKs, extracted libraries, disassemblies, local dependencies, generated install artifacts, and signing material are not committed. Generated files belong under ignored `build/` or `input/` directories.
