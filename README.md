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

## Run the server

Python 3.11 or newer is required.

```text
cd server
python -m fruitspy --config config.json
```

For protocol diagnostics, add `--verbose`. `config.json` is the dependency-free `lan` profile: it binds all service sockets and automatically selects the machine's local IPv4 address. Firewalls must allow the four ports listed above.

`config.internet.example.json` is the initial direct-connect Internet profile. Copy it to a machine-local configuration, replace `games.example.net` with the public DNS name used by the patched clients, and expose TCP 6667/28910 plus UDP 27900/27901. Do not expose the service publicly yet: connection admission and per-source rate limiting remain roadmap work.

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

The suite covers cryptography, PeerChat, QR2 registration, server discovery, NatNeg pairing, and deterministic APK patching.

## Online play

LAN support remains the compatibility baseline. Initial online development now includes explicit `lan` and `internet` profiles, observed public-port publication for hosts behind NAT, bounded protocol frames, and IPv4-or-DNS APK patch targets. Public deployment, rate limiting, relay fallback, abuse controls, and release work are tracked in [ROADMAP.md](ROADMAP.md).

## Release status

Pre-release preservation project. The repository intentionally excludes proprietary APKs, generated APK signatures, packet captures, UI dumps, and private signing keys. A project license and public-release review remain required before changing the repository to public visibility.
