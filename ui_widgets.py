"""
Reusable dark-modern widgets for the Codenames UI.

Everything is drawn with Kivy canvas instructions (rounded rectangles,
thin borders, dots) because the project ships no image assets.
"""

from kivy.animation import Animation
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from theme import (
    ACCENT,
    ACCENT_DARK,
    ACCENT_HI,
    BG,
    BLUE,
    BORDER,
    BORDER_SOFT,
    CARD_FACES,
    DIM,
    KEY_FACES,
    MUTED,
    NEUTRAL,
    RADIUS,
    RADIUS_CARD,
    RADIUS_SM,
    RED,
    SURFACE,
    SURFACE_HI,
    TEAM_COLORS,
    TEAM_HEX,
    TEXT,
    TRANSPARENT,
    fade,
    hex_rgba,
    is_compact_size,
    mix,
    scaled,
)


# ---------------------------------------------------------------------
# SCREEN BACKGROUND
# ---------------------------------------------------------------------

class ScreenRoot(BoxLayout):
    """Full-screen container with the dark backdrop and soft glows."""

    def __init__(self, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(22))
        kwargs.setdefault("spacing", dp(12))
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*BG)
            self._backdrop = Rectangle()

            self._blue_glow_color = Color(*fade(BLUE, 0.055))
            self._blue_glow = Ellipse()

            self._red_glow_color = Color(*fade(RED, 0.045))
            self._red_glow = Ellipse()

        self.bind(pos=self._sync_backdrop, size=self._sync_backdrop)
        self.bind(size=self._sync_padding)
        self._sync_backdrop()
        self._sync_padding()

    def _sync_padding(self, *args):
        width, height = self.size

        if width <= 0 or height <= 0:
            return

        compact = is_compact_size(width, height)
        padding = dp(10) if compact else dp(22)
        spacing = dp(6) if compact else dp(12)

        if self.padding[0] != padding:
            self.padding = [padding, padding, padding, padding]

        if self.spacing != spacing:
            self.spacing = spacing

    def _sync_backdrop(self, *args):
        width, height = self.size

        self._backdrop.pos = self.pos
        self._backdrop.size = (width, height)

        self._blue_glow.pos = (
            self.x - width * 0.30,
            self.y + height * 0.42,
        )
        self._blue_glow.size = (width * 1.15, height * 1.05)

        self._red_glow.pos = (
            self.x + width * 0.32,
            self.y - height * 0.62,
        )
        self._red_glow.size = (width * 1.05, height * 1.05)


# ---------------------------------------------------------------------
# DECORATION
# ---------------------------------------------------------------------

class LogoMark(Widget):
    """64px app mark: rounded tile with a split red/blue square."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(64), dp(64)))

        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*SURFACE_HI)
            self._tile = RoundedRectangle(radius=[dp(16)] * 4)

            Color(*BORDER)
            self._tile_border = Line(width=dp(1))

        with self.canvas.after:
            Color(*RED)
            self._red_half = RoundedRectangle(radius=[dp(6)] * 4)

            Color(*BLUE)
            self._blue_half = RoundedRectangle(radius=[dp(6)] * 4)

        self.bind(pos=self._sync_mark, size=self._sync_mark)
        self._sync_mark()

    def _sync_mark(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        self._tile.pos = self.pos
        self._tile.size = self.size
        self._tile.radius = [dp(16)] * 4

        self._tile_border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            dp(16),
        )

        side = dp(13)
        centre_x = self.center_x
        centre_y = self.center_y

        self._red_half.pos = (centre_x - side, centre_y - side)
        self._red_half.size = (side, side * 2)

        self._blue_half.pos = (centre_x, centre_y - side)
        self._blue_half.size = (side, side * 2)


class Scrim(Widget):
    """Full-size translucent layer used behind modal panels."""

    def __init__(self, colour=(0, 0, 0, 0.72), **kwargs):
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*colour)
            self._rect = Rectangle()

        self.bind(pos=self._sync_scrim, size=self._sync_scrim)
        self._sync_scrim()

    def _sync_scrim(self, *args):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def on_touch_down(self, touch):
        # Swallow taps so the board underneath cannot be clicked.
        if self.collide_point(*touch.pos):
            return True

        return super().on_touch_down(touch)


# ---------------------------------------------------------------------
# PANEL
# ---------------------------------------------------------------------

class Panel(BoxLayout):
    """Rounded surface panel with a 1px border."""

    def __init__(
        self,
        fill=SURFACE,
        border=BORDER,
        radius=RADIUS,
        **kwargs
    ):
        super().__init__(**kwargs)

        self.radius = radius

        with self.canvas.before:
            self._fill_color = Color(*fill)
            self._rect = RoundedRectangle(radius=[radius] * 4)

            self._border_color = Color(*border)
            self._border = Line(width=dp(1))

        self.bind(pos=self._sync_panel, size=self._sync_panel)
        self._sync_panel()

    def _sync_panel(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [self.radius] * 4

        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self.radius,
        )


# ---------------------------------------------------------------------
# BUTTONS
# ---------------------------------------------------------------------

class FlatButton(Button):
    """Flat, rounded button with a few theme variants."""

    selected = BooleanProperty(False)

    def __init__(
        self,
        variant="default",
        tone=None,
        radius=RADIUS_SM,
        **kwargs
    ):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        kwargs.setdefault("background_disabled_normal", "")
        kwargs.setdefault("background_disabled_down", "")
        kwargs.setdefault("background_color", TRANSPARENT)
        kwargs.setdefault("bold", True)
        kwargs.setdefault("halign", "center")

        super().__init__(**kwargs)

        self.variant = variant
        self.tone = tone
        self.radius = radius
        self._pressed = False

        with self.canvas.before:
            self._fill_color = Color(*SURFACE_HI)
            self._rect = RoundedRectangle(radius=[radius] * 4)

            self._border_color = Color(*BORDER)
            self._border = Line(width=dp(1))

        self.bind(
            pos=self._sync_button,
            size=self._sync_button,
            state=self._sync_button,
            disabled=self._sync_button,
            selected=self._sync_button,
        )
        self._sync_button()

    def _palette(self):
        if self.disabled:
            return (SURFACE, BORDER_SOFT, DIM)

        if self.variant == "primary":
            return (ACCENT, ACCENT_HI, ACCENT_DARK)

        if self.variant == "ghost":
            return (TRANSPARENT, BORDER, TEXT)

        if self.variant == "danger":
            return (
                mix(SURFACE, RED, 0.18),
                mix(BORDER, RED, 0.55),
                hex_rgba("FFD9D9"),
            )

        if self.tone in TEAM_COLORS:
            team = TEAM_COLORS[self.tone]

            if self.selected:
                return (
                    mix(SURFACE_HI, team, 0.22),
                    team,
                    hex_rgba("FFFFFF"),
                )

            return (
                SURFACE_HI,
                mix(BORDER, team, 0.35),
                TEXT,
            )

        return (SURFACE_HI, BORDER, TEXT)

    def _sync_button(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        fill, border, text = self._palette()

        if self.state == "down":
            fill = mix(fill, (0, 0, 0, 1), 0.12)

        self._fill_color.rgba = fill
        self._border_color.rgba = border

        self.color = text
        self.disabled_color = text

        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [self.radius] * 4

        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self.radius,
        )


# ---------------------------------------------------------------------
# SCORE CHIP
# ---------------------------------------------------------------------

class ScoreChip(BoxLayout):
    """Small pill that shows one team's remaining words."""

    text = StringProperty("")

    def __init__(self, team, compact=False, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint", (None, None))

        if compact:
            kwargs.setdefault("width", dp(62))
            kwargs.setdefault("height", dp(28))
            kwargs.setdefault("padding", [dp(6), dp(2), dp(6), dp(2)])
            kwargs.setdefault("spacing", dp(4))
        else:
            kwargs.setdefault("width", dp(104))
            kwargs.setdefault("height", dp(38))
            kwargs.setdefault("padding", [dp(12), dp(4), dp(12), dp(4)])
            kwargs.setdefault("spacing", dp(8))

        super().__init__(**kwargs)

        self.team = team
        self.compact = compact
        self._active = False

        with self.canvas.before:
            self._fill_color = Color(*SURFACE)
            self._rect = RoundedRectangle(radius=[RADIUS_SM] * 4)

            self._border_color = Color(*BORDER)
            self._border = Line(width=dp(1))

        self.team_label = Label(
            text=team.upper(),
            font_size=dp(8) if compact else dp(10),
            bold=True,
            color=MUTED,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=dp(26) if compact else dp(42),
        )

        self.num_label = Label(
            text="0",
            font_size=dp(14) if compact else dp(19),
            bold=True,
            color=TEAM_COLORS.get(team, TEXT),
            halign="right",
            valign="middle",
            size_hint=(1, 1),
        )

        self.add_widget(self.team_label)
        self.add_widget(self.num_label)

        self.bind(
            text=self._on_text,
            pos=self._sync_chip,
            size=self._sync_chip,
        )
        self._sync_chip()

    def set_remaining(self, amount):
        self.text = f"{self.team.upper()}: {amount}"

    def _on_text(self, *args):
        value = self.text or ""
        amount = value.split(":", 1)[-1].strip()

        if amount:
            self.num_label.text = amount

    def set_active(self, active):
        self._active = bool(active)
        self._sync_chip()

    def _sync_chip(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        team = TEAM_COLORS.get(self.team, MUTED)

        if self._active:
            fill = mix(SURFACE, team, 0.12)
            border = fade(team, 0.7)
        else:
            fill = SURFACE
            border = BORDER

        self._fill_color.rgba = fill
        self._border_color.rgba = border

        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [RADIUS_SM] * 4

        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            RADIUS_SM,
        )


# ---------------------------------------------------------------------
# TURN BAR
# ---------------------------------------------------------------------

class PulseDot(Widget):
    """Small glowing dot with a looping pulse animation."""

    pulse_alpha = NumericProperty(1.0)

    def __init__(self, colour=RED, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(10), dp(10)))

        super().__init__(**kwargs)

        with self.canvas:
            self._colour = Color(*colour)
            self._dot = Ellipse()

        self._animation = (
            Animation(
                pulse_alpha=0.25,
                duration=0.85,
                transition="in_out_sine",
            )
            + Animation(
                pulse_alpha=1.0,
                duration=0.85,
                transition="in_out_sine",
            )
        )
        self._animation.repeat = True
        self._animation.start(self)

        self.bind(
            pos=self._sync_dot,
            size=self._sync_dot,
            pulse_alpha=self._sync_dot,
            parent=self._on_parent,
        )
        self._sync_dot()

    def set_colour(self, colour):
        self._colour.rgba = colour

    def _sync_dot(self, *args):
        self._dot.pos = self.pos
        self._dot.size = self.size
        self._colour.a = self.pulse_alpha

    def _on_parent(self, instance, parent):
        if parent is None:
            self._animation.cancel(self)


class TurnBar(Panel):
    """Turn indicator: pulsing dot, big label, context line."""

    def __init__(self, compact=False, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", dp(40) if compact else dp(48))
        kwargs.setdefault(
            "padding",
            [dp(10), dp(5), dp(10), dp(5)]
            if compact
            else [dp(14), dp(8), dp(14), dp(8)],
        )
        kwargs.setdefault("spacing", dp(7) if compact else dp(10))

        super().__init__(radius=RADIUS_SM, **kwargs)

        self.compact = compact

        with self.canvas.before:
            self._edge_color = Color(*RED)
            self._edge = RoundedRectangle(radius=[dp(2)] * 4)

        self.dot = PulseDot(RED, size_hint=(None, None))
        self.dot.size_hint = (None, None)
        self.dot.width = dp(8) if compact else dp(10)
        self.dot.height = dp(8) if compact else dp(10)

        self.label = Label(
            text="",
            font_size=dp(12) if compact else dp(15),
            bold=True,
            color=TEXT,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=dp(102) if compact else dp(180),
        )

        self.sub = Label(
            text="",
            font_size=dp(10) if compact else dp(12),
            color=MUTED,
            halign="left",
            valign="middle",
            shorten=True,
            max_lines=1,
            size_hint=(1, 1),
        )
        self.sub.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )

        self.extra = Label(
            text="",
            markup=True,
            font_size=dp(10) if compact else dp(11),
            color=MUTED,
            halign="right",
            valign="middle",
            size_hint=(None, 1),
            width=0,
        )

        self.add_widget(self.dot)
        self.add_widget(self.label)
        self.add_widget(self.sub)
        self.add_widget(self.extra)

        self.bind(pos=self._sync_bar, size=self._sync_bar)
        self._sync_bar()

    @property
    def text(self):
        return self.label.text

    @text.setter
    def text(self, value):
        self.label.text = value

    def set_team(self, team, words_left=None, note=None, extra=None):
        colour = TEAM_COLORS.get(team, MUTED)

        self._fill_color.rgba = mix(SURFACE, colour, 0.16)
        self._border_color.rgba = fade(colour, 0.45)
        self._edge_color.rgba = colour
        self.dot.set_colour(colour)

        self.label.text = f"{str(team).upper()} TEAM TURN"

        if words_left is not None:
            self.sub.text = f"{words_left} words remaining"
        elif note is not None:
            self.sub.text = note

        if extra is not None:
            self.extra.text = extra
            if extra:
                self.extra.texture_update()
                self.extra.width = self.extra.texture_size[0] + dp(2)
            else:
                self.extra.width = 0

        self._sync_bar()

    def _sync_bar(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        self._edge.pos = (self.x + dp(1), self.y + dp(7))
        self._edge.size = (dp(3), max(dp(2), self.height - dp(14)))


# ---------------------------------------------------------------------
# BOARD CARDS
# ---------------------------------------------------------------------

class BoardCard(Button):
    """
    Two-sided card.

    The word side is a dark surface; the answer side is tinted with the
    card's hidden role. A guess crossfades one into the other over 0.22s
    and adds a short scale pop.
    """

    reveal_progress = NumericProperty(0.0)
    pop = NumericProperty(0.0)

    key_style = False

    def __init__(self, word, role=None, **kwargs):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        kwargs.setdefault("background_disabled_normal", "")
        kwargs.setdefault("background_disabled_down", "")
        kwargs.setdefault("background_color", TRANSPARENT)
        kwargs.setdefault("text", str(word).upper())
        kwargs.setdefault("font_size", dp(16))
        kwargs.setdefault("bold", True)
        kwargs.setdefault("color", TEXT)
        kwargs.setdefault("halign", "center")
        kwargs.setdefault("valign", "middle")

        super().__init__(**kwargs)

        self.word = word
        self.role = role
        self.revealed = False
        self.card_index = 0
        self._dim = False

        with self.canvas.before:
            self._face_color = Color(*SURFACE_HI)
            self._face = RoundedRectangle(radius=[RADIUS_CARD] * 4)

            self._border_color = Color(*BORDER)
            self._border = Line(width=dp(1))

            self._pip_color = Color(*fade(hex_rgba("FFFFFF"), 0.0))
            self._pip = Ellipse()

        self.bind(
            pos=self._sync_card,
            size=self._sync_card,
            reveal_progress=self._sync_card,
            pop=self._sync_card,
            disabled=self._sync_card,
        )
        self._sync_card()

    # -- faces --------------------------------------------------------

    def _faces(self):
        return KEY_FACES if self.key_style else CARD_FACES

    def _face_spec(self):
        faces = self._faces()
        return faces.get(self.role, faces["neutral"])

    def _sync_card(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        self._fit_font()

        progress = self.reveal_progress
        spec = self._face_spec()

        fill = mix(SURFACE_HI, spec["fill"], progress)
        border = mix(BORDER, spec["border"], progress)
        text = mix(TEXT, spec["text"], progress)

        if self._dim:
            fill = scaled(fill, 0.55)
            border = scaled(border, 0.55)
            text = scaled(text, 0.72)

        grow = dp(2.2) * self.pop

        x = self.x - grow
        y = self.y - grow
        width = self.width + grow * 2
        height = self.height + grow * 2

        self._face_color.rgba = fill
        self._border_color.rgba = border
        self.color = text
        self.disabled_color = text

        self._face.pos = (x, y)
        self._face.size = (width, height)
        self._face.radius = [RADIUS_CARD] * 4

        self._border.rounded_rectangle = (
            x,
            y,
            width,
            height,
            RADIUS_CARD,
        )

        self._pip_color.rgba = fade(
            spec["text"],
            0.8 * progress if not self._dim else 0.3,
        )
        self._pip.size = (dp(6), dp(6))
        self._pip.pos = (
            x + width - dp(13),
            y + height - dp(13),
        )

    # -- behaviour ----------------------------------------------------

    def _fit_font(self):
        """Keep long words readable inside small phone cards."""

        word = str(getattr(self, "word", "") or "")
        length = max(1, len(word))

        width_budget = max(dp(1), self.width - dp(6))

        # Key cards also render the role line and a "×" guess marker,
        # so they need a little more horizontal headroom.
        if self.key_style:
            width_size = width_budget / ((length + 2) * 0.70)
        else:
            width_size = width_budget / (length * 0.62)

        height_size = self.height * (0.24 if self.key_style else 0.34)

        size = max(dp(7), min(width_size, height_size, dp(22)))

        if abs(size - self.font_size) > 0.4:
            self.font_size = size

    def reveal(self, role=None, animate=True):
        if self.revealed:
            return

        self.revealed = True

        if role in CARD_ROLES:
            self.role = role

        self.disabled = True

        if animate:
            Animation(
                reveal_progress=1.0,
                duration=0.22,
                transition="out_quad",
            ).start(self)

            pop = (
                Animation(pop=1.0, duration=0.09, transition="out_quad")
                + Animation(pop=0.0, duration=0.13, transition="in_quad")
            )
            pop.start(self)
        else:
            self.reveal_progress = 1.0

    def reset(self):
        self.revealed = False
        self._dim = False
        self.reveal_progress = 0.0
        self.pop = 0.0
        self.disabled = False


# Small helper so reveal() can validate roles without importing the
# tuple from network.py (which would create a circular expectation).
CARD_ROLES = ("red", "blue", "neutral", "assassin")


class KeyCard(BoardCard):
    """Always-visible answer card used by spymasters and the end screen."""

    key_style = True

    def __init__(self, word, role, **kwargs):
        kwargs.setdefault("font_size", dp(13))
        kwargs.setdefault("markup", True)

        super().__init__(word, role=role, **kwargs)

        self.disabled = True
        self.spy_word = word
        self.spy_role = role
        self.reveal_progress = 1.0
        self._refresh_text()

    def _refresh_text(self):
        marker = "  ×" if self.revealed else ""
        self.text = (
            f"{str(self.word).upper()}{marker}\n"
            f"[size=9]{str(self.role).upper()}[/size]"
        )
        self.markup = True

    def mark_revealed(self):
        if self.revealed:
            return

        self.revealed = True
        self._dim = True
        self._refresh_text()
        self._sync_card()


# ---------------------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------------------

class ScrollLabel(ScrollView):
    """Vertically scrolling, markup-aware label."""

    def __init__(self, text="", font_size=dp(13), **kwargs):
        kwargs.setdefault("do_scroll_x", False)
        kwargs.setdefault("bar_width", dp(3))
        kwargs.setdefault("scroll_type", ["bars", "content"])

        super().__init__(**kwargs)

        self.label = Label(
            text=text,
            markup=True,
            font_size=font_size,
            color=TEXT,
            halign="left",
            valign="top",
            size_hint_y=None,
            height=0,
        )
        self.label.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )
        self.label.bind(
            texture_size=lambda instance, size: setattr(
                instance, "height", max(size[1], dp(1))
            )
        )

        self.add_widget(self.label)

    @property
    def text(self):
        return self.label.text

    @text.setter
    def text(self, value):
        self.label.text = value


class FlatInput(TextInput):
    """Rounded dark text input."""

    def __init__(self, **kwargs):
        kwargs.setdefault("multiline", False)
        kwargs.setdefault("font_size", dp(15))
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_active", "")
        kwargs.setdefault("background_color", TRANSPARENT)
        kwargs.setdefault("foreground_color", TEXT)
        kwargs.setdefault("cursor_color", ACCENT)
        kwargs.setdefault("hint_text_color", fade(MUTED, 0.85))
        kwargs.setdefault("selection_color", fade(ACCENT, 0.3))
        kwargs.setdefault("padding", [dp(14), dp(12), dp(14), dp(10)])
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", dp(46))

        super().__init__(**kwargs)

        with self.canvas.before:
            self._fill_color = Color(*SURFACE_HI)
            self._rect = RoundedRectangle(radius=[RADIUS_SM] * 4)

            self._border_color = Color(*BORDER)
            self._border = Line(width=dp(1))

        self.bind(
            pos=self._sync_input,
            size=self._sync_input,
            focus=self._sync_input,
        )
        self._sync_input()

    def _sync_input(self, *args):
        if self.width <= 0 or self.height <= 0:
            return

        self._border_color.rgba = (
            fade(ACCENT, 0.65) if self.focus else BORDER
        )

        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [RADIUS_SM] * 4

        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            RADIUS_SM,
        )


def heading(text, size=dp(26), colour=TEXT):
    """Convenience label factory used by the screens."""

    return Label(
        text=text,
        font_size=size,
        bold=True,
        color=colour,
        halign="left",
        valign="middle",
        size_hint_y=None,
        height=size + dp(10),
    )


def muted_label(text, size=dp(12), halign="left"):
    return Label(
        text=text,
        font_size=size,
        color=MUTED,
        halign=halign,
        valign="middle",
        size_hint_y=None,
        height=size + dp(8),
    )
