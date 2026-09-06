# FruitSpy

FruitSpy is a standalone GameSpy-compatible multiplayer service for Fruit Ninja 1.7.6. The first checkpoint restores two-player LAN matchmaking and direct gameplay without depending on the retired GameSpy infrastructure.

## Checkpoint 1: LAN multiplayer

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

## Run the server

Python 3.11 or newer is required.

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

Check all four local listeners after startup:

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
| `relay.session_seconds` | 900 | Hard lifetime of each relay allocation |
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

Channel-limit rejections return IRC numeric `405`; key updates that would exceed a collection return numeric `263` and apply no partial changes. Existing keys remain updateable at capacity. Structured `collection_limit_rejected` events identify the bounded resource without logging key contents.

Command-budget exhaustion returns numeric `263` and disconnects the offending PeerChat client before additional buffered commands can run. State-creation exhaustion returns `263` without disconnecting; the rejected command makes no partial change, while updates to existing state remain available.

The state sweeper removes expired QR2 registrations and NatNeg setup sessions on the configured cadence even when no client request arrives. Expired registered hosts trigger normal Server Browser deletion updates. Authenticated relay traffic refreshes its NatNeg setup session when present, while the relay allocation retains its independent activity and hard-TTL lifecycle.

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

The repository does not distribute Fruit Ninja or a prebuilt APK. Given a legally obtained Fruit Ninja 1.7.6 APK, rewrite its GameSpy endpoints with:

```text
python server/patch_apk.py original.apk patched-unsigned.apk \
  --server-host 192.168.100.2 \
  --report server/apk-patch-report.json
```

Replace `192.168.100.2` with the server address reachable by the devices. Short DNS names such as `games.example.net` are also accepted for Internet deployments. The replacement must fit the shortest embedded GameSpy hostname. Align and sign the generated APK using your own Android signing key. The patcher modifies only the `armeabi-v7a` and `x86` libraries; the legacy `armeabi` library remains untouched.

## Verification

```text
cd server
python -m unittest
```

The suite covers cryptography, configuration validation, admission controls, connection deadlines, listener health, malformed-input recovery, PeerChat, QR2 registration and rate limiting, server discovery, NatNeg pairing, direct-path cancellation, relay endpoint proof, opaque forwarding, hard TTL, byte and packet limits, global relay capacity, and deterministic APK patching.

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

## Online play

LAN support remains the compatibility baseline. The guarded direct-first service is deployed on a public IPv4 VPS with automatic relay fallback after three seconds. A controlled Wi-Fi/cellular pair that previously had a one-way direct path completed two consecutive relayed games and a reverse-host game. A LAN game under the same `auto` policy remained direct and allocated no relay. Evidence, configuration, and failure classification are in [DEPLOYMENT.md](DEPLOYMENT.md); further hardening remains tracked in [ROADMAP.md](ROADMAP.md).

## Release status

Pre-release preservation project. The repository intentionally excludes proprietary APKs, generated APK signatures, packet captures, UI dumps, and private signing keys. A project license and public-release review remain required before changing the repository to public visibility.
