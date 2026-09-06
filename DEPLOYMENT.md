# FruitSpy Internet Alpha Deployment

End-user walkthroughs:

- [Host FruitSpy at home](docs/HOME_HOSTING.md)
- [Host FruitSpy on a VPS](docs/VPS_HOSTING.md)

## Scope

This runbook deploys the single-node, direct-connect Internet alpha. It does not provide a gameplay relay, accounts, transport encryption, or protection equivalent to a modern public game service. Keep access limited to known testers until the two-network validation matrix passes.

FruitSpy currently requires public IPv4 reachability. The APK patch target may be an IPv4 address or a DNS name shorter than 20 ASCII bytes because the native-library rewrite is size-preserving.

## Network contract

The same host must receive all four GameSpy services:

| Protocol | Port | Purpose |
| --- | ---: | --- |
| UDP | 27900 | Availability and QR2 host registration |
| TCP | 6667 | PeerChat staging rooms |
| TCP | 28910 | Server Browser discovery and negotiation messages |
| UDP | 27901 | NatNeg endpoint exchange |

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

The unit runs with a dynamic unprivileged identity, a read-only filesystem view, restricted address families, bounded file descriptors, bounded tasks, and a 256 MiB memory ceiling. `ExecStartPost` waits up to 15 seconds for all four protocol checks. A failed startup check causes the unit to fail and follow its bounded restart policy.

For accounts without root access, `deploy/fruitspy.user.service` runs from `~/fruitspy` with configuration in `~/.config/fruitspy/config.json`. Enable user lingering before logout and use `systemctl --user`; see the [VPS hosting guide](docs/VPS_HOSTING.md#rootless-systemd-alternative). Rootless deployment cannot change the host firewall, so an administrator or provider control panel must permit the four service ports.

Inspect structured events without enabling payload-level diagnostics:

```text
sudo journalctl --unit fruitspy.service --since today
```

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

From the repository root, patch each lawfully owned Fruit Ninja 1.7.6 APK with the exact public DNS name:

```text
python server/patch_apk.py original.apk patched-unsigned.apk \
  --server-host games.example.net \
  --report server/apk-patch-report.json
```

Align and sign each generated APK with the operator's own key. Never upload APKs, signing keys, passwords, packet captures, or device identifiers to the repository.

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
- NatNeg pairs but gameplay times out: direct traversal failed after endpoint exchange; likely symmetric NAT, CGNAT, or a restrictive mobile network.
- Local health passes but both remote clients fail: upstream firewall, security group, port forwarding, or DNS.

Each `service=natneg event=client_report` record includes the client index, negotiation result, NAT type, and mapping scheme without retaining the game name or packet payload. Treat these client-reported values as advisory rather than proof of a bidirectional path. A successful gameplay trace produced `result=deadbeat_partner`, while a failed Wi-Fi/cellular trace produced `result=success` from both clients. Packet capture or completed gameplay is the acceptance signal; unknown numeric values remain visible through the corresponding `_code` fields.

Do not weaken admission limits or disable the firewall to hide a classified failure. Capture the exact failed stage and change only the responsible boundary.

### Captured traversal boundary

A controlled comparison isolated the Internet failure after successful discovery and endpoint exchange:

- In the known-good LAN trace, the negotiated game sockets exchanged three NatNeg `CONNECT` datagrams in each direction. Five seconds later they exchanged `hbgs` setup traffic, followed by 328 non-NatNeg datagrams over the same endpoints during completed gameplay.
- In the failed Wi-Fi/cellular trace, the Wi-Fi client sent nine NatNeg `CONNECT` datagrams to the server-observed cellular endpoint at approximately 715 ms intervals. It received no peer datagram, and no `hbgs` or gameplay packet followed.
- Both Internet clients reached the VPS, joined the same NatNeg session, and used `use_game_port=False`. The Wi-Fi client reported full-cone NAT with consistent-port mapping; the cellular client reported unknown NAT with an unrecognized mapping.

The endpoint exchange is correct, but the direct UDP path is not bidirectional. This rules out FruitSpy listener health and GT2 initialization as the immediate boundary. The trace cannot distinguish endpoint-dependent carrier NAT from carrier filtering, but either condition requires a relay or a mutually reachable overlay network; changing the four GameSpy service ports will not fix it.

## Rollback

```text
sudo systemctl disable --now fruitspy.service
```

Remove the four FruitSpy firewall rules and DNS record after the test window. Direct peer sessions are ephemeral; FruitSpy has no durable matchmaking database to migrate or recover.

## Relay gate

The Wi-Fi/cellular comparison satisfies the relay evidence gate: discovery, PeerChat, host publication, and NatNeg pairing completed, but the negotiated peer path remained one-way and never reached `hbgs`. Implement relay fallback only after a bounded direct attempt, keep direct UDP as the default, and leave the proven LAN path unchanged.
