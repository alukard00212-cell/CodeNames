"""QR invite payloads, the canvas QR renderer and the Android scanner.

The payload is a tiny deep link:

    codenames://join?v=1&h=192.168.43.1&p=5000&s=MyHotspot&w=secret

`h`/`p` are required. `s`/`w` (hotspot SSID/password) are optional and only
let the joining phone tell the player which network to connect to; Android
does not allow an app to join a Wi-Fi network silently.
"""

from urllib.parse import parse_qs, quote, urlencode, urlparse

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.properties import StringProperty
from kivy.uix.widget import Widget

try:
    import segno
except ImportError:  # pragma: no cover - buildozer installs it on device
    segno = None


SCHEME = "codenames"
HOST = "join"
DEFAULT_VERSION = "1"

# Default request code used by zxing-android-embedded's IntentIntegrator.
SCAN_REQUEST_CODE = 0x0000C0DE


# ---------------------------------------------------------------------
# PAYLOAD
# ---------------------------------------------------------------------

def build_join_payload(host, port, ssid="", password=""):
    """Build the text that gets encoded inside the host's QR code."""

    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 5000

    params = {
        "v": DEFAULT_VERSION,
        "h": str(host or "").strip(),
        "p": str(port),
    }

    if ssid:
        params["s"] = str(ssid)

    if password:
        params["w"] = str(password)

    return (
        f"{SCHEME}://{HOST}?"
        + urlencode(params, quote_via=quote, safe="")
    )


def _valid_host(value):
    if not value:
        return False

    value = str(value).strip()

    if not value or len(value) > 120:
        return False

    return not any(character.isspace() for character in value)


def parse_join_payload(text):
    """Parse a QR payload (or a manual `ip` / `ip:port` fallback).

    Returns a dict with `host`, `port`, `ssid` and `password`, or None
    when the text is not a usable Codenames invite.
    """

    if not text:
        return None

    value = str(text).strip().strip('"')

    if not value:
        return None

    if "://" in value:
        parsed = urlparse(value)

        if (
            parsed.scheme.lower() != SCHEME
            or parsed.netloc.lower() != HOST
        ):
            return None

        query = parse_qs(parsed.query)

        host = (query.get("h") or [""])[0].strip()
        raw_port = (query.get("p") or [""])[0].strip()

        if not _valid_host(host) or not raw_port.isdigit():
            return None

        port = int(raw_port)

        if not 0 < port < 65536:
            return None

        return {
            "host": host,
            "port": port,
            "ssid": (query.get("s") or [""])[0],
            "password": (query.get("w") or [""])[0],
            "version": (query.get("v") or [DEFAULT_VERSION])[0],
        }

    # Desktop/manual fallback: "192.168.1.5" or "192.168.1.5:5000".
    host = value
    port = 5000

    if value.count(":") == 1:
        raw_host, raw_port = value.rsplit(":", 1)

        if raw_port.isdigit():
            host = raw_host.strip()
            port = int(raw_port)

    if not _valid_host(host) or not 0 < port < 65536:
        return None

    return {
        "host": host,
        "port": port,
        "ssid": "",
        "password": "",
        "version": DEFAULT_VERSION,
    }


# ---------------------------------------------------------------------
# CANVAS RENDERER
# ---------------------------------------------------------------------

class QRCodeWidget(Widget):
    """Renders a QR code as canvas rectangles (no textures, no PIL)."""

    data = StringProperty("")

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (220, 220))

        super().__init__(**kwargs)

        self._qr = None
        self.bind(
            data=self._redraw,
            pos=self._redraw,
            size=self._redraw,
        )
        self._redraw()

    def _matrix(self):
        if not self.data or segno is None:
            return None

        try:
            qr = segno.make(self.data, error="m")
            matrix = [list(row) for row in qr.matrix]
        except Exception:
            return None

        if not matrix or len(matrix) != len(matrix[0]):
            return None

        return matrix

    def _redraw(self, *args):
        self.canvas.clear()

        width, height = self.size

        if width <= 1 or height <= 1:
            return

        matrix = self._matrix()

        if matrix is None:
            return

        modules = len(matrix)
        quiet = 4
        total = modules + quiet * 2
        cell = min(width, height) / total
        side = cell * total

        left = self.center_x - side / 2.0
        bottom = self.center_y - side / 2.0

        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(pos=(left, bottom), size=(side, side))

            Color(0.03, 0.045, 0.06, 1)

            for row_index, row in enumerate(matrix):
                column = 0

                while column < modules:
                    if not row[column]:
                        column += 1
                        continue

                    run_start = column

                    while column < modules and row[column]:
                        column += 1

                    run = column - run_start

                    x = left + (quiet + run_start) * cell
                    y = (
                        bottom
                        + (total - quiet - row_index - 1) * cell
                    )

                    Rectangle(
                        pos=(x, y),
                        size=(run * cell + 0.35, cell + 0.35),
                    )


# ---------------------------------------------------------------------
# ANDROID SCANNER (zxing-android-embedded)
# ---------------------------------------------------------------------

def is_scan_supported():
    from kivy.utils import platform

    return platform == "android"


class _Scanner:

    def __init__(self):
        self._callback = None
        self._bound = False
        self._integrator = None

    def start(self, callback):
        """Open the Android QR scanner. Returns False when unavailable."""

        if not is_scan_supported():
            return False

        try:
            from android import activity, mActivity
            from jnius import autoclass

            integrator = autoclass(
                "com.google.zxing.integration.android.IntentIntegrator"
            )(mActivity)

            integrator.setPrompt("Scan the host's Codenames QR code")
            integrator.setBeepEnabled(False)
            integrator.setBarcodeImageEnabled(False)
            integrator.setOrientationLocked(False)
            integrator.setRequestCode(SCAN_REQUEST_CODE)

            self._callback = callback
            self._integrator = integrator

            if not self._bound:
                activity.bind(
                    on_activity_result=self._on_activity_result
                )
                self._bound = True

            integrator.initiateScan()

            return True

        except Exception:
            self._callback = None
            self._integrator = None
            self._unbind()

            return False

    def _unbind(self):
        if not self._bound:
            return

        try:
            from android import activity

            activity.unbind(
                on_activity_result=self._on_activity_result
            )
        except Exception:
            pass

        self._bound = False

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != SCAN_REQUEST_CODE:
            return

        data = None

        try:
            if intent is not None and result_code == -1:
                data = intent.getStringExtra("SCAN_RESULT")
        except Exception:
            data = None

        callback = self._callback
        self._callback = None
        self._integrator = None
        self._unbind()

        if callback is None:
            return

        Clock.schedule_once(
            lambda dt: callback(str(data) if data else None),
            0,
        )


_scanner = _Scanner()


def scan_qr_code(callback):
    """Start the scanner and call `callback(text_or_None)` with the result."""

    return _scanner.start(callback)
