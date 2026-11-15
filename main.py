import random
import threading

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

import platform_utils

from network import (
    DISCOVERY_PORT,
    DiscoveryResponder,
    GameClient,
    GameServer,
    PORT,
    discover_hosts,
)

from qr_join import (
    build_join_payload,
    is_scan_supported,
    parse_join_payload,
    scan_qr_code,
    QRCodeWidget,
)

from theme import (
    ACCENT,
    ACCENT_DARK,
    BG,
    BLUE,
    BORDER,
    BORDER_SOFT,
    DIM,
    MUTED,
    RADIUS,
    RADIUS_SM,
    RED,
    SURFACE,
    SURFACE_HI,
    TEAM_COLORS,
    TEAM_HEX,
    TEXT,
    fade,
    hex_rgba,
    is_compact_size,
    layout_mode,
    mix,
    safe_markup,
)

from ui_widgets import (
    BoardCard,
    FlatButton,
    FlatInput,
    KeyCard,
    LogoMark,
    Panel,
    Scrim,
    ScoreChip,
    ScreenRoot,
    ScrollLabel,
    TurnBar,
    heading,
    muted_label,
)

from word_library import WordLibrary


# A full match needs 4 players in the lobby. The host is a player
# too (they pick a team and role), so this means host + 3 clients.
MIN_PLAYERS = 4

# How many log lines the sidebar keeps visible at once.
LOG_VISIBLE = 8


class CodenamesApp(App):

    def build(self):
        Window.clearcolor = BG
        Window.title = "Codenames"

        if platform not in ("android", "ios"):
            Window.size = (1200, 720)

        if platform == "android":
            try:
                Window.softinput_mode = "below_target"
            except Exception:
                pass

        self.word_library = WordLibrary("en")

        self.network_server = None
        self.network_client = None

        self.host_poll_event = None
        self.client_poll_event = None

        # Hosting metadata used by LAN discovery and the QR fallback.
        self.host_port = PORT
        self.host_ip = "127.0.0.1"
        self.host_ssid = ""
        self.host_network_kind = "WI-FI"
        self.hotspot_ssid = ""
        self.hotspot_password = ""
        self.scanned_ssid = ""

        # LAN discovery: host advertises, client scans.
        self.discovery = None
        self.discovery_error = None
        self.host_network_event = None
        self.lobby_scan_event = None
        self.lobby_scan_thread = None
        self.join_lobbies = []
        self.lobby_list_box = None
        self.lobby_status_label = None
        self._last_scan_payload = None

        # Shown above the lobby list after a failed/lost connection.
        self._join_notice = None

        # Screen rebuilding is driven by orientation / size breakpoints.
        self._screen_builder = self.create_menu
        self._screen_name = "menu"
        self._layout_signature_saved = self._layout_signature()
        self._resize_event = None
        self._overlay_root = None
        self._qr_refresh_event = None
        self._manual_join_open = False

        # ---------------------------------------------------
        # HOST GAME STATE
        # ---------------------------------------------------
        self.current_team = "red"
        self.remaining = {"red": 9, "blue": 8}
        self.words = []
        self.roles = []
        self.cards = []
        self.revealed_cards = set()
        self.game_over = False
        self.spymaster_cards = []

        # The host is a player too: team + role in the lobby.
        self.host_team = "red"
        self.host_role = "spymaster"

        # ---------------------------------------------------
        # CLIENT GAME STATE
        # ---------------------------------------------------
        self.client_name = "Player"
        self.client_key = None
        self.client_team = None
        self.client_role = "operative"
        self.client_lobby = []

        self.client_words = []
        self.client_cards = []
        self.client_key_roles = []
        self.client_revealed = set()
        self.client_revealed_roles = {}
        self.client_remaining = {"red": 9, "blue": 8}
        self.client_current_team = "red"
        self.client_selection_pending = False
        self.client_game_over = False

        # ---------------------------------------------------
        # SHARED UI STATE
        # ---------------------------------------------------
        self.match_log = []
        self.log_label = None
        self.game_over_root = None
        self._game_over_shown = False

        Window.bind(size=self._on_window_resize)
        Window.bind(on_keyboard=self._on_keyboard)

        # A plain container so every screen can be a ScreenRoot with
        # its own backdrop, padding and glows.
        container = BoxLayout(orientation="vertical")
        container.add_widget(self.create_menu())

        return container

    # -----------------------------------------------------------------
    # RESPONSIVE / LIFECYCLE
    # -----------------------------------------------------------------

    def _layout_signature(self):
        width, height = Window.size
        return (layout_mode(width, height), is_compact_size(width, height))

    def _on_window_resize(self, *args):
        if getattr(self, "root", None) is None:
            return

        if self._resize_event is not None:
            self._resize_event.cancel()

        self._resize_event = Clock.schedule_once(
            self._apply_layout_change, 0.18
        )

    def _apply_layout_change(self, dt):
        self._resize_event = None

        signature = self._layout_signature()

        if signature == self._layout_signature_saved:
            return

        self._layout_signature_saved = signature

        # The result panel is centred and clamped, so it survives a
        # rotation. Rebuilding underneath it would lose the final board.
        if self._game_over_shown:
            return

        # A QR overlay is cheap to reopen; drop it so the rebuilt
        # screen uses the new orientation.
        if self._overlay_root is not None:
            self._close_overlay(rebuild=False)

        builder = getattr(self, "_screen_builder", None)

        if builder is None or getattr(self, "root", None) is None:
            return

        self.root.clear_widgets()
        self.root.add_widget(builder())

    def show_screen(self, builder, name):
        self._screen_builder = builder
        self._screen_name = name
        self._layout_signature_saved = self._layout_signature()

        if name != "join":
            self.stop_lobby_scan()

        root = getattr(self, "root", None)

        if root is None:
            return

        root.clear_widgets()
        root.add_widget(builder())

    def on_pause(self):
        # Keep hosting/connections alive when Android backgrounds the app
        # (for example while the QR scanner is open).
        return True

    def on_resume(self):
        if hasattr(self, "host_poll_event"):
            pass

    def _on_keyboard(self, window, key, scancode, codepoint, modifier):
        # Android back button / desktop Esc.
        if key != 27:
            return False

        if self._overlay_root is not None:
            self._close_overlay()
            return True

        if self._screen_name != "menu":
            self.return_to_menu(None)
            return True

        return False

    def _clamp_width(self, value, margin=dp(24)):
        width = Window.width

        if width <= 0:
            return value

        return min(value, max(dp(240), width - margin))

    def _host_spymaster_builder(self):
        lobby = (
            self.network_server.server.get_lobby()
            if self.network_server
            else []
        )

        return self.create_spymaster_game_screen(
            self.words,
            self.roles,
            self.host_team,
            lobby,
            remaining=dict(self.remaining),
            current_team=self.current_team,
        )

    def _client_spymaster_builder(self):
        return self.create_spymaster_game_screen(
            self.client_words,
            self.client_key_roles,
            self.client_team,
            getattr(self, "client_lobby", []),
            remaining=dict(self.client_remaining),
            current_team=self.client_current_team,
            for_client=True,
        )

    # -----------------------------------------------------------------
    # SCROLL / PANEL HELPERS
    # -----------------------------------------------------------------

    def _vertical_scroll(self, content):
        scroll = ScrollView(
            do_scroll_x=False,
            bar_width=dp(3),
            scroll_type=["bars", "content"],
        )

        content.size_hint_x = 1
        scroll.add_widget(content)

        scroll.bind(
            width=lambda instance, width: setattr(
                content, "width", width
            )
        )

        return scroll

    def _centered_scroll(self, card, margin=dp(12)):
        scroll = ScrollView(
            do_scroll_x=False,
            bar_width=dp(3),
            scroll_type=["bars", "content"],
        )

        holder = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
        )

        def sync(instance, size):
            holder.height = max(size[1], card.height + margin * 2)

        scroll.bind(size=sync)
        card.bind(size=lambda *args: sync(scroll, scroll.size))
        card.size_hint = (None, None)
        holder.add_widget(card)
        scroll.add_widget(holder)

        scroll.bind(
            width=lambda instance, width: setattr(holder, "width", width)
        )

        return scroll

    def _two_panel_content(self, left, right, left_height, right_height):
        compact = self._is_compact()
        portrait = self._layout_mode() == "portrait"

        if portrait:
            left.size_hint = (1, None)
            left.height = left_height
            right.size_hint = (1, None)
            right.height = right_height

            column = BoxLayout(
                orientation="vertical",
                spacing=dp(10),
                size_hint=(1, None),
            )
            column.height = left_height + right_height + dp(10)
            column.add_widget(left)
            column.add_widget(right)

            return self._vertical_scroll(column)

        row = BoxLayout(
            orientation="horizontal",
            spacing=dp(10) if compact else dp(16),
        )
        row.add_widget(left)
        row.add_widget(right)

        if compact:
            row.size_hint = (1, None)
            row.height = max(left_height, right_height)
            return self._vertical_scroll(row)

        return row

    def _game_content(self, board_panel, sidebar):
        compact = self._is_compact()
        portrait = self._layout_mode() == "portrait"

        content = BoxLayout(
            orientation="vertical" if portrait else "horizontal",
            spacing=dp(8) if compact else dp(14),
        )

        if portrait:
            board_panel.size_hint = (1, None)
            board_panel.height = dp(200)

            def sync(instance, size):
                # Keep the board as large as possible without starving
                # the sidebar: roughly 58% board / 42% log + actions.
                side = min(
                    size[0],
                    max(dp(150), (size[1] - dp(8)) * 0.58),
                )
                board_panel.height = side

            content.bind(size=sync)
            sidebar.size_hint = (1, 1)

            content.add_widget(board_panel)
            content.add_widget(sidebar)

        else:
            board_panel.size_hint = (1, 1)
            sidebar.size_hint = (None, 1)
            sidebar.width = dp(220) if compact else dp(258)

            content.add_widget(board_panel)
            content.add_widget(sidebar)

        return content

    def _game_sidebar(self, action=None, info=None, note=None, extras=(), after=()):
        compact = self._is_compact()
        portrait = self._layout_mode() == "portrait"

        sidebar = BoxLayout(
            orientation="vertical",
            spacing=dp(8) if compact else dp(12),
        )

        for widget in extras:
            sidebar.add_widget(widget)

        sidebar.add_widget(self._build_log_panel())

        for widget in after:
            sidebar.add_widget(widget)

        if portrait:
            row = BoxLayout(
                orientation="horizontal",
                size_hint=(1, None),
                height=dp(44) if compact else dp(50),
                spacing=dp(8),
            )

            if info is not None:
                info.size_hint = (1, 1)
                info.height = 0
                info.halign = "left"
                info.font_size = dp(10)
                info.text_size = (0, None)
                info.bind(
                    width=lambda instance, width: setattr(
                        instance, "text_size", (width, None)
                    )
                )
                row.add_widget(info)

            if action is not None:
                action.size_hint = (1, 1)
                action.height = 0
                row.add_widget(action)

            sidebar.add_widget(row)

            if note is not None:
                note.height = dp(14)
                sidebar.add_widget(note)

        else:
            if action is not None:
                sidebar.add_widget(action)

            if info is not None:
                sidebar.add_widget(info)

            if note is not None:
                sidebar.add_widget(note)

        return sidebar

    def _is_compact(self):
        width, height = Window.size
        return is_compact_size(width, height)

    def _layout_mode(self):
        width, height = Window.size
        return layout_mode(width, height)

    # -----------------------------------------------------------------
    # SHARED UI HELPERS
    # -----------------------------------------------------------------

    def _label(
        self,
        text="",
        size=dp(14),
        color=TEXT,
        bold=False,
        halign="left",
        markup=False,
        height=None,
        shorten=False,
        max_lines=None,
    ):
        label = Label(
            text=text,
            font_size=size,
            color=color,
            bold=bold,
            halign=halign,
            valign="middle",
            markup=markup,
            shorten=shorten,
            max_lines=max_lines or 0,
        )

        if height is not None:
            label.size_hint_y = None
            label.height = height

        if halign != "center":
            label.bind(
                width=lambda instance, width: setattr(
                    instance, "text_size", (width, None)
                )
            )

        return label

    def _accent_rule(self, width=dp(120)):
        return Panel(
            fill=ACCENT,
            border=ACCENT,
            radius=dp(2),
            size_hint=(None, None),
            width=width,
            height=dp(3),
        )

    def _make_chip(self, team, remaining):
        chip = ScoreChip(team, compact=self._is_compact())
        chip.set_remaining(remaining.get(team, 0))
        return chip

    def _build_top_bar(
        self,
        brand_text,
        remaining,
        action_text,
        action_callback,
        badge_text=None,
        badge_team=None
    ):
        compact = self._is_compact()

        bar = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(38) if compact else dp(44),
            spacing=dp(6) if compact else dp(10),
        )

        brand = Label(
            text=brand_text,
            font_size=dp(12) if compact else dp(15),
            bold=True,
            color=TEXT,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=dp(86) if compact else dp(140),
        )
        brand.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )

        bar.add_widget(brand)

        # The badge is nice on tablets/desktops but steals too much width
        # on a phone top bar.
        if badge_text and not compact:
            badge = Label(
                text=badge_text,
                font_size=dp(11),
                bold=True,
                color=TEAM_COLORS.get(badge_team, MUTED),
                halign="left",
                valign="middle",
                size_hint=(None, 1),
                width=dp(62),
            )
            badge.bind(
                width=lambda instance, width: setattr(
                    instance, "text_size", (width, None)
                )
            )
            bar.add_widget(badge)

        bar.add_widget(Widget(size_hint_x=1))

        red_chip = self._make_chip("red", remaining)
        blue_chip = self._make_chip("blue", remaining)

        bar.add_widget(red_chip)
        bar.add_widget(blue_chip)

        action = FlatButton(
            text=action_text,
            variant="ghost",
            font_size=dp(10) if compact else dp(12),
            size_hint=(None, 1),
            width=dp(62) if compact else dp(96),
        )
        action.bind(on_press=action_callback)
        bar.add_widget(action)

        return bar, red_chip, blue_chip

    def _build_log_panel(self):
        panel = Panel(
            orientation="vertical",
            padding=[dp(14), dp(14), dp(14), dp(14)],
            spacing=dp(8),
        )

        title = self._label(
            "MATCH LOG",
            dp(10),
            MUTED,
            bold=True,
            height=dp(14),
        )

        self.log_label = ScrollLabel(font_size=dp(12))

        panel.add_widget(title)
        panel.add_widget(self.log_label)

        self.render_log()

        return panel

    def reset_match_log(self):
        self.match_log = []
        self.render_log()

    def add_log(self, text, kind="neutral"):
        self.match_log.append((kind, text))
        del self.match_log[:-40]
        self.render_log()

    def add_turn_log(self, team):
        team_hex = TEAM_HEX.get(team, "8B96A8")

        self.add_log(
            f"Turn passed · "
            f"[b][color=#{team_hex}]{str(team).upper()}[/color][/b]",
            team,
        )

    def render_log(self):
        label = getattr(self, "log_label", None)

        if label is None:
            return

        entries = self.match_log[-LOG_VISIBLE:]

        if not entries:
            label.text = "[color=#5C6675]No moves yet.[/color]"
            return

        lines = []

        for kind, text in entries:
            dot = TEAM_HEX.get(kind, "8B96A8")
            lines.append(f"[color=#{dot}]•[/color]  {text}")

        label.text = "\n".join(lines)

    def _role_button(self, team, role, callback, height=dp(58)):
        button = FlatButton(
            text=(
                f"[b]{team.upper()}[/b]\n"
                f"[size=10]{role.upper()}[/size]"
            ),
            markup=True,
            tone=team,
            font_size=dp(13),
            size_hint=(1, None),
            height=height,
        )
        button.bind(
            on_press=lambda instance: callback(team, role)
        )
        return button

    def _refresh_role_buttons(self, buttons, team, role):
        for (button_team, button_role), button in buttons.items():
            button.selected = (
                button_team == team and button_role == role
            )

    def _legend_text(self, roles):
        counts = {
            "red": roles.count("red"),
            "blue": roles.count("blue"),
            "neutral": roles.count("neutral"),
            "assassin": roles.count("assassin"),
        }

        parts = []

        for name in ("red", "blue", "neutral", "assassin"):
            colour = TEAM_HEX.get(name, "8B96A8")
            parts.append(
                f"[color=#{colour}]•[/color] {name.upper()} {counts[name]}"
            )

        return "    ".join(parts)
# START MULTIPLAYER MATCH
    def start_host_match(self, instance):

        if not self.network_server:
            return

        server = self.network_server.server

        lobby = server.get_lobby()

        clients = [
            player
            for player in lobby
            if not player.get("is_host")
        ]

        total_players = len(clients) + 1  # +1 for the host

        if total_players < MIN_PLAYERS:

            needed = MIN_PLAYERS - total_players

            self.host_status_label.text = (
                f"At least {MIN_PLAYERS} players are needed "
                f"(host + {MIN_PLAYERS - 1}). "
                f"Currently {total_players}; waiting for "
                f"{needed} more."
            )
            return

        if not all(
            player.get("team") in ("red", "blue")
            for player in clients
        ):
            self.host_status_label.text = (
                "Every player must choose a team first."
            )
            return

        # The host's own role was picked in the lobby.
        self.host_team = server.host_team
        self.host_role = server.host_role

        # Decide which team starts
        self.current_team = random.choice(
            ["red", "blue"]
        )

        if self.current_team == "red":

            self.remaining = {
                "red": 9,
                "blue": 8
            }

        else:

            self.remaining = {
                "red": 8,
                "blue": 9
            }

        # Reset the game
        self.game_over = False
        self.cards = []
        self.revealed_cards = set()
        self.spymaster_cards = []
        self._game_over_shown = False
        self.game_over_root = None
        self.reset_match_log()

        # Generate words and roles ONCE
        try:

            self.words = (
                self.word_library.get_random_words(25)
            )

            self.roles = self.generate_roles()

        except (ValueError, FileNotFoundError) as error:

            self.host_status_label.text = str(error)
            return

        # Public information for everyone. The secret key is NOT
        # included, so operatives cannot cheat.
        self.network_server.broadcast({
            "type": "game_started",
            "words": self.words,
            "current_team": self.current_team,
            "remaining": dict(self.remaining)
        })

        # Only send the key to the spymasters of each team.
        for team in ("red", "blue"):

            self.network_server.send_to_role(
                team,
                "spymaster",
                {
                    "type": "spy_key",
                    "team": team,
                    "words": self.words,
                    "roles": self.roles,
                    "current_team": self.current_team,
                    "remaining": dict(self.remaining)
                }
            )

        # Stop lobby polling
        if self.host_poll_event:
            self.host_poll_event.cancel()
            self.host_poll_event = None

        # Start listening for client actions
        self.host_poll_event = Clock.schedule_interval(
            self.update_host_game_events,
            0.1
        )

        platform_utils.keep_screen_on()

        # A spymaster sees the secret key, an operative sees the
        # normal public board.
        if self.host_role == "spymaster":
            self.show_screen(
                self._host_spymaster_builder,
                "host_spymaster",
            )
        else:
            self.show_screen(self.create_game_board, "host_game")
    # MAIN MENU
    def create_menu(self):

        compact = self._is_compact()

        root = ScreenRoot()

        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(390)),
            height=dp(404) if compact else dp(468),
            spacing=dp(8) if compact else dp(10),
        )

        mark_wrap = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
            height=dp(64) if compact else dp(78),
        )
        mark_wrap.add_widget(LogoMark())

        eyebrow = self._label(
            "LAN MULTIPLAYER",
            dp(10) if compact else dp(11),
            ACCENT,
            bold=True,
            halign="center",
            height=dp(18),
        )

        title = Label(
            text="CODENAMES",
            font_size=dp(38) if compact else dp(50),
            bold=True,
            color=TEXT,
            size_hint=(1, None),
            height=dp(50) if compact else dp(62),
        )

        rule_wrap = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
            height=dp(16),
        )
        rule_wrap.add_widget(self._accent_rule())

        gap = Widget(
            size_hint_y=None,
            height=dp(8) if compact else dp(14),
        )

        play_button = FlatButton(
            text="PLAY",
            variant="primary",
            font_size=dp(15) if compact else dp(16),
            size_hint=(1, None),
            height=dp(46) if compact else dp(52),
        )

        how_to_play = FlatButton(
            text="HOW TO PLAY",
            variant="ghost",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(44) if compact else dp(48),
        )

        exit_button = FlatButton(
            text="EXIT",
            variant="ghost",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(44) if compact else dp(48),
        )

        play_button.bind(
            on_press=self.show_play_screen
        )

        how_to_play.bind(
            on_press=self.show_how_to_play
        )

        exit_button.bind(
            on_press=self.stop
        )

        card.add_widget(mark_wrap)
        card.add_widget(eyebrow)
        card.add_widget(title)
        card.add_widget(rule_wrap)
        card.add_widget(gap)
        card.add_widget(play_button)
        card.add_widget(how_to_play)
        card.add_widget(exit_button)

        root.add_widget(self._centered_scroll(card))

        return root

    # SHOW PLAY SCREEN
    def show_play_screen(self, instance):
        self.show_screen(self.create_play_screen, "play")

    # PLAY SCREEN
    def create_play_screen(self):

        compact = self._is_compact()

        root = ScreenRoot()

        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(390)),
            height=dp(360) if compact else dp(392),
            spacing=dp(8) if compact else dp(10),
        )

        eyebrow = self._label(
            "LAN MATCH",
            dp(10) if compact else dp(11),
            ACCENT,
            bold=True,
            halign="center",
            height=dp(20),
        )

        title = Label(
            text="PLAY",
            font_size=dp(34) if compact else dp(40),
            bold=True,
            color=TEXT,
            size_hint=(1, None),
            height=dp(46) if compact else dp(54),
        )

        subtitle = self._label(
            "Host a game on this phone or join one\n"
            "that is already running nearby.",
            dp(11) if compact else dp(12),
            MUTED,
            halign="center",
            height=dp(38) if compact else dp(42),
        )
        subtitle.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )

        gap = Widget(
            size_hint_y=None,
            height=dp(10) if compact else dp(16),
        )

        host_button = FlatButton(
            text="HOST GAME",
            variant="primary",
            font_size=dp(14) if compact else dp(15),
            size_hint=(1, None),
            height=dp(48) if compact else dp(52),
        )

        join_button = FlatButton(
            text="JOIN GAME",
            variant="default",
            font_size=dp(14) if compact else dp(15),
            size_hint=(1, None),
            height=dp(48) if compact else dp(52),
        )

        back_button = FlatButton(
            text="BACK",
            variant="ghost",
            font_size=dp(12) if compact else dp(13),
            size_hint=(1, None),
            height=dp(42) if compact else dp(44),
        )

        host_button.bind(
            on_press=self.host_game
        )

        join_button.bind(
            on_press=self.show_join_screen
        )

        back_button.bind(
            on_press=self.return_to_menu
        )

        card.add_widget(eyebrow)
        card.add_widget(title)
        card.add_widget(subtitle)
        card.add_widget(gap)
        card.add_widget(host_button)
        card.add_widget(join_button)
        card.add_widget(back_button)

        root.add_widget(self._centered_scroll(card))

        return root
    # HOST GAME
    def _start_network_server(self):
        """Prefer port 5000, fall back to any free port."""

        error = None

        for port in (PORT, 0):
            candidate = GameServer(port=port)

            try:
                candidate.start()
            except OSError as exc:
                error = exc
                continue

            return candidate, None

        return None, error

    def host_game(self, instance):

        self.cleanup_network()

        self.host_error = None
        self.network_server, self.host_error = (
            self._start_network_server()
        )

        if self.network_server:
            # The host's default assignment lives on the server.
            self.host_team = self.network_server.server.host_team
            self.host_role = self.network_server.server.host_role

            # Port 0 (a busy 5000) is resolved by the OS; read it back so
            # discovery replies always contain the real port.
            self.host_port = (
                self.network_server.server.server_address[1]
            )
            self.host_ip = platform_utils.get_host_ip()

            self._refresh_host_network()
            self._start_discovery()

        platform_utils.keep_screen_on()

        self.show_screen(self.create_host_screen, "host")

        if self.network_server:
            self.host_poll_event = Clock.schedule_interval(
                self.update_host_lobby, 0.2
            )

            # The network name can change while hosting (hotspot switched
            # on mid-lobby), so keep the header fresh.
            self.host_network_event = Clock.schedule_interval(
                self._refresh_host_network, 4.0
            )

    # -----------------------------------------------------------------
    # LAN DISCOVERY (HOST SIDE)
    # -----------------------------------------------------------------

    def _refresh_host_network(self, *args):
        """Read the current Wi-Fi/hotspot name (main thread only)."""

        kind, name = platform_utils.get_network_label()

        self.host_network_kind = kind
        self.host_ssid = name

        label = getattr(self, "host_network_label", None)

        if label is not None:
            label.text = self._network_label_text()

        return True

    def _network_label_text(self):
        kind = self.host_network_kind or "WI-FI"
        name = self.host_ssid

        if not name:
            return f"{kind} · name hidden"

        return f"{kind} · {name}"

    def _discovery_info(self):
        """Payload a probing client receives. Runs on the UDP thread."""

        players = 1

        server = self.network_server

        if server is not None:

            try:
                players = len(server.server.get_lobby())
            except Exception:
                players = 1

        return {
            "host": self.host_ip,
            "port": int(self.host_port or PORT),
            "ssid": self.host_ssid,
            "kind": self.host_network_kind,
            "players": players,
            "max": 8,
        }

    def _start_discovery(self):
        self.discovery_error = None

        self.discovery = DiscoveryResponder(self._discovery_info)

        if self.discovery.start():
            return

        self.discovery_error = self.discovery.error
        self.discovery = None

    def _ask_for_network_permission(self):
        """Android hides the SSID without the location permission."""

        if platform != "android":
            return

        if getattr(self, "_asked_network_permission", False):
            self._refresh_host_network()
            return

        self._asked_network_permission = True

        granted = platform_utils.request_network_name_permission(
            self._refresh_host_network
        )

        if granted:
            self._refresh_host_network()

    # HOST LOBBY SCREEN
    def create_host_screen(self):

        compact = self._is_compact()

        root = ScreenRoot()

        # ---------------------------------------------------------
        # HEADER
        # ---------------------------------------------------------

        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(48) if compact else dp(58),
            spacing=dp(8),
        )

        titles = BoxLayout(orientation="vertical", spacing=0)
        titles.add_widget(
            self._label(
                "HOSTING",
                dp(10) if compact else dp(11),
                ACCENT,
                bold=True,
                height=dp(14) if compact else dp(16),
            )
        )
        titles.add_widget(
            self._label(
                "HOST LOBBY",
                dp(20) if compact else dp(26),
                TEXT,
                bold=True,
                height=dp(28) if compact else dp(34),
            )
        )

        back_button = FlatButton(
            text="BACK",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(None, 1),
            width=dp(90) if compact else dp(150),
        )
        back_button.bind(on_press=self.return_to_menu)

        header.add_widget(titles)
        header.add_widget(back_button)

        root.add_widget(header)

        # ---------------------------------------------------------
        # NETWORK BANNER (which Wi-Fi / hotspot the players share)
        # ---------------------------------------------------------

        network_row = Panel(
            orientation="horizontal",
            fill=SURFACE_HI,
            border=BORDER_SOFT,
            radius=RADIUS_SM,
            padding=[
                dp(12) if compact else dp(16),
                dp(6),
                dp(12) if compact else dp(16),
                dp(6),
            ],
            spacing=dp(8),
            size_hint=(1, None),
            height=dp(34) if compact else dp(40),
        )

        network_row.add_widget(
            self._label(
                "ON THIS NETWORK",
                dp(9) if compact else dp(10),
                MUTED,
                bold=True,
                height=dp(12) if compact else dp(14),
            )
        )

        self.host_network_label = self._label(
            self._network_label_text(),
            dp(12) if compact else dp(13),
            ACCENT,
            bold=True,
            halign="right",
            shorten=True,
        )
        network_row.add_widget(self.host_network_label)

        root.add_widget(network_row)

        # ---------------------------------------------------------
        # SERVER FAILED
        # ---------------------------------------------------------

        if not self.network_server:

            self.start_match_button = None
            self.host_qr_button = None
            self.host_network_label.text = "SERVER OFFLINE"

            panel = Panel(
                orientation="vertical",
                padding=[dp(28), dp(28), dp(28), dp(28)],
                spacing=dp(12),
                size_hint=(1, None),
                height=dp(190),
            )

            panel.add_widget(
                self._label(
                    "COULD NOT START THE SERVER",
                    dp(18),
                    RED,
                    bold=True,
                    height=dp(30),
                )
            )
            panel.add_widget(
                self._label(
                    self.host_error or "The hosting server failed.",
                    dp(13),
                    MUTED,
                    height=dp(48),
                )
            )
            panel.add_widget(
                self._label(
                    "Check whether another program is using the port, "
                    "then try again.",
                    dp(12),
                    MUTED,
                    height=dp(40),
                )
            )

            root.add_widget(panel)
            root.add_widget(Widget(size_hint_y=1))

            return root

        # ---------------------------------------------------------
        # LOBBY CONTENT
        # ---------------------------------------------------------

        # Keep the banner truthful and ask Android for the SSID once.
        self._refresh_host_network()
        self._ask_for_network_permission()

        left = Panel(
            orientation="vertical",
            padding=[
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
            ],
            spacing=dp(8) if compact else dp(12),
            size_hint_x=1.1,
        )

        right = Panel(
            orientation="vertical",
            padding=[
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
                dp(14) if compact else dp(20),
            ],
            spacing=dp(7) if compact else dp(10),
            size_hint_x=0.9,
        )

        # Join block ----------------------------------------------

        join_panel = Panel(
            orientation="vertical",
            fill=SURFACE_HI,
            border=BORDER_SOFT,
            radius=RADIUS_SM,
            padding=[dp(12), dp(10), dp(12), dp(10)],
            spacing=dp(5),
            size_hint=(1, None),
            height=dp(116) if compact else dp(124),
        )

        join_panel.add_widget(
            self._label(
                "JOIN THE MATCH",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        join_hint = (
            "Players on this Wi-Fi or hotspot see this lobby "
            "and can join with one tap."
        )

        if self.discovery_error is not None:
            join_hint = (
                "Auto-discovery could not start. Use the QR code "
                "instead."
            )

        join_panel.add_widget(
            self._label(
                join_hint,
                dp(10) if compact else dp(11),
                TEXT if self.discovery_error is None else RED,
                height=dp(34),
            )
        )

        # The QR code is the fallback for networks that block the UDP
        # discovery broadcast (guest Wi-Fi with client isolation).
        self.host_qr_button = FlatButton(
            text="SHOW QR CODE",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, None),
            height=dp(36) if compact else dp(40),
        )
        self.host_qr_button.bind(on_press=self.show_qr_modal)
        join_panel.add_widget(self.host_qr_button)

        left.add_widget(join_panel)

        # Player list ---------------------------------------------

        left.add_widget(
            self._label(
                "PLAYERS",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        self.host_players_label = ScrollLabel(
            font_size=dp(11) if compact else dp(12)
        )
        left.add_widget(self.host_players_label)

        self.host_status_label = self._label(
            "Waiting for players...",
            dp(11) if compact else dp(12),
            MUTED,
            height=dp(46),
        )
        left.add_widget(self.host_status_label)

        left.add_widget(
            self._label(
                "Minimum 4 players — host + 3 guests.",
                dp(10) if compact else dp(11),
                MUTED,
                height=dp(16),
            )
        )

        # Role picker ---------------------------------------------

        right.add_widget(
            self._label(
                "YOUR TEAM & ROLE",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        role_grid = GridLayout(
            cols=2,
            rows=2,
            spacing=dp(6) if compact else dp(8),
            size_hint=(1, None),
            height=dp(112) if compact else dp(126),
        )

        self.host_role_buttons = {}
        assignments = (
            ("red", "spymaster"),
            ("red", "operative"),
            ("blue", "spymaster"),
            ("blue", "operative"),
        )

        for team, role in assignments:
            button = self._role_button(
                team,
                role,
                self.choose_host_assignment,
                height=dp(52) if compact else dp(58),
            )
            self.host_role_buttons[(team, role)] = button
            role_grid.add_widget(button)

        self._refresh_role_buttons(
            self.host_role_buttons,
            self.host_team,
            self.host_role,
        )

        right.add_widget(role_grid)

        self.host_role_label = self._label(
            self.get_host_role_text(),
            dp(11) if compact else dp(12),
            MUTED,
            height=dp(18),
        )
        right.add_widget(self.host_role_label)

        right.add_widget(
            self._label(
                "MATCH SETTINGS",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        minimum_row = Panel(
            orientation="horizontal",
            fill=SURFACE_HI,
            border=BORDER_SOFT,
            radius=RADIUS_SM,
            padding=[dp(12), dp(8), dp(12), dp(8)],
            spacing=dp(8),
            size_hint=(1, None),
            height=dp(38),
        )
        minimum_row.add_widget(
            self._label("Minimum players", dp(12), MUTED)
        )
        minimum_row.add_widget(
            Label(
                text=str(MIN_PLAYERS),
                font_size=dp(13),
                bold=True,
                color=TEXT,
                size_hint=(None, 1),
                width=dp(24),
                halign="right",
                valign="middle",
            )
        )
        right.add_widget(minimum_row)

        right.add_widget(Widget(size_hint_y=1))

        self.start_match_button = FlatButton(
            text=f"START GAME (1/{MIN_PLAYERS} PLAYERS)",
            variant="primary",
            font_size=dp(14) if compact else dp(15),
            size_hint=(1, None),
            height=dp(48) if compact else dp(52),
        )
        self.start_match_button.disabled = True
        self.start_match_button.bind(
            on_press=self.start_host_match
        )
        right.add_widget(self.start_match_button)

        right.add_widget(
            self._label(
                "Roles lock when the match begins.",
                dp(10) if compact else dp(11),
                MUTED,
                halign="center",
                height=dp(16),
            )
        )

        content = self._two_panel_content(
            left,
            right,
            left_height=dp(308) if compact else dp(332),
            right_height=dp(390) if compact else dp(420),
        )

        root.add_widget(content)

        self.update_host_lobby(0)

        return root
    # -----------------------------------------------------------------
    # QR JOIN MODAL
    # -----------------------------------------------------------------

    def _current_join_payload(self):
        self.host_ip = platform_utils.get_host_ip()
        port = int(getattr(self, "host_port", PORT) or PORT)

        return build_join_payload(
            self.host_ip,
            port,
            self.hotspot_ssid,
            self.hotspot_password,
        )

    def _open_overlay(self, panel):
        if self._overlay_root is not None:
            self._close_overlay()

        container = FloatLayout()

        for child in reversed(list(self.root.children)):
            self.root.remove_widget(child)
            container.add_widget(child)

        container.add_widget(Scrim())

        container.add_widget(self._centered_scroll(panel, margin=dp(10)))

        self._overlay_root = container
        self.root.add_widget(container)

    def _close_overlay(self, instance=None, rebuild=True):
        if self._overlay_root is None:
            return

        self._overlay_root = None

        if self._qr_refresh_event is not None:
            self._qr_refresh_event.cancel()
            self._qr_refresh_event = None

        if not rebuild:
            return

        builder = getattr(self, "_screen_builder", None)

        self.root.clear_widgets()
        self.root.add_widget(
            builder() if builder else self.create_menu()
        )

    def show_qr_modal(self, instance=None):
        if not self.network_server:
            return

        compact = self._is_compact()
        qr_side = dp(190) if compact else dp(220)

        panel = Panel(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(356)),
            height=qr_side + dp(396),
            padding=[dp(22), dp(18), dp(22), dp(18)],
            spacing=dp(7),
        )

        panel.add_widget(
            self._label(
                "HOSTING",
                dp(10),
                ACCENT,
                bold=True,
                halign="center",
                height=dp(14),
            )
        )
        panel.add_widget(
            self._label(
                "SCAN TO JOIN",
                dp(22) if compact else dp(24),
                TEXT,
                bold=True,
                halign="center",
                height=dp(32),
            )
        )

        panel.add_widget(
            self._label(
                "Fallback only: use this when the lobby list is empty.",
                dp(9) if compact else dp(10),
                MUTED,
                halign="center",
                height=dp(26),
            )
        )

        qr_holder = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
            height=qr_side,
        )
        self.qr_widget = QRCodeWidget(size=(qr_side, qr_side))
        qr_holder.add_widget(self.qr_widget)
        panel.add_widget(qr_holder)

        self.qr_hint_label = self._label(
            "",
            dp(10),
            MUTED,
            halign="center",
            height=dp(30),
        )
        self.qr_hint_label.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )
        panel.add_widget(self.qr_hint_label)

        panel.add_widget(
            self._label(
                "OPTIONAL HOTSPOT DETAILS",
                dp(9),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        self.qr_ssid_input = FlatInput(
            text=self.hotspot_ssid or self.host_ssid,
            hint_text="Hotspot name (optional)",
            height=dp(40),
        )
        self.qr_password_input = FlatInput(
            text=self.hotspot_password,
            hint_text="Hotspot password (optional)",
            height=dp(40),
        )

        for field in (self.qr_ssid_input, self.qr_password_input):
            field.bind(on_text=self._schedule_qr_refresh)
            panel.add_widget(field)

        button_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(42),
            spacing=dp(8),
        )

        refresh_button = FlatButton(
            text="UPDATE CODE",
            variant="primary",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        refresh_button.bind(on_press=self._refresh_qr_preview)

        close_button = FlatButton(
            text="CLOSE",
            variant="ghost",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        close_button.bind(on_press=self._close_overlay)

        button_row.add_widget(refresh_button)
        button_row.add_widget(close_button)
        panel.add_widget(button_row)

        if platform == "android":
            settings_row = BoxLayout(
                orientation="horizontal",
                size_hint=(1, None),
                height=dp(34),
                spacing=dp(8),
            )

            wifi_button = FlatButton(
                text="WI-FI SETTINGS",
                variant="ghost",
                font_size=dp(10),
                size_hint=(1, 1),
            )
            wifi_button.bind(
                on_press=lambda instance: platform_utils.open_wifi_settings()
            )

            hotspot_button = FlatButton(
                text="HOTSPOT",
                variant="ghost",
                font_size=dp(10),
                size_hint=(1, 1),
            )
            hotspot_button.bind(
                on_press=lambda instance: (
                    platform_utils.open_hotspot_settings()
                )
            )

            settings_row.add_widget(wifi_button)
            settings_row.add_widget(hotspot_button)
            panel.add_widget(settings_row)

        self._open_overlay(panel)
        self._refresh_qr_preview()

    def _schedule_qr_refresh(self, *args):
        if self._qr_refresh_event is not None:
            self._qr_refresh_event.cancel()

        self._qr_refresh_event = Clock.schedule_once(
            self._refresh_qr_preview, 0.35
        )

    def _refresh_qr_preview(self, *args):
        self._qr_refresh_event = None

        if not hasattr(self, "qr_widget"):
            return

        self.hotspot_ssid = self.qr_ssid_input.text.strip()
        self.hotspot_password = self.qr_password_input.text.strip()

        payload = self._current_join_payload()
        self.qr_widget.data = payload

        port = int(getattr(self, "host_port", PORT) or PORT)
        ip = getattr(self, "host_ip", "127.0.0.1")

        if ip == "127.0.0.1":
            hint = (
                "No Wi-Fi address found. Turn on the hotspot or join "
                "a Wi-Fi network first."
            )
        elif platform == "android":
            hint = (
                "Every phone must be on this Wi-Fi/hotspot. "
                "Keep this screen open while they scan."
            )
        else:
            hint = (
                f"Desktop test address: {ip}:{port}"
            )

        self.qr_hint_label.text = hint

    # HOST ROLE TEXT
    def get_host_role_text(self):

        return (
            f"HOST ROLE: "
            f"{self.host_team.upper()} "
            f"{self.host_role.upper()}"
        )
    # HOST: PICK OWN TEAM AND ROLE
    def choose_host_assignment(self, team, role):

        if not self.network_server:
            return

        success = (
            self.network_server.server.set_host_assignment(
                team,
                role
            )
        )

        if not success:

            self.host_status_label.text = (
                f"{team.upper()} already has a spymaster."
            )

            return

        self.host_team = team
        self.host_role = role

        self._refresh_role_buttons(
            self.host_role_buttons,
            team,
            role,
        )

        if hasattr(self, "host_role_label"):
            self.host_role_label.text = self.get_host_role_text()

        if hasattr(self, "host_status_label"):
            self.host_status_label.text = (
                f"You are now {team.upper()} {role.upper()}."
            )

        self.network_server.send_lobby_update()
    # UPDATE HOST LOBBY
    def update_host_lobby(self, dt):

        if not self.network_server:
            return False

        lobby_changed = False

        for event in self.network_server.get_events():

            event_type = event.get("type")

            # -----------------------------------------------------
            # PLAYER JOINED
            # -----------------------------------------------------

            if event_type == "player_joined":

                self.host_status_label.text = (
                    f"{event.get('name', 'Player')} joined successfully!"
                )

                lobby_changed = True

            # -----------------------------------------------------
            # PLAYER LEFT
            # -----------------------------------------------------

            elif event_type == "player_left":

                self.host_status_label.text = (
                    f"{event.get('name', 'Player')} disconnected."
                )

                lobby_changed = True

            # -----------------------------------------------------
            # CLIENT SENT A LOBBY MESSAGE
            # -----------------------------------------------------

            elif event_type == "client_message":

                message = event.get("data", {})

                if not isinstance(message, dict):
                    continue

                # Only team/role choices are valid in the lobby
                if message.get("type") != "choose_team":
                    continue

                player_key = event.get("player_key")
                player_name = event.get("name", "Player")

                requested_team = message.get("team")
                requested_role = message.get(
                    "role",
                    "operative"
                )

                success = (
                    self.network_server.server.set_player_assignment(
                        player_key,
                        requested_team,
                        requested_role
                    )
                )

                if success:

                    self.host_status_label.text = (
                        f"{player_name} joined the "
                        f"{str(requested_team).upper()} team "
                        f"as {str(requested_role).upper()}."
                    )

                    lobby_changed = True

                else:

                    self.network_server.send_to_player(
                        player_key,
                        {
                            "type": "selection_rejected",
                            "name": player_name,
                            "player_key": player_key,
                            "message": (
                                f"{str(requested_team).upper()} "
                                f"already has a spymaster "
                                f"(or that role is invalid)."
                            )
                        }
                    )

        if lobby_changed:
            self.network_server.send_lobby_update()

        lobby = self.network_server.server.get_lobby()

        clients = [
            player
            for player in lobby
            if not player.get("is_host")
        ]

        total_players = len(clients) + 1  # +1 for the host

        # ---------------------------------------------------------
        # UPDATE HOST LOBBY DISPLAY
        # ---------------------------------------------------------

        lines = [
            f"[color=#{'8B96A8'}]PLAYERS · "
            f"{total_players}/{MIN_PLAYERS} READY[/color]"
        ]

        for player in lobby:

            name = safe_markup(player.get("name", "Player"))
            team = player.get("team")

            dot = TEAM_HEX.get(team, "5C6675")
            role_text = str(
                player.get("role", "operative")
            ).upper()

            tag_colour = TEAM_HEX.get(team, "8B96A8")

            line = (
                f"[color=#{dot}]•[/color]  [b]{name}[/b]"
            )

            if player.get("is_host"):
                line += (
                    "   [color=#2DD4BF][size=10]HOST[/size][/color]"
                )

            if team:
                line += (
                    f"   [color=#{tag_colour}][size=10]"
                    f"{str(team).upper()} {role_text}[/size][/color]"
                )
            else:
                line += (
                    "   [color=#5C6675][size=10]NO TEAM[/size][/color]"
                )

            lines.append(line)

        # ---------------------------------------------------------
        # ROLE LAYOUT HINTS (do not block starting)
        # ---------------------------------------------------------

        hints = []

        for team in ("red", "blue"):

            members = [
                player
                for player in lobby
                if player.get("team") == team
            ]

            if not members:
                continue

            if not any(
                player.get("role") == "spymaster"
                for player in members
            ):
                hints.append(
                    f"{team.upper()} has no spymaster."
                )

            if not any(
                player.get("role") == "operative"
                for player in members
            ):
                hints.append(
                    f"{team.upper()} has no operative."
                )

        if hints:
            lines.append("")
            for hint in hints:
                lines.append(
                    f"[color=#8B96A8]{safe_markup(hint)}[/color]"
                )

        self.host_players_label.text = "\n".join(lines)

        # ---------------------------------------------------------
        # CAN THE HOST START?
        # ---------------------------------------------------------

        players_ready = (
            total_players >= MIN_PLAYERS
            and all(
                player.get("team") in ("red", "blue")
                for player in clients
            )
        )

        if self.start_match_button:

            self.start_match_button.disabled = (
                not players_ready
            )

            if players_ready:

                self.start_match_button.text = "START GAME"

            else:

                self.start_match_button.text = (
                    f"START GAME "
                    f"({total_players}/{MIN_PLAYERS} PLAYERS)"
                )

        return True
    # OPEN JOIN SCREEN
    def show_join_screen(self, instance):

        self.cleanup_network()
        self._manual_join_open = False
        self.join_lobbies = []

        self.show_screen(self.create_join_screen, "join")

    def _return_to_join(self, notice):
        """Bounce back to the lobby list after losing the host."""

        self._join_notice = notice
        self.show_join_screen(None)

    # JOIN SCREEN
    def create_join_screen(self):

        compact = self._is_compact()
        android = platform == "android"

        root = ScreenRoot()

        # The desktop fallback adds the manual address block below.
        base_height = (
            dp(556 if android else 690)
            if compact
            else dp(596 if android else 726)
        )

        card = Panel(
            orientation="vertical",
            width=self._clamp_width(dp(430)),
            height=base_height,
            size_hint=(None, None),
            padding=[
                dp(22) if compact else dp(28),
                dp(18) if compact else dp(26),
                dp(22) if compact else dp(28),
                dp(18) if compact else dp(26),
            ],
            spacing=dp(8) if compact else dp(10),
        )

        self.join_card = card
        self._join_base_height = base_height

        eyebrow = self._label(
            "LAN MATCH",
            dp(10) if compact else dp(11),
            ACCENT,
            bold=True,
            height=dp(16),
        )

        title = self._label(
            "JOIN GAME",
            dp(26) if compact else dp(30),
            TEXT,
            bold=True,
            height=dp(36) if compact else dp(40),
        )

        subtitle = self._label(
            "Pick a host on this network.\n"
            "Same Wi-Fi or hotspot required.",
            dp(11) if compact else dp(12),
            MUTED,
            height=dp(36),
        )
        subtitle.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )

        card.add_widget(eyebrow)
        card.add_widget(title)
        card.add_widget(subtitle)
        card.add_widget(
            Widget(
                size_hint_y=None,
                height=dp(2) if compact else dp(4),
            )
        )

        card.add_widget(
            self._label(
                "PLAYER NAME",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        self.join_name_input = FlatInput(
            hint_text="Enter your name",
            height=dp(42) if compact else dp(46),
        )
        card.add_widget(self.join_name_input)

        card.add_widget(
            self._label(
                "LOBBIES ON THIS NETWORK",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        # Each discovered host becomes one row inside this scroll view.
        self.lobby_list_box = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            spacing=dp(6),
        )
        self.lobby_list_box.bind(
            minimum_height=self.lobby_list_box.setter("height")
        )

        self.lobby_scroll = ScrollView(
            do_scroll_x=False,
            bar_width=dp(3),
            size_hint=(1, None),
            height=dp(140) if compact else dp(156),
        )
        self.lobby_scroll.add_widget(self.lobby_list_box)
        card.add_widget(self.lobby_scroll)

        self._render_lobbies(self.join_lobbies)

        action_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(42) if compact else dp(46),
            spacing=dp(8),
        )

        refresh_button = FlatButton(
            text="SEARCH AGAIN",
            variant="default",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, 1),
        )
        refresh_button.bind(on_press=self.refresh_lobbies_now)

        scan_button = FlatButton(
            text="SCAN QR",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, 1),
        )
        scan_button.bind(on_press=self.scan_to_join)

        action_row.add_widget(refresh_button)
        action_row.add_widget(scan_button)
        card.add_widget(action_row)

        self.join_status_label = self._label(
            self._join_notice
            or "Searching for hosts on this network...",
            dp(10) if compact else dp(11),
            MUTED if self._join_notice is None else RED,
            halign="center",
            height=dp(34),
        )
        self.join_status_label.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )

        # Discovery and the connect flow share one status line.
        self.lobby_status_label = self.join_status_label

        card.add_widget(self.join_status_label)

        if not android:

            # Desktop regression path: two windows on one PC can always
            # be linked explicitly, even when discovery is filtered.
            card.add_widget(
                self._label(
                    "HOST ADDRESS (DESKTOP TEST)",
                    dp(10),
                    MUTED,
                    bold=True,
                    height=dp(14),
                )
            )

            self.join_ip_input = FlatInput(
                text="127.0.0.1:5000",
                hint_text="192.168.1.5:5000",
                height=dp(42) if compact else dp(46),
            )
            card.add_widget(self.join_ip_input)

            connect_button = FlatButton(
                text="CONNECT",
                variant="default",
                font_size=dp(12),
                size_hint=(1, None),
                height=dp(42),
            )
            connect_button.bind(on_press=self.connect_to_host)
            card.add_widget(connect_button)

        else:

            self.join_ip_input = None

        back_button = FlatButton(
            text="BACK TO MENU",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, None),
            height=dp(40),
        )
        back_button.bind(on_press=self.return_to_menu)
        card.add_widget(back_button)

        root.add_widget(self._centered_scroll(card))

        # Start (or resume) discovery. This also runs after a rotation
        # rebuilds the screen.
        self.start_lobby_scan()

        return root

    # -----------------------------------------------------------------
    # LAN DISCOVERY (CLIENT SIDE)
    # -----------------------------------------------------------------

    def start_lobby_scan(self):

        if self._screen_name != "join":
            return

        self.stop_lobby_scan()

        self.lobby_scan_event = Clock.schedule_interval(
            self.refresh_lobbies, 3.0
        )

        Clock.schedule_once(self.refresh_lobbies, 0)

    def stop_lobby_scan(self):

        if self.lobby_scan_event is not None:
            self.lobby_scan_event.cancel()
            self.lobby_scan_event = None

    def refresh_lobbies_now(self, instance=None):

        Clock.schedule_once(self.refresh_lobbies, 0)

    def refresh_lobbies(self, dt=0):

        if self._screen_name != "join":
            return False

        # Never stack scans: one probe round at a time.
        if (
            self.lobby_scan_thread is not None
            and self.lobby_scan_thread.is_alive()
        ):
            return True

        if (
            self.lobby_status_label is not None
            and self.network_client is None
            and not self.join_lobbies
            and self._join_notice is None
        ):
            self.lobby_status_label.text = (
                "Searching for hosts on this network..."
            )

        thread = threading.Thread(
            target=self._lobby_scan_worker,
            daemon=True
        )
        self.lobby_scan_thread = thread
        thread.start()

        return True

    def _lobby_scan_worker(self):

        try:
            hosts = discover_hosts(timeout=1.6)

        except Exception:
            hosts = []

        Clock.schedule_once(
            lambda dt: self._apply_lobby_results(hosts),
            0,
        )

    def _apply_lobby_results(self, hosts):

        if self._screen_name != "join":
            return

        self.join_lobbies = list(hosts or [])
        self._render_lobbies(self.join_lobbies)

        if self.lobby_status_label is None:
            return

        if self.network_client is not None:
            return

        self._join_notice = None

        count = len(self.join_lobbies)

        if count == 0:
            self.lobby_status_label.text = (
                "No host yet. Both phones must be on the same "
                "Wi-Fi or hotspot."
            )
        elif count == 1:
            self.lobby_status_label.text = "1 host found. Tap JOIN."
        else:
            self.lobby_status_label.text = (
                f"{count} hosts found. Tap JOIN."
            )

    def _render_lobbies(self, hosts):

        box = self.lobby_list_box

        if box is None:
            return

        compact = self._is_compact()

        box.clear_widgets()

        if not hosts:

            box.add_widget(
                self._label(
                    "No host found yet.\n"
                    "Both phones must share the same Wi-Fi or hotspot.",
                    dp(10) if compact else dp(11),
                    MUTED,
                    halign="center",
                    height=dp(52),
                )
            )

            return

        for host in hosts:
            box.add_widget(self._lobby_row(host))

    def _lobby_row(self, host):

        compact = self._is_compact()

        row = Panel(
            orientation="horizontal",
            fill=SURFACE_HI,
            border=BORDER_SOFT,
            radius=RADIUS_SM,
            padding=[dp(10), dp(6), dp(8), dp(6)],
            spacing=dp(8),
            size_hint=(1, None),
            height=dp(50) if compact else dp(54),
        )

        labels = BoxLayout(orientation="vertical", spacing=0)

        labels.add_widget(
            self._label(
                host.get("ssid") or "Codenames host",
                dp(12) if compact else dp(13),
                TEXT,
                bold=True,
                height=dp(18),
                shorten=True,
            )
        )

        players = int(host.get("players") or 1)
        kind = str(host.get("kind") or "WI-FI")

        labels.add_widget(
            self._label(
                f"{players} player{'s' if players != 1 else ''} · {kind}",
                dp(9) if compact else dp(10),
                MUTED,
                height=dp(14),
            )
        )

        join_button = FlatButton(
            text="JOIN",
            variant="primary",
            font_size=dp(11) if compact else dp(12),
            size_hint=(None, 1),
            width=dp(64) if compact else dp(74),
        )
        join_button.bind(
            on_press=lambda instance, entry=dict(host): (
                self.join_discovered_host(entry)
            )
        )

        row.add_widget(labels)
        row.add_widget(join_button)

        return row

    def join_discovered_host(self, host):

        if not host:
            return

        name = (
            self.join_name_input.text.strip() or "Player"
        )[:20]

        port = host.get("port")

        try:
            port = int(port)

        except (TypeError, ValueError):
            port = PORT

        self.client_name = name
        self.scanned_ssid = str(host.get("ssid") or "")

        self._connect_to_host(host.get("host"), port, name)

    # CONNECT: DISCOVERY, QR OR MANUAL
    def scan_to_join(self, instance=None):
        if not is_scan_supported():
            self.join_status_label.text = (
                "QR scanning is available in the Android app."
            )
            return

        self.join_status_label.text = "Opening the camera..."

        started = scan_qr_code(
            self._handle_scan_result,
            self._handle_scan_error,
        )

        if not started:
            self.join_status_label.text = (
                "Could not open the camera. Pick a lobby above, or "
                "allow the camera in Settings."
            )

    def _handle_scan_error(self, reason):
        label = getattr(self, "join_status_label", None)

        if reason == "permission":

            if label is not None:
                label.text = (
                    "Camera permission is blocked. Allow it in "
                    "Settings, then scan again."
                )

            self.show_camera_help()

            return

        if label is not None:
            label.text = (
                "The QR scanner could not start on this phone."
            )

    def show_camera_help(self):
        """Explain how to re-enable the camera after a denial."""

        panel = Panel(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(330)),
            height=dp(268),
            padding=[dp(20), dp(18), dp(20), dp(18)],
            spacing=dp(10),
        )

        panel.add_widget(
            self._label(
                "CAMERA BLOCKED",
                dp(10),
                RED,
                bold=True,
                halign="center",
                height=dp(14),
            )
        )
        panel.add_widget(
            self._label(
                "The QR scanner needs the camera permission.",
                dp(12),
                TEXT,
                halign="center",
                height=dp(40),
            )
        )
        panel.add_widget(
            self._label(
                "Settings > Apps > Codenames > Permissions > Camera",
                dp(10),
                MUTED,
                halign="center",
                height=dp(48),
            )
        )

        row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(44),
            spacing=dp(8),
        )

        settings_button = FlatButton(
            text="OPEN SETTINGS",
            variant="primary",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        settings_button.bind(
            on_press=lambda instance: (
                platform_utils.open_app_settings()
            )
        )

        close_button = FlatButton(
            text="CLOSE",
            variant="ghost",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        close_button.bind(on_press=self._close_overlay)

        row.add_widget(settings_button)
        row.add_widget(close_button)
        panel.add_widget(row)

        self._open_overlay(panel)

    def _handle_scan_result(self, data):
        if not data:
            self.join_status_label.text = "Scan cancelled."
            return

        payload = parse_join_payload(data)

        if payload is None:
            self.join_status_label.text = (
                "That QR code is not a Codenames invite."
            )
            return

        self._last_scan_payload = payload
        self.scanned_ssid = payload.get("ssid", "")

        name = (
            self.join_name_input.text.strip() or "Player"
        )[:20]
        self.client_name = name

        self.join_status_label.text = "QR read. Connecting..."

        self._connect_to_host(
            payload["host"],
            payload["port"],
            name,
        )

    def connect_to_host(self, instance=None):
        """Desktop-only manual address fallback."""

        if self.join_ip_input is None:
            return

        raw = self.join_ip_input.text.strip()

        player_name = (
            self.join_name_input.text.strip() or "Player"
        )[:20]
        self.client_name = player_name

        if not raw:
            self.join_status_label.text = (
                "Please enter the host address."
            )
            return

        payload = parse_join_payload(raw)

        if payload is None:
            self.join_status_label.text = (
                "Use an address like 192.168.1.5:5000."
            )
            return

        self.scanned_ssid = payload.get("ssid", "")

        self._connect_to_host(
            payload["host"],
            payload["port"],
            player_name,
        )

    def _drop_client(self):
        """Close the client socket and stop polling it (safe to repeat)."""

        poll = getattr(self, "client_poll_event", None)

        if poll is not None:
            poll.cancel()
            self.client_poll_event = None

        client = self.network_client

        if client is None:
            return

        self.network_client = None

        try:
            client.close()

        except Exception:
            pass

    def _connect_to_host(self, host, port, player_name):
        self.stop_lobby_scan()
        self._drop_client()

        self.network_client = GameClient()

        self.join_status_label.text = "Connecting..."

        self.network_client.connect(
            host,
            port,
            player_name,
        )

        self.client_poll_event = Clock.schedule_interval(
            self.update_client_status, 0.2
        )
    # CLIENT: UPDATE SCORES AND TURN
    def update_client_display(self):

        if hasattr(self, "client_red_score_label"):
            self.client_red_score_label.set_remaining(
                self.client_remaining["red"]
            )
            self.client_red_score_label.set_active(
                self.client_current_team == "red"
            )

        if hasattr(self, "client_blue_score_label"):
            self.client_blue_score_label.set_remaining(
                self.client_remaining["blue"]
            )
            self.client_blue_score_label.set_active(
                self.client_current_team == "blue"
            )

        if hasattr(self, "client_turn_label"):
            self.client_turn_label.set_team(
                self.client_current_team,
                words_left=self.client_remaining.get(
                    self.client_current_team
                ),
                note=(
                    "Reveal a word, then pass when you are done."
                    if self.client_role == "operative"
                    else "You see the key — operatives do not."
                ),
            )

        self.update_spymaster_display(
            self.client_remaining,
            self.client_current_team
        )
    # SPYMASTER: UPDATE SCORES AND TURN
    def update_spymaster_display(self, remaining, current_team):

        if hasattr(self, "spymaster_red_score_label"):
            self.spymaster_red_score_label.set_remaining(
                remaining["red"]
            )
            self.spymaster_red_score_label.set_active(
                current_team == "red"
            )

        if hasattr(self, "spymaster_blue_score_label"):
            self.spymaster_blue_score_label.set_remaining(
                remaining["blue"]
            )
            self.spymaster_blue_score_label.set_active(
                current_team == "blue"
            )

        if hasattr(self, "spymaster_turn_label"):
            self.spymaster_turn_label.set_team(
                current_team,
                words_left=remaining.get(current_team),
                note="You see the key — operatives do not.",
            )
    # SPYMASTER: MARK A CARD AS REVEALED
    def mark_spymaster_reveal(self, index):

        cards = getattr(self, "spymaster_cards", [])

        if not isinstance(index, int):
            return

        if not 0 <= index < len(cards):
            return

        card = cards[index]

        if getattr(card, "revealed", False):
            return

        card.mark_revealed()
    # CLIENT: APPLY REVEALED CARD 
    def apply_client_reveal(self, event):

        index = event.get("index")
        role = event.get("role")

        if not isinstance(index, int):
            return

        if role not in ("red", "blue", "neutral", "assassin"):
            return

        first_time = index not in self.client_revealed
        previous_team = self.client_current_team

        self.client_revealed.add(index)
        self.client_revealed_roles[index] = role

        # The board may have been replaced by the spymaster key,
        # so update it only when it exists.
        if 0 <= index < len(getattr(self, "client_cards", [])):

            card = self.client_cards[index]

            if not getattr(card, "revealed", False):
                card.reveal(role)

        # The guess is logged even for spymasters (no board widgets).
        if first_time:

            word = ""

            if 0 <= index < len(getattr(self, "client_words", [])):
                word = self.client_words[index]

            guesser = event.get("guesser") or "Player"

            self.add_log(
                f"{safe_markup(guesser)} guessed "
                f"[b]{safe_markup(word)}[/b] · "
                f"[color=#{TEAM_HEX.get(role, '8B96A8')}]"
                f"{role.upper()}[/color]",
                role,
            )

        self.mark_spymaster_reveal(index)

        # The final broadcast includes the full key so every screen can
        # reveal the board once the match is over.
        roles = event.get("roles")

        if isinstance(roles, list) and len(roles) == 25:
            self.client_key_roles = list(roles)

        self.client_remaining = event.get(
            "remaining",
            self.client_remaining
        )

        self.client_current_team = event.get(
            "current_team",
            self.client_current_team
        )

        if self.client_current_team != previous_team:
            self.add_turn_log(self.client_current_team)

        self.client_selection_pending = False

        self.client_game_over = event.get(
            "game_over", False
        )

        self.update_client_display()
        self.update_client_controls()

        if self.client_game_over:

            self.end_game(
                event.get("message", "GAME OVER")
            )
    # HOST PERMISSION FUNCTION
    def update_host_controls(self):

        if not hasattr(self, "cards"):
            return

        can_play = (
            not self.game_over
            and self.current_team == self.host_team
            and self.host_role == "operative"
        )

        for card in self.cards:

            card.disabled = (
                card.revealed or not can_play
            )

        if hasattr(self, "host_end_turn_button"):

            self.host_end_turn_button.disabled = not can_play
    # CLIENT GAME BOARD
    def create_client_game_board(self):

        compact = self._is_compact()

        self.client_cards = []

        if not hasattr(self, "client_revealed"):
            self.client_revealed = set()

        # Operatives play the cards, spymasters only watch.
        can_play = self.client_role == "operative"

        root = ScreenRoot()

        # TOP BAR
        top_bar, self.client_red_score_label, self.client_blue_score_label = (
            self._build_top_bar(
                "CODENAMES",
                self.client_remaining,
                "LEAVE",
                self.return_to_menu,
                badge_text=self.client_team.upper(),
                badge_team=self.client_team,
            )
        )
        root.add_widget(top_bar)

        # CURRENT TURN
        self.client_turn_label = TurnBar(compact=compact)
        root.add_widget(self.client_turn_label)

        # BOARD
        board_panel = Panel(
            orientation="vertical",
            padding=[dp(8) if compact else dp(12)] * 4,
            spacing=dp(6) if compact else dp(8),
            size_hint_x=1,
        )

        board = GridLayout(
            cols=5,
            rows=5,
            spacing=dp(5) if compact else dp(8),
            padding=dp(2),
        )

        for index, word in enumerate(self.client_words):

            card = BoardCard(word)
            card.card_index = index

            card.disabled = (
                not can_play
                or self.client_team != self.client_current_team
            )

            card.bind(
                on_press=self.client_card_pressed
            )

            self.client_cards.append(card)
            board.add_widget(card)

        # Re-apply already revealed words after an orientation rebuild.
        known_roles = dict(
            getattr(self, "client_revealed_roles", {}) or {}
        )

        if len(getattr(self, "client_key_roles", [])) == 25:
            for index in self.client_revealed:
                if 0 <= index < 25:
                    known_roles.setdefault(
                        index, self.client_key_roles[index]
                    )

        for index in sorted(self.client_revealed):
            if 0 <= index < len(self.client_cards):
                role = known_roles.get(index)

                if role in ("red", "blue", "neutral", "assassin"):
                    self.client_cards[index].reveal(
                        role, animate=False
                    )

        board_panel.add_widget(board)

        # SIDEBAR
        self.client_end_turn_button = FlatButton(
            text="END TURN",
            variant="primary",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(44) if compact else dp(48),
        )
        self.client_end_turn_button.bind(
            on_press=self.client_end_turn
        )
        self.client_end_turn_button.disabled = not can_play

        self.client_info_label = self._label(
            "Connected to host",
            dp(11),
            MUTED,
            halign="left",
            height=dp(34),
        )

        sidebar = self._game_sidebar(
            action=self.client_end_turn_button,
            info=self.client_info_label,
        )

        root.add_widget(
            self._game_content(
                board_panel,
                sidebar,
            )
        )

        self.update_client_display()
        self.update_client_controls()

        return root
    # CLIENT: SELECT CARD
    def client_card_pressed(self, card):

        if self.client_game_over:
            return

        if self.client_role != "operative":
            return

        if self.client_team != self.client_current_team:
            return

        if self.client_selection_pending:
            return

        if card.card_index in self.client_revealed:
            return

        if self.network_client is None:
            self.client_info_label.text = "No connection to the host."
            return

        success = self.network_client.send_message({
            "type": "reveal_card",
            "index": card.card_index
        })

        if success:

            self.client_selection_pending = True
            self.update_client_controls()

        else:

            self.client_info_label.text = (
                "Could not contact host."
            )
    # CLIENT: END TURN
    def client_end_turn(self, instance):

        if self.client_game_over:
            return

        if self.client_role != "operative":
            return

        if self.client_team != self.client_current_team:
            return

        if self.client_selection_pending:
            return

        if self.network_client is None:
            self.client_info_label.text = "No connection to the host."
            return

        success = self.network_client.send_message({
            "type": "end_turn"
        })

        if success:

            self.client_selection_pending = True
            self.update_client_controls()
    # CLIENT WAITING SCREEN
    def create_client_waiting_screen(self):

        compact = self._is_compact()

        root = ScreenRoot()

        card = Panel(
            orientation="vertical",
            width=self._clamp_width(dp(460)),
            height=dp(430) if compact else dp(470),
            size_hint=(None, None),
            padding=[
                dp(22) if compact else dp(26),
                dp(18) if compact else dp(24),
                dp(22) if compact else dp(26),
                dp(18) if compact else dp(24),
            ],
            spacing=dp(8) if compact else dp(10),
        )

        card.add_widget(
            self._label(
                "LOBBY",
                dp(10) if compact else dp(11),
                ACCENT,
                bold=True,
                height=dp(16),
            )
        )
        card.add_widget(
            self._label(
                "WAITING FOR HOST",
                dp(22) if compact else dp(26),
                TEXT,
                bold=True,
                height=dp(32) if compact else dp(36),
            )
        )

        team = (self.client_team or "none").upper()
        role = self.client_role.upper()
        team_hex = TEAM_HEX.get(self.client_team, "8B96A8")

        self.client_role_label = Label(
            text=(
                "[size=10][color=#8B96A8]YOUR TEAM[/color][/size]\n"
                f"[color=#{team_hex}][b]{team}[/b][/color]\n"
                "[size=10][color=#8B96A8]YOUR ROLE[/color][/size]\n"
                f"[b]{role}[/b]"
            ),
            markup=True,
            font_size=dp(14) if compact else dp(16),
            color=TEXT,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=dp(82) if compact else dp(96),
        )

        card.add_widget(self.client_role_label)

        card.add_widget(
            self._label(
                "PLAYERS",
                dp(10),
                MUTED,
                bold=True,
                height=dp(16),
            )
        )

        self.client_lobby_label = ScrollLabel(
            font_size=dp(11) if compact else dp(12)
        )
        self.client_lobby_label.text = self._client_lobby_markup()
        card.add_widget(self.client_lobby_label)

        card.add_widget(
            self._label(
                "Waiting for the host to start the game...",
                dp(10) if compact else dp(11),
                MUTED,
                halign="center",
                height=dp(28),
            )
        )

        back_button = FlatButton(
            text="LEAVE GAME",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, None),
            height=dp(40),
        )
        back_button.bind(on_press=self.return_to_menu)
        card.add_widget(back_button)

        root.add_widget(self._centered_scroll(card))

        return root

    def _client_lobby_markup(self):
        """Markup player list shared by lobby-style screens."""

        lines = []

        for player in getattr(self, "client_lobby", []):

            name = safe_markup(player.get("name", "Player"))
            team = player.get("team")
            role = str(player.get("role", "operative")).upper()

            dot = TEAM_HEX.get(team, "5C6675")
            tag = TEAM_HEX.get(team, "8B96A8")

            line = f"[color=#{dot}]•[/color]  [b]{name}[/b]"

            if player.get("is_host"):
                line += (
                    "   [color=#2DD4BF][size=10]HOST[/size][/color]"
                )

            if team:
                line += (
                    f"   [color=#{tag}][size=10]"
                    f"{str(team).upper()} {role}[/size][/color]"
                )
            else:
                line += (
                    "   [color=#5C6675][size=10]NO TEAM[/size][/color]"
                )

            lines.append(line)

        return "\n".join(lines) if lines else (
            "[color=#5C6675]No players yet.[/color]"
        )
    # UPDATE CLIENT STATUS
    def update_client_status(self, dt):

        if not self.network_client:
            return False

        for event in self.network_client.get_events():

            event_type = event.get("type")

            # PLAYER SUCCESSFULLY JOINED
            if event_type == "welcome":

                self.client_name = event.get(
                    "name",
                    "Player"
                )

                self.client_key = event.get(
                    "player_key"
                )

                self.client_team = event.get(
                    "team",
                    None
                )

                self.client_role = event.get(
                    "role",
                    "operative"
                )

                self.client_lobby = event.get(
                    "lobby",
                    []
                )

                self.show_screen(
                    self.create_team_selection_screen,
                    "team_selection",
                )

                self.update_team_selection_lobby()

            # HOST CREATED A MATCH
            elif event_type == "match_ready":

                self.show_screen(
                    self.create_client_waiting_screen,
                    "client_waiting",
                )

            # HOST STARTED THE GAME
            elif event_type == "game_started":

                words = event.get("words", [])

                if len(words) != 25:

                    if hasattr(self, "join_status_label"):

                        self.join_status_label.text = (
                            "Invalid board received from host."
                        )

                    continue

                self.client_words = words

                self.client_current_team = (
                    event.get("current_team", "red")
                )

                self.client_remaining = event.get(
                    "remaining",
                    {"red": 9, "blue": 8}
                )

                self.client_revealed = set()
                self.client_revealed_roles = {}
                self.client_selection_pending = False
                self.client_game_over = False
                self._game_over_shown = False
                self.game_over_root = None
                self.reset_match_log()

                self.show_screen(
                    self.create_client_game_board,
                    "client_game",
                )

            # HOST SENT THE SECRET KEY TO SPYMASTERS
            elif event_type == "spy_key":

                # Operatives must never receive this message.
                if self.client_role != "spymaster":
                    continue

                self.receive_spy_key(event)

            # HOST REVEALED A CARD
            elif event_type == "card_revealed":

                self.apply_client_reveal(event)

            # HOST CHANGED THE TURN
            elif event_type == "turn_changed":

                previous_team = self.client_current_team

                self.client_current_team = event.get(
                    "current_team",
                    self.client_current_team
                )

                self.client_remaining = event.get(
                    "remaining",
                    self.client_remaining
                )

                self.client_selection_pending = False

                if self.client_current_team != previous_team:
                    self.add_turn_log(self.client_current_team)

                self.update_client_display()
                self.update_client_controls()
            # HOST REJECTED A CARD SELECTION
            elif event_type == "selection_rejected":

                # Only notify the player who made the rejected request
                rejected_key = event.get("player_key")

                if rejected_key is not None:

                    if rejected_key != getattr(
                        self,
                        "client_key",
                        None
                    ):
                        continue

                elif event.get("name") != getattr(
                    self,
                    "client_name",
                    None
                ):
                    continue

                self.client_selection_pending = False

                self.update_client_controls()

                message = event.get(
                    "message",
                    "Action rejected."
                )

                if hasattr(self, "client_info_label"):
                    self.client_info_label.text = message

                if hasattr(self, "team_selection_status"):
                    self.team_selection_status.text = message
            # CONNECTION ERROR
            elif event_type == "error":

                message = str(event.get("message", ""))

                if getattr(self, "scanned_ssid", ""):
                    message += (
                        f"\nConnect this phone to the "
                        f"'{self.scanned_ssid}' Wi-Fi/hotspot, "
                        f"then try again."
                    )

                if hasattr(self, "join_status_label"):

                    self.join_status_label.text = (
                        f"COULD NOT CONNECT:\n{message}"
                    )

                if hasattr(self, "client_info_label"):

                    self.client_info_label.text = (
                        f"Connection error: {message}"
                    )

                # The connection is dead: drop the client so the next
                # JOIN starts clean and discovery can report again.
                self.client_selection_pending = False
                self._drop_client()

                # Back on the join screen: keep looking so the player
                # can tap a different host.
                if self._screen_name == "join":
                    self.start_lobby_scan()
                elif self._screen_name not in ("menu", "game"):
                    self._return_to_join("Connection lost.")

            # DISCONNECTED
            elif event_type == "disconnected":

                self.client_selection_pending = False

                self._drop_client()

                if hasattr(self, "client_info_label"):

                    self.client_info_label.text = (
                        "Disconnected from host."
                    )

                elif (
                    hasattr(self, "join_status_label")
                    and self._join_notice is None
                ):

                    self.join_status_label.text = (
                        "Disconnected from host."
                    )

                if self._screen_name == "join":
                    self.start_lobby_scan()
                elif self._screen_name not in ("menu", "game"):
                    self._return_to_join("The host closed the lobby.")
            elif event_type == "lobby_update":

                self.client_lobby = event.get(
                    "lobby",
                    []
                )

                # Find our own player entry
                for player in self.client_lobby:

                    if player.get("key") == getattr(
                        self,
                        "client_key",
                        None
                    ):

                        self.client_team = player.get(
                            "team"
                        )

                        self.client_role = player.get(
                            "role",
                            "operative"
                        )

                        break

                self.update_team_selection_lobby()

                if hasattr(
                    self,
                    "team_selection_status"
                ):

                    if self.client_team:

                        self.team_selection_status.text = (
                            f"You joined the "
                            f"{self.client_team.upper()} team "
                            f"as "
                            f"{self.client_role.upper()}."
                        )

                    else:

                        self.team_selection_status.text = (
                            "No team selected."
                        )

        return True
    # CLIENT: RECEIVE THE SPYMASTER KEY
    def receive_spy_key(self, event):

        words = event.get("words", [])
        roles = event.get("roles", [])

        if len(words) != 25 or len(roles) != 25:
            return

        self.client_words = words
        self.client_key_roles = roles

        self.client_team = event.get(
            "team",
            self.client_team or "red"
        )

        self.client_current_team = event.get(
            "current_team",
            self.client_current_team
        )

        self.client_remaining = event.get(
            "remaining",
            self.client_remaining
        )

        self.show_screen(
            self._client_spymaster_builder,
            "client_spymaster",
        )
    # UPDATE CLIENT PERMISSION
    def update_client_controls(self):

        can_play = (
            not self.client_game_over
            and not self.client_selection_pending
            and self.client_team == self.client_current_team
            and self.client_role == "operative"
        )

        for card in getattr(self, "client_cards", []):

            card.disabled = (
                card.card_index in self.client_revealed
                or not can_play
            )

        if hasattr(self, "client_end_turn_button"):

            self.client_end_turn_button.disabled = not can_play

        if hasattr(self, "client_info_label"):

            if self.client_game_over:
                self.client_info_label.text = "GAME OVER"

            elif self.client_role != "operative":
                self.client_info_label.text = (
                    "Spymaster: watch the board and give clues."
                )

            elif self.client_selection_pending:
                self.client_info_label.text = "Waiting for host..."

            elif not can_play:
                self.client_info_label.text = (
                    f"Waiting for {self.client_current_team.upper()} team..."
                )

            else:
                self.client_info_label.text = "Your team's turn!"
    # CLEANUP NETWORK CONNECTIONS
    def cleanup_network(self):

        if self.host_poll_event:
            self.host_poll_event.cancel()
            self.host_poll_event = None

        if self.host_network_event:
            self.host_network_event.cancel()
            self.host_network_event = None

        self.stop_lobby_scan()

        if self.discovery:
            self.discovery.stop()
            self.discovery = None

        self._drop_client()

        if self.network_server:

            self.network_server.stop()
            self.network_server = None
    # APP CLOSE
    def on_stop(self):

        self.cleanup_network()
    # SPYMASTER KEY
    def create_spymaster_game_screen(
        self,
        words,
        roles,
        team,
        players=None,
        remaining=None,
        current_team=None,
        for_client=False
    ):

        if remaining is None:
            remaining = getattr(
                self,
                "remaining",
                {"red": 9, "blue": 8}
            )

        if current_team is None:
            current_team = getattr(
                self,
                "current_team",
                "red"
            )

        compact = self._is_compact()
        portrait = self._layout_mode() == "portrait"

        self.spymaster_cards = []

        root = ScreenRoot()

        # -------------------------------------------------
        # TOP BAR
        # -------------------------------------------------

        top_bar, self.spymaster_red_score_label, self.spymaster_blue_score_label = (
            self._build_top_bar(
                "SPYMASTER",
                remaining,
                "LEAVE" if for_client else "MENU",
                self.return_to_menu,
                badge_text=str(team or "").upper(),
                badge_team=team,
            )
        )
        root.add_widget(top_bar)

        # -------------------------------------------------
        # CURRENT TURN
        # -------------------------------------------------

        self.spymaster_turn_label = TurnBar(compact=compact)
        root.add_widget(self.spymaster_turn_label)

        # -------------------------------------------------
        # SECRET KEY BOARD
        # -------------------------------------------------

        board_panel = Panel(
            orientation="vertical",
            padding=[dp(8) if compact else dp(12)] * 4,
            spacing=dp(6) if compact else dp(8),
            size_hint_x=1,
        )

        board = GridLayout(
            cols=5,
            rows=5,
            spacing=dp(5) if compact else dp(7),
            padding=dp(2),
        )

        for index, (word, role) in enumerate(zip(words, roles)):

            card = KeyCard(word, role)
            card.card_index = index

            self.spymaster_cards.append(card)
            board.add_widget(card)

        # Restore the marks that were already guessed before a rebuild.
        revealed = (
            getattr(self, "client_revealed", set())
            if for_client
            else getattr(self, "revealed_cards", set())
        )

        for index in sorted(revealed or ()):
            if 0 <= index < len(self.spymaster_cards):
                self.spymaster_cards[index].mark_revealed()

        board_panel.add_widget(board)

        # -------------------------------------------------
        # SIDEBAR
        # -------------------------------------------------

        # On a phone in portrait the player list would squeeze the log
        # into uselessness, so it only appears when there is room.
        show_players = not (compact and portrait)

        players_panel = None

        if show_players:
            players_panel = Panel(
                orientation="vertical",
                padding=[
                    dp(10) if compact else dp(14),
                    dp(8) if compact else dp(12),
                    dp(10) if compact else dp(14),
                    dp(8) if compact else dp(12),
                ],
                spacing=dp(6),
                size_hint=(1, None),
                height=dp(118) if compact else dp(168),
            )

            players_panel.add_widget(
                self._label(
                    "PLAYERS",
                    dp(10),
                    MUTED,
                    bold=True,
                    height=dp(14),
                )
            )

            players_text = ""

            if players:

                for player in players:

                    name = safe_markup(player.get("name", "Player"))

                    if player.get("is_host"):
                        name += " · HOST"

                    player_team = player.get("team")
                    player_role = player.get("role", "operative")

                    if player_team:
                        colour = TEAM_HEX.get(player_team, "8B96A8")
                        players_text += (
                            f"[color=#{colour}]•[/color] "
                            f"[b]{name}[/b]\n"
                            f"   [size=10][color=#{colour}]"
                            f"{str(player_team).upper()} "
                            f"{str(player_role).upper()}[/color][/size]\n"
                        )
                    else:
                        players_text += (
                            f"[color=#5C6675]•[/color] "
                            f"[b]{name}[/b] · NO TEAM\n"
                        )

            self.spymaster_players_label = ScrollLabel(
                text=players_text or "[color=#5C6675]No players.[/color]",
                font_size=dp(11),
            )
            players_panel.add_widget(self.spymaster_players_label)

        note = self._label(
            "Cards cannot be selected. Give clues verbally.",
            dp(10) if compact else dp(11),
            MUTED,
            halign="center",
            shorten=True,
            max_lines=2,
            height=dp(28) if compact else dp(34),
        )

        sidebar = self._game_sidebar(
            note=note,
            after=() if players_panel is None else (players_panel,),
        )

        root.add_widget(self._game_content(board_panel, sidebar))

        self.update_spymaster_display(remaining, current_team)
        self.render_log()

        return root
    # RETURN
    def return_to_host_game(self, instance):

        self.show_screen(self.create_game_board, "host_game")
    # OPEN GAME BOARD
    def open_game_board(self, instance):

        # Broadcast the public board information
        if self.network_server:

            self.network_server.broadcast({
                "type": "game_started",
                "words": self.words,
                "current_team": self.current_team,
                "remaining": dict(self.remaining)
            })

        self.show_screen(self.create_game_board, "host_game")

        # Start listening for client actions
        if self.host_poll_event:

            self.host_poll_event.cancel()

        self.host_poll_event = Clock.schedule_interval(
            self.update_host_game_events,
            0.1
        )
    # CREATE GAME BOARD
    def create_game_board(self):

        compact = self._is_compact()

        self.cards = []

        root = ScreenRoot()

        # TOP BAR
        top_bar, self.red_score_label, self.blue_score_label = (
            self._build_top_bar(
                "CODENAMES",
                self.remaining,
                "MENU",
                self.return_to_menu,
                badge_text=self.host_team.upper(),
                badge_team=self.host_team,
            )
        )
        root.add_widget(top_bar)

        # CURRENT TURN
        self.turn_label = TurnBar(compact=compact)
        root.add_widget(self.turn_label)

        # GAME BOARD
        board_panel = Panel(
            orientation="vertical",
            padding=[dp(8) if compact else dp(12)] * 4,
            spacing=dp(6) if compact else dp(8),
            size_hint_x=1,
        )

        board = GridLayout(
            cols=5,
            rows=5,
            spacing=dp(5) if compact else dp(8),
            padding=dp(2),
        )

        for index, (word, role) in enumerate(
            zip(self.words, self.roles)
        ):

            card = BoardCard(word, role=role)
            card.card_index = index
            card.bind(on_press=self.card_pressed)

            self.cards.append(card)
            board.add_widget(card)

        # Re-apply guesses that happened before an orientation rebuild.
        for index in sorted(getattr(self, "revealed_cards", set())):
            if 0 <= index < len(self.cards) and 0 <= index < len(self.roles):
                self.cards[index].reveal(
                    self.roles[index], animate=False
                )

        board_panel.add_widget(board)

        # SIDEBAR
        self.host_end_turn_button = FlatButton(
            text="END TURN",
            variant="primary",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(44) if compact else dp(48),
        )
        self.host_end_turn_button.bind(
            on_press=self.end_turn
        )

        note = self._label(
            "Reveal a word, then pass when you are done.",
            dp(10) if compact else dp(11),
            MUTED,
            halign="center",
            shorten=True,
            max_lines=2,
            height=dp(28) if compact else dp(34),
        )

        sidebar = self._game_sidebar(
            action=self.host_end_turn_button,
            note=note,
        )

        root.add_widget(self._game_content(board_panel, sidebar))

        self.update_host_controls()
        self.update_score_display()
        self.update_turn_display()
        self.render_log()

        return root
    # GENERATE ROLES
    def generate_roles(self):

        if self.current_team == "red":

            roles = (
                ["red"] * 9
                + ["blue"] * 8
                + ["neutral"] * 7
                + ["assassin"]
            )

        else:

            roles = (
                ["red"] * 8
                + ["blue"] * 9
                + ["neutral"] * 7
                + ["assassin"]
            )

        random.shuffle(roles)

        return roles
    # HOST: PROCESS CARD SELECTION
    # CARD CLICK
    def card_pressed(self, card):

        if self.game_over:
            return

        if self.host_role != "operative":
            return

        if self.current_team != self.host_team:
            return

        self.reveal_card(card.card_index, guesser="HOST")
    # HOST: REVEAL CARD AND APPLY GAME RULES
    def reveal_card(self, index, guesser=None):

        if self.game_over:
            return False

        if not isinstance(index, int):
            return False

        # The authoritative deck lives in self.roles, not in the
        # widgets: the host may be a spymaster and have no board.
        if not 0 <= index < len(self.roles):
            return False

        if index in self.revealed_cards:
            return False

        self.revealed_cards.add(index)

        role = self.roles[index]
        word = (
            self.words[index]
            if 0 <= index < len(self.words)
            else ""
        )

        game_message = None

        # Log the guess before a possible turn change is logged.
        if guesser:
            self.add_log(
                f"{safe_markup(guesser)} guessed "
                f"[b]{safe_markup(word)}[/b] · "
                f"[color=#{TEAM_HEX.get(role, '8B96A8')}]"
                f"{role.upper()}[/color]",
                role,
            )

        # Only touch the host's board when it is actually on screen.
        if 0 <= index < len(getattr(self, "cards", [])):

            card = self.cards[index]

            if not card.revealed:
                card.reveal(role)

        # RED CARD
        if role == "red":

            self.remaining["red"] -= 1

            if self.remaining["red"] <= 0:

                game_message = "RED TEAM WINS!"

            elif self.current_team != "red":

                self.switch_team()

        # BLUE CARD
        elif role == "blue":

            self.remaining["blue"] -= 1

            if self.remaining["blue"] <= 0:

                game_message = "BLUE TEAM WINS!"

            elif self.current_team != "blue":

                self.switch_team()

        # NEUTRAL CARD
        elif role == "neutral":

            self.switch_team()

        # ASSASSIN
        elif role == "assassin":

            if self.current_team == "red":

                game_message = (
                    "ASSASSIN!\nBLUE TEAM WINS!"
                )

            else:

                game_message = (
                    "ASSASSIN!\nRED TEAM WINS!"
                )

        # UPDATE HOST DISPLAY
        self.mark_spymaster_reveal(index)
        self.update_score_display()
        self.update_turn_display()

        # TELL EVERY CLIENT WHAT HAPPENED
        # Only the revealed card's role is sent while playing.
        if self.network_server:

            payload = {
                "type": "card_revealed",
                "index": index,
                "role": role,
                "guesser": guesser or "Player",
                "remaining": dict(self.remaining),
                "current_team": self.current_team,
                "game_over": game_message is not None,
                "message": game_message
            }

            # At the end the full key is public so every screen can
            # reveal the remaining cards.
            if game_message:
                payload["roles"] = list(self.roles)

            self.network_server.broadcast(payload)

        # FINISH GAME AFTER BROADCASTING THE RESULT
        if game_message:

            self.end_game(game_message)

        return True
    # UPDATE HOST SCORES
    def update_score_display(self):

        if hasattr(self, "red_score_label"):

            self.red_score_label.set_remaining(
                self.remaining["red"]
            )
            self.red_score_label.set_active(
                self.current_team == "red"
            )

        if hasattr(self, "blue_score_label"):

            self.blue_score_label.set_remaining(
                self.remaining["blue"]
            )
            self.blue_score_label.set_active(
                self.current_team == "blue"
            )

        self.update_spymaster_display(
            self.remaining,
            self.current_team
        )
    # HOST: RECEIVE PLAYER ACTIONS
    def update_host_game_events(self, dt):

        if not self.network_server:
            return False

        for event in self.network_server.get_events():

            if event.get("type") != "client_message":
                continue

            message = event.get("data", {})

            if not isinstance(message, dict):
                continue

            message_type = message.get("type")

            sender_team = event.get("team")
            sender_role = event.get("role", "operative")
            sender_name = event.get("name", "Player")
            sender_key = event.get("player_key")

            # -------------------------------------------------
            # SEND A PRIVATE REJECTION
            # -------------------------------------------------

            def reject(reason):

                self.network_server.send_to_player(
                    sender_key,
                    {
                        "type": "selection_rejected",
                        "name": sender_name,
                        "player_key": sender_key,
                        "message": reason
                    }
                )

            # TEAMS AND ROLES ARE LOCKED ONCE THE GAME STARTS
            if message_type == "choose_team":

                reject("The game has already started.")
                continue

            if message_type not in ("reveal_card", "end_turn"):
                continue

            if self.game_over:

                reject("The game is over.")
                continue

            # Only operatives touch the cards
            if sender_role != "operative":

                reject(
                    "Spymasters cannot reveal cards. "
                    "Give clues to your operatives."
                )

                continue

            # Reject actions from the wrong team
            if sender_team != self.current_team:

                reject(
                    f"It's {self.current_team.upper()}'s turn!"
                )

                continue

            # CARD SELECTION
            if message_type == "reveal_card":

                index = message.get("index")

                if not self.reveal_card(index, guesser=sender_name):

                    reject("That card cannot be selected.")

            # END TURN
            else:

                self.advance_turn()

        return True
    # CLIENT LOBBY UPDATER
    def update_team_selection_lobby(self):

        if hasattr(self, "team_selection_lobby"):

            self.team_selection_lobby.text = (
                self._client_lobby_markup()
            )
    # TEAM SELECTION
    def create_team_selection_screen(self):

        compact = self._is_compact()

        root = ScreenRoot()

        # ---------------------------------------------------------
        # HEADER
        # ---------------------------------------------------------

        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(46) if compact else dp(58),
            spacing=dp(8),
        )

        titles = BoxLayout(orientation="vertical", spacing=0)
        titles.add_widget(
            self._label(
                "LOBBY",
                dp(10) if compact else dp(11),
                ACCENT,
                bold=True,
                height=dp(14) if compact else dp(16),
            )
        )
        titles.add_widget(
            self._label(
                "CHOOSE TEAM & ROLE" if compact else "CHOOSE YOUR TEAM & ROLE",
                dp(18) if compact else dp(24),
                TEXT,
                bold=True,
                height=dp(24) if compact else dp(32),
                shorten=True,
                max_lines=1,
            )
        )

        leave_button = FlatButton(
            text="LEAVE",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(None, 1),
            width=dp(82) if compact else dp(130),
        )
        leave_button.bind(on_press=self.return_to_menu)

        header.add_widget(titles)
        header.add_widget(leave_button)
        root.add_widget(header)

        # ---------------------------------------------------------
        # CONTENT
        # ---------------------------------------------------------

        left = Panel(
            orientation="vertical",
            padding=[
                dp(14) if compact else dp(22),
                dp(12) if compact else dp(20),
                dp(14) if compact else dp(22),
                dp(12) if compact else dp(20),
            ],
            spacing=dp(8) if compact else dp(10),
            size_hint_x=1.05,
        )

        right = Panel(
            orientation="vertical",
            padding=[
                dp(14) if compact else dp(20),
                dp(12) if compact else dp(20),
                dp(14) if compact else dp(20),
                dp(12) if compact else dp(20),
            ],
            spacing=dp(8),
            size_hint_x=0.95,
        )

        self.team_selection_label = Label(
            text=(
                f"[color=#8B96A8][size=11]PLAYER[/size][/color]  "
                f"[b]{safe_markup(self.client_name)}[/b]"
            ),
            markup=True,
            font_size=dp(13) if compact else dp(14),
            color=TEXT,
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=dp(20) if compact else dp(22),
        )
        self.team_selection_label.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )
        left.add_widget(self.team_selection_label)

        left.add_widget(
            self._label(
                "Each team has one spymaster (sees the key) and any "
                "number of operatives (pick cards).",
                dp(11) if compact else dp(12),
                MUTED,
                height=dp(32) if compact else dp(36),
            )
        )

        left.add_widget(
            self._label(
                "PICK A SLOT",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        assignment_grid = GridLayout(
            cols=2,
            rows=2,
            spacing=dp(6) if compact else dp(8),
            size_hint=(1, None),
            height=dp(104) if compact else dp(126),
        )

        self.team_buttons = {}
        assignments = (
            ("red", "spymaster"),
            ("red", "operative"),
            ("blue", "spymaster"),
            ("blue", "operative"),
        )

        for team, role in assignments:

            button = self._role_button(
                team,
                role,
                self.select_team,
                height=dp(49) if compact else dp(59),
            )

            self.team_buttons[(team, role)] = button
            assignment_grid.add_widget(button)

        self._refresh_role_buttons(
            self.team_buttons,
            self.client_team,
            self.client_role,
        )

        left.add_widget(assignment_grid)

        self.team_selection_status = self._label(
            (
                f"You joined the {self.client_team.upper()} team "
                f"as {self.client_role.upper()}."
                if self.client_team
                else "No team selected."
            ),
            dp(11) if compact else dp(12),
            MUTED,
            shorten=True,
            max_lines=2,
            height=dp(28) if compact else dp(30),
        )
        left.add_widget(self.team_selection_status)

        left.add_widget(Widget(size_hint_y=1))

        # ---------------------------------------------------------
        # PLAYER LIST
        # ---------------------------------------------------------

        right.add_widget(
            self._label(
                "PLAYERS IN LOBBY",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        self.team_selection_lobby = ScrollLabel(
            font_size=dp(11) if compact else dp(12)
        )
        self.team_selection_lobby.text = self._client_lobby_markup()
        right.add_widget(self.team_selection_lobby)

        right.add_widget(
            self._label(
                "Wait for the host to start the match.",
                dp(10) if compact else dp(11),
                MUTED,
                halign="center",
                height=dp(24),
            )
        )

        content = self._two_panel_content(
            left,
            right,
            left_height=dp(304) if compact else dp(340),
            right_height=dp(208) if compact else dp(250),
        )

        root.add_widget(content)

        return root
    # SELECTING TEAM AND ROLE
    def select_team(self, team, role="operative"):

        if not self.network_client:
            return

        success = self.network_client.send_message({
            "type": "choose_team",
            "team": team,
            "role": role
        })

        if success:

            if hasattr(self, "team_buttons"):
                self._refresh_role_buttons(
                    self.team_buttons,
                    team,
                    role,
                )

            if hasattr(self, "team_selection_status"):

                self.team_selection_status.text = (
                    f"Requesting {team.upper()} "
                    f"{role.upper()}..."
                )

        else:

            if hasattr(self, "team_selection_status"):

                self.team_selection_status.text = (
                    "Could not contact host."
                )
    # SWITCH TURN
    def switch_team(self):
        
        if self.current_team == "red":
            self.current_team = "blue"
        else:
            self.current_team = "red"

        self.add_turn_log(self.current_team)

        self.update_turn_display()
        self.update_host_controls()
    # END TURN MANUALLY
    def end_turn(self, instance):

        if self.game_over:
            return

        # Only operatives end turns, and only on their own turn
        if self.host_role != "operative":
            return

        if self.current_team != self.host_team:
            return

        self.advance_turn()
    # UPDATE TURN DISPLAY
    def update_turn_display(self):

        if hasattr(self, "turn_label"):

            self.turn_label.set_team(
                self.current_team,
                words_left=self.remaining.get(self.current_team),
                note=(
                    "Reveal a word, then pass when you are done."
                    if self.host_role == "operative"
                    else "You see the key — operatives do not."
                ),
            )

        self.update_spymaster_display(
            self.remaining,
            self.current_team
        )

    def get_turn_text(self):

        return f"{self.current_team.upper()} TEAM TURN"
    # REVEAL THE WHOLE BOARD AT THE END OF A MATCH
    def _reveal_full_board(self):

        roles = getattr(self, "roles", None)

        if not roles or len(roles) != 25:
            roles = getattr(self, "client_key_roles", [])

        if len(roles) != 25:
            return

        boards = list(getattr(self, "cards", [])) + list(
            getattr(self, "client_cards", [])
        )

        for card in boards:

            if getattr(card, "revealed", False):
                continue

            index = getattr(card, "card_index", None)

            if isinstance(index, int) and 0 <= index < 25:
                card.reveal(roles[index], animate=False)

        # Spymaster keys only need a "guessed" mark, not a colour change.
        for card in getattr(self, "spymaster_cards", []):
            index = getattr(card, "card_index", None)

            if isinstance(index, int) and 0 <= index < 25:
                card.mark_revealed()

    # END GAME
    def end_game(self, message):

        self.game_over = True

        if self.network_client:
            self.client_game_over = True

        self._reveal_full_board()

        # The client already has its own overlay from apply_client_reveal.
        if getattr(self, "_game_over_shown", False):
            return

        self._game_over_shown = True

        upper = str(message).upper()

        winner = "blue" if "BLUE" in upper else "red"
        winner_colour = TEAM_COLORS.get(winner, TEXT)
        is_assassin = "ASSASSIN" in upper

        title_text = (
            "ASSASSIN!"
            if is_assassin
            else f"{winner.upper()} TEAM WINS"
        )

        detail_text = (
            "The assassin ended the match."
            if is_assassin
            else f"{winner.upper()} revealed all of its words."
        )

        # Move the live screen into a float container so the scrim and
        # result panel can sit on top of it.
        container = FloatLayout()

        for child in reversed(list(self.root.children)):
            self.root.remove_widget(child)
            container.add_widget(child)

        container.add_widget(Scrim())

        anchor = AnchorLayout(anchor_x="center", anchor_y="center")

        compact = self._is_compact()

        panel = Panel(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(430)),
            height=dp(286) if compact else dp(304),
            padding=[
                dp(22) if compact else dp(30),
                dp(18) if compact else dp(26),
                dp(22) if compact else dp(30),
                dp(18) if compact else dp(26),
            ],
            spacing=dp(8) if compact else dp(10),
        )

        panel.add_widget(
            self._label(
                "MATCH OVER",
                dp(10) if compact else dp(11),
                MUTED,
                bold=True,
                halign="center",
                height=dp(16),
            )
        )

        panel.add_widget(
            Label(
                text=title_text,
                font_size=dp(26) if compact else dp(32),
                bold=True,
                color=winner_colour,
                halign="center",
                valign="middle",
                shorten=True,
                max_lines=1,
                size_hint=(1, None),
                height=dp(40) if compact else dp(46),
            )
        )

        rule_wrap = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
            height=dp(14),
        )
        rule_wrap.add_widget(
            Panel(
                fill=winner_colour,
                border=winner_colour,
                radius=dp(2),
                size_hint=(None, None),
                width=dp(120),
                height=dp(3),
            )
        )
        panel.add_widget(rule_wrap)

        detail = self._label(
            detail_text,
            dp(12) if compact else dp(13),
            MUTED,
            halign="center",
            height=dp(32),
        )
        detail.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )
        panel.add_widget(detail)

        panel.add_widget(Widget(size_hint_y=None, height=dp(4)))

        menu_button = FlatButton(
            text="BACK TO MENU",
            variant="primary",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(46) if compact else dp(50),
        )
        menu_button.bind(on_press=self.return_to_menu)
        panel.add_widget(menu_button)

        anchor.add_widget(panel)
        container.add_widget(anchor)

        self.game_over_root = container
        self.root.add_widget(container)

       # =========================================================
    # HOW TO PLAY
    def show_how_to_play(self, instance):
        self.show_screen(self.create_how_to_play_screen, "how_to_play")

    def create_how_to_play_screen(self):

        compact = self._is_compact()

        root = ScreenRoot()

        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(46) if compact else dp(58),
            spacing=dp(8),
        )

        titles = BoxLayout(orientation="vertical", spacing=0)
        titles.add_widget(
            self._label(
                "RULES",
                dp(10) if compact else dp(11),
                ACCENT,
                bold=True,
                height=dp(14) if compact else dp(16),
            )
        )
        titles.add_widget(
            self._label(
                "HOW TO PLAY",
                dp(20) if compact else dp(26),
                TEXT,
                bold=True,
                height=dp(28) if compact else dp(34),
            )
        )

        back_button = FlatButton(
            text="BACK",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(None, 1),
            width=dp(82) if compact else dp(150),
        )
        back_button.bind(on_press=self.return_to_menu)

        header.add_widget(titles)
        header.add_widget(back_button)
        root.add_widget(header)

        panel = Panel(
            orientation="vertical",
            padding=[
                dp(16) if compact else dp(26),
                dp(12) if compact else dp(22),
                dp(16) if compact else dp(26),
                dp(12) if compact else dp(22),
            ],
        )

        rules = ScrollLabel(font_size=dp(12) if compact else dp(14))
        rules.text = (
            "[b][color=#2DD4BF]OBJECTIVE[/color][/b]\n"
            "Two teams race to find all of their words first.\n\n"

            "[b][color=#2DD4BF]THE BOARD[/color][/b]\n"
            "The board holds 25 random words. Each word secretly "
            "belongs to one of four categories:\n"
            "[color=#E14B4B]● RED[/color] — a Red Team word\n"
            "[color=#4C8DF6]● BLUE[/color] — a Blue Team word\n"
            "[color=#8B96A8]● NEUTRAL[/color] — belongs to neither team\n"
            "[color=#C05A5A]● ASSASSIN[/color] — picking it loses the game\n\n"

            "[b][color=#2DD4BF]ROLES[/color][/b]\n"
            "[b]SPYMASTER[/b] — sees the secret key and gives clues "
            "verbally. Spymasters cannot select cards.\n"
            "[b]OPERATIVE[/b] — does not see the key and selects the "
            "cards.\n"
            "Each team has one spymaster and at least one operative.\n\n"

            "[b][color=#2DD4BF]TAKING A TURN[/color][/b]\n"
            "The current team guesses a card by tapping it.\n"
            "Revealing your own word keeps the turn going.\n"
            "Revealing a neutral or enemy word passes the turn.\n\n"

            "[b][color=#2DD4BF]THE ASSASSIN[/color][/b]\n"
            "If a team chooses the Assassin, that team immediately "
            "loses.\n\n"

            "[b][color=#2DD4BF]WINNING[/color][/b]\n"
            "A team wins by revealing all of its own cards first."
        )
        panel.add_widget(rules)

        root.add_widget(panel)

        return root
    # RETURN TO MENU    
    def return_to_menu(self, instance):

        self.cleanup_network()

        self.game_over = False
        self.client_game_over = False
        self._game_over_shown = False
        self.game_over_root = None
        self._overlay_root = None
        self.reset_match_log()

        self.show_screen(self.create_menu, "menu")
    # TURN ADVANCE FUNCTION
    def advance_turn(self):

        if self.game_over:
            return

        self.switch_team()

        if self.network_server:

            self.network_server.broadcast({
                "type": "turn_changed",
                "current_team": self.current_team,
                "remaining": dict(self.remaining)
            })

if __name__ == "__main__":
    CodenamesApp().run()