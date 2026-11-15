[app]

# Android-first Codenames build. Desktop still runs from source with
# `python main.py` for local testing.
title = Codenames
package.name = codenames
package.domain = com.alukard

source.dir = .
# Python sources plus Data/Words/*.json (the word library).
source.include_exts = py,json
source.exclude_dirs = .venv, kivy_venv, __pycache__, bin, .buildozer, .git, .github, shots

version = 1.0

# segno draws the join QR code with pure Python, no image assets.
requirements = python3,kivy,segno

# p4a's master branch creates a venv and runs `pip install -U pip` inside
# it; on Python 3.14 that venv's pip is broken (ImportError: capture_logging).
# develop installs pure-Python deps with the hostpython pip instead.
p4a.branch = develop

orientation = all
fullscreen = 0

# ---------------------------------------------------------------------
# ANDROID
# ---------------------------------------------------------------------

android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a

# zxing-android-embedded provides the in-app camera scanner used by the
# JOIN GAME screen. It needs AndroidX and the camera permission.
android.gradle_dependencies = com.journeyapps:zxing-android-embedded:4.3.0
android.enable_androidx = True

android.permissions = INTERNET,CAMERA,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,WAKE_LOCK

android.accept_sdk_license = True

# The app never creates a hotspot or joins Wi-Fi networks itself; players
# use the phone's system hotspot / Wi-Fi. INTERNET is enough for the game
# socket, CAMERA is for the in-app QR scanner.

[buildozer]

log_level = 2
warn_on_root = 0
