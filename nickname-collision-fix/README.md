# Fruit Ninja 1.7.6 nickname-collision correction

Native matchmaking fix for `armeabi`, `armeabi-v7a`, and `x86`, included in FruitSpy Patcher. Before each new PeerChat connection, the game refreshes its network nickname from the configured nickname. After acceptance, it copies the SDK's actual nickname into its cached session identity **before** publishing connection success. A collision suffix therefore remains authoritative during the match without becoming the preferred name for the next connection. This is a client fix, not a fix for server-side dead sessions.

## Status and boundary

At the 2026-09-09 integration checkpoint, the guided main-patcher build applied the requested three-stage order and produced a signed APK byte-identical to the previously qualified `build/FruitSpy-Nickname-Fix-Preferred.apk` using the same endpoint and signing key. All nine patcher tests and 66 native nickname scenarios passed. These counts, artifact checks and physical-gameplay results are historical evidence, not new runs or a byte-identity claim for current configurable output. See [current FruitSpy Patcher integration](FINDINGS.md#current-fruitspy-patcher-integration) for the later build/test evidence and separate LVL acceptance boundary.

- All three ABI payloads are implemented, reproducible, and hash-checked.
- Native execution reproduces both the original renamed-host failure and the previous patch's sticky reconnect name in every ABI, then passes 22 corrected scenarios per ABI at two load addresses (66 total).
- `build/FruitSpy-Nickname-Fix-Preferred.apk` is signed with the existing FruitSpy certificate; v1/v2/v3 signatures and APK alignment passed verification. Its actual libraries also passed a collision-host/free-name-reconnect smoke check under Unicorn.
- **The preferred-name restoration revision passed four physical S20/S4 matches on 2026-09-09:** a forced-collision match followed by a free-name reconnect with each phone hosting. Both phones execute `armeabi-v7a`; neither game process restarted across the four matches. Captured registration and host-launch messages confirm suffixed identities during collisions and original configured identities afterward. The earlier five x86-emulator/S20 matches belong to the callback-only APK, not this revision.
- **The legacy `armeabi` binary also passed four physical S20/S4 matches on 2026-09-09**, using a separately signed test APK containing only that ABI. Both hosting roles recovered from forced collisions and restored the original names on reconnect without game restarts. The installed legacy library exactly matches the normal revised APK; this qualifies the binary on these phones, not actual ARMv5/ARMv6 hardware.
- **Mixed `armeabi`/`armeabi-v7a` multiplayer passed four additional S4/S20 matches over direct LAN UDP on 2026-09-09.** Each ABI hosted a forced-collision match and a free-name reconnect without game restarts. Packet captures confirm direct phone-to-phone gameplay, not relay gameplay.
- **Maximum-length configured nicknames passed a controlled mixed-ABI collision/restoration pair over LAN.** Both 16-character originals were preserved in 19-character accepted aliases, then restored exactly on reconnect after verified reservation release, without nickname edits or game restarts.
- **Intentional numeric suffixes passed three mixed-ABI LAN matches:** collision aliases retained configured `.25`/`.19`, no-restart reconnect removed only temporary `.62`/`.68`, and cold game restarts preserved the exact configured names. Both game process IDs changed for the cold-launch check.
- The user also reports Classic, Zen and Arcade working without slowdowns on both phones using `armeabi`, with no timing issues after backgrounding or device sleep.
- **No further x86 device testing will be performed on this workstation.** The user reported a PC softlock from known, unrelated emulator instability and elected to handle remaining x86 issues through public-release issue tracking and user feedback. Existing native checks and historical live results are retained; the interrupted revised-build test is not counted as a pass.
- The configured nickname is not edited or suffix-stripped. Each connection requests it exactly, including an intentional numeric suffix; an empty setting leaves the game's existing generated candidate unchanged.
- Actual ARMv5/ARMv6 hardware and direct Internet NAT traversal remain unqualified; direct LAN gameplay is qualified. Actual legacy-hardware tests are not a release gate at the user's direction; hardware-specific issues go through user feedback and the issue tracker. No further mobile-data or relay-specific testing is planned unless an observed issue makes it necessary. All game UI actions remain user-only. The legacy-only APK was a qualification artifact; it did not replace the normal three-ABI build.

The canonical APK builder is FruitSpy Patcher (`patcher/patch_apk.py`), with the nickname implementation in `patcher/nickname_patch.py`. It applies the **slow-motion fix → matchmaking fix → custom server patch** to all three ABIs. Only a package different from `com.halfbrick.fruitninja` then triggers native and Java LVL removal, before optional identity edits, alignment and signing. The original package and launcher-only changes retain original LVL. This subfolder retains the native payload source/binaries, payload build tools, regression harness, and findings; it no longer provides a separate APK builder. Integration does not change the nickname payloads or server behavior.

See [FINDINGS.md](FINDINGS.md) for the diagnosis, ABI addresses, implementation invariants, and validation evidence.

## Build a corrected APK

Run from the repository root with a supported Python release (3.11 or newer is recommended and also satisfies the server requirement), a JDK (`keytool`), and Android SDK Build Tools (`zipalign`, `apksigner`). See the root [README](../README.md) for dependency installation and tool discovery. Ordinary APK patching uses the Python standard library and the checked-in payloads; LLVM and Unicorn are only needed for the optional development workflows below.

```text
python patcher/patch_apk.py "Fruit Ninja 1.7.6.apk" --server-host 192.168.100.2
```

Use your FruitSpy IPv4 address or short DNS hostname for `--server-host`. The source must be the clean Fruit Ninja 1.7.6 APK with SHA-256:

```text
5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650
```

By default FruitSpy Patcher writes only `<launcher name> FruitSpy - <IP/hostname>.apk` beside the source APK, or `FruitSpy - <IP/hostname>.apk` when the launcher name is exactly `FruitSpy`. Skipping launcher customization uses `Fruit Ninja` as the filename's launcher name. An explicit second positional argument overrides the output path; use it if the display name cannot form a portable filename. Existing outputs are refused; choose a fresh filename for another build. `--report PATH` optionally saves the diagnostic JSON report identifying FruitSpy Patcher and recording the selected stage order, exact nickname hook bytes, payload/library hashes, optional identity and LVL records, and signing verification when signing is enabled. The nickname input hash matches the clock output hash; the final library hash includes the endpoint patch and, only for a changed package, native LVL removal.

- Signing is enabled by default and uses the main patcher's persistent local key directory. Reuse both the installed app's package name and signing key to preserve update compatibility.
- `--signing-dir PATH` selects that directory; `--android-sdk`, `--keytool`, `--zipalign`, and `--apksigner` override tool discovery.
- `--unsigned` builds an inspection artifact only; it is not installable as-is.
- The main patcher takes the **clean** APK, not an already patched APK, so its entire transformation chain is validated.
- `--package-name` and `--launcher-name` are independent optional choices; omitted values are prompted separately in an interactive run and skipped in `--non-interactive` mode. Empty answers preserve the corresponding original name. See the root [naming and installation effects](../README.md#patch-a-locally-owned-apk); a different package starts with separate private data.

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
