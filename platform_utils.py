"""Small platform helpers used by the mobile-first Codenames build.

Everything here degrades gracefully on desktop so the same code base can
still be used for local testing with two windows on one machine.
"""

import socket

from kivy.utils import platform


IS_ANDROID = platform == "android"


# ---------------------------------------------------------------------
# NETWORK ADDRESS DISCOVERY
# ---------------------------------------------------------------------

_LOOPBACK = "127.0.0.1"


def _ioctl_addresses():
    """Enumerate IPv4 addresses with SIOCGIFADDR (Linux/Android only)."""

    try:
        import fcntl
        import struct
    except ImportError:
        return []

    addresses = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        interfaces = socket.if_nameindex()

        for _, name in interfaces:
            request = struct.pack("256s", name[:15].encode("utf-8"))

            try:
                response = fcntl.ioctl(sock.fileno(), 0x8915, request)
            except OSError:
                continue

            address = socket.inet_ntoa(response[20:24])

            if address and address not in addresses:
                addresses.append(address)

    except (OSError, AttributeError):
        pass

    finally:
        sock.close()

    return addresses


def _route_addresses():
    """Ask the routing table which source address would be used."""

    addresses = []

    for destination in ("192.0.2.1", "8.8.8.8"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            sock.settimeout(0.2)
            sock.connect((destination, 80))
            address = sock.getsockname()[0]
        except OSError:
            address = None
        finally:
            sock.close()

        if address and address not in addresses:
            addresses.append(address)

    return addresses


def _hostname_addresses():
    addresses = []

    try:
        for info in socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
        ):
            address = info[4][0]

            if address and address not in addresses:
                addresses.append(address)

    except (OSError, UnicodeError):
        pass

    return addresses


def get_ipv4_addresses():
    """Return every plausible local IPv4 address, best first."""

    found = []

    for address in (
        _ioctl_addresses()
        + _route_addresses()
        + _hostname_addresses()
    ):
        if address and address != _LOOPBACK and address not in found:
            found.append(address)

    # Prefer private LAN ranges; hotspot gateways usually look like
    # 192.168.x.1, which naturally wins this ordering.
    def score(address):
        if address.startswith("192.168."):
            return 0

        if address.startswith("10."):
            return 1

        if address.startswith("172."):
            octets = address.split(".")

            if len(octets) == 4:
                try:
                    second = int(octets[1])
                except ValueError:
                    second = 0

                if 16 <= second <= 31:
                    return 2

        return 3

    found.sort(key=score)

    return found


def get_host_ip():
    """Best address to embed in the join QR code."""

    addresses = get_ipv4_addresses()

    if addresses:
        return addresses[0]

    return _LOOPBACK


# ---------------------------------------------------------------------
# NETWORK NAME (SSID / HOTSPOT NAME)
# ---------------------------------------------------------------------

# Android only reveals the Wi-Fi name to apps that hold the location
# permission (and, on some versions, only while location is enabled).
# Every lookup below is best effort: no name simply means the UI shows
# a neutral label.

def _wifi_manager():
    try:
        from android import mActivity
        from jnius import autoclass

        Context = autoclass("android.content.Context")

        return mActivity.getSystemService(Context.WIFI_SERVICE)

    except Exception:
        return None


def _clean_ssid(value):
    text = str(value or "").strip().strip('"').strip()

    if not text:
        return ""

    if text.lower() in ("<unknown ssid>", "unknown", "0x"):
        return ""

    return text


def get_network_label():
    """Return (kind, name) for the network this phone is using.

    `kind` is "HOTSPOT" when we are sharing a hotspot, otherwise "WI-FI".
    An empty `name` means Android withheld it (location off/denied).
    """

    if not IS_ANDROID:
        addresses = get_ipv4_addresses()

        if addresses:
            return ("WI-FI", "local network")

        return ("WI-FI", "")

    manager = _wifi_manager()

    if manager is None:
        return ("WI-FI", "")

    # getSoftApConfiguration() is public API from Android 11 and tells us
    # the name of the hotspot this phone is currently sharing.
    try:

        configuration = manager.getSoftApConfiguration()

        if configuration is not None:

            ssid = _clean_ssid(configuration.getSsid())

            if ssid:
                return ("HOTSPOT", ssid)

    except Exception:
        pass

    try:

        info = manager.getConnectionInfo()

        if info is not None:

            ssid = _clean_ssid(info.getSSID())

            if ssid:
                return ("WI-FI", ssid)

    except Exception:
        pass

    return ("WI-FI", "")


def request_network_name_permission(callback=None):
    """Ask for the location permission Android needs to read the SSID.

    Returns True when the permission is already granted. `callback` runs
    (on the Kivy thread) after the dialog closes, granted or not.
    """

    if not IS_ANDROID:
        return True

    try:
        from android.permissions import (
            Permission,
            check_permission,
            request_permissions,
        )
    except Exception:
        return False

    try:

        if check_permission(Permission.ACCESS_FINE_LOCATION):
            return True

        def _finished(*args):
            if callback is None:
                return

            try:
                from kivy.clock import Clock

                Clock.schedule_once(lambda dt: callback(), 0)

            except Exception:
                pass

        request_permissions(
            [Permission.ACCESS_FINE_LOCATION],
            _finished
        )

        return False

    except Exception:
        return False


# ---------------------------------------------------------------------
# ANDROID LIFECYCLE / SETTINGS
# ---------------------------------------------------------------------

def keep_screen_on():
    """Ask Android to keep the display awake while a match is running."""

    if not IS_ANDROID:
        return False

    try:
        from android import mActivity
        from jnius import autoclass

        LayoutParams = autoclass(
            "android.view.WindowManager$LayoutParams"
        )

        mActivity.getWindow().addFlags(
            LayoutParams.FLAG_KEEP_SCREEN_ON
        )

        return True

    except Exception:
        return False


def _start_settings_intent(action):
    try:
        from android import mActivity
        from jnius import autoclass

        Intent = autoclass("android.content.Intent")
        intent = Intent(action)

        mActivity.startActivity(intent)

        return True

    except Exception:
        return False


def open_wifi_settings():
    return _start_settings_intent(
        "android.settings.WIFI_SETTINGS"
    )


def open_hotspot_settings():
    # There is no public "hotspot settings" action on every Android
    # version, so fall back to the wireless settings page.
    if _start_settings_intent("android.settings.TETHER_SETTINGS"):
        return True

    return _start_settings_intent(
        "android.settings.WIRELESS_SETTINGS"
    )


def open_app_settings():
    """Open this app's system settings page (for a denied permission)."""

    if not IS_ANDROID:
        return False

    try:
        from android import mActivity
        from jnius import autoclass

        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        Settings = autoclass("android.provider.Settings")

        intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
        intent.setData(
            Uri.parse("package:" + mActivity.getPackageName())
        )

        mActivity.startActivity(intent)

        return True

    except Exception:
        return False


def vibrate(milliseconds=30):
    if not IS_ANDROID:
        return False

    try:
        from android import mActivity
        from jnius import autoclass

        Context = autoclass("android.content.Context")
        Vibrator = autoclass("android.os.Vibrator")

        service = mActivity.getSystemService(Context.VIBRATOR_SERVICE)

        if service is None:
            return False

        service.vibrate(int(milliseconds))

        return True

    except Exception:
        return False
