"""
Dark-modern visual theme for Codenames.

Every colour, radius and character face used by the UI lives here so
main.py and ui_widgets.py stay in sync with the approved design mock.
"""

from kivy.metrics import dp

import i18n


# ---------------------------------------------------------------------
# RESPONSIVE BREAKPOINTS
# ---------------------------------------------------------------------

COMPACT_BREAKPOINT = dp(520)


def layout_mode(width, height):
    """Return "portrait" or "landscape" for a window size."""

    return "landscape" if width > height else "portrait"


def is_compact_size(width, height):
    """Phones (in either orientation) get tighter spacing and fonts."""

    return min(width, height) < COMPACT_BREAKPOINT


def hex_rgba(value, alpha=1.0):
    """Turn "#RRGGBB" (or "RRGGBB") into a Kivy colour tuple."""

    value = value.lstrip("#")

    if len(value) == 3:
        value = "".join(character * 2 for character in value)

    red = int(value[0:2], 16) / 255.0
    green = int(value[2:4], 16) / 255.0
    blue = int(value[4:6], 16) / 255.0

    return (red, green, blue, alpha)


def mix(colour_a, colour_b, amount):
    """Linear blend between two colours (amount 0 = a, 1 = b)."""

    amount = max(0.0, min(1.0, amount))

    return tuple(
        first + (second - first) * amount
        for first, second in zip(colour_a, colour_b)
    )


def fade(colour, alpha):
    """Return a copy of a colour with a different alpha."""

    return (colour[0], colour[1], colour[2], alpha)


def scaled(colour, factor, alpha=None):
    """Darken/brighten a colour while keeping it opaque."""

    return (
        max(0.0, min(1.0, colour[0] * factor)),
        max(0.0, min(1.0, colour[1] * factor)),
        max(0.0, min(1.0, colour[2] * factor)),
        colour[3] if alpha is None else alpha,
    )


# ---------------------------------------------------------------------
# PALETTE
# ---------------------------------------------------------------------

BG = hex_rgba("0B0D12")
BG_DEEP = hex_rgba("05070A")
SURFACE = hex_rgba("151A23")
SURFACE_HI = hex_rgba("1C232E")
BORDER = hex_rgba("2A3340")
BORDER_SOFT = hex_rgba("232B36")
TEXT = hex_rgba("E8EDF5")
MUTED = hex_rgba("8B96A8")
DIM = hex_rgba("5C6675")
RED = hex_rgba("E14B4B")
BLUE = hex_rgba("4C8DF6")
ACCENT = hex_rgba("2DD4BF")
ACCENT_HI = hex_rgba("3EEAD6")
ACCENT_DARK = hex_rgba("04211D")
NEUTRAL = hex_rgba("5A6474")
ASSASSIN = hex_rgba("0A0C10")

TRANSPARENT = hex_rgba("000000", 0.0)

TEAM_COLORS = {
    "red": RED,
    "blue": BLUE,
    "neutral": NEUTRAL,
    "assassin": hex_rgba("8E2A2A"),
}

# Colours used inside Kivy markup strings.
TEAM_HEX = {
    "red": "E14B4B",
    "blue": "4C8DF6",
    "neutral": "8B96A8",
    "assassin": "C05A5A",
}

# The word side of a board card (top layer) and the answer side
# (revealed layer) for each possible role.
CARD_FACES = {
    "red": {
        "fill": hex_rgba("6B2F34"),
        "border": hex_rgba("A34A4A"),
        "text": hex_rgba("FFD9D9"),
    },
    "blue": {
        "fill": hex_rgba("2C4A7B"),
        "border": hex_rgba("5B7FB5"),
        "text": hex_rgba("D6E6FF"),
    },
    "neutral": {
        "fill": hex_rgba("2D343F"),
        "border": hex_rgba("4B5563"),
        "text": hex_rgba("C9D1DC"),
    },
    "assassin": {
        "fill": hex_rgba("120A0C"),
        "border": hex_rgba("A34A4A"),
        "text": hex_rgba("F0A6A6"),
    },
}

# The always-visible key used by spymasters and the game-over board.
KEY_FACES = {
    "red": {
        "fill": hex_rgba("A23232"),
        "border": hex_rgba("D75858"),
        "text": hex_rgba("FFFFFF"),
    },
    "blue": {
        "fill": hex_rgba("3566B8"),
        "border": hex_rgba("5B8BE0"),
        "text": hex_rgba("FFFFFF"),
    },
    "neutral": {
        "fill": hex_rgba("3E4653"),
        "border": hex_rgba("4B5563"),
        "text": hex_rgba("C9D1DC"),
    },
    "assassin": {
        "fill": hex_rgba("140A0C"),
        "border": hex_rgba("8E2A2A"),
        "text": hex_rgba("E8A0A0"),
    },
}


# ---------------------------------------------------------------------
# METRICS
# ---------------------------------------------------------------------

RADIUS = dp(14)
RADIUS_SM = dp(9)
RADIUS_CARD = dp(10)

FONT_TINY = dp(10)
FONT_SMALL = dp(12)
FONT_BODY = dp(14)
FONT_LABEL = dp(16)
FONT_TITLE = dp(26)
FONT_HERO = dp(52)


def safe_markup(value):
    """Return text safe for Kivy markup and shaped for RTL display."""

    text = str(value).replace("[", "(").replace("]", ")")
    return i18n.shape(text)
