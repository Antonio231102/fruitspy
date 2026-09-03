# Host FruitSpy at Home

This guide runs FruitSpy on a computer in your home and lets Fruit Ninja 1.7.6 clients connect over the Internet. It is intended for small, controlled sessions with people you know.

FruitSpy does not distribute Fruit Ninja. Each player must use a lawfully obtained Fruit Ninja 1.7.6 APK and sign their own patched copy.

## Before you begin

You need:

- A computer that can remain powered on while people play
- Python 3.11 or newer
- Administrator access to that computer
- Access to your router settings
- A public IPv4 address from your ISP
- A short DNS name that points to that public address
- FruitSpy source code
- Two test clients outside the server's home network for the first Internet test

The DNS name patched into the APK must be fewer than 20 ASCII characters. `fn.example.net` fits; a long dynamic-DNS hostname might not.

FruitSpy uses IPv4. An IPv6-only connection cannot host the current protocol.

## 1. Check for a public IPv4 address

Open your router's status page and record its WAN or Internet IPv4 address. Compare it with the public IPv4 address reported when you search the web for `what is my IP`.

You probably have carrier-grade NAT and cannot accept normal port forwards when:

- The two addresses differ, or
- The router WAN address starts with `100.64` through `100.127`, `10.`, `192.168.`, or `172.16` through `172.31`

If you are behind carrier-grade NAT, ask your ISP for a public IPv4 address. Some providers call this a public IP, static IP, or removal from CGNAT. A generic HTTP tunnel is not a substitute because FruitSpy requires raw TCP and UDP and must observe client UDP source endpoints.

## 2. Give the server computer a stable LAN address

Create a DHCP reservation in your router for the server computer. For example, reserve `192.168.1.50` for that computer's network adapter.

Do not rely on an address that the router can later assign to another device. Every port forward in this guide points to this LAN address.

On Windows, `ipconfig` displays the current IPv4 address and default gateway. The default gateway is usually the router administration address.

## 3. Configure a short DNS name

Create a DNS `A` record that points to your public IPv4 address. If your ISP changes the address periodically, use a dynamic-DNS service or an automatic DNS updater.

Requirements:

- The record must resolve to an IPv4 address.
- The complete hostname must be fewer than 20 ASCII characters.
- Every Internet-test APK must be patched with exactly this hostname.
- Update the record whenever your public address changes.

DNS changes can take time to propagate. Verify the result from a device outside your home network before troubleshooting FruitSpy.

## 4. Prepare FruitSpy

Download or extract FruitSpy to a permanent directory. The examples below use:

```text
C:\FruitSpy
```

Create the machine-local configuration from Command Prompt:

```text
cd C:\FruitSpy\server
copy config.internet.example.json config.local.json
notepad config.local.json
```

Set these fields:

```json
{
  "mode": "internet",
  "bind_host": "0.0.0.0",
  "advertise_host": "fn.example.net"
}
```

Replace `fn.example.net` with your DNS name. Keep the standard ports and checked-in safety limits for the first test. `config.local.json` is ignored by Git.

The GameSpy secret in the example is part of the original compatibility protocol. It is not a private account password.

## 5. Allow FruitSpy through the computer firewall

FruitSpy requires these inbound ports:

| Protocol | Port | Service |
| --- | ---: | --- |
| UDP | 27900 | Availability and QR2 |
| TCP | 6667 | PeerChat |
| TCP | 28910 | Server Browser |
| UDP | 27901 | NatNeg |

On Windows, open PowerShell as Administrator and run:

```powershell
New-NetFirewallRule `
  -DisplayName "FruitSpy TCP" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 6667,28910

New-NetFirewallRule `
  -DisplayName "FruitSpy UDP" `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 27900,27901
```

These rules open only FruitSpy's four protocol ports. They do not configure your router.

## 6. Forward the ports in your router

Create four port-forwarding entries targeting the reserved LAN address of the server computer:

| External port | Internal port | Protocol | Destination |
| ---: | ---: | --- | --- |
| 27900 | 27900 | UDP | Server LAN address |
| 6667 | 6667 | TCP | Server LAN address |
| 28910 | 28910 | TCP | Server LAN address |
| 27901 | 27901 | UDP | Server LAN address |

Do not select `TCP/UDP` when the router lets you choose the exact protocol. Do not forward gameplay port 6500 to the FruitSpy computer; FruitSpy uses NatNeg to exchange the game clients' observed endpoints.

Some routers have both an ISP gateway and a separate Wi-Fi router. If both devices perform NAT, either forward through both devices or place one in bridge/access-point mode.

## 7. Start and check the server

From Command Prompt:

```text
cd C:\FruitSpy\server
py -3 -m fruitspy --config config.local.json
```

Leave that window open. In a second window, check all four local services:

```text
cd C:\FruitSpy\server
py -3 -m fruitspy.healthcheck --config config.local.json --host 127.0.0.1
```

Expected result:

```text
PASS service=availability_qr_udp detail=ok
PASS service=peerchat_tcp detail=ok
PASS service=server_browser_tcp detail=ok
PASS service=natneg_udp detail=ok
```

This proves that FruitSpy is running locally. It does not prove that the ISP and router allow Internet traffic.

### Optional Windows startup task

For unattended use, create a Task Scheduler task that runs whether or not you are logged in:

- Trigger: `At startup`
- Program: the full path to `python.exe`
- Arguments: `-m fruitspy --config C:\FruitSpy\server\config.local.json`
- Start in: `C:\FruitSpy\server`
- Enable `Restart the task if it fails`
- Do not store APK signing passwords in the task

Run the health check after restarting Windows before opening an Internet test window.

## 8. Patch each test APK

From the FruitSpy directory:

```text
py -3 server\patch_apk.py original.apk patched-unsigned.apk ^
  --server-host fn.example.net ^
  --report server\apk-patch-report.json
```

Replace the example hostname with the exact DNS name from `config.local.json`. Align and sign the generated APK with your own Android signing key.

A self-signed patched APK cannot normally update an installed copy signed by a different key. Back up anything important before uninstalling the existing app, and never publish the original or patched APK, signing key, password, or extracted game assets.

## 9. Verify access from another network

Do not test only from the server's Wi-Fi. That checks router hairpin behavior rather than real Internet reachability.

From a computer on another network with the FruitSpy source available:

```text
cd server
python -m fruitspy.healthcheck \
  --config config.internet.example.json \
  --host fn.example.net \
  --timeout 3
```

All four services must pass. A web-based port checker normally tests only TCP and cannot prove that UDP 27900 and 27901 work.

## 10. Run the two-network game test

Use game devices on networks outside the server's home LAN:

1. Start FruitSpy and run the local health check.
2. Have device A create an online match.
3. Have device B discover and join it.
4. Confirm the server log contains QR2 registration, Server Browser discovery, two PeerChat participants, and `service=natneg event=peers_paired`.
5. Complete three consecutive games and a rematch.
6. Repeat with device B hosting.
7. Run the health check again.

A staging-room connection by itself is not a gameplay pass.

## Same-home client limitation

Do not use a game client on the same LAN as the self-hosted Internet server for the first direct-connect test. The server can observe that client's private or hairpin-NAT endpoint and publish an address that a remote peer cannot reach.

Supported test arrangements:

- Server and both clients at home: use the `lan` profile.
- Home server with both clients on external networks: use the `internet` profile.
- Home server with one local and one remote client: currently router-dependent and not a supported acceptance test.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Local health check fails | FruitSpy process, configuration path, duplicate processes, and computer firewall |
| Local health passes but remote health fails | CGNAT, router forwarding, ISP filtering, double NAT, and public DNS |
| Availability fails remotely | UDP 27900 and the APK hostname |
| PeerChat fails | TCP 6667 and connection admission logs |
| No games appear | QR2 registration and TCP 28910 |
| NatNeg never pairs | UDP 27901 and both clients' timestamps/session identifiers |
| NatNeg pairs but gameplay times out | Symmetric NAT, CGNAT, or restrictive mobile networking; this is evidence for the future relay milestone |
| It worked yesterday | Check whether the public IPv4 address or DNS record changed |

Do not disable all firewall protection as a troubleshooting step. Change one boundary at a time and rerun the health check.

## Stop hosting

Stop the FruitSpy process with `Ctrl+C`, disable its startup task if one was created, remove the four router forwards, and remove the Windows rules with elevated PowerShell:

```powershell
Remove-NetFirewallRule -DisplayName "FruitSpy TCP"
Remove-NetFirewallRule -DisplayName "FruitSpy UDP"
```

FruitSpy keeps matchmaking state only in memory; there is no database to remove. See [Internet Alpha Deployment](../DEPLOYMENT.md) for the protocol acceptance matrix and operational limits.
