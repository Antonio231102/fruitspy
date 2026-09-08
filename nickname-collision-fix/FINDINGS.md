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
- `adb install -r` returned `Success` on the Android x86 emulator and Galaxy S20. No uninstall, data clearing, or server restart was performed.

### Runtime qualification limit

An interruption occurred after installation but before the controlled live collision/match check. On resumption, ADB listed no devices; the emulator had no listening ADB transport and the S20 was disconnected. Therefore **no post-fix live match, direct/relay negotiation result, or physical legacy ARM result is claimed**.

To qualify that remaining runtime boundary: deliberately occupy each client's requested nickname, connect the two corrected clients, verify their accepted/cached identities agree, observe host launch and `PEERComplete`, and complete a Wi-Fi/cellular match with each device hosting in turn. Also check an ordinary connection and a reconnect. Those are qualification procedures, not completed results.

## Sources and retained evidence

- Local clean APK and its independently mapped native libraries; exact input digest is in `README.md` and the shared patcher allowlist.
- SDK public contracts: [peerConnectCallback and peerGetNick](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peer.h), [peerStartGame implementation](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerMain.c), and [player state layout](https://github.com/GameProgressive/UniSpySDK/blob/master/Peer/peerPlayers.h). The deployed binary, not an assumed SDK source version, determines the patch offsets.
- Original private investigation directory: `fruitspy-s20-pair-20260908T010926Z`, containing the decoded PeerChat exchange, final packet capture, native disassembly excerpts, and root-cause report. These captures and proprietary disassemblies are intentionally excluded from Git.
- `analysis/evidence.json` records the implementation/build identifiers and machine-verification results without copying those private artifacts.
