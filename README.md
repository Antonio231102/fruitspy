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

### Admission controls

The unified configuration includes the checked-in safety limits; operators should tune them only from measured traffic:

| Setting | Default | Behavior |
| --- | ---: | --- |
| `peerchat_connections` | 256 | Maximum concurrent PeerChat TCP connections |
| `server_browser_connections` | 128 | Maximum concurrent Server Browser TCP connections |
| `connections_per_source` | 16 | Per-source cap, enforced independently by each TCP service |
| `udp_packets_per_second` | 120 | QR2 and NatNeg token refill rate per source IPv4 address |
| `udp_burst` | 240 | Short UDP burst permitted by each service |
| `udp_tracked_sources` | 4096 | Hard bound on each UDP rate-limit table |
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
| `server_browser_idle_seconds` | 30 | Header and frame completion deadline |
| `rate_limit_entry_seconds` | 120 | Idle lifetime for UDP source accounting |

Rejected connections and protocol events use stable `service=... event=...` fields. Verbose logs omit PeerChat message bodies, nicknames, quit reasons, and raw Server Browser frames. Source addresses remain available for abuse diagnosis and should be retained only as long as operationally necessary.

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

## Online play

LAN support remains the compatibility baseline. The guarded direct-first service is deployed on a public IPv4 VPS with automatic relay fallback after three seconds. A controlled Wi-Fi/cellular pair that previously had a one-way direct path completed two consecutive relayed games and a reverse-host game. A LAN game under the same `auto` policy remained direct and allocated no relay. Evidence, configuration, and failure classification are in [DEPLOYMENT.md](DEPLOYMENT.md); further hardening remains tracked in [ROADMAP.md](ROADMAP.md).

## Release status

Pre-release preservation project. The repository intentionally excludes proprietary APKs, generated APK signatures, packet captures, UI dumps, and private signing keys. A project license and public-release review remain required before changing the repository to public visibility.
