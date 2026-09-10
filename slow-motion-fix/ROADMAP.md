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

**Functional acceptance: fully functional, with high confidence**, accepted by the project owner on 2026-09-09. Evidence now includes physical `armeabi` and `armeabi-v7a` matches in both hosting directions, mixed-ABI games, lifecycle and S20 120 Hz checks, historical x86 runtime passes, 72 accelerated native clock-boundary scenarios, and an integrated main-patcher build byte-identical to the qualified signed APK. Further long-session investigation is deferred until user reports identify a problem; no additional endurance test is a release gate. Historical evidence and untested runtime boundaries remain explicit in `VALIDATION.md`. This functional acceptance is separate from the unresolved license, provenance, owner privacy, and public-release decisions.

## Phase 1 — Compose with the FruitSpy client patch

Purpose: make the compatibility fix usable with an arbitrary public or self-hosted FruitSpy endpoint.

- [x] Root the combined transformation in the allowlisted original Fruit Ninja 1.7.6 APK instead of a VPS-specific intermediate.
- [x] Define one public pipeline in `../patcher/` that applies the monotonic-clock correction, nickname-collision matchmaking fix, and configurable FruitSpy endpoint transformation in that order.
- [x] Keep the compatibility analysis, assembly, reproducible payload binaries, and payload hashes under `slow-motion-fix/`; keep APK orchestration and verification under `patcher/`.
- [x] Require the exact supported whole-APK input hash before producing output, with per-library hashes as additional checks.
- [x] Apply clock, nickname, and endpoint transformations to `armeabi`, `armeabi-v7a`, and `x86`.
- [x] Implement the ABI-specific FruitSpy endpoint and NatNeg resolver patch for legacy `armeabi`.
- [x] Accept every endpoint form promised by FruitSpy: IPv4 or a DNS name of at most 18 ASCII characters.
- [x] Offer `--report PATH` for a diagnostic JSON report with input, output, per-library, payload, configuration, and tool-version hashes. Ordinary builds write only `Fruit Ninja v1.7.6 FruitSpy <IPv4/domain>.apk` beside the source APK; an explicit second positional argument overrides that output path.
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
- Deferred, non-blocking coverage: a separate new-round-after-each-interruption result is not recorded.
- [x] Verify low-word wrap and guard boundaries through accelerated execution of all three current ABI timer paths: 72 scenario runs and 222 native frame updates passed at two load bases. Additional physical long-session investigation is deferred until user reports warrant it.
- [x] Confirm high-refresh game speed: the user reports no speed issues with the S20 set to 120 Hz.
- Deferred, non-blocking coverage: severe-load device behavior beyond the recorded gameplay checks; native 250 ms guard boundaries already passed.
- Deferred hardening, not an observed runtime failure or release blocker: deterministic handling of a failed `clock_gettime` syscall. The current helper does not check its return value; this limitation is retained rather than marked fixed.

### ABI and platform scope

- [x] Runtime-test the hardened direct-syscall x86 payload and the final Unified x86 APK; normal speed and lifecycle recovery passed by user report.
- [x] Runtime-test the `armeabi` payload: forced-legacy physical S4/S20 qualification passed. Actual ARMv5/ARMv6 CPUs remain untested and are not a release gate by owner decision.
- [x] Qualify both physical ARM hosting directions; the integrated main patcher subsequently reproduced the already-qualified signed APK byte-for-byte.
- [ ] Verify Android 14 installation using the documented low-target-SDK bypass.
- [x] Document that ARM64 devices are supported only when they retain 32-bit ARM compatibility.
- [x] Document that 64-bit-only devices cannot load this APK because no `arm64-v8a` game library exists.

No further x86 emulator or actual ARMv5/ARMv6 device testing is planned. Historical passes remain evidence, not instructions to repeat excluded testing.

Exit criteria:

- [x] The final Unified x86 artifact has qualitative normal-speed confirmation and a synchronized emulator-to-real-device multiplayer pass: three consecutive games with the Galaxy S4, by user report.
- [x] Owner accepts the slow-motion patch as fully functional with high confidence on the recorded evidence. Additional long-session investigations are user-report-driven, not release gates.
- [x] Runtime evidence exists for each packaged ABI, with historical x86 results distinguished from the current revision's native checks. Untested hardware and error paths remain explicit; no universal crash-free claim is made.

## Phase 3 — Automated builder and binary verification

Purpose: turn the existing static evidence into repeatable release gates.

- [x] Add composition coverage for every supported ABI and IPv4/short-DNS endpoints in `../patcher/tests/test_patch_apk.py`, including a locally supplied clean-APK integration case. The recorded integrated campaign passed nine tests; this does not claim exhaustive corruption coverage.
- [x] Test rejection of an unsupported whole APK without producing output.
- [ ] Extend negative coverage to independently modified native libraries, payload binaries, instruction ranges, and ELF layouts; current runtime hash/preimage checks already fail closed.
- [ ] Test missing and duplicate native-library ZIP entries.
- [x] Reject already-patched whole APKs through the clean-input allowlist; the public pipeline does not support an idempotent re-patching mode.
- [x] Cover signature-entry removal in the real-APK integration case.
- [ ] Extend regression coverage for preservation of unrelated APK entries.
- [ ] Verify executable-segment growth, later file-offset shifts, non-overlap, section offsets, and file/virtual alignment.
- [ ] Verify that dynamic imports, relocations, and `DT_NEEDED` entries remain unchanged.
- [ ] Extend archive/report consistency coverage beyond the existing per-stage and final-library hash assertions. Reports are opt-in diagnostics, not an automatically written release manifest.
- [ ] Pin or document known-good `clang`, `ld.lld`, `llvm-objcopy`, `zipalign`, and `apksigner` versions.
- [ ] Add CI that rebuilds payloads, runs the test suite, builds an unsigned fixture, and checks deterministic hashes without access to a signing key or proprietary APK.

Exit criteria:

- [ ] Automated checks fail for every tested unsupported or tampered input.
- [ ] Payloads reproduce with the documented toolchain and match the release manifest.
- [ ] The checked-in evidence is generated or verified by commands documented in the repository.

## Phase 4 — Documentation and publication

Purpose: publish only maintainable compatibility source and reproducible evidence.

- [x] Update the compatibility status documents with the recorded owner acceptance and runtime boundaries. Keep dated test hashes and observations as historical evidence, not current output defaults.
- [x] Distinguish the earlier prototype, hardened timing candidate, and later integrated-patcher evidence.
- [x] Record the supplied device/ABI, refresh-rate, APK-hash, and qualitative results in `VALIDATION.md` and the nickname-fix evidence. Exact-duration measurements were waived; missing measurements remain explicitly unrecorded.
- [ ] Resolve remaining provenance/permission questions for minimal static offsets, protocol references, and any reused expression. Copyright and third-party notices are recorded in the root NOTICE; recording attribution does not supply missing permission. Historical local symbol dumps and build manifests are not required checkout artifacts.
- [x] Adopt the owner-approved GPL-2.0-or-later source license with upstream copyright notices, trademark ownership attribution, and a clear Halfbrick Studios non-affiliation statement. See the root LICENSE and NOTICE.
- [ ] Add a security policy, disclosure contact, contribution guidance, supported-version statement, and compatibility limitations.
- [x] Complete full reachable Git-history credential/proprietary-artifact scanning over 47 commits, 361 historical blobs, and 12 pending tracked files at the audit checkpoint. No confirmed credentials or proprietary game binaries were found; the small authored native payloads are intentional. The pre-rewrite audit evidence is local and ignored at `../build/publication-audit-20260910T020432Z/publication-report.json`, not a published checkout artifact.
- [x] Confirm that audited reachable history and pending tracked files contain no game APK, extracted game library, private signing material, capture, or game-asset dump. This does not certify ignored local files or a future release package.
- [x] Remove the retired generated `server/apk-patch-map.json` and `server/apk-patch-report.json` from the pushed reachable history. A fresh remote clone has neither path nor their three historical blobs; pruning one report-only commit reduced the reachable history from the audited 47 commits to 46.
- [x] Accept GitHub's retention of the old, non-sensitive generated reports. The owner chose to keep the existing repository; no additional purge or migration is planned, and this is not a release blocker.
- [ ] Resolve owner privacy decisions for historical author/committer attribution and remaining operational/test metadata. Accepting retention of the two generated reports does not resolve these separate publication decisions.
- [ ] Commit and push the final reviewed release contents and tag a versioned alpha release after approval; reachable-history cleanup is already pushed.
- [ ] Publish source, tests, patch payloads authored by this project, protocol/analysis notes, checksums, and patch tooling only—never a Fruit Ninja APK.
- [ ] Complete legal and trademark review before making the repository public.

Exit criteria:

- [ ] A third party can reproduce the documented unsigned output from public source and a lawful allowlisted APK.
- [x] Runtime claims match the recorded device/ABI matrix without implying universal modern-Android support.
- [x] Complete the bounded credential/proprietary-artifact audit; retain its limitations.
- [ ] Resolve remaining upstream permission, legal/trademark, and owner privacy decisions before publication. The project license is selected, not final release approval. The scoped dependency review's build-backend follow-up is non-blocking source-release hygiene; see the root roadmap before publishing packages.
- [ ] The owner approves the remaining release review, release contents, checksums, and repository visibility change.

## Deferred scope

These are not requirements for the first source release:

- Native `arm64-v8a` support, which would require a compatible 64-bit game engine rather than a timer-only patch.
- Fruit Ninja versions other than 1.7.6.
- General binary rewriting for unknown regional or vendor builds.
- Changes to unrelated storage, permission, audio, or graphics compatibility behavior.
