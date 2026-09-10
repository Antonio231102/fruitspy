# FruitSpy

FruitSpy is a standalone GameSpy-compatible multiplayer service for Fruit Ninja 1.7.6, replacing the retired GameSpy infrastructure. It supports two-player LAN play and Internet matchmaking with direct-first UDP negotiation and bounded automatic relay fallback. Direct LAN gameplay and relayed Internet gameplay have been exercised; direct Internet traversal remains unqualified, and mixed local/remote home-router topologies need explicit validation.

## Repository layout

- `server/` — standalone GameSpy-compatible services, deployment configuration, and protocol tests.
- `patcher/` — neutral interactive/non-interactive APK patcher, automatic local signing workflow, and patcher tests.
- `slow-motion-fix/` — slow-motion investigation, validation records, native payload source, reproducible payload binaries, and payload build tooling.
- `nickname-collision-fix/` — matchmaking-fix investigation, qualification records, native payload source/binaries, and native build and regression tools.

## Multiplayer services

Implemented services:

- Availability and QR2 on UDP 27900
- PeerChat on TCP 6667
- Server Browsing on TCP 28910
- NAT Negotiation and fallback gameplay relay on UDP 27901
- Encrypted PeerChat and Enctype-X Server Browser responses
- Two-player staging rooms, host publication, direct peer traversal, and bounded automatic relay fallback

The LAN path has been exercised across an Android emulator and a Galaxy S4 for repeated games. After matchmaking, gameplay traffic flows directly between the devices.

## Hosting guides

- [Host FruitSpy at home](docs/HOME_HOSTING.md) — Windows-oriented router, firewall, DNS, startup, and external-test instructions.
- [Host FruitSpy on a VPS](docs/VPS_HOSTING.md) — Ubuntu/Debian installation, root or rootless systemd, cloud firewall, health checks, updates, and removal.
- [Internet deployment reference](DEPLOYMENT.md) — operator acceptance matrix, relay behavior, failure classification, and limits.

A home-hosted FruitSpy server keeps mutually reachable clients on direct UDP. If the direct NatNeg exchange has not succeeded after the configured deadline, both clients are moved to a bounded relay on the existing UDP 27901 listener. Use a VPS or two external client networks for Internet acceptance testing; mixed local/remote play behind a home router still requires explicit validation.

### Resource expectations

The latest production-limit load campaign peaked at **42,112 KiB (about 41.1 MiB) of RAM** and completed in about seven seconds on the project VPS. It exercised every configured capacity boundary, including hundreds of TCP connections, thousands of matchmaking records, and 1,024 relay allocations.

For self-hosting, keep the supplied **256 MiB process memory limit**. A VPS with **at least 512 MiB of total RAM** is a conservative starting point because the operating system and administration services also need memory. The 41.1 MiB measurement includes the test runner and synthetic clients, but the capacity scenarios ran separately rather than saturating every limit simultaneously. It is a planning reference, not a guaranteed maximum. Sustained relay bandwidth and CPU throughput require separate measurement.

## Prerequisites and installation

Keep the complete source checkout: ordinary APK patching reads the shipped native payloads in both fix subprojects.

| Task | Required locally |
| --- | --- |
| Run the server from the checkout | **Python 3.11 or newer**, including its standard library. No `pip` dependencies or package installation are needed. |
| Produce an installable, signed APK | Python, your own supported clean APK, a **JDK providing both `java` and `keytool`**, and **Android SDK Build Tools providing `zipalign` and `apksigner`**. The patcher has no third-party Python dependencies. |
| Produce an unsigned intermediate with `--unsigned` | Python and your supported clean APK only; no Java or Android tools. This output is also unaligned and cannot be installed as-is. |
| Rebuild or investigate native payloads | Optional developer tools described below; not part of ordinary patching. |

Use one currently supported **Python 3.11+** environment for both server and patcher. The server declares that minimum in `server/pyproject.toml`; the standalone patcher needs no separate Python environment or dependency installation. This guide does not claim qualification on older Python releases.

### 1. Install Python

- **Windows:** install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/) and enable command-line access as described in the [Windows Python installation guide](https://docs.python.org/3/using/windows.html). Open a new PowerShell window afterward.
- **macOS:** use the [official Python installer](https://www.python.org/downloads/macos/).
- **Linux:** install your distribution's `python3` package and check its version; if it is older than 3.11, use a supported distribution/Python installation. Ubuntu/Debian's basic command is `sudo apt install python3`.

Check the interpreter you will actually use:

```text
python --version
```

The commands below use `python`. If your installation exposes it as `python3` on POSIX or `py -3` on Windows, use that command consistently instead. A virtual environment is optional. Running the checkout does not require `pip install`; the optional packaged VPS installation uses `pip`/setuptools to install console entry points, not additional runtime libraries.

### 2. Install signing tools on the APK-patching computer

Skip this step on a server-only machine or for `--unsigned`.

1. Install a JDK for your host OS/CPU, for example [Eclipse Temurin from Adoptium](https://adoptium.net/installation/). A Java runtime alone is insufficient because the first signed build uses `keytool` to create a local identity.
2. Download Google's [Android SDK Command-Line Tools only](https://developer.android.com/studio#command-line-tools-only) archive for your OS. Extract it so your chosen SDK directory contains `cmdline-tools/latest/bin/sdkmanager` (`sdkmanager.bat` on Windows) and the adjacent `lib` directory; avoid an extra nested `cmdline-tools` directory. See the official [SDK manager installation and usage instructions](https://developer.android.com/tools/sdkmanager).
3. Use that manager to install a stable **Build Tools** package. A full Android Studio/SDK installation is unnecessary: no SDK platform, emulator/system image, Platform Tools/ADB, Gradle, NDK, LLVM, or Unicorn is required to run FruitSpy or patch/sign with the shipped payloads. If Android Studio is already installed, its SDK Manager can install Build Tools instead.

Choose a JDK supported by your selected Command-Line Tools and Build Tools releases, and satisfy their host-OS requirements. FruitSpy does not pin or claim a tested minimum JDK/Build Tools version. In particular, an old package merely containing `apksigner` is insufficient: the patcher uses its v1/v2/v3/v4 signing switches (v4 is disabled), `verify --verbose --print-certs`, `zipalign -p`, and `keytool` password-file options. Consult the upstream [Build Tools release notes](https://developer.android.com/tools/releases/build-tools), [apksigner reference](https://developer.android.com/tools/apksigner), and your JDK provider's compatibility guidance rather than assuming every combination works.

Known-working signed-build toolchain on Windows: **Python 3.14.3, Eclipse Temurin JDK 21.0.9, and Android SDK Build Tools 36.1.0**. This is a smoke-tested combination, not a minimum-version requirement or a fresh SDK-manager installation test.

The examples below set variables for the **current terminal only**. Replace the JDK directory with your installed JDK home (not its `bin` directory), and use the SDK directory where you extracted Command-Line Tools. List available packages, then enter the numeric stable Build Tools version you selected, without the `build-tools;` prefix. Read and accept Google's package licenses when prompted.

**Windows PowerShell:**

```powershell
$env:JAVA_HOME = "C:\path\to\jdk"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:PATH = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\cmdline-tools\latest\bin;$env:PATH"
java -version
keytool -help
sdkmanager.bat "--sdk_root=$env:ANDROID_HOME" --list
$BuildToolsVersion = Read-Host "Build Tools version from the list"
sdkmanager.bat "--sdk_root=$env:ANDROID_HOME" "build-tools;$BuildToolsVersion"
sdkmanager.bat "--sdk_root=$env:ANDROID_HOME" --licenses
$env:PATH = "$env:ANDROID_HOME\build-tools\$BuildToolsVersion;$env:PATH"
apksigner.bat version
zipalign.exe
```

**POSIX shell (Linux/macOS):**

```sh
export JAVA_HOME="/path/to/jdk"
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
java -version
keytool -help
sdkmanager "--sdk_root=$ANDROID_HOME" --list
printf 'Build Tools version from the list: '
read -r BUILD_TOOLS_VERSION
sdkmanager "--sdk_root=$ANDROID_HOME" "build-tools;$BUILD_TOOLS_VERSION"
sdkmanager "--sdk_root=$ANDROID_HOME" --licenses
export PATH="$ANDROID_HOME/build-tools/$BUILD_TOOLS_VERSION:$PATH"
apksigner version
zipalign
```

On macOS, a registered JDK home can be found with `/usr/libexec/java_home`; an existing Android Studio SDK commonly lives at `$HOME/Library/Android/sdk` instead. Invoking `zipalign` without arguments prints usage and exits nonzero; this is an expected discovery check, not APK verification. Resolve missing-tool or Java-version errors before patching. To retain these settings, configure your Windows user environment variables or POSIX shell profile; reopen the terminal afterward.

The patcher accepts explicit executable paths first, otherwise checks `PATH`; `keytool` also falls back to `JAVA_HOME/bin`. Android tools then fall back to `--android-sdk`, `ANDROID_SDK_ROOT`, `ANDROID_HOME`, and standard SDK directories, choosing the highest numbered installed Build Tools candidate. `--android-sdk` does **not** override a tool already on `PATH`; use `--zipalign` and `--apksigner` to pin exact executables. Even with explicit tool paths, Java must remain available to the Android launcher scripts through `JAVA_HOME` or `PATH`.

### Optional development and device tools

- **Native payload reproduction only:** install [LLVM](https://releases.llvm.org/) with `clang`, `ld.lld`, and `llvm-objcopy` on `PATH`. The [slow-motion](slow-motion-fix/README.md#reproduce-the-native-payloads) and [nickname](nickname-collision-fix/README.md#reproduce-payloads) instructions rebuild and hash-check the payloads; ordinary patching only reads and verifies the shipped binaries.
- **Native execution regression harness only:** the nickname harness uses the pinned `unicorn==2.1.4` in `nickname-collision-fix/requirements-validation.txt`; see its [validation instructions](nickname-collision-fix/README.md#run-the-native-regression-harness). Do not install it for server operation or normal APK builds.
- **Device diagnostics only:** [Android Platform Tools/ADB](https://developer.android.com/tools/releases/platform-tools), an emulator, packet-capture tools, and device-specific access are optional investigation tools. An emulator or ADB is not required to build the APK; transferring it and using Android's package installer is a separate device-side step. Android must support the game's 32-bit native code. No phone UI automation is required.

## Run the server

From the **repository root**, with the [Python prerequisite](#1-install-python) satisfied:

```text
cd server
python -m fruitspy --config config.json
```

For protocol diagnostics, add `--verbose`. `config.json` is the unified direct-first configuration for local, home-hosted, and VPS deployments. It binds all four service sockets to `0.0.0.0`; firewalls must allow only the four ports listed above.

### Unified deployment model

FruitSpy coordinates discovery and NatNeg, then publishes each game host's server-observed QR2 source address and port. Direct-capable peers remain peer-to-peer. Under `relay.policy=auto`, a session that has not reported direct success before `fallback_seconds` receives a synthetic NatNeg peer response from UDP 27901; subsequent opaque game datagrams are forwarded only between that session's two proven endpoints. The IPv4 address or short DNS name used to reach FruitSpy is selected when patching the APK, independently of server socket binding.

Copy `config.json` to the ignored `config.local.json` only when a machine needs different bind addresses, ports, timeouts, admission limits, or relay policy:

```text
python -m fruitspy --config config.local.json
```

Do not expose a development checkout directly to the public Internet. Use an OS firewall that permits only the four protocol ports, run the process as an unprivileged account under a supervisor, and retain minimal logs. The guarded Linux deployment and two-network acceptance procedure are documented in [DEPLOYMENT.md](DEPLOYMENT.md).

In another terminal, change into the checkout's **`server/` directory**, then check all four local listeners after startup:

```text
python -m fruitspy.healthcheck --config config.json
```

The command validates Availability/QR2 and NatNeg responses, checks both TCP listeners, and exits nonzero if any service is unavailable.

### Operational metrics

FruitSpy exposes Prometheus text metrics on the separate administrative listener configured by `metrics.bind_host` and `metrics.port`. The checked-in endpoint is `http://127.0.0.1:9108/metrics`; configuration validation requires a loopback IP and rejects reuse of a public service port. Do not add port 9108 to the public firewall rules. Scrape locally or through an authenticated SSH tunnel:

```text
curl --fail http://127.0.0.1:9108/metrics
```

The schema is fixed and cardinality-bounded. It reports aggregate gauges for drain state, active clients, rooms, QR2 records, browser connections, NatNeg sessions, and relays; counters for drain lifecycle, discovery, negotiation, relay, errors, admission rejections, and amplification suppression; and fixed-bucket direct/relay setup latency histograms. Labels come only from closed server-defined sets. Metrics never contain source addresses, connection or session identifiers, cookies, nicknames, room names, hostnames, message bodies, or packet payloads.

Metrics exist only in process memory and reset when FruitSpy restarts. FruitSpy does not persist or transmit them. An external scraper controls any retention and must apply its own access and deletion policy.

### Admission controls

The unified configuration includes the checked-in safety limits; operators should tune them only from measured traffic:

| Setting | Default | Behavior |
| --- | ---: | --- |
| `peerchat_connections` | 256 | Maximum concurrent PeerChat TCP connections |
| `peerchat_channels` | 1024 | Maximum live PeerChat channel objects |
| `peerchat_channels_per_client` | 16 | Maximum simultaneous channel memberships per PeerChat client |
| `peerchat_keys_per_collection` | 64 | Maximum entries in each user, channel, or per-participant channel-key collection |
| `peerchat_commands_per_second` | 30 | Per-client PeerChat command-token refill rate |
| `peerchat_command_burst` | 60 | Maximum per-client command burst before disconnect |
| `peerchat_state_creations_per_second` | 8 | Per-client refill rate for new nick, room, membership, operator, and key state |
| `peerchat_state_creation_burst` | 32 | Maximum per-client burst of new persistent state |
| `server_browser_connections` | 128 | Maximum concurrent Server Browser TCP connections |
| `connections_per_source` | 16 | Per-source cap, enforced independently by each TCP service |
| `udp_packets_per_second` | 120 | Shared QR2/NatNeg token refill rate per source IPv4 address |
| `udp_burst` | 240 | Maximum per-source UDP burst across both UDP listeners |
| `udp_global_packets_per_second` | 4096 | Global QR2/NatNeg packet-token refill rate |
| `udp_global_burst` | 8192 | Maximum aggregate UDP burst across both UDP listeners |
| `udp_source_violation_burst` | 30 | Consecutive source-rate rejections before a temporary source ban |
| `udp_tracked_sources` | 4096 | Hard bound on the shared UDP source-accounting table |
| `reported_servers` | 2048 | Hard bound on QR2 server registrations |
| `nat_sessions` | 4096 | Hard bound on concurrent NatNeg cookie sessions |
| `relay.policy` | `auto` | Direct-first negotiation with bounded fallback; set `direct` to disable relay allocation |
| `relay.fallback_seconds` | 3 | Delay before an unconfirmed direct path moves to relay |
| `relay.session_seconds` | 900 | Idle lifetime of each relay allocation; authenticated traffic refreshes it |
| `relay.packet_bytes` | 4096 | Maximum forwarded UDP payload |
| `relay.bytes_per_second` | 262144 | Per-endpoint relay byte-token refill rate |
| `relay.byte_burst` | 524288 | Per-endpoint initial and maximum byte burst |
| `relay.sessions` | 1024 | Global concurrent relay allocation limit |
| `peerchat_handshake_seconds` | 15 | Absolute deadline for PeerChat registration |
| `state_expiry_interval_seconds` | 5 | Background cadence for reported-server and NatNeg session expiry |
| `server_browser_idle_seconds` | 30 | Header and frame completion deadline |
| `rate_limit_entry_seconds` | 120 | Idle lifetime for UDP source accounting |
| `udp_source_ban_seconds` | 60 | Temporary ban duration after repeated per-source rate violations |
| `drain_seconds` | `30` | Maximum time existing gameplay relays may continue after graceful drain starts |
| `metrics.bind_host` | `127.0.0.1` | Loopback-only administrative metrics listener; non-loopback addresses are rejected |
| `metrics.port` | `9108` | Prometheus text endpoint at `/metrics`; must differ from every public service port |

Rejected connections and protocol events use stable `service=... event=...` fields. Verbose logs omit PeerChat message bodies, nicknames, quit reasons, and raw Server Browser frames. Network-controlled fields that remain operationally necessary are capped at 256 emitted characters; backslashes, line breaks, terminal controls, Unicode format controls, and non-ASCII separators are escaped before interpolation. Source addresses remain available for abuse diagnosis and should be retained only as long as operationally necessary.

For one controlled game attempt, capture aggregate counters immediately before launching the clients and compare them afterward:

```text
python -m fruitspy.diagnostics capture fruitspy-before.json
python -m fruitspy.diagnostics compare fruitspy-before.json --game-result passed
```

Use `--game-result failed` when gameplay did not complete. The comparison reports the Availability, QR registration, PeerChat, discovery, NatNeg, and direct/relay boundaries independently, followed by any admission or protocol rejection deltas. A failed game after a successful direct report, or after relay forwarding with zero drops, is classified beyond the FruitSpy server boundary. Delete the temporary snapshot after recording the aggregate result.

Channel-limit rejections return IRC numeric `405`; key updates that would exceed a collection return numeric `263` and apply no partial changes. Existing keys remain updateable at capacity. Structured `collection_limit_rejected` events identify the bounded resource without logging key contents.

Command-budget exhaustion returns numeric `263` and disconnects the offending PeerChat client before additional buffered commands can run. State-creation exhaustion returns `263` without disconnecting; the rejected command makes no partial change, while updates to existing state remain available.

The state sweeper removes expired QR2 registrations and NatNeg setup sessions on the configured cadence even when no client request arrives. Expired registered hosts trigger normal Server Browser deletion updates. Authenticated traffic from either bound relay endpoint refreshes both its NatNeg setup session, when present, and the relay's independent idle deadline. Unauthenticated traffic cannot extend that deadline. Idle relays close after `relay.session_seconds`; the concurrent relay cap remains a hard resource bound.

QR2 and NatNeg share one global packet budget and one per-source table, so moving traffic between the two UDP ports cannot bypass admission. A source that continues transmitting after exhausting its burst is temporarily banned across both listeners after `udp_source_violation_burst` consecutive rejections. An accepted packet resets that violation run. Global exhaustion does not penalize individual sources. Structured `udp_admission_rejected` events identify global limits, source limits, newly started bans, active bans, and source-table capacity without parsing packet contents.

UDP response generation is fail-closed against reflection amplification. QR2 counts each reply against the triggering datagram and permits at most a fixed `2:1` byte ratio; the measured worst case is the 11-byte unavailable response to a 6-byte availability request. NatNeg counts every immediate datagram produced by one request, including packets sent to both peers, and permits at most `3:1`; pairing produces the measured maximum of 61 response bytes from a 21-byte `INIT`. Automatic fallback remains within the same cumulative boundary: two minimum `INIT` claims produce 122 bytes total across acknowledgements, connect packets, and relay pings from 42 bytes received. A response set that exceeds its limit is suppressed atomically, and an over-limit fallback cannot allocate relay state. Authenticated gameplay relay forwarding is exactly `1:1` and remains subject to the separate packet-size and byte-rate limits.

### Graceful service drain

`SIGTERM` and `SIGINT` idempotently move FruitSpy from running to draining. Use the supervisor rather than killing the Python process directly:

```text
sudo systemctl stop fruitspy.service
```

The process immediately closes the PeerChat and Server Browser listeners and their active control connections. QR2 availability replies become unavailable, new QR2 registration traffic is ignored, new NatNeg cookies are rejected, scheduled relay fallbacks are canceled, and no relay allocation may start. An already-created NatNeg session may still finish its direct setup; established direct gameplay continues peer-to-peer and is unaffected by process exit.

Existing gameplay relays continue forwarding for at most `drain_seconds`. The process exits early when the final relay closes naturally. At the deadline, remaining relays close with `reason=server_drain_timeout`, and the server exits. During this window the loopback metrics endpoint remains available, `fruitspy_server_draining` is `1`, and `fruitspy_drain_events_total` records whether the drain completed or timed out. The checked-in systemd units allow five additional seconds beyond the configured 30-second relay deadline for process cleanup.

### Anonymous authentication boundary

FruitSpy intentionally provides no player accounts, product-key validation, Android LVL enforcement, or persistent nickname ownership. `CRYPT` proves only that the client speaks the title's shared protocol; the secret is embedded in every APK. `NICK` is an arbitrary display name whose uniqueness lasts only for the active connection. QR2 challenge-response and NatNeg endpoint claims establish temporary protocol and network reachability, not a licensed installation or player identity. Source addresses are used only for bounded operational controls.

Fruit Ninja's linked GameSpy SDK contains an unused `CDKEY` command path, but the title does not call it. Cross-reference analysis found no caller or function-pointer reference to the top-level CD-key API in the APK's `armeabi`, `armeabi-v7a`, or `x86` libraries. A traced two-device public match completed from fresh PeerChat connections through both `QUIT` commands without either client sending `CDKEY`. FruitSpy therefore treats `CDKEY` as an unsupported command rather than returning a false authentication success.

## Patch a locally owned APK

The repository does not distribute Fruit Ninja or a prebuilt APK. The patcher accepts only the clean Fruit Ninja 1.7.6 APK with SHA-256 `5e94d16234504f5d2b6948b59371d8535c4364249b76e2533bba09114c808650`. For `armeabi`, `armeabi-v7a`, and `x86`, it applies the **slow-motion fix → matchmaking fix → custom server patch**, in that order. The matchmaking fix keeps the game's session identity synchronized after nickname collisions and restores the configured nickname for each new connection; it does not fix server-side dead sessions. The patcher then removes the obsolete APK signature, aligns the result, and signs it.

Run APK commands from the **repository root**, not `server/` (use `cd ..` first if you followed the server commands in this terminal). Complete the [signing-tool installation](#2-install-signing-tools-on-the-apk-patching-computer) for the default signed output. For the guided workflow:

```text
python patcher/patch_apk.py
```

The prompts request the clean APK and a server address; no VPS, local IP, or DNS name is predefined. The patcher waits for the user-supplied endpoint before building. By default it writes only `Fruit Ninja v1.7.6 FruitSpy <IPv4/domain>.apk` beside the source APK, using the selected server address. An explicit second positional argument overrides the output path; existing outputs are refused, so choose a fresh filename for another build. For optional diagnostics, add `--report PATH` to save a JSON build report; internal patch validation, alignment, and signing verification run regardless of report output. For automation with the default filename, this single-line command works in both PowerShell and POSIX shells:

```text
python patcher/patch_apk.py "original.apk" --server-host 192.168.100.2 --non-interactive
```

Replace `192.168.100.2` with the local or remote IPv4 address reachable by the devices. Short DNS names such as `games.example.net` are accepted for Internet deployments. The DNS value must be at most 18 ASCII characters because every binary rewrite is size-preserving; the interactive prompt displays this warning before accepting the endpoint.

To select an SDK root explicitly (PowerShell and POSIX; replace the path):

```text
python patcher/patch_apk.py "original.apk" "FruitSpy-signed.apk" --server-host games.example.net --android-sdk "/path/to/Android/Sdk" --non-interactive
```

To pin all three tools with the variables from the installation examples, use **PowerShell**:

```powershell
python patcher/patch_apk.py "original.apk" "FruitSpy-signed.apk" --server-host games.example.net --android-sdk "$env:ANDROID_HOME" --keytool "$env:JAVA_HOME\bin\keytool.exe" --zipalign "$env:ANDROID_HOME\build-tools\$BuildToolsVersion\zipalign.exe" --apksigner "$env:ANDROID_HOME\build-tools\$BuildToolsVersion\apksigner.bat" --non-interactive
```

Or a **POSIX shell**:

```sh
python patcher/patch_apk.py "original.apk" "FruitSpy-signed.apk" --server-host games.example.net --android-sdk "$ANDROID_HOME" --keytool "$JAVA_HOME/bin/keytool" --zipalign "$ANDROID_HOME/build-tools/$BUILD_TOOLS_VERSION/zipalign" --apksigner "$ANDROID_HOME/build-tools/$BUILD_TOOLS_VERSION/apksigner" --non-interactive
```

On the first signed build, the patcher generates a random local RSA signing identity and stores it under `%LOCALAPPDATA%\FruitSpy` on Windows, `~/Library/Application Support/FruitSpy` on macOS, or `${XDG_DATA_HOME:-~/.local/share}/fruitspy` on Linux. Use `--signing-dir PATH` to select a different local signing directory. Subsequent builds reuse that identity so they can update an existing FruitSpy installation. Back up this directory: losing it requires uninstalling the existing patched app before installing a build signed by a replacement key. Never upload the keystore, its `signing.json`, generated APKs, or manifests containing deployment-specific addresses to the repository.

The first FruitSpy-signed build cannot update the original game or a patched build signed by someone else's key. Back up any game data you need before uninstalling a differently signed installation. Transfer your signed APK to a compatible Android device and authorize installation from your chosen source as required by that Android version.

Use `--unsigned` only when an unsigned, unaligned intermediate is required. That mode does not require Java or Android Build Tools; alignment and signing must then be completed before installation.

```text
python patcher/patch_apk.py "original.apk" "FruitSpy-unsigned.apk" --server-host games.example.net --unsigned --non-interactive
```

## Verification

These are optional developer checks, not installation steps. Start the following combined suite commands from the **repository root**:

```text
cd server
python -m unittest
cd ..
python -m unittest patcher.tests.test_patch_apk
```

The server suite covers cryptography, configuration validation, admission controls, connection deadlines, listener health, malformed-input recovery, PeerChat, QR2 registration and rate limiting, server discovery, NatNeg pairing, direct-path cancellation, relay endpoint proof, opaque forwarding, activity-aware relay idle expiry, byte and packet limits, and global relay capacity. The separate patcher suite covers deterministic clock/nickname/endpoint composition, all three ABIs, neutral endpoint input, local key persistence, alignment, and signing verification.

The combined commands above finish in the repository root. **Change into `server/` once before all the remaining verification commands in this section**, and remain there through the relay probe:

```text
cd server
```

The fuzz regressions use a deterministic mutation corpus against QR2 and NatNeg datagrams, Server Browser frames and filters, and encrypted PeerChat commands. The default case count keeps the complete suite fast:

```text
python -m unittest tests.test_fuzz
```

For an extended reproducible campaign, set `FRUITSPY_FUZZ_CASES` and `FRUITSPY_FUZZ_SEED` before running that module. For example on a POSIX shell:

```text
FRUITSPY_FUZZ_CASES=10000 FRUITSPY_FUZZ_SEED=0x10CEFADE \
  python -m unittest tests.test_fuzz
```

The configured-capacity load suite reads `server/config.json` directly and drives each production boundary to its exact limit, verifies fail-closed overflow behavior, releases capacity, and verifies recovery:

```text
python -m unittest tests.test_load
```

It covers 256 PeerChat and 128 Server Browser connections with the 16-per-source cap; 1,024 rooms, 16 memberships per client, and 64 entries in every key collection; command and state-creation bursts; the 240-packet source burst, 8,192-packet global burst, 4,096-source table, and 30-violation ban threshold; 2,048 reported servers and 4,096 NatNeg sessions; and 1,024 relays with the 524,288-byte per-endpoint burst. These tests use isolated loopback listeners and in-process datagram transports; do not direct synthetic load at the public service.

The deterministic failure-mode suite injects datagram loss, reordering, duplication, and delay; aborts live PeerChat and Server Browser clients; removes one relay while another is forwarding; and kills and restarts a temporary FruitSpy process:

```text
python -m unittest tests.test_failure_modes
```

It verifies retransmission recovery, state cleanup, nickname reuse, listener reconnection, relay isolation, process-local state reset, and four-protocol health after restart. The process test binds temporary loopback ports and does not restart or send fault traffic to the public service.

The opt-in relay lifetime probe establishes an authenticated two-endpoint relay, reports the relay path as successful from both peers, and exchanges opaque traffic in both directions every ten seconds for just over 15 minutes:

```text
python -m tests.relay_ttl_soak
```

It is intentionally excluded from test discovery because its wall-clock duration is the behavior under test. Run it only against a controlled service with no active players. The accelerated integration regression verifies the same deadline transition and confirms that an allocation closes after one full configured interval without authenticated endpoint traffic.

## Online play

LAN support remains the compatibility baseline. The guarded direct-first service is deployed on a public IPv4 VPS with automatic relay fallback after three seconds. A controlled Wi-Fi/cellular pair that previously had a one-way direct path completed two consecutive relayed games and a reverse-host game. A LAN game under the same `auto` policy remained direct and allocated no relay. Evidence, configuration, and failure classification are in [DEPLOYMENT.md](DEPLOYMENT.md); further hardening remains tracked in [ROADMAP.md](ROADMAP.md).

## Release status

Pre-release preservation project. The source and complete reachable-history publication audit is complete: it covered all 47 then-reachable commits, 361 blobs, and the 12 pending tracked files, with zero confirmed credentials or proprietary game binaries. The small authored native payloads are intentional repository content. Proprietary APKs, generated APK signatures, packet captures, UI dumps, and private signing keys remain excluded.

The two retired generated APK reports were removed from the published branch history; a fresh clone contains neither file nor their historical blobs. The owner accepts GitHub's retention of the old, non-sensitive reports. No further hosting-side purge or repository migration is planned, and this is not a release blocker.

This technical audit is **not public-release or legal approval**. Choosing a project license, resolving redistribution/provenance questions, and deciding which author/deployment metadata may be public remain explicit owner decisions before changing repository visibility. Local history cleanup does not erase existing clones or hosting-provider caches.
