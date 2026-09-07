# Host FruitSpy on a VPS

This guide installs FruitSpy on an IPv4 Linux virtual private server. A VPS is the preferred Internet-alpha topology because the matchmaking server is outside both players' home networks and can observe each client's public UDP endpoint.

FruitSpy is a preservation project for Fruit Ninja 1.7.6. It does not distribute the game. Each player must patch a lawfully obtained APK and sign their own copy.

## Before you begin

You need:

- A VPS with a public IPv4 address
- A current Linux distribution using systemd; the commands below target Ubuntu or Debian
- SSH access with a non-root account that can use `sudo`
- A short DNS name pointing to the VPS
- FruitSpy source code
- Two devices on independent networks for acceptance testing

The DNS name patched into the APK must be fewer than 20 ASCII characters. FruitSpy currently requires IPv4; an IPv6-only VPS is not supported.

A low-end general-purpose instance should be adequate for a controlled FruitSpy deployment. The latest production-limit campaign peaked at **42,112 KiB (about 41.1 MiB) of RAM** while exercising every configured capacity boundary. Keep the checked-in **256 MiB process memory limit** and use **at least 512 MiB of total VPS RAM** as a conservative starting point so the operating system retains headroom. The test measurement includes its synthetic clients, and its capacity scenarios ran separately rather than all at once; it is not a guaranteed worst-case maximum. CPU throughput and sustained relay bandwidth remain separate sizing limits.

## 1. Create and secure the VPS

Create an Ubuntu or Debian VPS with a dedicated public IPv4 address. Configure SSH key authentication before exposing FruitSpy. Keep the provider's recovery console available in case a firewall rule blocks SSH.

Update the operating system and install Python tooling:

```text
sudo apt update
sudo apt upgrade
sudo apt install python3 python3-venv
python3 --version
```

Python 3.11 or newer is required.

Do not install a web server or HTTP reverse proxy for FruitSpy. The game uses raw TCP and UDP, not HTTP.

## 2. Configure DNS

Create a DNS `A` record pointing to the VPS public IPv4 address. For example:

```text
fn.example.net -> 203.0.113.20
```

Requirements:

- The complete hostname must be fewer than 20 ASCII characters.
- It must resolve directly to the VPS IPv4 address.
- Do not enable an HTTP/CDN proxy for the record.
- Patch every test APK with this exact hostname.

Verify DNS from your computer and from the VPS. On Linux:

```text
getent ahostsv4 fn.example.net
```

The result must contain the VPS public IPv4 address.

## 3. Install FruitSpy

Place a clean FruitSpy checkout at `/opt/fruitspy`. Do not copy APKs, signing keys, passwords, packet captures, or device dumps to the VPS.

If Git access is available:

```text
git clone https://github.com/Antonio231102/fruitspy.git fruitspy
sudo install -d -m 0755 /opt/fruitspy
sudo cp -a fruitspy/. /opt/fruitspy/
```

Alternatively, upload a source archive, extract it locally, and copy only the clean repository contents to `/opt/fruitspy`.

Create the Python environment and install the server package:

```text
sudo python3 -m venv /opt/fruitspy/venv
sudo /opt/fruitspy/venv/bin/python -m pip install /opt/fruitspy/server
```

FruitSpy has no runtime packages outside the Python standard library. Installing it creates the `fruitspy-server` and `fruitspy-healthcheck` commands inside the virtual environment.

## 4. Create the server configuration

Create a machine-local configuration outside the repository:

```text
sudo install -d -m 0755 /etc/fruitspy
sudo cp /opt/fruitspy/server/config.json /etc/fruitspy/config.json
sudo chmod 0644 /etc/fruitspy/config.json
sudoedit /etc/fruitspy/config.json
```

Keep the default bind address:

```json
{
  "bind_host": "0.0.0.0"
}
```

Keep the standard ports and checked-in limits for the initial deployment. Patch each APK with the short DNS name or public IPv4 address that reaches the VPS; FruitSpy does not declare or advertise that hostname in its server configuration.

The GameSpy secret in this configuration is embedded in the original client protocol and is not an administrative password.

Keep the administrative metrics listener on loopback:

```json
{
  "metrics": {
    "bind_host": "127.0.0.1",
    "port": 9108
  }
}
```

FruitSpy rejects non-loopback metrics addresses and reuse of a public service port.

## 5. Open the provider firewall

In the VPS provider's firewall or security-group control panel, allow:

| Protocol | Destination port | Purpose |
| --- | ---: | --- |
| UDP | 27900 | Availability and QR2 registration |
| TCP | 6667 | PeerChat staging rooms |
| TCP | 28910 | Server Browser discovery |
| UDP | 27901 | NatNeg endpoint exchange and fallback gameplay relay |

Keep SSH restricted to your administrative source address when possible. During a controlled alpha, restrict FruitSpy to the known tester networks when their addresses are stable. Mobile carrier addresses can change and might require a temporary wider rule.

Do not open gameplay port 6500 on the VPS. Direct-capable clients send gameplay peer-to-peer. Automatic fallback reuses the existing UDP 27901 listener, so its packet-size, byte-rate, idle-timeout, and session-cap limits apply to relayed gameplay.

## 6. Configure the Linux firewall

Ubuntu and Debian users can use UFW. Allow SSH before enabling the firewall:

```text
sudo ufw allow OpenSSH
sudo ufw allow 6667/tcp
sudo ufw allow 28910/tcp
sudo ufw allow 27900/udp
sudo ufw allow 27901/udp
sudo ufw enable
sudo ufw status verbose
```

If the server already uses nftables, merge the rules in `/opt/fruitspy/deploy/nftables.rules.example` into the existing `inet filter input` chain. Do not replace an existing firewall file blindly, and validate candidate rules before loading them:

```text
sudo nft --check --file /path/to/candidate-rules.nft
```

Both the provider firewall and the operating-system firewall must allow the traffic.

## 7. Install the systemd service

The checked-in service runs FruitSpy with an ephemeral unprivileged identity and restricts filesystem writes, devices, kernel interfaces, address families, file descriptors, tasks, and memory.

Install and verify it:

```text
sudo cp /opt/fruitspy/deploy/fruitspy.service /etc/systemd/system/fruitspy.service
sudo systemd-analyze verify /etc/systemd/system/fruitspy.service
sudo systemctl daemon-reload
sudo systemctl enable --now fruitspy.service
sudo systemctl status fruitspy.service
```

The service runs a four-protocol readiness check after startup. It waits up to 15 seconds for Availability/QR2, PeerChat, Server Browser, and NatNeg. If startup health fails, systemd marks the service failed and applies its restart policy.


For maintenance or deployment, use `systemctl stop` or `systemctl restart`; do not kill the Python process directly. The unit sends `SIGTERM`, FruitSpy immediately refuses new matchmaking, and existing direct games continue peer-to-peer. Active relays may continue for the configured 30-second drain window before closing explicitly with `reason=server_drain_timeout`. The unit's 35-second stop deadline leaves five seconds for cleanup.
View recent logs:

```text
sudo journalctl --unit fruitspy.service --since today
```

Follow logs during a test:

```text
sudo journalctl --unit fruitspy.service --follow
```

Diagnostics omit chat bodies, nicknames, client-provided quit reasons, and raw Server Browser frames. Retain source-address logs only as long as needed to diagnose the alpha.

Inspect aggregate operational metrics from the VPS:

```text
curl --fail http://127.0.0.1:9108/metrics
```

Do not open TCP 9108 in UFW or the provider firewall. The endpoint has fixed, bounded labels and excludes source addresses, cookies, identifiers, nicknames, room names, hostnames, message content, and packet payloads. It resets on service restart and stores nothing on disk. Use an authenticated SSH tunnel rather than exposing it when remote collection is required.

While a stop is draining active relays, the metrics endpoint remains available with `fruitspy_server_draining 1`. Drain counters and structured `drain_started`, `drain_completed`, or `drain_timed_out` events distinguish clean maintenance from a forced relay deadline.

For a single game attempt, use `python -m fruitspy.diagnostics capture` immediately before launching the clients and `python -m fruitspy.diagnostics compare` afterward. The comparison reports each matchmaking stage and rejection delta without storing addresses, cookies, names, or payloads. See [Per-attempt diagnosis](../DEPLOYMENT.md#per-attempt-diagnosis) for the exact commands and interpretation.

### Rootless systemd alternative

If the VPS account cannot use `sudo`, FruitSpy can run from the account's home directory because all four service ports are above 1024. This does not grant permission to change the host firewall.

Use this layout:

```text
~/fruitspy/                         repository checkout
~/.config/fruitspy/config.json     machine-local Internet configuration
~/.config/systemd/user/fruitspy.service
```

Copy the unified configuration and protect it:

```text
mkdir -p ~/.config/fruitspy ~/.config/systemd/user
cp ~/fruitspy/server/config.json ~/.config/fruitspy/config.json
chmod 600 ~/.config/fruitspy/config.json
```

Edit `~/.config/fruitspy/config.json` only when the account needs different bind addresses, ports, limits, or timeouts. The public address remains a client APK patching choice.

Enable user-service persistence and install the rootless unit:

```text
loginctl enable-linger
cp ~/fruitspy/deploy/fruitspy.user.service ~/.config/systemd/user/fruitspy.service
systemctl --user daemon-reload
systemctl --user enable --now fruitspy.service
systemctl --user status fruitspy.service
```

Some providers require an administrator to enable lingering. Confirm `loginctl show-user \"$USER\" --property=Linger` reports `Linger=yes`; otherwise the user service can stop after the last login session.

The rootless unit runs directly from the source checkout with `/usr/bin/python3`, so it works when the provider has Python 3.11 or newer but does not install the optional `python3-venv` package. Follow logs with:

```text
journalctl --user --unit fruitspy.service --follow
```

The rootless account cannot install UFW or nftables rules. Configure the provider firewall in its control panel and have the VPS administrator confirm that the host firewall permits TCP 6667/28910 and UDP 27900/27901. Local health can pass while every public check times out if either firewall still blocks those ports.

## 8. Run local and public health checks

On the VPS:

```text
sudo /opt/fruitspy/venv/bin/python -m fruitspy.healthcheck \
  --config /etc/fruitspy/config.json \
  --host 127.0.0.1 \
  --timeout 2
```

For the rootless layout:

```text
cd ~/fruitspy/server
python3 -m fruitspy.healthcheck \
  --config ~/.config/fruitspy/config.json \
  --host 127.0.0.1 \
  --timeout 2
```

Expected output contains four passes:

```text
PASS service=availability_qr_udp detail=ok
PASS service=peerchat_tcp detail=ok
PASS service=server_browser_tcp detail=ok
PASS service=natneg_udp detail=ok
```

Then run the same health check from a computer on another network:

```text
cd server
python -m fruitspy.healthcheck \
  --config config.json \
  --host fn.example.net \
  --timeout 3
```

The local check proves process readiness. The remote check additionally exercises DNS, the provider firewall, and the host firewall. A web port-checking service usually tests only TCP and cannot verify both required UDP services.

## 9. Patch the client APKs

On your local computer, not on the VPS:

```text
python server/patch_apk.py original.apk patched.apk \
  --server-host fn.example.net \
  --non-interactive
```

Use the exact hostname in `/etc/fruitspy/config.json`. The patcher applies both client transformations to all three ABIs, creates or reuses a random per-user signing identity, aligns and signs the APK, verifies the result, and writes a manifest beside it. JDK `keytool` and Android SDK Build Tools are required.

The first patched installation cannot update an original copy or a build signed by another key. Later builds from this computer can update one another because the local key is persistent. Back up the FruitSpy signing directory documented in `README.md`; never upload an original or patched APK, signing material, generated deployment manifest, or extracted game assets to the VPS or repository.

## 10. Validate Internet gameplay

Use two clients on independent networks, such as two separate residential connections or one residential connection and one cellular connection:

1. Record a UTC test start time.
2. Confirm the local and remote four-protocol health checks pass.
3. Have device A host a match.
4. Have device B discover and join it.
5. Confirm the logs contain QR2 registration, Server Browser discovery, both PeerChat clients, and `service=natneg event=peers_paired` with one session identifier.
6. Confirm either `direct_established` or the complete `relay_activated`, two-peer `relay_ready`, and `relay_established` sequence.
7. Complete three consecutive games and a rematch.
8. Repeat with device B hosting.
9. Run the health checks again.

Success in the staging room alone is not a gameplay pass. Internet validation requires discovery, staging, NatNeg pairing, completed direct or relayed gameplay, a rematch, and both hosting directions.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `fruitspy.service` fails during startup | `systemctl status`, `journalctl`, configuration path, and duplicate listeners |
| Local health fails | FruitSpy process and `/etc/fruitspy/config.json` |
| Local passes but remote fails | Provider firewall, UFW/nftables, DNS, and whether the VPS actually has public IPv4 |
| Availability fails | UDP 27900 and the patched hostname |
| PeerChat fails | TCP 6667 and admission events |
| No game appears | QR2 registration, reported-server expiry, and TCP 28910 |
| NatNeg never pairs | UDP 27901, timestamps, and matching NatNeg session identifiers |
| NatNeg pairs but gameplay times out | Check for `direct_established`; otherwise require `relay_activated`, both `relay_peer_ready` events, and `relay_established`, then inspect relay drop metrics |

Do not solve a failed check by disabling the entire firewall or removing admission limits. Identify whether the failure is local process readiness, public reachability, matchmaking, NatNeg, or direct gameplay.

`client_report result_code` is the GameSpy boolean: `1` is success and `0` is failure. A success means the client accepted either its direct peer or FruitSpy's fallback ping; completed gameplay remains the end-to-end signal. See the [captured traversal boundary](../DEPLOYMENT.md#captured-traversal-boundary).

## Update FruitSpy

Stop the service before replacing code:

```text
sudo systemctl stop fruitspy.service
```

Replace `/opt/fruitspy` with the reviewed source, preserve `/etc/fruitspy/config.json`, and reinstall the package:

```text
sudo /opt/fruitspy/venv/bin/python -m pip install --force-reinstall /opt/fruitspy/server
sudo systemctl start fruitspy.service
sudo systemctl status fruitspy.service
```

For a rootless deployment, stop the user service, replace `~/fruitspy` while preserving `~/.config/fruitspy/config.json`, and restart:

```text
systemctl --user stop fruitspy.service
systemctl --user start fruitspy.service
systemctl --user status fruitspy.service
```

Run both health checks after every update. Do not overwrite the machine-local configuration with the example file without reviewing new settings.

## Stop and remove the service

```text
sudo systemctl disable --now fruitspy.service
sudo rm /etc/systemd/system/fruitspy.service
sudo systemctl daemon-reload
```

For a rootless deployment:

```text
systemctl --user disable --now fruitspy.service
rm ~/.config/systemd/user/fruitspy.service
systemctl --user daemon-reload
```

Remove the four FruitSpy rules from the provider firewall and UFW or nftables. Remove the DNS record when the host is no longer in use.

FruitSpy stores matchmaking state only in memory. There is no gameplay database to back up or delete. See [Internet Alpha Deployment](../DEPLOYMENT.md) for the full acceptance matrix, operational boundaries, and relay gate.
