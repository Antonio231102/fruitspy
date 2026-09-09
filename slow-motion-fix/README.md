# Fruit Ninja Android slow-motion fix

Compatibility investigation and native fix for Fruit Ninja 1.7.6. This subproject owns the timing diagnosis, native payload source, reproducible payload binaries, and runtime-validation records. The repository-level `patcher/` subproject owns APK orchestration, configurable FruitSpy endpoint patching, alignment, and signing.

## Status

- Root cause identified with high confidence: the native frame delta uses process CPU time rather than elapsed time.
- The fix redirects the timer to direct `clock_gettime(CLOCK_MONOTONIC)` helpers for `armeabi`, `armeabi-v7a`, and `x86`.
- Frame gaps above 250 ms are replaced with the engine's normal first-frame delta of 16,667 microseconds.
- Native payload source and reproducible machine-code payloads are included.
- An earlier wall-clock prototype corrected slow motion on an Android 9 x86 emulator and remained synchronized with an Android 4.4.2 ARMv7 device.
- The hardened ARMv7 implementation completed direct and relayed multiplayer games on Android 4.4.2 and Android 13 devices.
- The hardened x86 implementation and final Unified APK restored normal pace by user report; x86 background/resume and sleep/wake behavior passed.
- Exact-duration tests were waived in favor of qualitative speed confirmation plus emulator-to-real-device LAN synchronization. The final Unified APK passed three consecutive Android 9 x86 emulator-to-Galaxy S4 games with no desynchronization or disconnects, by user report.
- Emulator graphics/host-crash issues are excluded under the user's Intel-driver assumption, not a proven root cause. This does not exclude the earlier S4 native socket crash. Legacy `armeabi`, long-session wrap, and broader runtime coverage remain pending.

See `FINDINGS.md` for the evidence and implementation details, `VALIDATION.md` for the recorded validation boundary, `MANUAL_TEST_PLAN.md` for physical-device checks, and `ROADMAP.md` for remaining compatibility work.

## Layout

```text
README.md                         Subproject boundary and commands
FINDINGS.md                       Root-cause and implementation analysis
MANUAL_TEST_PLAN.md               Physical-device validation procedure
ROADMAP.md                        Remaining compatibility qualification
VALIDATION.md                     Recorded runtime results and limits
analysis/static-evidence.json     Minimal machine-readable static evidence
native/monotonic_arm.S            ARMv5/ARMv7 timer and delta guard
native/monotonic_x86.S            x86 timer and delta guard
native/payloads/                  Reproducible machine-code payloads
tools/build_native_payloads.py    LLVM-based payload reproducer
```

Proprietary APKs, extracted game libraries, disassemblies, symbols, generated APKs, and signing material are not part of this subproject.

## Reproduce the native payloads

Run from the FruitSpy repository root with LLVM `clang`, `ld.lld`, and `llvm-objcopy` available on `PATH`:

```text
python slow-motion-fix/tools/build_native_payloads.py
```

The command rebuilds each payload and rejects any hash mismatch.

## Build an installable patched APK

Use the neutral combined patcher from the repository root:

```text
python patcher/patch_apk.py
```

The patcher accepts only the allowlisted clean Fruit Ninja 1.7.6 APK, announces the slow motion fix patch, matchmaking fix patch, and custom server patch in that order, requires a user-supplied IPv4 address or short DNS name, applies all three patches to every packaged ABI, and verifies the signed output. See the root `README.md` for complete patching and signing instructions.

## Repository boundary

- `slow-motion-fix/` owns compatibility research, payload source, payload hashes, and validation evidence.
- `patcher/` owns the release-facing APK transformation and signing workflow.
- `server/` owns the replacement GameSpy services and has no APK-patching code.
