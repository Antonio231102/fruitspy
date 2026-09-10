# Fruit Ninja Android slow-motion fix

Compatibility investigation and native fix for Fruit Ninja 1.7.6. This subproject owns the timing diagnosis, native payload source, reproducible payload binaries, and runtime-validation records. The repository-level `patcher/` subproject owns APK orchestration, configurable FruitSpy endpoint patching, alignment, and signing.

## Status

**Fully functional, with high confidence.** Accepted by the project owner on 2026-09-09 based on the recorded runtime results and native verification. Further long-session investigation or changes will be driven by user reports, not additional pre-release endurance testing.

- Root cause identified with high confidence: the native frame delta uses process CPU time rather than elapsed time.
- The fix redirects the timer to direct `clock_gettime(CLOCK_MONOTONIC)` helpers for `armeabi`, `armeabi-v7a`, and `x86`.
- Frame gaps above 250 ms are replaced with the engine's normal first-frame delta of 16,667 microseconds.
- Native payload source and reproducible machine-code payloads are included.
- An earlier wall-clock prototype corrected slow motion on an Android 9 x86 emulator and remained synchronized with an Android 4.4.2 ARMv7 device.
- The hardened ARMv7 implementation completed direct and relayed multiplayer games on Android 4.4.2 and Android 13 devices.
- The hardened x86 implementation and final Unified APK restored normal pace by user report; x86 background/resume and sleep/wake behavior passed.
- Exact-duration tests were waived in favor of qualitative speed confirmation plus emulator-to-real-device LAN synchronization. The final Unified APK passed three consecutive Android 9 x86 emulator-to-Galaxy S4 games with no desynchronization or disconnects, by user report.
- Physical `armeabi` and `armeabi-v7a` gameplay and lifecycle checks passed; S20 120 Hz speed passed. Accelerated clock-wrap/guard verification passed 72 scenario runs and 222 native frame updates across all three ABIs. The integrated patcher passed nine tests and reproduced the qualified signed APK byte-for-byte.
- Emulator graphics/host-crash issues remain excluded under the user's Intel-driver assumption, not a proven root cause. Historical observations and untested hardware/error paths remain documented; acceptance does not assert that every device or failure path has been tested.

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

Use the root [README](../README.md) to install the patcher dependencies: a supported Python release (3.11 or newer recommended), a JDK with `keytool`, and Android SDK Build Tools with `zipalign` and `apksigner`. Ordinary patching does not require LLVM or Unicorn because it uses the checked-in native payloads.

By default, only `Fruit Ninja v1.7.6 FruitSpy <IPv4/domain>.apk` is written beside the source APK. An explicit second positional argument overrides the output path; existing outputs are refused. Add `--report PATH` only to save a diagnostic JSON build report.

## Repository boundary

- `slow-motion-fix/` owns compatibility research, payload source, payload hashes, and validation evidence.
- `patcher/` owns the release-facing APK transformation and signing workflow.
- `server/` owns the replacement GameSpy services and has no APK-patching code.
