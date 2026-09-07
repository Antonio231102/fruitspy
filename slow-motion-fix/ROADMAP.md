# Fruit Ninja Android Compatibility Public Release Roadmap

## Objective

Publish a reproducible, source-only compatibility fix for Fruit Ninja 1.7.6 that corrects slow motion without redistributing the game, extracted libraries, or project signing material. The release must compose with FruitSpy's configurable endpoint patch instead of being tied to the development VPS.

## Current checkpoint

- [x] Identify the slow-motion root cause: the native frame loop uses process CPU time as elapsed simulation time.
- [x] Replace the frame timer with direct `clock_gettime(CLOCK_MONOTONIC)` helpers for `armeabi`, `armeabi-v7a`, and `x86`.
- [x] Route frame deltas above 250,000 microseconds to the engine's normal first-frame delta of 16,667 microseconds.
- [x] Reproduce the native payloads byte-for-byte from checked-in ARM and x86 assembly.
- [x] Fail closed on unexpected native-library, payload, and patched-library hashes.
- [x] Verify ELF load-segment congruence, unchanged dynamic dependencies, APK entry changes, ZIP alignment, and APK signatures for the hardened candidate.
- [x] Confirm the diagnosis with the earlier wall-clock prototype on an affected Android 9 x86 emulator and an Android 4.4.2 ARMv7 device.
- [x] Install the exact hardened APK, SHA-256 `ee67e367f06d6cdbdb51ed5c5f9e892f7605439d3ff0876556bd99b856d01b21`, on Android 4.4.2 and Android 13 devices.
- [x] Smoke-test the hardened ARMv7 runtime path through completed direct and relayed multiplayer games on those physical devices.

The hardened implementation is therefore an ARMv7 runtime-tested candidate. Its quantitative timing behavior, abnormal-delta branch, hardened x86 payload, and legacy `armeabi` payload are not yet release-qualified.

## Phase 1 — Compose with the FruitSpy client patch

Purpose: make the compatibility fix usable with an arbitrary public or self-hosted FruitSpy endpoint.

- [x] Root the combined transformation in the allowlisted original Fruit Ninja 1.7.6 APK instead of a VPS-specific intermediate.
- [x] Define one public pipeline in `../patcher/` that applies the monotonic-clock correction followed by the configurable FruitSpy endpoint transformation.
- [x] Keep the compatibility analysis, assembly, reproducible payload binaries, and payload hashes under `slow-motion-fix/`; keep APK orchestration and verification under `patcher/`.
- [x] Require the exact supported whole-APK input hash before producing output, with per-library hashes as additional checks.
- [x] Apply both endpoint and clock transformations to `armeabi`, `armeabi-v7a`, and `x86`.
- [x] Implement the ABI-specific FruitSpy endpoint and NatNeg resolver patch for legacy `armeabi`.
- [x] Accept every endpoint form promised by FruitSpy: IPv4 or a DNS name of at most 18 ASCII characters.
- [x] Produce a host-neutral JSON manifest with input, output, per-library, payload, configuration, and tool-version hashes.
- [x] Generate and persist a random per-user signing identity by default, while retaining an explicit unsigned mode for external signing.
- [x] Cover IPv4 and short-DNS composition across all three ABIs.

Exit criteria:

- [x] A clean checkout plus one allowlisted user-supplied APK produces the combined FruitSpy and clock-corrected unsigned or signed APK.
- [x] Changing the configured FruitSpy host does not require editing expected native-library hashes.
- [x] Unsupported or partially patched inputs fail before any final output replaces an existing file.

## Phase 2 — Runtime qualification

Purpose: verify the final monotonic implementation rather than relying on the earlier `gettimeofday()` prototype.

### Primary timing behavior

- [ ] On the known affected Android 9 x86 environment, record two baseline 60-second rounds and two hardened-candidate rounds under unchanged display and power settings.
- [ ] Confirm each hardened nominal 60-second round lasts 57–63 real seconds.
- [ ] Repeat the quantitative timing check on the Android 13 physical device using its `armeabi-v7a` package runtime.
- [ ] Confirm fruit motion, countdowns, particles, menus, and audio no longer exhibit uniform slow motion or synchronization drift.

### Delta-guard and lifecycle behavior

- [ ] Background an active round for 30 seconds, resume, and verify that physics and the countdown do not jump forward.
- [ ] Lock the screen for 30 seconds, unlock, and verify the same behavior.
- [ ] Return to the menu and start a new round after each lifecycle interruption.
- [ ] Run a foreground session beyond 72 minutes and verify continuity across the low-32-bit microsecond wrap.
- [ ] Exercise visually busy gameplay and high-refresh mode where available to validate the 250 ms cutoff policy under load.
- [ ] Make `clock_gettime` failure deterministic in the native helper instead of consuming an uninitialized `timespec`, then statically and dynamically verify the chosen fallback.

### ABI and platform scope

- [ ] Runtime-test the hardened direct-syscall x86 payload; the successful prototype used a different helper and does not qualify it.
- [ ] Runtime-test the `armeabi` payload on a suitable device/emulator or label it unqualified and unsupported.
- [ ] Repeat at least one full online match in both hosting directions after the final combined patch pipeline is frozen.
- [ ] Verify Android 14 installation using the documented low-target-SDK bypass.
- [ ] Document that ARM64 devices are supported only when they retain 32-bit ARM compatibility.
- [ ] Document that 64-bit-only devices cannot load this APK because no `arm64-v8a` game library exists.

Exit criteria:

- [ ] The final hardened artifact passes the measured slow-motion acceptance test on a previously affected environment.
- [ ] Background, lock, wrap, and load tests produce no crash, freeze, large physics jump, timer discontinuity, or audio drift.
- [ ] Every advertised ABI has runtime evidence; untested ABIs are excluded from release claims.

## Phase 3 — Automated builder and binary verification

Purpose: turn the existing static evidence into repeatable release gates.

- [ ] Add an automated test suite for exact input-to-output transformations for every supported ABI.
- [ ] Test rejection of modified whole APKs, native libraries, payload binaries, instruction ranges, and ELF layouts.
- [ ] Test missing and duplicate native-library ZIP entries.
- [ ] Test idempotent handling of already-patched libraries.
- [ ] Test signature-entry removal without changing unrelated APK entries.
- [ ] Verify executable-segment growth, later file-offset shifts, non-overlap, section offsets, and file/virtual alignment.
- [ ] Verify that dynamic imports, relocations, and `DT_NEEDED` entries remain unchanged.
- [ ] Verify the generated patch report and release manifest against the actual archive.
- [ ] Pin or document known-good `clang`, `ld.lld`, `llvm-objcopy`, `zipalign`, and `apksigner` versions.
- [ ] Add CI that rebuilds payloads, runs the test suite, builds an unsigned fixture, and checks deterministic hashes without access to a signing key or proprietary APK.

Exit criteria:

- [ ] Automated checks fail for every tested unsupported or tampered input.
- [ ] Payloads reproduce with the documented toolchain and match the release manifest.
- [ ] The checked-in evidence is generated or verified by commands documented in the repository.

## Phase 4 — Documentation and publication

Purpose: publish only maintainable compatibility source and reproducible evidence.

- [ ] Update `README.md`, `FINDINGS.md`, `VALIDATION.md`, `MANUAL_TEST_PLAN.md`, `analysis/static-evidence.json`, and `build/release-manifest.json` with the final runtime record.
- [ ] Distinguish the earlier prototype evidence from hardened-candidate evidence in every status statement.
- [ ] Record device model, Android/API version, selected native ABI, refresh rate, APK hash, measured duration, and result for each release test.
- [ ] Review whether `analysis/libmortargame-armeabi-v7a-symbols.txt` is necessary and legally appropriate to publish; prefer the minimal factual offsets required for reproducibility.
- [ ] Choose and add a source license with owner approval.
- [ ] Add a security policy, disclosure contact, contribution guidance, supported-version statement, and compatibility limitations.
- [ ] Run full Git-history secret and proprietary-artifact scanning.
- [ ] Confirm that no APK, extracted game library, keystore, password, signing token, or local device artifact is tracked or packaged.
- [ ] Add a Git remote, push the reviewed history, and tag a versioned alpha release.
- [ ] Publish source, tests, patch payloads authored by this project, protocol/analysis notes, checksums, and patch tooling only—never a Fruit Ninja APK.
- [ ] Complete legal and trademark review before making the repository public.

Exit criteria:

- [ ] A third party can reproduce the documented unsigned output from public source and a lawful allowlisted APK.
- [ ] Runtime claims match the recorded device/ABI matrix without implying universal modern-Android support.
- [ ] Secret scanning and legal review are accepted.
- [ ] The owner approves the license, release contents, checksums, and repository visibility change.

## Deferred scope

These are not requirements for the first source release:

- Native `arm64-v8a` support, which would require a compatible 64-bit game engine rather than a timer-only patch.
- Fruit Ninja versions other than 1.7.6.
- General binary rewriting for unknown regional or vendor builds.
- Changes to unrelated storage, permission, audio, or graphics compatibility behavior.
