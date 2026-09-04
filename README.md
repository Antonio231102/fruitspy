# FruitSpy

FruitSpy is a standalone GameSpy-compatible multiplayer service for Fruit Ninja 1.7.6. The first checkpoint restores two-player LAN matchmaking and direct gameplay without depending on the retired GameSpy infrastructure.

## Checkpoint 1: LAN multiplayer

Implemented services:

- Availability and QR2 on UDP 27900
- PeerChat on TCP 6667
- Server Browsing on TCP 28910
- NAT Negotiation on UDP 27901
- Encrypted PeerChat and Enctype-X Server Browser responses
- Two-player staging rooms, host publication, and direct peer traversal

The LAN path has been exercised across an Android emulator and a Galaxy S4 for repeated games. After matchmaking, gameplay traffic flows directly between the devices.

## Hosting guides

- [Host FruitSpy at home](docs/HOME_HOSTING.md) — Windows-oriented router, firewall, DNS, startup, and external-test instructions.
- [Host FruitSpy on a VPS](docs/VPS_HOSTING.md) — Ubuntu/Debian installation, root or rootless systemd, cloud firewall, health checks, updates, and removal.
- [Internet alpha deployment reference](DEPLOYMENT.md) — operator acceptance matrix, failure classification, and relay gate.

Use a VPS when either game client will share the home network with a home-hosted FruitSpy server. The current Internet profile must observe each game client from outside that client's NAT; mixed local/remote play behind the server's router is not a supported acceptance topology.

## Run the server

Python 3.11 or newer is required.

```text
cd server
python -m fruitspy --config config.json
```

For protocol diagnostics, add `--verbose`. `config.json` is the dependency-free `lan` profile: it binds all service sockets and automatically selects the machine's local IPv4 address. Firewalls must allow the four ports listed above.

`config.internet.example.json` is the guarded direct-connect Internet profile. Copy it to the ignored `config.local.json`, replace `games.example.net` with the public DNS name used by the patched clients, and expose TCP 6667/28910 plus UDP 27900/27901. Start it with:

```text
python -m fruitspy --config config.local.json
```

Do not expose a development checkout directly to the public Internet. Use an OS firewall that permits only the four protocol ports, run the process as an unprivileged account under a supervisor, and retain minimal logs. The guarded Linux deployment and two-network acceptance procedure are documented in [DEPLOYMENT.md](DEPLOYMENT.md).

Check all four local listeners after startup:

```text
python -m fruitspy.healthcheck --config config.local.json
```

The command validates Availability/QR2 and NatNeg responses, checks both TCP listeners, and exits nonzero if any service is unavailable.

### Internet admission controls

Both profiles use the checked-in safety limits; Internet operators should tune them only from measured traffic:

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
| `peerchat_handshake_seconds` | 15 | Absolute deadline for PeerChat registration |
| `server_browser_idle_seconds` | 30 | Header and frame completion deadline |
| `rate_limit_entry_seconds` | 120 | Idle lifetime for UDP source accounting |

Rejected connections and protocol events use stable `service=... event=...` fields. In `internet` mode, verbose logs omit PeerChat message bodies, nicknames, quit reasons, and raw Server Browser frames. Source addresses remain available for abuse diagnosis and should be retained only as long as operationally necessary.

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

The suite covers cryptography, configuration validation, admission controls, connection deadlines, listener health, malformed-input recovery, PeerChat, QR2 registration and rate limiting, server discovery, NatNeg pairing, and deterministic APK patching.

## Online play

LAN support remains the compatibility baseline. Online development now includes explicit deployment profiles, observed public-port publication, bounded protocol and state resources, TCP and UDP admission controls, connection deadlines, structured NatNeg lifecycle events, a four-protocol health check, systemd supervision, an nftables allowlist example, privacy-aware Internet diagnostics, and IPv4-or-DNS APK patch targets. Cross-network validation, relay fallback, and broader abuse controls remain tracked in [ROADMAP.md](ROADMAP.md).

## Release status

Pre-release preservation project. The repository intentionally excludes proprietary APKs, generated APK signatures, packet captures, UI dumps, and private signing keys. A project license and public-release review remain required before changing the repository to public visibility.
