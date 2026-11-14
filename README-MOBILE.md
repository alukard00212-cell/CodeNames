# Codenames — Android / LAN build

The game is now Android-first. One phone hosts, everybody else joins by
scanning a QR code shown inside the app. No IP addresses are typed on a
phone.

## How a match works

1. The host starts the game with **HOST GAME**.
2. Guests connect to the same network:
   - the host turns on the phone's **system hotspot**, or
   - every phone joins the same Wi-Fi router.
3. The host taps **SHOW QR CODE** in the host lobby.
4. Each guest opens **JOIN GAME**, enters a name, and taps
   **SCAN QR CODE**. The scanner is built into the app.
5. The host starts the match when at least **4 players** (host + 3
   guests) have chosen a team.

The QR code contains the host's LAN address, port, and optionally the
hotspot name and password so guests can see which network to use. Android
does not allow an app to join a Wi-Fi network silently, so guests still
use the system Wi-Fi/hotspot screen; the QR removes address typing, not
the network connection step.

The app never creates a hotspot itself. Use the phone's own hotspot
settings (the QR dialog has **WI-FI SETTINGS** and **HOTSPOT** shortcuts).

## Desktop testing

Desktop still works as a fallback for development:

```powershell
.\kivy_venv\Scripts\python.exe main.py
```

On desktop, **JOIN GAME** shows the manual address field
(`192.168.1.5:5000`). The host lobby still uses the QR button; the QR hint
shows the desktop test address.

## Build the APK with GitHub Actions

The workflow lives in `.github/workflows/android.yml`. It uses the
official `kivy/buildozer` Docker image and uploads `bin/*.apk` as the
`codenames-apk` artifact.

The project is already a local Git repository with the first commit made.
All that is left is publishing it to GitHub. Pick whichever route suits
you:

**Option A - GitHub Desktop (no commands).** Install GitHub Desktop, choose
*File > Add local repository*, select `C:\Users\ALukard\Desktop\CodeNames`,
then click *Publish repository*. Keep it private or public, both work.

**Option B - Git CLI.** Git for Windows is installed. Open a **new**
terminal (so `PATH` is refreshed), create an empty repository on GitHub,
then run:

```powershell
cd C:\Users\ALukard\Desktop\CodeNames
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

**Option C - upload the zip.** `CodeNames-android-source.zip` (next to the
project folder on the Desktop) contains exactly the committed files. Create
a repository on GitHub, extract the zip, and use *Add file > Upload files*
with the extracted contents (do not upload the `.venv` or `kivy_venv`
folders - the zip already excludes them).

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

Build settings live in `buildozer.spec`:

- package id `com.alukard.codenames`
- `minapi 24`, `api 34`
- `arm64-v8a` and `armeabi-v7a`
- `segno` renders the QR code with the Kivy canvas
- `com.journeyapps:zxing-android-embedded:4.3.0` provides the scanner
- permissions: internet, camera, network state, Wi-Fi state, wake lock

## Phone notes

- Both portrait and landscape layouts are supported and rebuild when the
  phone rotates.
- The host must keep the app in the foreground while guests scan; the app
  requests a wake lock during a match.
- If the QR hint says "No Wi-Fi address found", enable the hotspot or join
  a Wi-Fi network before opening the QR dialog.
- If port 5000 is busy, the host automatically picks a free port and the
  QR code contains the real port.
- The scanner and in-app QR flow only run in the Android APK. Desktop
  shows the manual address fallback instead.

## Regressions covered by the local test harness

The development tests are kept outside the repository:

- `ui_smoke.py` — screens, roles, game-over reveal, 4-player gate
- `role_test.py` — TCP role routing and lobby rules
- `ui_anim.py` — reveal animation
- `ui_audit.py` / `mobile_layout_audit.py` — label overflow and clipping
  at 360x640, 390x844, 800x360, 844x390
- `qr_mobile_test.py` — QR payload round trip/rejection, canvas QR,
  Android join screen, hotspot toggle, responsive breakpoints
