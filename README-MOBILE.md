# Codenames — Android / LAN build

The game is Android-first. One phone hosts, and every other phone on the
same Wi-Fi or hotspot sees that lobby in a list and joins with one tap.
A QR code remains as a fallback for networks that block the discovery
broadcast. No IP addresses are typed on a phone.

## How a match works

1. The host starts the game with **HOST GAME**.
2. Everybody joins the same network:
   - the host turns on the phone's **system hotspot**, or
   - every phone joins the same Wi-Fi router.
3. The host lobby header shows the network in use, for example
   `HOTSPOT · Pixel_1234` or `WI-FI · HomeNet`. Players can compare it with
   their own Wi-Fi setting to confirm they are on the right network.
4. Each guest opens **JOIN GAME**, enters a name, and watches the
   **LOBBIES ON THIS NETWORK** list. The host appears there within a few
   seconds. Tap **JOIN**.
5. If the list stays empty, the guest taps **SCAN QR** and scans the code
   from **SHOW QR CODE** in the host lobby.
6. The host starts the match when at least **4 players** (host + 3
   guests) have chosen a team.

### How discovery works

The host answers UDP probes on port `5001`; the reply carries the host's
LAN address, the real TCP game port, the network name, and the current
player count. Guests broadcast a probe to `255.255.255.255`, to their own
`/24` subnet broadcast address, and to loopback. The join screen rescans
every 3 seconds while it is open.

Discovery needs both phones on the same subnet with client isolation
off — this is true for a phone hotspot and for most home routers. Guest
Wi-Fi and some public networks filter broadcast traffic; use the QR
fallback there.

### QR fallback

The QR code contains the host's LAN address, port, and optionally the
hotspot name and password. Android does not allow an app to join a Wi-Fi
network silently, so guests still use the system Wi-Fi/hotspot screen;
the QR removes the address typing, not the network step.

The scanner asks for the camera permission the first time it opens. If it
was denied earlier, the app shows a **CAMERA BLOCKED** dialog with an
**OPEN SETTINGS** button that jumps to the app's permission page.

The app never creates a hotspot itself. Use the phone's own hotspot
settings (the QR dialog has **WI-FI SETTINGS** and **HOTSPOT** shortcuts).

## Permissions the app asks for

| Permission | Why |
| --- | --- |
| `INTERNET` | the TCP game socket and the discovery broadcast |
| `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | network availability checks |
| `WAKE_LOCK` | keep the screen on while hosting or playing |
| `CAMERA` | in-app QR scanner (fallback path only) |
| `ACCESS_FINE_LOCATION`, `NEARBY_WIFI_DEVICES` | Android hides the Wi-Fi/hotspot name (SSID) from apps without them, so the host header would read `name hidden` |

Denying the location permission only degrades the network banner; joining
by lobby list or QR still works.

## Desktop testing

Desktop still works as a fallback for development:

```powershell
.\kivy_venv\Scripts\python.exe main.py
```

On desktop, **JOIN GAME** still shows the manual address field
(`192.168.1.5:5000`) in addition to the lobby list, so two windows on one
machine can always be linked even if the OS filters broadcast traffic. The
host lobby uses the same **SHOW QR CODE** button; its hint shows the
desktop test address.

## Build the APK with GitHub Actions

The workflow lives in `.github/workflows/android.yml`. It runs on a
native Ubuntu 24.04 runner, trims preinstalled toolchains to free disk
space, and uploads `bin/*.apk` as the `codenames-apk` artifact.

Open the repository's **Actions** tab, wait for **Build Android APK** to
finish, then download the artifact from the run page. Install the APK on
each phone (sideloading / "install unknown apps" must be allowed).

To rebuild without pushing, run the workflow manually with
**Run workflow**.

The first build takes roughly 20-40 minutes because Buildozer downloads
the Android SDK/NDK; later runs reuse the cache.

## Local APK build (optional)

With Docker installed:

```powershell
docker run --rm -v "${PWD}:/home/user/hostcwd" kivy/buildozer android debug
```

The APK appears in `bin/`.

> **Stale SDK caveat.** Buildozer 1.6.0 still pins
> `commandlinetools-linux-6514223_latest.zip`, which Google has retired
> (HTTP 404), so a local build fails at the SDK download step until the
> pin is patched or a newer Buildozer is used. The CI workflow handles
> this, which is why CI is the supported build path.

Build settings live in `buildozer.spec`:

- package id `com.alukard.codenames`
- `minapi 24`, `api 34`
- `arm64-v8a` and `armeabi-v7a`
- `p4a.branch = develop` (needed for the current Python/NDK pairing)
- `segno` renders the QR code with the Kivy canvas
- `com.journeyapps:zxing-android-embedded:4.3.0` provides the scanner
- permissions as listed in the table above

## Phone notes

- Both portrait and landscape layouts are supported and rebuild when the
  phone rotates.
- The host must keep the app in the foreground while guests connect; the
  app requests a wake lock during a match.
- If the host header says `name hidden`, the location permission was
  denied or system location is off. Discovery and joining still work.
- If the lobby list stays empty while both phones are on the same Wi-Fi,
  the network is probably isolating clients. Use **SCAN QR**.
- If port 5000 is busy, the host automatically picks a free port; both
  discovery replies and the QR code contain the real port.
- The scanner and in-app QR flow only run in the Android APK. Desktop
  shows the manual address fallback instead.

## Regressions covered by the local test harness

The development tests are kept outside the repository:

- `ui_smoke.py` — screens, roles, game-over reveal, 4-player gate
- `role_test.py` — TCP role routing and lobby rules
- `ui_anim.py` — reveal animation
- `ui_audit.py` / `mobile_layout_audit.py` / `lan_layout_audit.py` — label
  overflow and clipping at 360x640, 390x844, 800x360, 844x390
- `discovery_test.py` — UDP probe/responder round trip and empty network
- `lan_join_test.py` — host advertises, client lists the lobby, taps JOIN,
  reaches team selection, host sees the new player
- `qr_mobile_test.py` — QR payload round trip/rejection, canvas QR,
  Android join screen, hotspot toggle, responsive breakpoints
