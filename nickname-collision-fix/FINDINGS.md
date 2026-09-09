# Accepted-nickname synchronization findings

## Observed failure

An emulator on Wi-Fi and an S20 on cellular reached a two-player PeerChat staging room but did not launch the game. The preserved exchange contained:

- Rejection of the emulator's original nickname with numeric `433` (already in use).
- Successful registration as `nick23045.80`.
- The S20 identified as staging host `nick786.94`.
- Both current participants publishing ready flags; only two players in the staging room.
- Older unsuffixed sessions still present in the title room.
- No host launch command and no NAT-negotiation packets in the 110-packet final emulator capture.

The emulator's last automatch callback state was `PEERReady` (4), while transport startup waits for `PEERComplete` (5). A provider-memory snapshot retained `nick23045` at offset `0xa4`, despite the accepted connection name being `nick23045.80`. Later SDK fields showed disconnect/cleanup; those later fields are not evidence of a still-active SDK connection.

Disassembly of the deployed ARMv7 and x86 libraries, and subsequent independent mapping of legacy ARM, establishes the cause:

1. The nickname-error callback formats `%s.%i` into a temporary stack buffer.
2. It passes that candidate to `peerRetryWithNick` without updating the game provider's cached nickname.
3. The original successful-connect callback only publishes completion flags. It does not reconcile the cache with the SDK's accepted nickname.
4. The game passes its cached name to `peerIsPlayerHost` for the staging room.
5. That name can resolve to an old or different player, or no staging player. A renamed host fails its own host check and does not call `peerStartGame`.

This is a client identity defect, not proof that the emulator's private NAT address prevents transport. No claim is made about post-launch connectivity from the stalled capture.

### Why a common nickname is safe after correction

PeerChat enforces case-insensitive nickname uniqueness **among active connections**, not permanent account ownership. Two clients may request the same name; one must retry under a suffix. Synchronizing each provider to its own accepted SDK nickname makes both identities distinct for host selection. It neither evicts the first player nor merges their state.

The native retry limit and random suffix space are unchanged. The correction does not guarantee registration under an exhausted collision space.

### Server contribution, intentionally not changed

`server/fruitspy/peerchat.py` retains registered connections while awaiting `reader.read` without an application-level heartbeat deadline. Network changes can therefore leave original names occupied until TCP detects failure. Better dead-session detection would reduce that trigger but cannot replace client correctness for legitimate collisions. This subproject makes no server changes.

### Follow-up defect: accepted identity became the next preference

The callback-only patch fixed host recognition, but the same provider cache at `0xa4` is also passed to the next `peerConnect`. After a collision, that request therefore reused the accepted suffix even while the configured nickname field still contained the original name. A free server does not shorten a nickname the client explicitly requests.

The configured nickname has separate global storage, populated by the game's settings/load path and limited to 16 characters. The revision reads that storage immediately before a new connection and calls the original provider nickname setter. It retains accepted-name synchronization at successful registration. Thus the provider cache transitions from configured request to accepted session identity and back to the configured request on the next connection; saved settings are never overwritten.

## Implementation

### Connection request and success hooks

The anonymous `peerConnect` call is redirected to a request helper. It preserves the original register/stack arguments, calls the game's existing nickname setter with the configured nickname and provider, then tail-calls the original `peerConnect`. The setter leaves the current candidate unchanged if the configured string is empty. This hook does not run during `peerRetryWithNick` or active gameplay.

The successful-connect callback supplied at that same call remains redirected to the accepted-name helper. The original callback is left intact and tail-called after synchronization.

The helper:

1. Skips synchronization on a failed connection or null peer/provider.
2. Calls the library's existing `peerGetNickA` to obtain the accepted identity; handles its null return without dereferencing it.
3. Copies through the first NUL, with a maximum of 63 bytes plus a terminator, into the 64-byte provider cache beginning at `0xa4`.
4. Restores the callback arguments and callee-saved registers.
5. Tail-calls the original callback, so the game cannot observe success before the identity is synchronized.

No heap allocations, string formatting, networking, or per-frame work are added. Failed connection publication is unchanged. Each new connection requests the current configured nickname; collision retries retain the original game's behavior. On success, the final accepted name is copied rather than an unaccepted retry candidate. An intentional suffix in the configured nickname is preserved exactly.

This corrects the shared cached identity rather than bypassing only the failing host test. It does not add a new mid-session renaming feature.

### ABI mapping

All addresses below are relative to the ELF load base. Hook offsets are file offsets; the hook sites remain in the unchanged first executable segment.

| ABI | Callback-reference hook | Address base for reference | Original connected callback | `peerGetNickA` | Helper address | Helper bytes |
|---|---:|---:|---:|---:|---:|---:|
| `armeabi` | `0x19d964` | `0x19d6f4` | `0x19c67c` | `0x1a95ec` | `0x3a2d4c` | 92 |
| `armeabi-v7a` | `0x19d934` | `0x19d6c4` | `0x19c64c` | `0x1a95bc` | `0x3a5994` | 92 |
| `x86` | `0x188d19` | `0x3b22c8` | `0x1875a0` | `0x195500` | `0x3a63e8` | 79 |

The ARM callback hook replaces a four-byte PC-relative literal; the x86 callback hook replaces the six-byte `lea` computing its address. The additional request hook replaces the ARM `bl` or x86 `call` at the anonymous connection site. Neither hook changes the nickname-error callback, callback ABI, or original completion routine.

| ABI | Request-call hook | Configured nickname storage | Native nickname setter | Original `peerConnect` | Request helper | Total payload bytes |
|---|---:|---:|---:|---:|---:|---:|
| `armeabi` | `0x19d704` | `0x3b1cc8` | `0x19c820` | `0x1ac338` | `0x3a2da8` | 132 |
| `armeabi-v7a` | `0x19d6d4` | `0x3b55f8` | `0x19c7f0` | `0x1ac308` | `0x3a59f0` | 132 |
| `x86` | `0x188d72` | `0x3b6fa4` | `0x187910` | `0x198690` | `0x3a6438` | 123 |

The original game host predicates exercised by the regression harness are `0x19c900` (`armeabi`), `0x19c8d0` (`armeabi-v7a`), and `0x187b10` (`x86`).

### Payload placement and input safety

The standalone builder starts from the same fully allowlisted clean APK as the shared patcher. It applies the shared clock and endpoint transformations first. The nickname helper follows the existing clock payload in the executable load segment; x86 adds three zero alignment bytes before its helper.

The existing ELF injector performs placement, zero-gap, segment-overlap, alignment, and file-offset checks. The nickname stage supplies an updated placement descriptor without changing the clock payload. Exact original hook bytes and both payload hashes are required. Duplicate patching and altered hook instructions are rejected.

The shared APK builder/signing routines retain responsibility for removing obsolete signatures, selecting the existing signing identity, alignment, and signature verification. No proprietary game library or signing key is included in this subproject.

## Verification performed

### Native execution: all three ABIs

The reproducible harness in `tools/verify_native.py` executes actual APK machine code under Unicorn. Its player table uses the SDK's original hash-table and array-lookup implementations, a one-bucket fixture hash callback, and libc `strncpy`/`strcasecmp` substitutes. Both helpers, the native nickname setter/string routines, host predicate, accepted-name getter, and original completion callback are not mocked. An SDK connection-boundary observer records the actual requested nickname without performing network I/O.

For each ABI, at load bases `0x10000000` and `0x38000000`:

- The uncorrected callback reproduces the renamed-host failure.
- The previous callback-only patch reproduces a sticky `Alex.42` request despite configured name `Alex`; this was also reproduced from the actual earlier signed APK's three libraries.
- The corrected renamed host recognizes itself even while its original nickname belongs to an older title-room player.
- A renamed joiner does not misidentify itself as the live host that owns its requested original name.
- An ordinary unsuffixed host remains a host.
- A 63-byte SDK nickname remains bounded and usable for host lookup.
- A surviving provider requests `Alex` before successive accepted names `Alex.42`, `Alex.7`, and finally free `Alex`, retaining correct host recognition each time.
- Editing the configured name to `Beth.12` requests that exact value, not the old alias or a suffix-stripped version; a subsequent collision does not overwrite the setting.
- An empty configured name preserves the game's generated candidate.
- Failed connection, null provider, null peer, and unavailable getter paths preserve the cached identity and original completion result.
- Native calls preserve callee-saved registers and stack balance; callback writes are observed to ensure success is published only after synchronization. Adjacent provider bytes remain untouched.

Observed output:

```text
armeabi: host stall and sticky reconnect reproduced; 22 corrected native scenarios passed
armeabi-v7a: host stall and sticky reconnect reproduced; 22 corrected native scenarios passed
x86: host stall and sticky reconnect reproduced; 22 corrected native scenarios passed
```

This is native function execution with constructed protocol state, not a live network/concurrency test or a completed Android match.

### Build and artifact checks

- Rebuilding the assembly reproduces all three checked-in payload hashes.
- Compared with the previous signed APK, only the three `libmortargame.so` entries and v1 signature metadata change; all ZIP entry names/order and other contents remain unchanged.
- Altered bytes at either hook and duplicate application are rejected for every ABI.
- Final library digests match the generated manifest.
- The revised signed `FruitSpy-Nickname-Fix-Preferred.apk` targeting `217.154.27.122` has APK SHA-256:

```text
5dc4a4f96a1efcc3cb7de8597124cb8b62fc747c5c1fe7cc0548e47ac4748eb6
```

- The reused signer certificate SHA-256 is:

```text
7e9ad31c19d7ee447c8e1a58bc7ca4be51acf4489f55cf2258124c45d7213417
```

- v1, v2, and v3 verification passed; v4 is intentionally disabled by the shared signer. `zipalign` verification passed.
- Native smoke execution from the revised signed APK's actual three libraries confirms successful renamed hosting and a subsequent original-name request/acceptance.
- The revised APK was update-installed and hash-verified on the S20, S4, and emulator without uninstalling or clearing data. The S20 and S4 both use `armeabi-v7a`; the emulator used `x86`.

### Historical live qualification: callback-only build

On 2026-09-08, the earlier APK with SHA-256 `917c079ed8e8e01b4f481f96ade73e23fb2afe5a5eab098fac819da6962d21c1` completed five matches between the Android x86 emulator on Wi-Fi and the Galaxy S20 running `armeabi-v7a` on cellular. Installed hashes matched that earlier artifact, not the revised build above. The user exclusively performed all game UI actions; completion and visible symptoms below are user reports.

Plain PeerChat fixture connections initially reserved `nick23045` and `nick786` without joining any game room. A later fixture reserved the S20's then-current accepted nickname, `nick786.59`, to force another host-side rename.

| Match | Host and accepted nickname | Joiner and accepted nickname | Qualification |
|---|---|---|---|
| 1 | x86: `nick23045.26` | S20: `nick786.59` | Emulator capture contains `433`, retry, acceptance, and host `GML`; match completed without issue. |
| 2 | S20: `nick786.59` | x86: `nick23045.26` | Reversed hosting roles and reused accepted identities on reconnect; match completed successfully. |
| 3 | S20: `nick786.59.48` | x86: `nick23045.26` | S20's previous name was held by the fixture; its newly suffixed identity sent `GML`. User reported no slowdown, desynchronization, or disconnects. |
| 4 | S20: `nick786.38` | x86: `nick23045.3` | Another successful collision-recovery match, not the intended no-collision control: emulator requested `nick23045`, received `433`, and retried. |
| 5 | S20: `nick786.38` | x86: `nick23045.3` | Clean control after verified fixture release: emulator submitted `nick23045.3` and received `001` without `433` or retry; S20 retained its previous accepted identity and launched. Match completed correctly. |

The first reversed-role search expired before an opponent joined; a new search produced match 2. This was an empty-search timeout, not a reproduced renamed-host stall.

All five captures contain the host launch command and corresponding NAT-negotiation traffic. Server journal sessions `7df5a3a6`, `73439c23`, `72b87719`, `282ea01b`, and `5912d2b3` record successful reports from both peers, relay establishment, and gameplay forwarding. Observed relay metrics reported zero drops; this is not a measurement of all network packet loss.

After match 1, a read-only emulator memory snapshot found `nick23045.26` in the provider cache at offset `0xa4`, matching the accepted nickname in the capture. Its SDK peer pointer was already null after cleanup, so this is not a simultaneous live SDK/cache comparison.

### Fixture cleanup and suffix interpretation

The local reservation helpers exited before match 4, but three stale server-side TCP connections continued to own the reserved names. Match 4's fresh `433` exposed that failed cleanup. With both game clients disconnected and only those fixture sessions remaining, the test service was restarted. Each reserved name was then accepted and released in two probe rounds, with server EOF observed after `QUIT`; a socket check confirmed no established PeerChat connections remained before match 5. No server code was changed.

A displayed suffix does not establish that the current registration collided. Match 5 submitted an already suffixed name and was accepted directly, unlike match 4's original-name rejection. However, this was sticky provider state: the callback-only patch never reloaded the configured nickname before connecting. The original native harness supplied accepted identities directly and did not prove original-name request restoration. The revised harness now exercises that request boundary with a surviving provider.

### Physical live qualification: preferred-name restoration revision

On 2026-09-09, the revised signed APK identified above completed four matches between the Galaxy S20 on cellular and Galaxy S4 SGH-I337M on Wi-Fi. Both installed APK hashes matched the revised artifact; both phones execute `armeabi-v7a`. The user exclusively operated all game UI and reported successful completion and the expected visible nicknames for every match.

| Match | Host | Joiner | Observed result | NATNeg session |
|---|---|---|---|---|
| 1 | S20: `nick786.74` | S4: `nick3552.75` | Forced collision; S4 requested `nick3552`, received `433`, retried and was accepted; S20 sent host launch. | `13ad9aaa` |
| 2 | S20: `nick786` | S4: `nick3552` | Reservations released; S4 requested its original name once and received `001` without `433`; S20 launched with its original name. | `131048ec` |
| 3 | S4: `nick3552.74` | S20: `nick786.34` | Reservations restored; S4 requested its original name, received `433`, retried and was accepted, then sent host launch. | `457fcdb7` |
| 4 | S4: `nick3552` | S20: `nick786` | Reservations released; S4 requested its original name once and received `001` without `433`, then launched; S20 joined with its original name. | `0edfad59` |

The S20 game PID remained `22445` and the S4 PID remained `20146` throughout all four matches. Read-only S4 memory checks after the collision matches still found configured preference `nick3552`; the temporary accepted aliases did not overwrite it. These no-restart cycles verify preferred-name restoration with either physical phone hosting.

The reservation fixture ran over VPS loopback, held both original names without joining rooms, and released each with `QUIT` followed by server EOF. Successful acceptance/release probes and zero established PeerChat sockets preceded each free-name reconnect. The final fixture exited successfully; its remote script and the S4 capture binary/file were removed after evidence export. The FruitSpy service remained active and no PeerChat connections remained. No server code or game preferences were changed.

The final S4 capture contains 1,419 complete packets with zero kernel drops. It directly captures all four S4 registrations and the S20's forwarded room/launch or negotiation messages, not the S20's direct registration handshake. Every server session records successful reports from both peers, relay establishment, and gameplay forwarding; observed relay metrics reported zero drops, not a measurement of all network packet loss.

### Physical live qualification: forced legacy `armeabi` binary

On 2026-09-09, a test-only APK was derived from the revised three-ABI artifact by removing `lib/armeabi-v7a/libmortargame.so`, `lib/x86/libmortargame.so`, and old signature entries, then aligning and signing through the existing shared helpers. Every retained non-signature entry is byte-identical to the source APK. No native code or release artifact was changed.

- Test APK SHA-256: `2eaba89dac58a9aaa7477fd8781ee82ae9e073fce039963ac06931dc72ddb11f`.
- Sole native entry: `lib/armeabi/libmortargame.so`, SHA-256 `b55c117687bd728b8ae03acb560512eb64a718ba056cd0e74197c48101c0f118`, matching the revised release manifest.
- Existing certificate reused; v1/v2/v3 signatures and alignment verified. Both phones were fresh-installed with user authorization, without backup.
- Installed APK and extracted library hashes matched on both phones. S4 root process maps directly show the installed legacy library. S20 reports `primaryCpuAbi=armeabi`; its game ran with only that ABI installed, but Android denied direct process-map access. S20 loaded-library identity is therefore supported by package selection and installed-file evidence, not a live mapping snapshot.

The S20 remained on cellular and the S4 on Wi-Fi. The user exclusively operated the game UI and reported all four matches completed correctly with the nicknames below.

| Match | Host | Joiner | Result | NATNeg session |
|---|---|---|---|---|
| 1 | S20: `nick221.86` | S4: `nick27088.97` | Forced collision; S4 original-name rejection, retry and acceptance captured; renamed S20 sent host launch. | `0aa75048` |
| 2 | S20: `nick221` | S4: `nick27088` | Names released; S4 original name accepted without rejection or retry; S20 launched with its original name. | `5e9ca7c6` |
| 3 | S4: `nick27088.84` | S20: `nick221.82` | Forced collision; S4 original-name rejection, retry, acceptance and host launch captured. | `3b9fa255` |
| 4 | S4: `nick27088` | S20: `nick221` | Names released; S4 original name accepted without rejection or retry, then host launch; S20 joined with its original name. | `2a27db12` |

The S20 PID remained `1785` and the S4 PID remained `27610` across all four matches. After both collision matches, read-only S4 checks at configured-name storage `load_base + 0x3b1cc8` still found `nick27088`. Both hosting roles therefore exercised legacy-binary collision recovery and original-name restoration without game restarts.

Three additional S4 registrations preceded match 3, accepting `nick27088.72`, `nick27088.3`, and `nick27088.87`. Each requested the original name and retried after `433`, but none contains a captured host launch. These are retained separately and are not counted as completed matches; no user-reported cause is assigned to them.

The VPS-loopback reservation fixture joined no rooms. Before each free-name reconnect, both names were released with server EOF, accepted and released by availability probes, and a socket check found zero established PeerChat connections. After the final match, the fixture exited successfully; captures and journal were saved, all three diagnostics stopped, and remote fixture/capture artifacts were removed with absence checks. FruitSpy remained active with zero PeerChat connections. The installed legacy-only test APKs were left on both phones.

The final S4 capture contains 1,588 complete packets, seven registration exchanges and zero kernel drops. All four completed-match sessions have both successful client reports, relay establishment and gameplay forwarding in the server journal. Capture visibility remains S4 direct registration plus S20 forwarded identities/messages; observed zero relay drops does not measure all network packet loss.

### Legacy gameplay and timing: user-reported checks

After the legacy-only series, the user reported that `armeabi` worked correctly on both the S4 and S20 in Classic, Zen and Arcade, without slowdowns. Backgrounding the app and putting either device to sleep did not trigger timing issues. These are user-operated observations, not automated frame-time measurements. The S20 was subsequently update-installed with the normal three-ABI APK, preserving app data, while the S4 retained the legacy-only APK.

### Mixed-ABI qualification: direct LAN gameplay

On 2026-09-09, the S20 running `armeabi-v7a` and S4 running `armeabi` completed four matches on the same Wi-Fi network. The S20 selected `primaryCpuAbi=armeabi-v7a`; its installed APK and extracted library hashes matched the normal revised artifact. The S4's extracted legacy library hash remained unchanged. All game UI actions and completion reports were user-only.

| Match | Host | Joiner | Registration result | Direct UDP endpoints |
|---|---|---|---|---|
| 1 | S20: `nick221.65` | S4: `nick27088.59` | S4 requested original name, received `433`, retried and was accepted; renamed S20 launched. | S4 `192.168.100.17:45446` ↔ S20 `192.168.100.9:6500` |
| 2 | S20: `nick221` | S4: `nick27088` | Originals free; S4 accepted without rejection or retry; original-name S20 launched. | S4 `192.168.100.17:48134` ↔ S20 `192.168.100.9:47357` |
| 3 | S4: `nick27088.73` | S20: `nick221.44` | S4 original-name rejection, retry, acceptance and host launch captured. | S20 `192.168.100.9:58464` ↔ S4 `192.168.100.17:6500` |
| 4 | S4: `nick27088` | S20: `nick221` | Originals free; S4 accepted without rejection or retry, then launched; S20 joined as original name. | S20 `192.168.100.9:46027` ↔ S4 `192.168.100.17:57782` |

Both game processes remained unchanged across the series: S20 PID `24370`, S4 PID `27610`. S4 configured-name storage still contained `nick27088` after match 3. Both ABI directions therefore passed collision recovery and no-restart preferred-name restoration.

Every match negotiated `gt lan` and a host `gs` port, with sustained bidirectional UDP captured between the phones' private addresses. The complete server journal records no relay establishment or forwarding during this series. Matchmaking still used FruitSpy; the gameplay path was direct LAN, not a cellular or relay test.

After match 1, both diagnostic SSH channels reset. The game processes remained running; a finite journal recovered the missing follower output. The orphaned loopback reservation helper was identified by its exact command line and terminated without restarting the service. A replacement fixture accepted both originals, released them with server EOF, and successfully accepted/released them again as availability probes. Zero established PeerChat sockets preceded match 2. The usual EOF/probe/zero-socket release also preceded match 4. This interruption is diagnostic infrastructure evidence, not an in-game interruption-recovery test.

The final capture contains 982 complete packets and four S4 registration exchanges, with zero kernel drops. After export, all diagnostics stopped and the remote fixture/capture artifacts were removed with absence checks. FruitSpy remained active with zero established PeerChat connections. S20 remains on the normal three-ABI APK; S4 remains on the legacy-only test APK.

### Manual same-name player collision

After the mixed-ABI series, the user set both phones' configured nicknames to `testing` and reported successful game initiation, with one device displaying `testing.58`. This directly exercises a collision between two real game clients rather than fixture-held names. Game initiation and the suffixed display passed by user observation; the renamed device, host role, completed-match outcome and subsequent reconnect behavior were not reported. No packet capture was taken for this manual test, and it is excluded from the completed qualification match count.

### Maximum-length nickname collision and restoration

On 2026-09-09, the user first configured both clients as `0123456789abcdef` and reported game initiation with one original name and one full `0123456789abcdef.94` alias. Registration preceded capture; the renamed device and completed-match outcome were not reported. The user then changed names and requested a controlled repeat. This initial attempt is kept separate and is not counted as a completed qualification match.

For the repeat, the S4 (`armeabi`) used `0123456789abcdef` and the S20 (`armeabi-v7a`) used `fedcba9876543210`. Both are exactly 16 characters. Distinct originals prevent a new inter-client collision after the fixture releases them. A VPS-loopback fixture reserved both names without joining rooms, and capture was running before either registration.

| Match | S4 host | S20 joiner | Captured result |
|---|---|---|---|
| Collision | `0123456789abcdef.25` | `fedcba9876543210.19` | S4 requested its exact original, received `433`, retried and was accepted, then launched. Both accepted identities preserve the full 16-character name plus suffix, 19 characters total. |
| Free-name reconnect | `0123456789abcdef` | `fedcba9876543210` | After verified fixture release, S4 requested its original once and received `001` without rejection or retry, then launched. S20's forwarded identity was its exact original. |

The user reported both matches completed successfully with the displayed names above. S20 PID `24370` and S4 PID `27610` remained unchanged; the configured nicknames were not edited between the controlled matches. Read-only S4 memory after the collision still contained exactly `0123456789abcdef` followed by NUL in configured-name storage. This verifies the legacy-host maximum-length boundary and no-restart exact-name restoration, not cold-start preference persistence.

Both matches negotiated LAN gameplay and exchanged bidirectional UDP directly: collision S20 `192.168.100.9:45193` ↔ S4 `192.168.100.17:37002`; reconnect S20 `192.168.100.9:59891` ↔ S4 `192.168.100.17:47558`. S4 registration was captured directly; S20 accepted names were observed through forwarded messages and user reports, not its direct handshake.

Both fixture names were released with server EOF and passed acceptance/release probes; zero PeerChat connections preceded the reconnect. The final capture contains 467 complete packets and two complete registration exchanges, with zero kernel drops. Diagnostics were stopped after export, remote fixture/capture artifacts removed with absence checks, and FruitSpy remained active with zero established PeerChat/browser connections.

### Intentional numeric suffix collision and persistence

On 2026-09-09, the S4 (`armeabi`, host) used `0123456789abc.25` and the S20 (`armeabi-v7a`, joiner) used `fedcba9876543.19`. Both configured names are exactly 16 characters; their numeric suffixes are intentional. Capture began before registration, with a VPS-loopback fixture reserving both exact originals without joining rooms.

| Match | S4 host | S20 joiner | Captured result |
|---|---|---|---|
| Collision | `0123456789abc.25.62` | `fedcba9876543.19.68` | S4 requested its exact original, received `433`, retried with the additional suffix and received `001`, then launched. Both intentional suffixes survived in the 19-character aliases. |
| No-restart reconnect | `0123456789abc.25` | `fedcba9876543.19` | After verified reservation release and availability probes, S4 requested its exact original once and received `001` without rejection or retry, then launched. Only temporary `.62`/`.68` disappeared. |
| Cold-restart reconnect | `0123456789abc.25` | `fedcba9876543.19` | After user-operated game restarts without nickname edits, S4 again requested its exact original once and received `001` without rejection or retry, then launched. S20's forwarded identity remained its exact configured name. |

The user reported all three matches completed successfully with the names above. S4 PID `27610` and S20 PID `24370` remained unchanged across collision and no-restart reconnect, then changed to `11973` and `1083`, respectively, for the cold-launch match. Read-only S4 configured-name memory contained exactly `0123456789abc.25` followed by NUL after collision, reconnect and cold restart. This qualifies intentional-suffix preservation and cold-launch persistence for this pairing; it is not a direct S20 preference-memory inspection.

All three matches negotiated LAN gameplay and exchanged bidirectional direct UDP. S20/S4 port pairs were `41647`/`41190` for collision, `37666`/`41947` for reconnect, and `34586`/`6500` after restart. S4 registration was captured directly; S20 accepted names were observed through forwarded messages and user reports, not its direct handshake.

The final capture contains 600 complete packets and three registration exchanges, with zero kernel drops. The server journal contained no errors. Reservations were released and probed free before reconnect; no PeerChat connections remained before the cold-launch match. Diagnostics were stopped, remote fixture/capture artifacts removed with absence verified, and FruitSpy remained active with zero established PeerChat/browser connections. Installed APKs were unchanged.

### Remaining runtime boundaries

The revised S20/S4 physical qualification covers 17 completed matches: four `armeabi-v7a` relay matches, four forced-`armeabi` relay matches, four mixed-ABI direct LAN matches, two maximum-length mixed-ABI LAN matches, and three intentional-suffix mixed-ABI LAN matches. The first three series exercise both hosting roles; the maximum-length and intentional-suffix series exercise the legacy S4 host. Every series includes no-restart original-name restoration; the intentional-suffix series additionally verifies cold-launch persistence. These results do not qualify execution on an actual ARMv5/ARMv6 CPU.

Further x86 device testing on this workstation is discontinued at the user's direction. The user reported that known emulator instability, unrelated to this project, caused a PC softlock. The interrupted revised-build attempt supplies no completed-match result. The release decision relies on existing native checks and historical live evidence, with remaining x86 issues to be handled through public-release issue tracking and user feedback.

No actual ARMv5/ARMv6 hardware was tested, and the user excludes those tests as a release gate; hardware-specific issues will be addressed through user feedback and the issue tracker. Direct LAN gameplay is now qualified, but direct Internet NAT traversal remains untested. No further mobile-data or relay-specific tests are planned unless an observed issue makes them necessary. The captures contain each capturing client's own registration and S20 forwarded messages, not the S20's direct handshake. No live `PEERComplete` or simultaneous S20 SDK/cache memory snapshot was taken.

Recovery of an interrupted P2P session is outside project scope at the user's direction. Server-side consequences that could impair subsequent matchmaking remain in scope. In the reviewed 2026-09-09 Wi-Fi interruption, host discovery succeeded before the S20 switched Wi-Fi → cellular → Wi-Fi; the active search did not recover. The service did not restart or log a server warning/error in the failure window, both PeerChat sessions closed, the host advertisement was removed, and the remaining QR record expired with no active records left. This is not a recovery pass or a blanket proof of all server failure paths. Private review evidence is retained under `build/wifi-recovery-20260909T041843Z`.

## Sources and retained evidence

- Local clean APK and its independently mapped native libraries; exact input digest is in `README.md` and the shared patcher allowlist.
- SDK public contracts: [peerConnectCallback and peerGetNick](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peer.h), [peerStartGame implementation](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerMain.c), and [player state layout](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerPlayers.h). The deployed binary, not an assumed SDK source version, determines the patch offsets.
- Original private investigation directory: `fruitspy-s20-pair-20260908T010926Z`, containing the decoded PeerChat exchange, final packet capture, native disassembly excerpts, and root-cause report. These captures and proprietary disassemblies are intentionally excluded from Git.
- Controlled post-fix private evidence directory: `build/controlled-20260908T033105Z`, containing the final 1,573-packet emulator capture, decoded PeerChat streams, server journals, device/build checks, fixture-release probes, and per-match/user observations. Raw captures and connection details remain excluded from Git.
- Revised physical-pair private evidence directory: `build/physical-pair-20260909T000211Z`, containing the final 1,419-packet S4 capture, four decoded PeerChat streams, server journal, installed-build checks, unchanged process IDs, fixture-release evidence, and per-match/user observations. Final capture SHA-256: `5f7936cdbb8eaa5164236ce2b4f22f9b5babe65229b6420577c1a4ec0590df7d`. Raw captures and connection details remain excluded from Git.
- Forced-legacy private evidence directory: `build/legacy-physical-20260909T014550Z`, containing the legacy-only test APK and derivation manifest, installed-file hashes, runtime ABI evidence, final 1,588-packet S4 capture, seven decoded registrations, server journal, release probes, cleanup checks and per-match/user observations. Final capture SHA-256: `56c0346f79bb27fda7f27e74936c5b9d6453dd7e021b96ab429e9529cd36504b`. These raw artifacts remain excluded from Git.
- Mixed-ABI private evidence directory: `build/mixed-abi-20260909T034701Z`, containing update-install and library hashes, Wi-Fi readiness, four decoded S4 registrations, per-match bidirectional LAN flow summaries, the final 982-packet capture, recovered server journal, fixture release/recovery records and user reports. Final capture SHA-256: `83f3b7ef24529242d817d3647453efc662d8c18319bff8e73398394d13b576ff`. Raw artifacts remain excluded from Git.
- Maximum-length private evidence directory: `build/max-nickname-20260909T042602Z`, containing the separate initial user report, controlled collision/reconnect observations, two decoded S4 registrations, exact configured-name memory checks, LAN flow summaries, final 467-packet capture, server journal, release probes and cleanup checks. Final capture SHA-256: `dc885b535113c4499fb7380273a4dfec2da7cf5b64b9398646bea960f611800f`. Raw artifacts remain excluded from Git.
- Intentional-suffix private evidence directory: `build/intentional-suffix-20260909T044547Z`, containing collision/reconnect/cold-launch user reports, three decoded S4 registrations, configured-name memory checks, process IDs, LAN flow summaries, final 600-packet capture, server journal, release probes and cleanup checks. Final capture SHA-256: `e272d7276f7884310192b2c95495b7c1201f759674ba2013609f4839f08c6f11`. Raw artifacts remain excluded from Git.
- `analysis/evidence.json` records the implementation/build identifiers and machine-verification results without copying those private artifacts.
