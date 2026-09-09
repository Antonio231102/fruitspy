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

### Remaining runtime boundaries

The preferred-name restoration revision's remaining user-operated test uses the S20 on mobile data and S4 on Wi-Fi: force a collision and complete a match, confirm server-side release of the original name, then reconnect without restarting the game and verify the original configured name is requested and accepted. Repeat with the other phone hosting. Capture registration evidence rather than relying on the displayed suffix alone.

Further x86 device testing on this workstation is discontinued at the user's direction. The user reported that known emulator instability, unrelated to this project, caused a PC softlock. The interrupted revised-build attempt supplies no completed-match result. The release decision relies on existing native checks and historical live evidence, with remaining x86 issues to be handled through public-release issue tracking and user feedback.

No physical legacy `armeabi` device was tested. The earlier five live matches used relay transport, not direct peer-to-peer gameplay. Their emulator capture contains its own registration handshake and the S20's relayed room/launch messages, not the S20's direct handshake. No live `PEERComplete` or simultaneous S20 SDK/cache memory snapshot was taken.

## Sources and retained evidence

- Local clean APK and its independently mapped native libraries; exact input digest is in `README.md` and the shared patcher allowlist.
- SDK public contracts: [peerConnectCallback and peerGetNick](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peer.h), [peerStartGame implementation](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerMain.c), and [player state layout](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerPlayers.h). The deployed binary, not an assumed SDK source version, determines the patch offsets.
- Original private investigation directory: `fruitspy-s20-pair-20260908T010926Z`, containing the decoded PeerChat exchange, final packet capture, native disassembly excerpts, and root-cause report. These captures and proprietary disassemblies are intentionally excluded from Git.
- Controlled post-fix private evidence directory: `build/controlled-20260908T033105Z`, containing the final 1,573-packet emulator capture, decoded PeerChat streams, server journals, device/build checks, fixture-release probes, and per-match/user observations. Raw captures and connection details remain excluded from Git.
- `analysis/evidence.json` records the implementation/build identifiers and machine-verification results without copying those private artifacts.
