# FruitSpy Internet Alpha Deployment

End-user walkthroughs:

- [Host FruitSpy at home](docs/HOME_HOSTING.md)
- [Host FruitSpy on a VPS](docs/VPS_HOSTING.md)

## Scope

This runbook deploys the single-node, direct-first Internet service with bounded UDP relay fallback. It does not provide accounts, transport encryption, or protection equivalent to a modern public game service. Keep access limited to known testers until the validation matrix passes.

FruitSpy currently requires public IPv4 reachability. The APK patch target may be an IPv4 address or a DNS name of at most 18 ASCII bytes because the native-library rewrite is size-preserving.

## Network contract

The same host must receive all four GameSpy services:

| Protocol | Port | Purpose |
| --- | ---: | --- |
| UDP | 27900 | Availability and QR2 host registration |
| TCP | 6667 | PeerChat staging rooms |
| TCP | 28910 | Server Browser discovery and negotiation messages |
| UDP | 27901 | NatNeg endpoint exchange and fallback gameplay relay |

Publish one stable DNS A record for the host. If the host is behind a router, forward each TCP or UDP port with the protocol shown above. Do not place a generic HTTP reverse proxy in front of these binary protocols.

## Install

The checked-in systemd unit expects this layout:

```text
/opt/fruitspy/                 repository checkout
/opt/fruitspy/venv/            Python virtual environment
/etc/fruitspy/config.json      machine-local Internet configuration
```

Copy or check out the repository at `/opt/fruitspy` without including local APKs, signing material, or captures. Then, on a current Linux host:

```text
sudo install -d -m 0755 /etc/fruitspy
sudo python3 -m venv /opt/fruitspy/venv
sudo /opt/fruitspy/venv/bin/pip install /opt/fruitspy/server
sudo cp /opt/fruitspy/server/config.json /etc/fruitspy/config.json
sudo chmod 0644 /etc/fruitspy/config.json
```

Use the checked-in configuration as the starting point:

- Keep `bind_host` at `0.0.0.0` unless the machine has a dedicated service address.
- Start with the checked-in admission limits. Change them only from measured test traffic.
- Patch clients with the short DNS name or public IPv4 address that reaches this host. The server configuration does not declare or advertise that client patch target.

The GameSpy secret in this compatibility configuration is embedded in the original client and is not an authentication credential.

### Relay policy

The checked-in configuration uses `relay.policy=auto`. FruitSpy first gives each peer the server-observed address of the other peer. If no authenticated client success report arrives within `relay.fallback_seconds`, the server sends each still-negotiating client a NatNeg `CONNECT_PING` from UDP 27901. The stock GameSpy state machine then treats that source as its peer, and FruitSpy forwards subsequent datagrams between only the two endpoints that complete the expected NatNeg exchange.

Set `relay.policy` to `direct` to prohibit relay allocations. Automatic fallback uses the same UDP 27901 listener and requires no additional firewall rule. Its safety boundaries are:

| Setting | Checked-in value | Boundary |
| --- | ---: | --- |
| `fallback_seconds` | 3 | Direct-attempt window before fallback |
| `session_seconds` | 900 | Relay inactivity timeout; authenticated endpoint traffic refreshes it |
| `packet_bytes` | 4096 | Maximum relayed UDP payload |
| `bytes_per_second` | 262144 | Per-endpoint byte-token refill rate |
| `byte_burst` | 524288 | Per-endpoint burst ceiling |
| `sessions` | 1024 | Global active relay cap |

Relay payloads are opaque. FruitSpy does not inspect or modify `hbgs` or gameplay messages.

`session_seconds` is an idle deadline, not an absolute match deadline. Traffic is accepted only from the two endpoints that completed the cookie-bearing relay handshake, and accepted traffic from either endpoint refreshes the deadline. This lets an active game continue beyond 15 minutes without allowing unrelated sources to retain its allocation. The 1,024-session cap, packet limit, and per-endpoint byte budgets remain hard bounds.

## Firewall

`deploy/nftables.rules.example` contains the four required allow rules. Merge them into the existing `inet filter input` chain; do not replace the host firewall wholesale. Preserve loopback, established traffic, and administrative access before enabling a default-drop policy.

Also restrict the same ports in the hosting provider's security group when one exists. During alpha validation, prefer source-IP allowlisting for the two tester networks. Mobile carrier addresses can change and may require temporary wider access.

Check a candidate nftables file before applying it:

```text
sudo nft --check --file /path/to/candidate-rules.nft
```

## Supervision

Install and start the hardened systemd unit:

```text
sudo cp /opt/fruitspy/deploy/fruitspy.service /etc/systemd/system/fruitspy.service
sudo systemctl daemon-reload
sudo systemctl enable --now fruitspy.service
sudo systemctl status fruitspy.service
```

The unit runs with a dynamic unprivileged identity, a read-only filesystem view, restricted address families, bounded file descriptors, bounded tasks, and a 256 MiB memory ceiling. The production-limit test campaign peaked at 42,112 KiB (about 41.1 MiB) RSS, including the test runner and synthetic clients; keep the 256 MiB ceiling because the scenarios did not saturate every limit simultaneously. `ExecStartPost` waits up to 15 seconds for all four protocol checks. A failed startup check causes the unit to fail and follow its bounded restart policy.

For accounts without root access, `deploy/fruitspy.user.service` runs from `~/fruitspy` with configuration in `~/.config/fruitspy/config.json`. Enable user lingering before logout and use `systemctl --user`; see the [VPS hosting guide](docs/VPS_HOSTING.md#rootless-systemd-alternative). Rootless deployment cannot change the host firewall, so an administrator or provider control panel must permit the four service ports.

The checked-in units send `SIGTERM` and allow 35 seconds for shutdown: 30 seconds for the configured relay drain plus five seconds for cleanup. `systemctl stop` or `systemctl restart` closes matchmaking listeners and control connections immediately while allowing established relays to forward until they close or reach the drain deadline. Direct games remain peer-to-peer and continue without the service. Do not use `kill -9` for routine deployment.

Inspect structured events without enabling payload-level diagnostics:

```text
sudo journalctl --unit fruitspy.service --since today
```

Inspect bounded aggregate metrics locally:

```text
curl --fail http://127.0.0.1:9108/metrics
```

The metrics listener is separate from the four GameSpy listeners. Configuration validation requires `metrics.bind_host` to be a loopback IP address and rejects a metrics port that matches any public service port. Never open TCP 9108 in the host or provider firewall; use an authenticated SSH tunnel if a remote scraper needs access. The fixed schema excludes IP addresses, connection/session identifiers, cookies, nicknames, room names, hostnames, messages, and payloads. Values are process-local and reset on restart; any external retention is the operator's responsibility.

During a graceful stop, `fruitspy_server_draining` becomes `1`. `fruitspy_drain_events_total` distinguishes a completed drain from a timeout, while `relay_closed` logs identify forced closures with `reason=server_drain_timeout`.

### Per-attempt diagnosis

Capture a counter baseline immediately before launching either game client:

```text
python -m fruitspy.diagnostics capture /tmp/fruitspy-before.json
```

After the attempt, report whether gameplay passed or failed:

```text
python -m fruitspy.diagnostics compare /tmp/fruitspy-before.json \
  --game-result failed
rm /tmp/fruitspy-before.json
```

Do not restart FruitSpy between these commands. The comparison rejects a baseline if any counter decreased because process-local metrics cannot span a restart. Do not run the health check inside the capture window: its Availability and NatNeg probes are intentionally real protocol traffic.

The output follows the client-visible pipeline:

| Stage | Evidence |
| --- | --- |
| `availability` | An accepted Fruit Ninja Availability request reached UDP 27900 |
| `qr_registration` | A host received and answered its QR2 challenge |
| `peerchat` | Two encrypted clients registered and joined staging rooms |
| `discovery` | Server Browser returned a nonempty host list |
| `natneg` | Two endpoint claims paired under one NatNeg cookie |
| `path` | A direct success report arrived, or a relay activated, became ready, was accepted by both clients, and forwarded gameplay |
| `rejections` | Admission and protocol rejection deltas, followed by their fixed service/reason series |

`SUMMARY failure_domain=server_pipeline` identifies the first incomplete server-observed boundary in the preceding lines. For a failed game, `client_or_peer_path` means a client reported direct negotiation success but FruitSpy cannot observe subsequent peer-to-peer gameplay. `client_or_gameplay` requires an established relay that forwarded packets with zero server drops. These conclusions isolate the server boundary; they do not claim that the application UI or gameplay logic succeeded.

The snapshot contains only fixed aggregate counters. It excludes source addresses, session cookies, connection identifiers, nicknames, room and host names, messages, and payloads. Use the timestamp-correlated structured journal only when per-session sequencing is needed.

## Health check

Run from the server itself:

```text
/opt/fruitspy/venv/bin/python -m fruitspy.healthcheck \
  --config /etc/fruitspy/config.json \
  --host 127.0.0.1 \
  --timeout 2
```

A healthy process reports `PASS` for Availability/QR2, PeerChat, Server Browser, and NatNeg and exits with status 0. Any failed listener reports `FAIL` and produces status 1. This checks local process readiness; it does not prove that upstream firewalls, DNS, port forwarding, or client networks can reach the server.

From a separate network, verify TCP exposure with a TCP connection tool and UDP exposure with packet capture or a copied FruitSpy health check using the public DNS name. Do not treat an ICMP ping as protocol readiness.

## Patch test clients

From the repository root, build each test client from the clean, lawfully owned Fruit Ninja 1.7.6 APK using the exact public DNS name:

```text
python patcher/patch_apk.py original.apk patched.apk \
  --server-host games.example.net \
  --non-interactive
```

The patcher rejects any whole-APK hash other than the supported clean release, applies the slow-motion fix, matchmaking fix, and custom server patch in that order to all three packaged ABIs, and writes a manifest beside the output. By default it discovers JDK `keytool` plus Android SDK `zipalign` and `apksigner`, creates a random per-user signing key on the first run, aligns and signs the APK, and verifies both alignment and its legacy-compatible v1 signature. Later builds reuse the same local identity for update compatibility.

Back up the FruitSpy signing directory documented in `README.md`. Losing it requires uninstalling the existing patched application before installing a build signed by a new key. Never upload source or generated APKs, signing keys, signing passwords, deployment manifests, packet captures, or device identifiers to the repository.

## Two-network validation matrix

Use two devices on genuinely independent networks. Disable Wi-Fi on a cellular test device or place the devices behind separate residential routers. Record UTC timestamps for correlation with server logs.

1. Confirm both devices resolve the patched DNS name to the intended public IPv4 address.
2. Run the local four-protocol health check immediately before testing.
3. Launch the patched game on both devices.
4. Have device A create an online match and remain in the staging room.
5. Have device B discover and join that match.
6. Confirm logs show QR2 registration, Server Browser discovery, both PeerChat participants, and `service=natneg event=peers_paired` for one session identifier.
7. Complete a game, return to staging, rematch, and complete two more consecutive games.
8. Repeat with device B hosting.
9. Confirm the health check still passes after malformed probes, disconnects, and rematches.
10. Preserve only the minimum redacted timing and outcome notes needed to classify failures.

A direct-connect pass requires discovery, room exchange, NatNeg pairing, gameplay completion, three consecutive games, a rematch, and both hosting directions. A staging-room success alone is not a direct gameplay pass.

## Failure classification

- No Availability response: DNS, UDP 27900, firewall, or wrong APK target.
- No PeerChat connection: TCP 6667 or admission limit.
- Empty discovery: QR2 challenge/registration, reported-server expiry, or TCP 28910.
- NatNeg never pairs: UDP 27901, mismatched session cookie, or one peer did not reach the service.
- NatNeg pairs but neither `direct_established` nor `relay_activated` appears: relay policy, fallback scheduling, or premature service restart.
- `relay_activated` appears without two `relay_peer_ready` events: one client did not accept or return the server's fallback ping on UDP 27901.
- `relay_established` appears but gameplay stalls: inspect `relay_metrics` drops, packet-size and byte-rate limits, then the client gameplay state.
- Local health passes but both remote clients fail: upstream firewall, security group, port forwarding, or DNS.

Each `service=natneg event=client_report` record includes the client index, the GameSpy `negResult` boolean, NAT type, and mapping scheme without retaining the game name or packet payload. `result=success result_code=1` means the client accepted a direct or relay `CONNECT_PING`; `result=failure result_code=0` means negotiation failed. NAT type and mapping values remain client-reported diagnostics. Completed gameplay is still the end-to-end acceptance signal.

Do not weaken admission limits or disable the firewall to hide a classified failure. Capture the exact failed stage and change only the responsible boundary.

### Captured traversal boundary

A controlled comparison isolated the Internet failure after successful discovery and endpoint exchange:

- In the known-good LAN trace, the negotiated game sockets exchanged three NatNeg `CONNECT` datagrams in each direction. Five seconds later they exchanged `hbgs` setup traffic, followed by 328 non-NatNeg datagrams over the same endpoints during completed gameplay.
- In the failed Wi-Fi/cellular trace, the Wi-Fi client sent nine NatNeg `CONNECT` datagrams to the server-observed cellular endpoint at approximately 715 ms intervals. It received no peer datagram, and no `hbgs` or gameplay packet followed.
- Both Internet clients reached the VPS, joined the same NatNeg session, and used `use_game_port=False`. The Wi-Fi client reported full-cone NAT with consistent-port mapping; the cellular client reported unknown NAT with an unrecognized mapping.

The endpoint exchange is correct, but the direct UDP path is not bidirectional. This rules out FruitSpy listener health and GT2 initialization as the immediate boundary. The trace cannot distinguish endpoint-dependent carrier NAT from carrier filtering, but either condition requires a relay or a mutually reachable overlay network; changing the four GameSpy service ports will not fix it.

### Relay validation outcome

With `relay.policy=auto` and a three-second deadline, the same Wi-Fi/cellular pair moved to relay after the direct path remained unconfirmed. Both endpoints returned the fallback ping, reported `result_code=1`, and established the relay within 301 ms of activation. The pair completed two consecutive games and another game with hosting reversed.
Relay lifecycle totals were 312 packets / 14,488 bytes, 297 packets / 13,400 bytes, and 554 packets / 25,396 bytes for the reverse-host game, with zero drops in all three sessions.

The same build and policy were then exercised on the LAN. Direct success reports arrived 16 ms and 27 ms after pairing, canceled the scheduled fallback, and gameplay completed without a relay allocation.

## Rollback

```text
sudo systemctl disable --now fruitspy.service
```

Remove the four FruitSpy firewall rules and DNS record after the test window. Direct and relay sessions are ephemeral; FruitSpy has no durable matchmaking database to migrate or recover.

## Relay implementation result

The evidence gate is closed. FruitSpy now attempts direct traversal first, switches only unconfirmed sessions to the UDP 27901 relay after a bounded timeout, and leaves the proven LAN path direct. The relay accepts opaque traffic only after both server-observed endpoints return the expected cookie-bearing NatNeg ping.

The relay lifetime gate found that the original timer was absolute and would close active gameplay at 900 seconds. `session_seconds` now measures authenticated endpoint inactivity instead. An accelerated regression verifies active forwarding across the original deadline, rejection of an unauthenticated keepalive, and cleanup after one quiet interval. The production service sustained 92 bidirectional rounds over 905.005 seconds, forwarding 184 packets with zero drops; its final periodic record at 900.996 seconds crossed the former absolute deadline.
