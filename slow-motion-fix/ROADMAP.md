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

The hardened implementation has ARMv7 multiplayer runtime evidence and user-confirmed x86 normal-speed, background/resume, and sleep/wake passes. The final Unified x86 emulator and Galaxy S4 completed three consecutive games without desynchronization or disconnects, satisfying the synchronization gate. Both hosting directions, long-session wrap, and legacy `armeabi` runtime coverage remain pending. Emulator graphics/host-crash issues are excluded under the user's Intel-driver assumption; see `VALIDATION.md` for the evidence and limits.

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

- [x] Confirm normal hardened x86 game pace by user observation, including the final Unified APK on Genymotion.
- [x] Confirm final Unified x86 emulator-to-real-device synchronization: the user reported three consecutive games with the Galaxy S4, without desynchronization or disconnects.

Exact game-length timer tests are waived by user decision. Qualitative normal speed plus emulator-to-device LAN synchronization replaces the measured-duration gate; no exact elapsed-time claim is made.

### Delta-guard and lifecycle behavior

- [x] Confirm x86 background/resume restores the last gameplay state (user report; interruption duration not measured).
- [x] Confirm x86 sleep/wake restores the last gameplay state (user report; interruption duration not measured).
- [x] Confirm lifecycle behavior on physical ARM runtimes: user reports no timing issues after backgrounding or device sleep with `armeabi` on both phones and `armeabi-v7a` on the S20.
- [ ] Return to the menu and start a new round after each lifecycle interruption.
- [x] Verify low-word wrap and guard boundaries through accelerated execution of all three current ABI timer paths: 72 scenario runs and 222 native frame updates passed at two load bases. A physical foreground run beyond 72 minutes remains optional long-session stability coverage, not required arithmetic verification.
- [x] Confirm high-refresh game speed: the user reports no speed issues with the S20 set to 120 Hz.
- [ ] Exercise severe-load behavior to qualify the 250 ms cutoff policy; the 120 Hz pass does not establish guard-branch execution under long frame stalls.
- [ ] Make `clock_gettime` failure deterministic in the native helper instead of consuming an uninitialized `timespec`, then statically and dynamically verify the chosen fallback.

### ABI and platform scope

- [x] Runtime-test the hardened direct-syscall x86 payload and the final Unified x86 APK; normal speed and lifecycle recovery passed by user report.
- [ ] Runtime-test the `armeabi` payload on a suitable device/emulator or label it unqualified and unsupported.
- [ ] Repeat at least one full online match in both hosting directions after the final combined patch pipeline is frozen.
- [ ] Verify Android 14 installation using the documented low-target-SDK bypass.
- [ ] Document that ARM64 devices are supported only when they retain 32-bit ARM compatibility.
- [ ] Document that 64-bit-only devices cannot load this APK because no `arm64-v8a` game library exists.

Exit criteria:

- [x] The final Unified x86 artifact has qualitative normal-speed confirmation and a synchronized emulator-to-real-device multiplayer pass: three consecutive games with the Galaxy S4, by user report.
- [ ] Lifecycle, wrap, and load coverage is complete for supported runtimes, with no in-scope physics jump, timer discontinuity, or audio drift; x86 background and sleep recovery already passed. Excluded graphics/host-crash issues do not count as crash-free evidence.
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
