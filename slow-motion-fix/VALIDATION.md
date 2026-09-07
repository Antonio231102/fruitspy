# Runtime validation record

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
Status: **ARMv7 multiplayer smoke passed; quantitative timing qualification pending**

Static verification:

- Native payloads reproduce byte-for-byte from the checked-in ARM and x86 assembly.
- All three timer calls branch to their injected monotonic helpers.
- All three delta-conversion paths pass through the 250,000-microsecond guard.
- ELF load segments retain file/virtual alignment; virtual addresses and dynamic dependencies are unchanged.
- APK payload comparison changes only the three `libmortargame.so` entries, excluding regenerated signatures.
- Four-byte ZIP alignment passes.
- APK Signature Schemes v1, v2, and v3 pass with the FruitSpy signer certificate.

The exact hardened build subsequently completed direct and relayed multiplayer games through the `armeabi-v7a` runtime path on Android 4.4.2 and Android 13 physical devices. That proves the injected ARMv7 path executes without an observed multiplayer regression; it does not substitute for measured slow-motion or lifecycle-gap testing.

## Remaining runtime coverage

The hardened candidate still requires:

- Quantitative normal-speed confirmation on the known affected Android 9 x86 environment
- Runtime qualification of the hardened x86 direct-syscall payload
- Runtime qualification of the legacy `armeabi` payload
- Background/resume and screen lock/unlock confirmation
- A foreground session crossing the approximately 71.6-minute low-word wrap interval
- High-refresh-rate and modern physical ARM-device coverage where available
- Repeated matches sufficient to support a reliability claim

The earlier prototype's wall-clock-adjustment risk is structurally removed by `CLOCK_MONOTONIC`; it is no longer a required clock-change test. Runtime testing remains necessary to qualify the injected syscall and guard paths across every advertised ABI and lifecycle boundary.
