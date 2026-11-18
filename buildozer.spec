[app]

# Android-first Codenames build. Desktop still runs from source with
# `python main.py` for local testing.
title = Codenames
package.name = codenames
package.domain = com.alukard

source.dir = .
# Python sources, language packs and bundled fonts.
source.include_exts = py,json,ttf,txt
source.exclude_dirs = .venv, kivy_venv, __pycache__, bin, .buildozer, .git, .github, shots

version = 1.0

requirements = python3,kivy,arabic_reshaper,python-bidi

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

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,WAKE_LOCK,ACCESS_FINE_LOCATION,NEARBY_WIFI_DEVICES

android.accept_sdk_license = True

# The app never creates a hotspot or joins Wi-Fi networks itself; players
# use the phone's system hotspot / Wi-Fi. INTERNET is enough for the game
# socket, and ACCESS_FINE_LOCATION / NEARBY_WIFI_DEVICES let the lobby
# show the name of the Wi-Fi network or shared hotspot (Android hides the
# SSID otherwise).

[buildozer]

log_level = 2
warn_on_root = 0
