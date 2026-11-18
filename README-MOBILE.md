# Codenames — Android / LAN build

The game is Android-first. One phone hosts, and every other phone on the
same Wi-Fi or hotspot sees that lobby in a list and joins with one tap.
No QR codes and no IP addresses are typed on a phone.

## How a match works

1. Set your player name from the chip in the top-left of the main menu.
   It is saved on the device and used whenever you host or join.
2. Choose the language with the button in the bottom-right of the main
   menu (`English` / `فارسی`). Switching asks for confirmation and
   rebuilds the UI with the selected language and word list.
3. The host starts the game with **HOST GAME**.
4. Everybody joins the same network:
   - the host turns on the phone's **system hotspot**, or
   - every phone joins the same Wi-Fi router.
5. In the host lobby the host picks a team and role and a spymaster mode:
   - **1 · BOTH TEAMS**: one shared spymaster (sees the full key and can
     clue for either team). Both spymasters may share one phone.
   - **2 · ONE PER TEAM**: one spymaster per team (the default).
   A **NO ROLE** option is available to clear a slot while shuffling a
   full lobby. Everyone must have a role before the match can start.
6. Each guest opens **JOIN GAME** and watches the **LOBBIES ON THIS
   NETWORK** list. The host appears there within a few seconds (shown by
   host name and player count). Tap **JOIN**.
7. The host starts the match when at least **4 players** (host + 3
   guests) have chosen valid teams/roles for the selected spymaster mode.
8. After a match ends, **BACK TO LOBBY** returns everyone to the lobby
   with the same connections and roles so the next round can start
   quickly. Leaving a match from the host aborts it back to the host
   lobby; clients can leave to the main menu.

### How discovery works

The host answers UDP probes on port `5001`; the reply carries the host's
LAN address, the real TCP game port, the network name, the host player
name, and the current player count. Guests broadcast a probe to
`255.255.255.255`, to their own `/24` subnet broadcast address, and to
loopback. The join screen rescans every 3 seconds while it is open.

Discovery needs both phones on the same subnet with client isolation
off — this is true for a phone hotspot and for most home routers.

The app never creates or joins a hotspot/Wi-Fi network itself. Use the
phone's own system settings.

## Permissions the app asks for

| Permission | Why |
| --- | --- |
| `INTERNET` | the TCP game socket and the discovery broadcast |
| `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | network availability checks |
| `WAKE_LOCK` | keep the screen on while hosting or playing |
| `ACCESS_FINE_LOCATION`, `NEARBY_WIFI_DEVICES` | Android hides the Wi-Fi/hotspot name (SSID) from apps without them |

Denying the location permission only degrades the network name that may
be shown; joining by the lobby list still works.

## Desktop testing

Desktop still works as a fallback for development:

```powershell
.\kivy_venv\Scripts\python.exe main.py
```

On desktop, **JOIN GAME** still shows a manual address field
(`192.168.1.5:5000`) in addition to the lobby list, so two windows on one
machine can always be linked even if the OS filters broadcast traffic.

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
- bundled Vazirmatn font for Latin + Persian (UI strings and words)
- permissions as listed in the table above

## Phone notes

- Both portrait and landscape layouts are supported and rebuild when the
  phone rotates.
- The host must keep the app in the foreground while guests connect; the
  app requests a wake lock during a match.
- The Android back button uses in-app confirmations (exit, close/leave
  lobby, leave match) instead of leaving the app immediately.
- If the lobby list stays empty while both phones are on the same Wi-Fi,
  the network is probably isolating clients.
- If port 5000 is busy, the host automatically picks a free port; the
  discovery reply contains the real port.
- Word lists and translations are bundled per language in
  `Data/Lang/<code>.json`. A new APK is required after changing bundled
  word lists or strings.

## Regressions covered by the local test harness

The development tests are kept outside the repository (see temp harness
files used during development). After this refactor the LAN/role/layout
harnesses need to be updated for the new lobby flow, saved names,
spymaster modes, and translated UI.
