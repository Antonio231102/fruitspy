# Fruit Ninja 1.7.6 Android slow-motion investigation

## Conclusion

The native game loop uses C `clock()` as if it were elapsed wall time. On Android, `clock()` measures process CPU time. Time spent blocked on display pacing, descheduled, or waiting for another thread is omitted. The game therefore advances simulation by less time than the player experiences, producing uniform slow motion.

Confidence in this root cause is **high**. The relevant value is not merely an FPS counter: the native step path computes `(clock() - previous) / 1,000,000`, stores that float as frame delta, and passes it into the game update object before rendering.

The current binary fix is **accepted as fully functional, with high confidence**, by the project owner on 2026-09-09. The initial wall-clock prototype established the diagnosis; the hardened implementation uses injected `CLOCK_MONOTONIC` code and guards abnormal frame gaps. Physical ARM gameplay, lifecycle and high-refresh checks, historical x86 runtime results, and accelerated native clock-boundary execution support this acceptance. See `VALIDATION.md` for exact artifacts, results and limitations. Further long-session investigation will be driven by user reports, not additional pre-release endurance testing.

## Scope and source identity

Analyzed APK:

- Package: `com.halfbrick.fruitninja`
- Version: `1.7.6` (`versionCode` 1706)
- Minimum SDK: 7
- No explicit `targetSdkVersion` is present; Android therefore treats the target as the minimum SDK.
- Native ABIs: `armeabi`, `armeabi-v7a`, and `x86`
- Native engine: one `libmortargame.so` per ABI
- Original APK SHA-256: `5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650`

The initial investigation used a separately copied FruitSpy VPS APK that already contained the restored online-service addresses. These historical validation artifacts were:

- Input SHA-256: `74e96335b643ee79704b89a3f488c9c70274bf0bd69911dcee758c4bb24e06f7`
- Runtime-validated wall-clock prototype SHA-256: `0aca4a049da2940ce46b0b2523e0d65439f02912e8729f05ac4efec5ee53fc22`
- Statically verified hardened candidate SHA-256: `ee67e367f06d6cdbdb51ed5c5f9e892f7605439d3ff0876556bd99b856d01b21`
- Signer SHA-256: `95e24a3785f3a47bd19a8acb3920fe5d0a25e70b42169df262d6a0f15947c448`

Both candidates use the same signer as the existing FruitSpy VPS APK, permitting an update install without changing package identity.

The current release-facing pipeline is `../patcher/patch_apk.py`. It starts from the allowlisted original APK and applies the hardened clock correction, nickname-collision matchmaking fix, and neutral user-configured endpoint transformation in that order. It uses a persistent per-user signing identity rather than the historical development signer. Ordinary builds write only a server-named APK; `--report PATH` explicitly requests a diagnostic JSON build report.

## Runtime validation

On 2026-09-04, the user manually exercised the signed build on the Android 9 `emulator-5554` device and the Android 4.4.2 Galaxy S4 `jfltecan` device. The Android 9 environment normally exhibited slow motion; with this patch it ran at normal speed and remained fully synchronized with the Galaxy S4. The pair successfully played using the FruitSpy VPS services and LAN peer path without observed desynchronization.

This confirms the diagnosis and the `x86` correction on the affected Android 9 environment. It also provides an ARMv7 regression check on Android 4.4.2. The result covers one reported cross-version session, not repeated-match reliability or the lifecycle/time-adjustment edge cases listed below. See `VALIDATION.md` for the exact coverage.


## Evidence chain

### Java render path

`MortarGameView` extends Android's `GLSurfaceView`. It installs a renderer without changing the render mode, so Android uses continuous rendering.

Every `MortarGameView.Renderer.onDrawFrame()` call performs:

1. `MortarGameActivity.waitForPendingEvents()`
2. `GameManager.Render()`
3. `NativeGameLib.step()`

`waitForPendingEvents()` posts a semaphore-release callback to a UI-thread `Handler` and waits up to 100 ms. This can add wall time to a frame, but waiting time is not counted by a process CPU clock.

Android's documented `GLSurfaceView` queue-stuffing model also blocks `eglSwapBuffers()` when the buffer queue is full. That wait tracks VSYNC and is likewise excluded from process CPU time.

### Native frame timer

The `armeabi-v7a` build contains the frame timer at virtual/file address `0x001e31f8`:

```text
0x001e3204  call clock@plt
0x001e3228  load previous clock value
0x001e322c  subtract previous from current
0x001e3230  convert signed integer delta to float
0x001e3234  load 1,000,000.0
0x001e3238  divide delta by 1,000,000.0
0x001e3240  store resulting frame delta
0x001e3244  store current clock value as previous
```

The first invocation initializes the previous value to `current - 16666`, explicitly producing approximately 1/60 second for the first frame.

The native step path at `0x001e4934` calls this timer and then passes the resulting float to the game update object's virtual method before rendering. This establishes that the value controls simulation advancement.

Equivalent timer implementations and hardened branch sites exist in all packaged ABIs:

| ABI | Timer call | Monotonic helper | Delta conversion | Guard helper |
|---|---:|---:|---:|---:|
| `armeabi` | `0x001e4518` | `0x003a2ce4` | `0x001e4544` | `0x003a2d30` |
| `armeabi-v7a` | `0x001e3204` | `0x003a592c` | `0x001e3230` | `0x003a5978` |
| `x86` | `0x001d4ed6` | `0x003a639c` | `0x001d4ef4` | `0x003a63cc` |

The injected helpers invoke Linux `clock_gettime(CLOCK_MONOTONIC)` directly and return the low 32 bits of `seconds * 1,000,000 + nanoseconds / 1,000`. This preserves the timer's existing 1,000,000-unit divisor and modular 32-bit arithmetic without adding imports or shared-library dependencies.

### Why newer systems expose the bug

Android/Bionic's `clock()` is a process CPU-time clock. The Android 5.0 implementation explicitly calls `clock_gettime(CLOCK_PROCESS_CPUTIME_ID, ...)`. The preceding implementation used `times()` and also returned user-plus-system process CPU ticks. Android 5.0 improved precision; it did not redefine `clock()` as CPU time.

The likely version/hardware interaction is:

- On old, slow hardware, the process spent much of each frame actively using the CPU, so CPU time happened to approximate elapsed time.
- Modern devices finish game work earlier and spend more of the display interval blocked by `eglSwapBuffers()`, scheduler waits, or the UI-thread semaphore.
- Those waits do not advance `clock()`, so the simulation receives a delta smaller than the actual display interval.
- The Android 5.0 high-resolution implementation may make the faulty measurement more consistently visible, but static evidence does not prove that Lollipop is the exact first affected release.

This mechanism matches a documented Android NDK slow-motion failure: changing a game delta clock from `CLOCK_PROCESS_CPUTIME_ID` to `CLOCK_MONOTONIC` fixed the reported issue.

## Fix implementation

### Hardened monotonic patch

The current candidate redirects only the frame timer. It does not patch unrelated `clock()` uses such as random seeding or profiling.

Each ABI receives two small helpers:

1. A monotonic microsecond source implemented with the architecture's Linux `clock_gettime` syscall.
2. A guard immediately before integer-to-float conversion. Unsigned deltas through 250,000 microseconds pass unchanged; larger deltas are replaced with 16,667 microseconds, matching the engine's existing first-frame initialization.

The low 32-bit microsecond clock wraps approximately every 71.6 minutes. Consecutive unsigned subtraction remains correct across an ordinary wrap. The abnormal-delta guard also prevents background/resume gaps, screen suspension gaps, or severe scheduler discontinuities from advancing the simulation in one large step.

Implementation properties:

- 104-byte ARM payload and 73-byte x86 payload
- Direct `CLOCK_MONOTONIC` syscall; no adjustable wall-clock dependency
- No added ELF imports, relocations, or `DT_NEEDED` entries
- Existing ELF virtual addresses remain unchanged
- ARMv7 and x86 later file offsets shift by one 4 KiB page; ARMv5 uses an existing file gap
- Exact input, payload, and output hashes fail closed on unsupported binaries
- Deterministic payload reproduction from the checked-in assembly

The integrated main patcher produced an aligned, signed APK byte-identical to the qualified build. Recorded runtime evidence and accelerated execution of all three native timer paths support the owner's high-confidence functional acceptance. Untested hardware and error paths remain documented; no new timing-patch change is scheduled solely for speculative long-session concerns.

### Initial wall-clock prototype

The first candidate redirected the timer to an existing `gettimeofday()` microsecond helper. Its minimal three- to five-byte call-site changes established the diagnosis and passed the reported Android 9 x86 / Android 4.4.2 ARMv7 cross-version session. It remains useful as evidence, but adjustable wall time and unbounded resume deltas make it inferior to the hardened candidate.

### Removing the Java semaphore wait

Making `waitForPendingEvents()` a no-op could increase frame throughput, but it does not correct the timer's semantics and may introduce lifecycle/UI races. It is not recommended as the first fix.

### Fixed simulation multiplier

Multiplying the measured delta by a device-specific constant would hide the symptom and vary with CPU/GPU load. It is not viable.

## Compatibility limits beyond slow motion

This patch does not make the APK universal on every current Android device:

- The APK contains no `arm64-v8a` library. ARM64 devices require 32-bit ARM compatibility to run it; a 64-bit-only device cannot load it.
- Android 14 blocks normal installation of apps targeting below API 23. Because this APK effectively targets API 7, testing requires `adb install --bypass-low-target-sdk-block ...` unless it was already installed before the OS upgrade.
- Storage, permission, audio, and vendor graphics behavior are separate compatibility surfaces.

## External references

- AOSP Android 5.0 `clock()` implementation: https://android.googlesource.com/platform/bionic/+/android-5.0.0_r1/libc/bionic/clock.cpp
- AOSP change from `times()` to `CLOCK_PROCESS_CPUTIME_ID`: https://android.googlesource.com/platform/bionic/+/02542b3bbd6370e904e6bccba1032185b9f0eb75%5E1..02542b3bbd6370e904e6bccba1032185b9f0eb75/
- Android game-loop and `eglSwapBuffers()` pacing documentation: https://developer.android.com/games/develop/gameloops
- Analogous NDK slow-motion diagnosis and fix: https://groups.google.com/g/android-ndk/c/dgrqhgHWbHI
- Android 14 minimum installable target API behavior: https://developer.android.com/about/versions/14/behavior-changes-all#minimum-target-api-level
