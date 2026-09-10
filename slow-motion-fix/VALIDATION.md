# Runtime validation record

## Current acceptance — 2026-09-09

**Fully functional, with high confidence**, accepted by the project owner based on the recorded runtime and native evidence. The integrated main patcher also passed nine tests and 66 native nickname scenarios and produced a signed APK byte-identical to the qualified build. Further long-session timing/stability investigation and potential changes will be initiated in response to user reports, not as additional release gates. This acceptance preserves the scope and limitations of each observation below.

## 2026-09-04 cross-version LAN/VPS test

Artifact: `Fruit Ninja 1.7.6 - FruitSpy VPS - Clock Fix.apk`
SHA-256: `0aca4a049da2940ce46b0b2523e0d65439f02912e8729f05ac4efec5ee53fc22`
Implementation: initial `gettimeofday()` prototype
Status: **passed for the tested configuration**

The user manually tested the signed prototype on both deployed devices:

| Role | Device | Android | Native path | Result |
|---|---|---:|---|---|
| Modern runtime | Android emulator `emulator-5554` | 9 | `x86` compatibility library on an x86_64 system image | Game ran at normal speed |
| Legacy reference | Samsung Galaxy S4 `SGH-I337M` / `jfltecan` | 4.4.2 | ARMv7 | Game remained at normal speed |

Observed behavior reported by the user:

- The Android 9 emulator, which normally exhibited slow motion, ran in full temporal synchronization with the Galaxy S4.
- The two clients successfully played through the FruitSpy VPS services while using their LAN gameplay path.
- No gameplay desynchronization was observed.

This confirms the process-CPU-time diagnosis and the timer-source correction on the tested Android 9 x86 environment. It also confirms no observable ARMv7 speed regression on Android 4.4.2 and one successful cross-version online session.

## Hardened monotonic candidate

Artifact: `Fruit Ninja 1.7.6 - FruitSpy VPS - Monotonic Clock Fix.apk`
SHA-256: `ee67e367f06d6cdbdb51ed5c5f9e892f7605439d3ff0876556bd99b856d01b21`
Implementation: injected `CLOCK_MONOTONIC` source plus abnormal-delta guard
Status: **ARMv7 multiplayer smoke passed; x86 normal-speed and lifecycle smoke passed; final Unified x86-to-S4 synchronization passed for three consecutive games by user report**

Static verification:

- Native payloads reproduce byte-for-byte from the checked-in ARM and x86 assembly.
- All three timer calls branch to their injected monotonic helpers.
- All three delta-conversion paths pass through the 250,000-microsecond guard.
- ELF load segments retain file/virtual alignment; virtual addresses and dynamic dependencies are unchanged.
- APK payload comparison changes only the three `libmortargame.so` entries, excluding regenerated signatures.
- Four-byte ZIP alignment passes.
- APK Signature Schemes v1, v2, and v3 pass with the FruitSpy signer certificate.

The exact hardened build subsequently completed direct and relayed multiplayer games through the `armeabi-v7a` runtime path on Android 4.4.2 and Android 13 physical devices. That proves the injected ARMv7 path executes without an observed multiplayer regression; it does not substitute for lifecycle-gap testing on those devices.

## Hardened x86 and Unified APK manual results

The user reported slow motion with the endpoint-only baseline on the Android 9 Pixel_3a AVD and normal speed with the hardened monotonic candidate above. Its x86 `libmortargame.so` was verified byte-identical to the Unified APK's x86 library.

The exact `Fruit Ninja 1.7.6 - FruitSpy Unified.apk` was then installed on the Genymotion Pixel_3 emulator (`vbox86p`, x86). The installed APK SHA-256 was verified as `bfdc8d436dc47ab362271b5c80e07bdd3983ea0e56cf01e5f985b8f64cba1996`.

User-confirmed results for the x86 testing:

- **Normal game speed: passed qualitatively, with very strong user confidence.** Exact round durations were not measured.
- **Background/resume: passed.** Returning to the app resumed gameplay from its last state.
- **Sleep/wake: passed.** Waking the emulated device resumed gameplay from its last state.
- **Final Unified x86-to-S4 multiplayer: passed.** The user reported three consecutive completed games with no desynchronization or disconnects.

These are manual behavior reports, not instrumented proof of branch execution or measured interruption durations. No separate audio or post-resume new-round result was supplied.

### Graphics/crash exclusion

Resume color corruption also occurred with the untouched original APK on the same Android Emulator 34.1.20 AVD using Intel hardware rendering. Genymotion did not eliminate the graphical or crashing issues. A previous Windows crash record identified `ig9icd64.dll` as the faulting module in the host QEMU process; later emulator logs contained `bad color buffer handle` errors.

By the user's scope decision, these issues are treated as an assumed Intel-driver problem outside this project. The precise cause of every crash is not proven, and this exclusion is not a claim of crash-free operation.

### Revised speed acceptance

The user waived exact game-length timer tests. Normal pace is accepted qualitatively. The final Unified x86-to-real-device synchronization gate is now satisfied by the three-game result below; this acceptance does not rely on earlier prototype synchronization or ARMv7-only matches.
 
### 2026-09-08 final Unified three-game result

Both devices were clean-installed with `Fruit Ninja 1.7.6 - FruitSpy Unified.apk`, SHA-256 `bfdc8d436dc47ab362271b5c80e07bdd3983ea0e56cf01e5f985b8f64cba1996`, and installed hashes were verified:

- Pixel_3a Android 9 AVD, using the x86 native library and Android Emulator 34.1.20 with Intel hardware rendering.
- Samsung Galaxy S4 SGH-I337M, Android 4.4.2, ARMv7.

The user initially reported stalled matchmaking, then confirmed that the emulator and S4 proceeded to play three games in a row without desynchronization or disconnects. Record this as a completed manual multiplayer pass, not a terminal matchmaking failure.

The saved stall-window captures showed successful discovery, staging-room membership, and exchanged ready messages, but no NAT-negotiation or gameplay handoff in that window. They do not establish why matchmaking was delayed or the transport used by the subsequently reported games. The APK targeted the VPS; direct versus relayed gameplay and both hosting directions were not confirmed for this three-game result.

Exact durations were not measured. This bounded three-game pass is not a general reliability or crash-free guarantee; the earlier S4 native socket crash remains a separate unresolved observation, outside the emulator graphics exclusion.

## 2026-09-09 S20 ARMv7 refresh-rate and lifecycle results

On the current patched build using `armeabi-v7a`, the user reports:

- Setting the S20 to 120 Hz causes no game-speed issues.
- Backgrounding the app and returning causes no timing issues.
- Locking the device and returning causes no timing issues.

These qualify the reported high-refresh and lifecycle behavior on this physical ARMv7 runtime. They are qualitative user observations, not measured timing or instrumented proof of the 250 ms guard branch. Interruption durations, a separate new round after each interruption, and severe-load behavior were not reported.

## 2026-09-09 accelerated native clock-boundary verification

The current signed nickname/clock combined APK, `nickname-collision-fix/build/FruitSpy-Nickname-Fix-Preferred.apk` (SHA-256 `5dc4a4f96a1efcc3cb7de8597124cb8b62fc747c5c1fe7cc0548e47ac4748eb6`), passed accelerated timer-path execution in Unicorn 2.1.4 for `armeabi`, `armeabi-v7a`, and `x86`.

Twelve scenarios per ABI at load bases `0x10000000` and `0x38000000` produced **72 passing scenario executions and 222 native frame updates**, in approximately 0.53 seconds. Coverage includes normal frames, unsigned low-word wrap with subsequent frames, signed-boundary crossing, landing exactly on zero, 250,000/250,001-microsecond guard boundaries both with and without wrap, a 30-second gap, an unsigned-maximum gap, zero elapsed time, and wrap at a later monotonic-clock epoch. Disabling the guard cutoff in disposable emulated memory caused the expected assertion mismatch in each ABI; APK files were not modified.

The harness mapped the actual APK libraries, initialized the timer's active flag and previous timestamp, and supplied successful `clock_gettime(CLOCK_MONOTONIC)` results at the syscall boundary. All clock arithmetic, subtraction, guard, float conversion/division, frame-delta storage and subsequent previous-clock updates executed as native library instructions. A 999-nanosecond remainder also exercised truncation to microseconds. Stored deltas matched single-precision expectations exactly.

This qualifies the wrap arithmetic and guard boundaries without waiting 72 minutes. The low-word boundary follows the system monotonic clock, not time since app launch; a short session can cross it. This does not qualify syscall-failure handling, first-call static initialization, actual game rendering/update behavior, or long-session resource stability. No Android emulator or phone UI was used. Full per-frame results, library hashes, addresses and negative controls are retained privately in `nickname-collision-fix/build/clock-boundaries-20260909T051152Z/results.json`.

## Coverage boundaries and report-driven follow-up

The accepted patch does not require additional endurance testing before release:

- Physical `armeabi` and `armeabi-v7a` execution, both hosting directions, mixed-ABI gameplay, and repeated matches are recorded in `../nickname-collision-fix/FINDINGS.md`; those combined builds include the clock correction. The integrated builder reproduced the same signed APK byte-for-byte.
- Background/resume and sleep/wake passed by user report on x86, physical `armeabi`, and S20 `armeabi-v7a`. S20 120 Hz game speed also passed. A separate new-round-after-each-interruption result and severe-load device behavior are not independently recorded.
- Low-word wrap and guard boundaries passed accelerated native execution in all three ABI libraries. Additional long-session timing or stability investigations are deferred until user reports warrant them; no 72-minute physical session is required for acceptance.
- Actual ARMv5/ARMv6 hardware and syscall-failure handling remain unqualified. The unchecked `clock_gettime` error path remains a documented hardening opportunity, not an observed device failure or an implemented fix.

The earlier prototype's wall-clock-adjustment risk is structurally removed by `CLOCK_MONOTONIC`; changing civil time is not a required test. High-confidence functional acceptance does not imply exhaustive testing of every device, error path or session duration.
