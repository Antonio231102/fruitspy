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

The native retry limit and random suffix space are unchanged. The correction operates after successful registration; it does not guarantee registration under an exhausted collision space.

### Server contribution, intentionally not changed

`server/fruitspy/peerchat.py` retains registered connections while awaiting `reader.read` without an application-level heartbeat deadline. Network changes can therefore leave original names occupied until TCP detects failure. Better dead-session detection would reduce that trigger but cannot replace client correctness for legitimate collisions. This subproject makes no server changes.

## Implementation

### Patch point

Redirect the successful-connect callback supplied by the game's anonymous `peerConnect` call to a small helper. Leave the original callback intact and tail-call it after synchronization.

The helper:

1. Skips synchronization on a failed connection or null peer/provider.
2. Calls the library's existing `peerGetNickA` to obtain the accepted identity; handles its null return without dereferencing it.
3. Copies through the first NUL, with a maximum of 63 bytes plus a terminator, into the 64-byte provider cache beginning at `0xa4`.
4. Restores the callback arguments and callee-saved registers.
5. Tail-calls the original callback, so the game cannot observe success before the identity is synchronized.

No heap allocations, string formatting, networking, or per-frame work are added. Failed connection publication is unchanged. The final accepted name is copied rather than each retry candidate, which may itself be rejected. Reconnects can replace or remove a previous suffix.

This corrects the shared cached identity rather than bypassing only the failing host test. It does not add a new mid-session renaming feature.

### ABI mapping

All addresses below are relative to the ELF load base. Hook offsets are file offsets; the hook sites remain in the unchanged first executable segment.

| ABI | Callback-reference hook | Address base for reference | Original connected callback | `peerGetNickA` | Helper address | Helper bytes |
|---|---:|---:|---:|---:|---:|---:|
| `armeabi` | `0x19d964` | `0x19d6f4` | `0x19c67c` | `0x1a95ec` | `0x3a2d4c` | 92 |
| `armeabi-v7a` | `0x19d934` | `0x19d6c4` | `0x19c64c` | `0x1a95bc` | `0x3a5994` | 92 |
| `x86` | `0x188d19` | `0x3b22c8` | `0x1875a0` | `0x195500` | `0x3a63e8` | 79 |

The ARM hooks replace a four-byte PC-relative literal. The x86 hook replaces the six-byte `lea` instruction that computes the callback address from the GOT base. Neither changes the nickname-error callback, callback ABI, or original completion routine.

The original game host predicates exercised by the regression harness are `0x19c900` (`armeabi`), `0x19c8d0` (`armeabi-v7a`), and `0x187b10` (`x86`).

### Payload placement and input safety

The standalone builder starts from the same fully allowlisted clean APK as the shared patcher. It applies the shared clock and endpoint transformations first. The nickname helper follows the existing clock payload in the executable load segment; x86 adds three zero alignment bytes before its helper.

The existing ELF injector performs placement, zero-gap, segment-overlap, alignment, and file-offset checks. The nickname stage supplies an updated placement descriptor without changing the clock payload. Exact original hook bytes and both payload hashes are required. Duplicate patching and altered hook instructions are rejected.

The shared APK builder/signing routines retain responsibility for removing obsolete signatures, selecting the existing signing identity, alignment, and signature verification. No proprietary game library or signing key is included in this subproject.

## Verification performed

### Native execution: all three ABIs

The reproducible harness in `tools/verify_native.py` executes actual APK machine code under Unicorn. Its player table uses the SDK's original hash-table and array-lookup implementations, a one-bucket fixture hash callback, and libc `strncpy`/`strcasecmp` substitutes. The game host predicate, SDK room/local-host checks, accepted-name getter, helper, and original completion callback are not mocked.

For each ABI, at load bases `0x10000000` and `0x38000000`:

- The uncorrected callback reproduces the renamed-host failure.
- The corrected renamed host recognizes itself even while its original nickname belongs to an older title-room player.
- A renamed joiner does not misidentify itself as the live host that owns its requested original name.
- An ordinary unsuffixed host remains a host.
- A 63-byte SDK nickname remains bounded and usable for host lookup.
- Reconnects update the suffix and subsequently restore the unsuffixed name.
- Failed connection, null provider, null peer, and unavailable getter paths preserve the cached identity and original completion result.
- Native calls preserve callee-saved registers and stack balance; callback writes are observed to ensure success is published only after synchronization. Adjacent provider bytes remain untouched.

Observed output:

```text
armeabi: baseline stall reproduced; 18 corrected native scenarios passed
armeabi-v7a: baseline stall reproduced; 18 corrected native scenarios passed
x86: baseline stall reproduced; 18 corrected native scenarios passed
```

This is native function execution with constructed protocol state, not a live network/concurrency test or a completed Android match.

### Build and artifact checks

- Rebuilding the assembly reproduces all three checked-in payload hashes.
- Compared with the shared unsigned baseline, only the three `libmortargame.so` ZIP entries change. All other entry bytes and entry ordering remain unchanged.
- Altered hook bytes and duplicate application are rejected for every ABI.
- Final library digests match the generated manifest.
- The signed build targeting `217.154.27.122` has APK SHA-256:

```text
917c079ed8e8e01b4f481f96ade73e23fb2afe5a5eab098fac819da6962d21c1
```

- The reused signer certificate SHA-256 is:

```text
7e9ad31c19d7ee447c8e1a58bc7ca4be51acf4489f55cf2258124c45d7213417
```

- v1, v2, and v3 verification passed; v4 is intentionally disabled by the shared signer. `zipalign` verification passed.
- `adb install -r` returned `Success` on the Android x86 emulator and Galaxy S20. No uninstall, data clearing, or server restart was performed during update installation.

### Live qualification: x86 and ARMv7

On 2026-09-08, the corrected APK completed five matches between the Android x86 emulator on Wi-Fi and the Galaxy S20 running `armeabi-v7a` on cellular. Installed APK hashes matched the signed artifact above. The user exclusively performed game launches, navigation, matchmaking, and gameplay; match completion and visible symptoms below are user reports, not assistant UI observations.

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

A displayed suffix does not establish that the current registration collided. Match 5 submitted an already suffixed name and was accepted directly, unlike match 4's original-name rejection. The patch copies the SDK's accepted nickname, including any suffix; it does not reset names or strip suffixes. Unsuffixed acceptance and subsequent suffix removal are covered by native execution, not by this live control.

### Remaining runtime boundaries

No physical legacy `armeabi` device was tested. All five live matches used relay transport, so direct peer-to-peer gameplay is not qualified by these results. The emulator capture contains its own registration handshake and the S20's relayed room/launch messages, not the S20's direct registration handshake. No live `PEERComplete` memory snapshot or simultaneous S20 SDK/cache snapshot was taken; launch traffic, transport establishment, and user-reported completion establish the observed end-to-end result.

## Sources and retained evidence

- Local clean APK and its independently mapped native libraries; exact input digest is in `README.md` and the shared patcher allowlist.
- SDK public contracts: [peerConnectCallback and peerGetNick](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peer.h), [peerStartGame implementation](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerMain.c), and [player state layout](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerPlayers.h). The deployed binary, not an assumed SDK source version, determines the patch offsets.
- Original private investigation directory: `fruitspy-s20-pair-20260908T010926Z`, containing the decoded PeerChat exchange, final packet capture, native disassembly excerpts, and root-cause report. These captures and proprietary disassemblies are intentionally excluded from Git.
- Controlled post-fix private evidence directory: `build/controlled-20260908T033105Z`, containing the final 1,573-packet emulator capture, decoded PeerChat streams, server journals, device/build checks, fixture-release probes, and per-match/user observations. Raw captures and connection details remain excluded from Git.
- `analysis/evidence.json` records the implementation/build identifiers and machine-verification results without copying those private artifacts.
