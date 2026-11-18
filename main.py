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
import i18n

from settings import (
    NAME_MAX_LENGTH,
    clean_player_name,
    settings_for_app,
)

from network import (
    DISCOVERY_PORT,
    DiscoveryResponder,
    GameClient,
    GameServer,
    PORT,
    SHARED_TEAM,
    discover_hosts,
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


# Minimum players depend on spymaster mode:
# 1 shared spymaster -> 3 players, 2 spymasters -> 4 players.
# The host is a player too (they pick a team and role).
MIN_PLAYERS = 4  # 2-spymaster/default minimum.

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

        # Settings and language must exist before any widget is created:
        # every label is translated as it is built. The bundled font is
        # registered as Kivy's default family (Latin + Persian glyphs).
        self.settings = settings_for_app(self)
        i18n.load(self.settings.language)
        i18n.install_fonts()

        self.player_name = self.settings.player_name
        self.word_library, self.word_error = self._make_word_library()

        self.network_server = None
        self.network_client = None

        self.host_poll_event = None
        self.client_poll_event = None

        # Hosting metadata used by LAN discovery.
        self.host_port = PORT
        self.host_ip = "127.0.0.1"
        self.host_ssid = ""
        self.host_network_kind = "WI-FI"

        # LAN discovery: host advertises, client scans.
        self.discovery = None
        self.discovery_error = None
        self.host_network_event = None
        self.lobby_scan_event = None
        self.lobby_scan_thread = None
        self.join_lobbies = []
        self.lobby_list_box = None
        self.lobby_status_label = None

        # Shown above the lobby list after a failed/lost connection.
        self._join_notice = None

        # Screen rebuilding is driven by orientation / size breakpoints.
        self._screen_builder = self.create_menu
        self._screen_name = "menu"
        self._layout_signature_saved = self._layout_signature()
        self._resize_event = None
        self._overlay_root = None

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

        # 1 = one shared spymaster for both teams,
        # 2 = one spymaster per team (the default).
        self.spymaster_mode = 2

        # The host is a player too: team + role in the lobby.
        self.host_team = "red"
        self.host_role = "spymaster"

        # ---------------------------------------------------
        # CLIENT GAME STATE
        # ---------------------------------------------------
        self.client_name = self.player_name
        self.client_key = None
        self.client_team = None
        self.client_role = None
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

    def _make_word_library(self):
        """Load the word list, keeping a readable error instead of crashing."""

        try:
            return WordLibrary(i18n.code()), None

        except (ValueError, FileNotFoundError) as error:
            return None, str(error)

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

        # A modal overlay is cheap to reopen; drop it so the rebuilt
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
        # Keep hosting/connections alive when Android backgrounds the app.
        return True

    def on_resume(self):
        if hasattr(self, "host_poll_event"):
            pass

    def _on_keyboard(self, window, key, scancode, codepoint, modifier):
        # Android back button / desktop Esc.
        if key != 27:
            return False

        # The result overlay is the topmost thing on screen.
        if self._game_over_shown:
            self.return_to_lobby(None)
            return True

        # Any modal dialog closes first.
        if self._overlay_root is not None:
            self._close_overlay()
            return True

        name = self._screen_name

        if name == "menu":
            self.confirm_exit()

        elif name in ("play", "join", "how_to_play"):
            self.return_to_menu(None)

        elif name == "host":
            self.confirm_close_lobby()

        elif name in ("team_selection", "client_waiting"):
            self.confirm_leave_lobby()

        elif name in (
            "host_game",
            "host_spymaster",
            "client_game",
            "client_spymaster",
        ):
            self.confirm_leave_match()

        return True

    def _clamp_width(self, value, margin=dp(24)):
        width = Window.width

        if width <= 0:
            return value

        return min(value, max(dp(240), width - margin))

    # -----------------------------------------------------------------
    # CONFIRM DIALOGS / SETTINGS
    # -----------------------------------------------------------------

    def _confirm(self, title, message, action_text, on_confirm, danger=False):
        """Small modal with cancel + one confirming action."""

        compact = self._is_compact()

        panel = Panel(
            orientation="vertical",
            width=self._clamp_width(dp(350)),
            height=dp(204) if compact else dp(222),
            size_hint=(None, None),
            padding=[
                dp(20) if compact else dp(24),
                dp(16) if compact else dp(20),
                dp(20) if compact else dp(24),
                dp(16) if compact else dp(20),
            ],
            spacing=dp(8) if compact else dp(10),
        )

        panel.add_widget(
            self._label(
                title,
                dp(11),
                RED if danger else ACCENT,
                bold=True,
                halign="center",
                height=dp(18),
            )
        )

        detail = self._label(
            message,
            dp(12) if compact else dp(13),
            TEXT,
            halign="center",
            height=dp(56),
        )
        detail.bind(
            width=lambda instance, width: setattr(
                instance, "text_size", (width, None)
            )
        )
        panel.add_widget(detail)
        panel.add_widget(Widget(size_hint_y=1))

        row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(44) if compact else dp(48),
            spacing=dp(8),
        )

        cancel_button = FlatButton(
            text="CANCEL",
            variant="ghost",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, 1),
        )
        cancel_button.bind(on_press=self._close_overlay)

        confirm_button = FlatButton(
            text=action_text,
            variant="danger" if danger else "primary",
            font_size=dp(11) if compact else dp(12),
            size_hint=(1, 1),
        )

        def confirmed(instance):
            self._close_overlay(rebuild=False)
            on_confirm()

        confirm_button.bind(on_press=confirmed)

        row.add_widget(cancel_button)
        row.add_widget(confirm_button)
        panel.add_widget(row)

        self._open_overlay(panel)

    def confirm_exit(self):
        self._confirm(
            i18n.t("EXIT CODENAMES?"),
            i18n.t("Are you sure you want to exit?"),
            i18n.t("EXIT"),
            platform_utils.quit_app,
            danger=True,
        )

    def confirm_close_lobby(self):
        count = 0

        if self.network_server is not None:
            try:
                count = max(
                    0,
                    len(self.network_server.server.get_lobby()) - 1,
                )
            except Exception:
                count = 0

        self._confirm(
            i18n.t("CLOSE THE LOBBY?"),
            i18n.t(
                "{count} connected players will be disconnected.",
                count=count,
            ),
            i18n.t("CLOSE"),
            lambda: self.return_to_menu(None),
            danger=True,
        )

    def confirm_leave_lobby(self):
        self._confirm(
            i18n.t("LEAVE THE LOBBY?"),
            i18n.t("You will leave this lobby."),
            i18n.t("LEAVE"),
            lambda: self.return_to_menu(None),
            danger=True,
        )

    def confirm_leave_match(self):
        host_side = self._screen_name in ("host_game", "host_spymaster")

        self._confirm(
            i18n.t("LEAVE THE MATCH?"),
            i18n.t(
                "The match will end for every player."
                if host_side
                else "You will leave this match."
            ),
            i18n.t("LEAVE"),
            lambda: self.return_to_lobby(None)
            if host_side
            else self.return_to_menu(None),
            danger=True,
        )

    def show_name_dialog(self, instance=None):
        compact = self._is_compact()

        panel = Panel(
            orientation="vertical",
            width=self._clamp_width(dp(340)),
            height=dp(196) if compact else dp(208),
            size_hint=(None, None),
            padding=[dp(20), dp(16), dp(20), dp(16)],
            spacing=dp(8) if compact else dp(10),
        )

        panel.add_widget(
            self._label(
                "CHANGE NAME",
                dp(10),
                ACCENT,
                bold=True,
                halign="center",
                height=dp(18),
            )
        )

        field = FlatInput(
            text=(
                ""
                if self.player_name == "Player"
                else self.player_name
            ),
            hint_text="Enter your name",
            max_length=NAME_MAX_LENGTH,
            height=dp(44),
        )
        panel.add_widget(field)

        panel.add_widget(
            self._label(
                "{count} characters max.".format(
                    count=NAME_MAX_LENGTH
                ),
                dp(10),
                MUTED,
                halign="center",
                height=dp(16),
            )
        )
        panel.add_widget(Widget(size_hint_y=1))

        row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(44),
            spacing=dp(8),
        )

        cancel_button = FlatButton(
            text="CANCEL",
            variant="ghost",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        cancel_button.bind(on_press=self._close_overlay)

        save_button = FlatButton(
            text="SAVE",
            variant="primary",
            font_size=dp(11),
            size_hint=(1, 1),
        )

        def saved(instance):
            name = clean_player_name(
                field.text,
                fallback=self.player_name,
            )

            self.settings.set("player_name", name)
            self.player_name = name
            self.client_name = name

            self._close_overlay(rebuild=False)
            self.show_screen(self.create_menu, "menu")

        save_button.bind(on_press=saved)

        row.add_widget(cancel_button)
        row.add_widget(save_button)
        panel.add_widget(row)

        self._open_overlay(panel)
        field.focus = True

    def show_language_dialog(self, instance=None):
        current = i18n.code()
        target = next(
            (
                entry
                for entry in i18n.available_languages()
                if entry["code"] != current
            ),
            None,
        )

        if target is None:
            return

        self._confirm(
            i18n.t("CHANGE LANGUAGE?"),
            i18n.t(
                "Switch the game to {language}?",
                language=target["native_name"],
            ),
            i18n.t("CHANGE"),
            lambda: self.set_language(target["code"]),
        )

    def set_language(self, code):
        self.settings.set("language", code)
        i18n.load(code)
        i18n.install_fonts()
        self.word_library, self.word_error = self._make_word_library()

        self.show_screen(self.create_menu, "menu")

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
            text=i18n.t(text),
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
            text=i18n.t(brand_text),
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
                text=i18n.t(badge_text),
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
            i18n.t(
                "Turn passed · {team}",
                team=(
                    f"[b][color=#{team_hex}]"
                    f"{i18n.t(str(team).upper())}[/color][/b]"
                ),
            ),
            team,
        )

    def render_log(self):
        label = getattr(self, "log_label", None)

        if label is None:
            return

        entries = self.match_log[-LOG_VISIBLE:]

        if not entries:
            label.text = (
                f"[color=#5C6675]{i18n.t('No moves yet.')}[/color]"
            )
            return

        lines = []

        for kind, text in entries:
            dot = TEAM_HEX.get(kind, "8B96A8")
            lines.append(f"[color=#{dot}]•[/color]  {text}")

        label.text = "\n".join(lines)

    def _role_button(
        self,
        team,
        role,
        callback,
        height=dp(58),
        text=None,
        tone=None,
    ):
        if text is None:
            team_text = (
                i18n.t("SHARED")
                if team == SHARED_TEAM
                else i18n.t(str(team).upper())
            )
            role_text = i18n.t(str(role).upper())
            text = (
                f"[b]{team_text}[/b]\n"
                f"[size=10]{role_text}[/size]"
            )

        button = FlatButton(
            text=text,
            markup=True,
            tone=tone if tone is not None else team,
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
                f"[color=#{colour}]•[/color] "
                f"{i18n.t(name.upper())} {counts[name]}"
            )

        return "    ".join(parts)
    def _lobby_blocker(self, lobby):
        """Return a human-readable reason the match cannot start yet."""

        total_players = len(lobby)
        min_players = 3 if self.spymaster_mode == 1 else 4

        if total_players < min_players:
            return i18n.t(
                "At least {minimum} players are needed "
                "(host + {others}). Currently {total}; "
                "waiting for {needed} more.",
                minimum=min_players,
                others=min_players - 1,
                total=total_players,
                needed=min_players - total_players,
            )

        unassigned = sum(
            1
            for player in lobby
            if player.get("team") is None
            or player.get("role") is None
        )

        if unassigned:
            return i18n.t(
                "{count} players still need a role.",
                count=unassigned,
            )

        mode = self.spymaster_mode

        if mode == 1:
            spymasters = [
                player
                for player in lobby
                if player.get("role") == "spymaster"
            ]

            if not (
                len(spymasters) == 1
                and spymasters[0].get("team") == SHARED_TEAM
            ):
                return i18n.t("One shared spymaster is required.")

            for team in ("red", "blue"):
                if not any(
                    player.get("team") == team
                    and player.get("role") == "operative"
                    for player in lobby
                ):
                    return i18n.t(
                        "{team} has no operative.",
                        team=i18n.t(team.upper()),
                    )

            return None

        for team in ("red", "blue"):
            if not any(
                player.get("team") == team
                and player.get("role") == "spymaster"
                for player in lobby
            ):
                return i18n.t(
                    "{team} has no spymaster.",
                    team=i18n.t(team.upper()),
                )

            if not any(
                player.get("team") == team
                and player.get("role") == "operative"
                for player in lobby
            ):
                return i18n.t(
                    "{team} has no operative.",
                    team=i18n.t(team.upper()),
                )

        return None

    # START MULTIPLAYER MATCH
    def start_host_match(self, instance):

        if not self.network_server:
            return

        if self.word_library is None:
            self.host_status_label.text = (
                self.word_error or i18n.t("The hosting server failed.")
            )
            return

        server = self.network_server.server

        lobby = server.get_lobby()

        self.spymaster_mode = server.spymaster_mode

        blocker = self._lobby_blocker(lobby)

        if blocker is not None:
            self.host_status_label.text = blocker
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
            "remaining": dict(self.remaining),
            "spymaster_mode": self.spymaster_mode,
        })

        # Only the spymasters receive the secret key. In mode 1 that
        # is the single shared spymaster; in mode 2 both team
        # spymasters get the full key (they may share one phone).
        self.network_server.send_to_spymasters({
            "type": "spy_key",
            "words": self.words,
            "roles": self.roles,
            "current_team": self.current_team,
            "remaining": dict(self.remaining),
        })

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

        layer = FloatLayout()

        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=self._clamp_width(dp(390)),
            height=dp(330) if compact else dp(374),
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
            text=i18n.t("CODENAMES"),
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

        play_button.bind(
            on_press=self.show_play_screen
        )

        how_to_play.bind(
            on_press=self.show_how_to_play
        )

        card.add_widget(mark_wrap)
        card.add_widget(eyebrow)
        card.add_widget(title)
        card.add_widget(rule_wrap)
        card.add_widget(gap)
        card.add_widget(play_button)
        card.add_widget(how_to_play)

        layer.add_widget(self._centered_scroll(card))

        # Player name chip (top-left) ------------------------------

        name_holder = AnchorLayout(
            anchor_x="left",
            anchor_y="top",
            size_hint=(None, None),
            size=(
                self._clamp_width(dp(250), margin=dp(16)),
                dp(42),
            ),
            pos_hint={"x": 0, "top": 1},
        )

        name_button = FlatButton(
            text=(
                f"{i18n.t('YOUR NAME')} · {self.player_name}"
            ),
            variant="ghost",
            font_size=dp(11),
            size_hint=(1, 1),
        )
        name_button.bind(on_press=self.show_name_dialog)
        name_holder.add_widget(name_button)

        # Language button (bottom-right) ---------------------------

        language_holder = AnchorLayout(
            anchor_x="right",
            anchor_y="bottom",
            size_hint=(None, None),
            size=(dp(150), dp(42)),
            pos_hint={"right": 1, "y": 0},
        )

        language_button = FlatButton(
            text=i18n.native_name(),
            variant="ghost",
            font_size=dp(12),
            size_hint=(1, 1),
        )
        language_button.bind(on_press=self.show_language_dialog)
        language_holder.add_widget(language_button)

        layer.add_widget(name_holder)
        layer.add_widget(language_holder)

        root.add_widget(layer)

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
            text=i18n.t("PLAY"),
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
            # The host is a player too: the server keeps the lobby row.
            server = self.network_server.server
            server.host_name = self.player_name

            server.set_spymaster_mode(self.spymaster_mode)

            self.host_team = server.host_team
            self.host_role = server.host_role

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

        return True

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
            "host_name": self.player_name,
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
        # HEADER (host name + leave; the back key asks the same thing)
        # ---------------------------------------------------------

        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(46) if compact else dp(58),
            spacing=dp(8),
        )

        header.add_widget(
            self._label(
                self.player_name,
                dp(20) if compact else dp(26),
                TEXT,
                bold=True,
                height=dp(28) if compact else dp(34),
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
        leave_button.bind(
            on_press=lambda instance: self.confirm_close_lobby()
        )

        header.add_widget(leave_button)
        root.add_widget(header)

        # ---------------------------------------------------------
        # SERVER FAILED
        # ---------------------------------------------------------

        if not self.network_server:

            self.start_match_button = None
            self.host_role_buttons = {}

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

        # Keep the SSID truthful: it is the fallback label when a client
        # cannot read the host name, and discovery replies carry it.
        self._refresh_host_network()
        self._ask_for_network_permission()

        server = self.network_server.server
        self.spymaster_mode = server.spymaster_mode
        self.host_team = server.host_team
        self.host_role = server.host_role

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
            height=dp(74) if compact else dp(82),
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
                "Auto-discovery could not start. Players can still "
                "join from the lobby list."
            )

        join_panel.add_widget(
            self._label(
                join_hint,
                dp(10) if compact else dp(11),
                TEXT if self.discovery_error is None else RED,
                height=dp(34),
            )
        )

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

        self.host_role_buttons = {}

        role_box = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            spacing=dp(6) if compact else dp(8),
        )

        role_box.height = self._build_host_role_controls(
            role_box, compact
        )
        right.add_widget(role_box)

        self.host_role_label = self._label(
            self.get_host_role_text(),
            dp(11) if compact else dp(12),
            MUTED,
            height=dp(18),
        )
        right.add_widget(self.host_role_label)

        self.host_role_hint = self._label(
            (
                "One shared spymaster sees the key for both teams; "
                "everyone else is an operative."
                if self.spymaster_mode == 1
                else "Each team has one spymaster (sees the key) and "
                "any number of operatives (pick cards)."
            ),
            dp(9) if compact else dp(10),
            MUTED,
            height=dp(26) if compact else dp(30),
        )
        right.add_widget(self.host_role_hint)

        right.add_widget(
            self._label(
                "MATCH SETTINGS",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        mode_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(34) if compact else dp(38),
            spacing=dp(6),
        )

        self.host_mode_buttons = {}

        for mode, mode_text in (
            (1, "1 · BOTH TEAMS"),
            (2, "2 · ONE PER TEAM"),
        ):
            button = FlatButton(
                text=mode_text,
                variant=(
                    "primary"
                    if self.spymaster_mode == mode
                    else "ghost"
                ),
                font_size=dp(10) if compact else dp(11),
                size_hint=(1, 1),
            )
            button.bind(
                on_press=lambda instance, value=mode: (
                    self.set_spymaster_mode(value)
                )
            )

            self.host_mode_buttons[mode] = button
            mode_row.add_widget(button)

        right.add_widget(mode_row)

        right.add_widget(Widget(size_hint_y=1))

        min_players = 3 if self.spymaster_mode == 1 else 4

        self.start_match_button = FlatButton(
            text=i18n.t(
                "START GAME ({count}/{minimum} PLAYERS)",
                count=1,
                minimum=min_players,
            ),
            variant="primary",
            font_size=dp(12) if compact else dp(14),
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
            left_height=dp(270) if compact else dp(294),
            right_height=dp(400) if compact else dp(428),
        )

        root.add_widget(content)

        self.update_host_lobby(0)

        return root

    def _build_host_role_controls(self, container, compact):
        """Fill the host's role picker for the current spymaster mode."""

        return self._fill_role_picker(
            container,
            compact,
            self.host_role_buttons,
            self.choose_host_assignment,
            self.host_team,
            self.host_role,
        )

    def _fill_role_picker(
        self,
        container,
        compact,
        buttons,
        callback,
        team,
        role,
    ):
        """Build the mode-aware team/role grid + NO ROLE button."""

        buttons.clear()

        button_height = dp(48) if compact else dp(54)

        if self.spymaster_mode == 1:
            shared = self._role_button(
                SHARED_TEAM,
                "spymaster",
                callback,
                height=button_height,
                text=f"[b]{i18n.t('SPYMASTER · BOTH TEAMS')}[/b]",
                tone=None,
            )
            buttons[(SHARED_TEAM, "spymaster")] = shared
            container.add_widget(shared)

            operative_row = BoxLayout(
                orientation="horizontal",
                size_hint=(1, None),
                height=button_height,
                spacing=dp(6) if compact else dp(8),
            )

            for team_name in ("red", "blue"):
                button = self._role_button(
                    team_name,
                    "operative",
                    callback,
                    height=button_height,
                )
                buttons[(team_name, "operative")] = button
                operative_row.add_widget(button)

            container.add_widget(operative_row)

            used_height = button_height * 2 + dp(8)

        else:
            grid = GridLayout(
                cols=2,
                rows=2,
                spacing=dp(6) if compact else dp(8),
                size_hint=(1, None),
                height=button_height * 2 + dp(8),
            )

            for team_name, role_name in (
                ("red", "spymaster"),
                ("red", "operative"),
                ("blue", "spymaster"),
                ("blue", "operative"),
            ):
                button = self._role_button(
                    team_name,
                    role_name,
                    callback,
                    height=button_height,
                )
                buttons[(team_name, role_name)] = button
                grid.add_widget(button)

            container.add_widget(grid)
            used_height = grid.height

        no_role_height = dp(32) if compact else dp(36)

        no_role = self._role_button(
            None,
            None,
            callback,
            height=no_role_height,
            text=f"[b]{i18n.t('NO ROLE')}[/b]",
            tone=None,
        )
        buttons[(None, None)] = no_role
        container.add_widget(no_role)

        self._refresh_role_buttons(buttons, team, role)

        return used_height + no_role_height + dp(6)

    # HOST ROLE TEXT
    def get_host_role_text(self):
        team = self.host_team
        role = self.host_role

        if not team or not role:
            return i18n.t("NO ROLE")

        team_text = (
            i18n.t("SHARED")
            if team == SHARED_TEAM
            else i18n.t(str(team).upper())
        )

        return i18n.t(
            "HOST ROLE: {team} {role}",
            team=team_text,
            role=i18n.t(str(role).upper()),
        )

    def _assignment_sentence(self, team, role):
        if not team or not role:
            return i18n.t("No role selected.")

        team_text = (
            i18n.t("SHARED")
            if team == SHARED_TEAM
            else i18n.t(str(team).upper())
        )

        return i18n.t(
            "You are now {team} {role}.",
            team=team_text,
            role=i18n.t(str(role).upper()),
        )

    # HOST: PICK OWN TEAM AND ROLE
    def choose_host_assignment(self, team, role):

        if not self.network_server:
            return

        server = self.network_server.server

        if team is None or role is None:
            success = server.set_host_assignment(None, None)
        else:
            success = server.set_host_assignment(team, role)

        if not success:

            self.host_status_label.text = (
                i18n.t("That role is already taken.")
                if role == "spymaster"
                else i18n.t("No role selected.")
            )

            return False

        self.host_team = server.host_team
        self.host_role = server.host_role

        self._refresh_role_buttons(
            self.host_role_buttons,
            self.host_team,
            self.host_role,
        )

        if hasattr(self, "host_role_label"):
            self.host_role_label.text = self.get_host_role_text()

        if hasattr(self, "host_status_label"):
            self.host_status_label.text = self._assignment_sentence(
                team, role
            )

        self.network_server.send_lobby_update()
        self.update_host_lobby(0)

        return True

    def set_spymaster_mode(self, mode):
        """Switch modes; every spymaster assignment is cleared."""

        if not self.network_server:
            return

        if mode == 1:
            lobby = self.network_server.server.get_lobby()

            if len(lobby) >= 4:
                if hasattr(self, "host_status_label"):
                    self.host_status_label.text = i18n.t(
                        "Cannot use 1 spymaster with 4 or more players in the lobby."
                    )

                self.update_host_lobby(0)
                return

        if not self.network_server.server.set_spymaster_mode(mode):
            return

        self.spymaster_mode = mode
        self.host_team = self.network_server.server.host_team
        self.host_role = self.network_server.server.host_role

        self.network_server.send_lobby_update()

        # Rebuild the picker (the grid shape changes with the mode).
        self.show_screen(self.create_host_screen, "host")

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

                self.host_status_label.text = i18n.t(
                    "{name} joined successfully!",
                    name=safe_markup(event.get("name", "Player")),
                )

                lobby_changed = True

            # -----------------------------------------------------
            # PLAYER LEFT
            # -----------------------------------------------------

            elif event_type == "player_left":

                self.host_status_label.text = i18n.t(
                    "{name} disconnected.",
                    name=safe_markup(event.get("name", "Player")),
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
                requested_role = message.get("role")

                success = (
                    self.network_server.server.set_player_assignment(
                        player_key,
                        requested_team,
                        requested_role
                    )
                )

                if success:

                    if requested_team and requested_role:
                        team_text = (
                            i18n.t("SHARED")
                            if requested_team == SHARED_TEAM
                            else i18n.t(
                                str(requested_team).upper()
                            )
                        )

                        self.host_status_label.text = i18n.t(
                            "{name} joined the {team} team as {role}.",
                            name=safe_markup(player_name),
                            team=team_text,
                            role=i18n.t(
                                str(requested_role).upper()
                            ),
                        )
                    else:
                        self.host_status_label.text = i18n.t(
                            "{name} is picking a role.",
                            name=safe_markup(player_name),
                        )

                    lobby_changed = True

                else:

                    self.network_server.send_to_player(
                        player_key,
                        {
                            "type": "selection_rejected",
                            "reason": (
                                "role_taken"
                                if requested_role == "spymaster"
                                else "invalid_role"
                            ),
                            "name": player_name,
                            "player_key": player_key,
                        }
                    )

        if lobby_changed:
            self.network_server.send_lobby_update()

        lobby = self.network_server.server.get_lobby()

        total_players = len(lobby)
        min_players = 3 if self.spymaster_mode == 1 else 4

        # ---------------------------------------------------------
        # UPDATE HOST LOBBY DISPLAY
        # ---------------------------------------------------------

        lines = [
            "[color=#8B96A8]"
            + i18n.t(
                "PLAYERS · {count}/{minimum} READY",
                count=total_players,
                minimum=min_players,
            )
            + "[/color]"
        ]

        for player in lobby:

            name = safe_markup(player.get("name", "Player"))
            team = player.get("team")
            role = player.get("role")

            dot = TEAM_HEX.get(team, "5C6675")

            line = (
                f"[color=#{dot}]•[/color]  [b]{name}[/b]"
            )

            if player.get("is_host"):
                line += (
                    "   [color=#2DD4BF][size=10]"
                    f"{i18n.t('HOST')}[/size][/color]"
                )

            if team and role:
                tag_colour = (
                    "8B96A8"
                    if team == SHARED_TEAM
                    else TEAM_HEX.get(team, "8B96A8")
                )
                team_text = (
                    i18n.t("SHARED")
                    if team == SHARED_TEAM
                    else i18n.t(str(team).upper())
                )

                line += (
                    f"   [color=#{tag_colour}][size=10]"
                    f"{team_text} {i18n.t(str(role).upper())}"
                    "[/size][/color]"
                )
            else:
                line += (
                    "   [color=#5C6675][size=10]"
                    f"{i18n.t('NO ROLE')}[/size][/color]"
                )

            lines.append(line)

        # ---------------------------------------------------------
        # ROLE LAYOUT HINTS (do not block starting)
        # ---------------------------------------------------------

        blocker = self._lobby_blocker(lobby)

        if blocker is not None and total_players >= min_players:
            lines.append("")
            lines.append(
                f"[color=#8B96A8]{safe_markup(blocker)}[/color]"
            )

        self.host_players_label.text = "\n".join(lines)

        # ---------------------------------------------------------
        # CAN THE HOST START?
        # ---------------------------------------------------------

        if hasattr(self, "host_mode_buttons"):
            one_spymaster = self.host_mode_buttons.get(1)

            if one_spymaster is not None:
                one_spymaster.disabled = (
                    self.spymaster_mode != 1 and total_players >= 4
                )

        if self.start_match_button:

            self.start_match_button.disabled = blocker is not None

            if blocker is None:
                self.start_match_button.text = i18n.t("START GAME")

            elif total_players < min_players:
                self.start_match_button.text = i18n.t(
                    "START GAME ({count}/{minimum} PLAYERS)",
                    count=total_players,
                    minimum=min_players,
                )

            else:
                self.start_match_button.text = blocker

        return True
    # -----------------------------------------------------------------
    # MODAL OVERLAY
    # -----------------------------------------------------------------

    def _open_overlay(self, panel):
        if self._overlay_root is not None:
            self._close_overlay()

        container = FloatLayout()

        # `children` is stored newest-first and restored as-is on close.
        original = list(self.root.children)

        for child in original:
            self.root.remove_widget(child)
            container.add_widget(child)

        container.add_widget(Scrim())

        container.add_widget(self._centered_scroll(panel, margin=dp(10)))

        container.original_children = original
        self._overlay_root = container
        self.root.add_widget(container)

    def _close_overlay(self, instance=None, rebuild=True):
        container = self._overlay_root

        if container is None:
            return

        self._overlay_root = None

        if rebuild:
            builder = getattr(self, "_screen_builder", None)

            self.root.clear_widgets()
            self.root.add_widget(
                builder() if builder else self.create_menu()
            )

            return

        # Keep the underlying screen: unwrap the float layer again so
        # the next overlay does not stack on a stale container.
        original = list(getattr(container, "original_children", []))

        self.root.clear_widgets()
        self.root.children = original

        # OPEN JOIN SCREEN
    def show_join_screen(self, instance):

        self.cleanup_network()
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
            dp(500 if android else 634)
            if compact
            else dp(536 if android else 666)
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

        action_row.add_widget(refresh_button)
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
            self.lobby_status_label.text = i18n.t(
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
            self.lobby_status_label.text = i18n.t(
                "No host yet. Both phones must be on the same "
                "Wi-Fi or hotspot."
            )
        else:
            self.lobby_status_label.text = i18n.t(
                "{count} hosts found. Tap JOIN.",
                count=count,
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

        title = str(host.get("host_name") or "").strip()

        if not title:
            title = str(host.get("ssid") or "").strip()

        if not title:
            title = i18n.t("Codenames host")

        labels.add_widget(
            self._label(
                title,
                dp(12) if compact else dp(13),
                TEXT,
                bold=True,
                height=dp(18),
                shorten=True,
            )
        )

        players = int(host.get("players") or 1)

        labels.add_widget(
            self._label(
                i18n.t("{players} players", players=players),
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

        try:
            port = int(host.get("port"))

        except (TypeError, ValueError):
            port = PORT

        self.client_name = self.player_name

        self._connect_to_host(host.get("host"), port, self.player_name)

    # -----------------------------------------------------------------
    # MANUAL ADDRESS FALLBACK
    # -----------------------------------------------------------------

    @staticmethod
    def parse_address(text):
        """Turn `192.168.1.5:5000` (or just a host) into (host, port)."""

        raw = str(text or "").strip()

        if not raw:
            return None

        raw = raw.replace("codenames://", "", 1).strip().strip("/")

        if "/" in raw:
            raw = raw.split("/", 1)[0]

        if "@" in raw:
            raw = raw.rsplit("@", 1)[-1]

        port = PORT

        if ":" in raw:
            host, _, port_text = raw.rpartition(":")

            try:
                port = int(port_text)

            except ValueError:
                return None

        else:
            host = raw

        host = host.strip()

        if not host:
            return None

        return host, port

    def connect_to_host(self, instance=None):
        """Desktop-only manual address fallback."""

        if self.join_ip_input is None:
            return

        raw = self.join_ip_input.text.strip()

        self.client_name = self.player_name

        if not raw:
            self.join_status_label.text = i18n.t(
                "Please enter the host address."
            )
            return

        parsed = self.parse_address(raw)

        if parsed is None:
            self.join_status_label.text = i18n.t(
                "Use an address like 192.168.1.5:5000."
            )
            return

        self._connect_to_host(parsed[0], parsed[1], self.player_name)

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

        self.join_status_label.text = i18n.t("Connecting...")

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

            guesser = event.get("guesser") or i18n.t("Player")

            self.add_log(
                i18n.t(
                    "{name} guessed {word} · {team}",
                    name=safe_markup(guesser),
                    word=f"[b]{safe_markup(word)}[/b]",
                    team=(
                        f"[color=#{TEAM_HEX.get(role, '8B96A8')}]"
                        f"{i18n.t(str(role).upper())}[/color]"
                    ),
                ),
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
                event.get("winner", "red"),
                event.get("reason", "words"),
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
                self.confirm_leave_match,
                badge_text=str(self.client_team or "").upper(),
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
            self.client_info_label.text = i18n.t("No connection to the host.")
            return

        success = self.network_client.send_message({
            "type": "reveal_card",
            "index": card.card_index
        })

        if success:

            self.client_selection_pending = True
            self.update_client_controls()

        else:

            self.client_info_label.text = i18n.t("Could not contact host.")
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
            self.client_info_label.text = i18n.t("No connection to the host.")
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

        team = self.client_team
        role = self.client_role
        team_hex = (
            "8B96A8"
            if team == SHARED_TEAM
            else TEAM_HEX.get(team, "8B96A8")
        )

        if team and role:
            team_text = (
                i18n.t("SHARED")
                if team == SHARED_TEAM
                else i18n.t(str(team).upper())
            )
            role_text = i18n.t(str(role).upper())
        else:
            team_text = i18n.t("NONE")
            role_text = i18n.t("NO ROLE")

        self.client_role_label = Label(
            text=(
                "[size=10][color=#8B96A8]"
                f"{i18n.t('YOUR TEAM')}[/color][/size]\n"
                f"[color=#{team_hex}][b]{team_text}[/b][/color]\n"
                "[size=10][color=#8B96A8]"
                f"{i18n.t('YOUR ROLE')}[/color][/size]\n"
                f"[b]{role_text}[/b]"
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
        back_button.bind(on_press=self.confirm_leave_lobby)
        card.add_widget(back_button)

        root.add_widget(self._centered_scroll(card))

        return root

    def _client_lobby_markup(self):
        """Markup player list shared by lobby-style screens."""

        lines = []

        for player in getattr(self, "client_lobby", []):

            name = safe_markup(player.get("name", "Player"))
            team = player.get("team")
            role = player.get("role")

            dot = "5C6675" if team == SHARED_TEAM else TEAM_HEX.get(
                team, "5C6675"
            )

            line = f"[color=#{dot}]•[/color]  [b]{name}[/b]"

            if player.get("is_host"):
                line += (
                    "   [color=#2DD4BF][size=10]"
                    f"{i18n.t('HOST')}[/size][/color]"
                )

            if team and role:
                tag = (
                    "8B96A8"
                    if team == SHARED_TEAM
                    else TEAM_HEX.get(team, "8B96A8")
                )
                team_text = (
                    i18n.t("SHARED")
                    if team == SHARED_TEAM
                    else i18n.t(str(team).upper())
                )

                line += (
                    f"   [color=#{tag}][size=10]"
                    f"{team_text} {i18n.t(str(role).upper())}"
                    "[/size][/color]"
                )
            else:
                line += (
                    "   [color=#5C6675][size=10]"
                    f"{i18n.t('NO ROLE')}[/size][/color]"
                )

            lines.append(line)

        return "\n".join(lines) if lines else (
            f"[color=#5C6675]{i18n.t('No players yet.')}[/color]"
        )

    def _client_assignment_text(self):
        team = self.client_team
        role = self.client_role

        if not team or not role:
            return i18n.t("No role selected.")

        team_text = (
            i18n.t("SHARED")
            if team == SHARED_TEAM
            else i18n.t(str(team).upper())
        )

        return i18n.t(
            "You joined the {team} team as {role}.",
            team=team_text,
            role=i18n.t(str(role).upper()),
        )

    def _refresh_client_role_labels(self):
        """Update the waiting screen card + lobby chip after a change."""

        if hasattr(self, "client_role_label"):
            self.client_role_label.text = self._client_lobby_role_markup()

        if hasattr(self, "team_selection_status"):
            self.team_selection_status.text = (
                self._client_assignment_text()
            )

        if hasattr(self, "team_selection_lobby"):
            self.team_selection_lobby.text = self._client_lobby_markup()

        if hasattr(self, "client_lobby_label"):
            self.client_lobby_label.text = self._client_lobby_markup()

    def _client_lobby_role_markup(self):
        team = self.client_team
        role = self.client_role
        team_hex = (
            "8B96A8"
            if team == SHARED_TEAM
            else TEAM_HEX.get(team, "8B96A8")
        )

        if team and role:
            team_text = (
                i18n.t("SHARED")
                if team == SHARED_TEAM
                else i18n.t(str(team).upper())
            )
            role_text = i18n.t(str(role).upper())
        else:
            team_text = i18n.t("NONE")
            role_text = i18n.t("NO ROLE")

        return (
            "[size=10][color=#8B96A8]"
            f"{i18n.t('YOUR TEAM')}[/color][/size]\n"
            f"[color=#{team_hex}][b]{team_text}[/b][/color]\n"
            "[size=10][color=#8B96A8]"
            f"{i18n.t('YOUR ROLE')}[/color][/size]\n"
            f"[b]{role_text}[/b]"
        )

    def _rejection_text(self, event):
        """Translate the host's rejection code (language is per device)."""

        reason = str(event.get("reason") or "")
        team = event.get("current_team") or ""

        if reason == "game_started":
            return i18n.t("The game has already started.")

        if reason == "game_over":
            return i18n.t("The game is over.")

        if reason == "not_operative":
            return i18n.t(
                "Spymasters cannot reveal cards. "
                "Give clues to your operatives."
            )

        if reason == "wrong_turn":
            return i18n.t(
                "It's {team}'s turn!",
                team=i18n.t(str(team).upper()),
            )

        if reason == "invalid_card":
            return i18n.t("That card cannot be selected.")

        if reason == "role_taken":
            return i18n.t("That role is already taken.")

        if reason == "invalid_role":
            return i18n.t("No role selected.")

        legacy = str(event.get("message") or "")

        return i18n.t(legacy) if legacy else i18n.t("Action rejected.")
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
                    None
                )

                self.client_lobby = event.get(
                    "lobby",
                    []
                )

                self.spymaster_mode = int(
                    event.get("spymaster_mode", 2)
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

                        self.join_status_label.text = i18n.t(
                            "Invalid board received from host."
                        )

                    continue

                self.client_words = words

                self.spymaster_mode = int(
                    event.get(
                        "spymaster_mode",
                        self.spymaster_mode,
                    )
                )

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

                message = self._rejection_text(event)

                if hasattr(self, "client_info_label"):
                    self.client_info_label.text = message

                if hasattr(self, "team_selection_status"):
                    self.team_selection_status.text = message
            # CONNECTION ERROR
            elif event_type == "error":

                reason = str(event.get("reason") or "")
                message = str(event.get("message") or "")

                if not message:
                    message = i18n.t(
                        {
                            "invalid_join": "Could not join that lobby.",
                        }.get(reason, "Connection error.")
                    )

                message = i18n.t(message)

                if hasattr(self, "join_status_label"):

                    self.join_status_label.text = i18n.t(
                        "COULD NOT CONNECT:\n{message}",
                        message=message,
                    )

                if hasattr(self, "client_info_label"):

                    self.client_info_label.text = i18n.t(
                        "Connection error: {message}",
                        message=message,
                    )

                # The connection is dead: drop the client so the next
                # JOIN starts clean and discovery can report again.
                self.client_selection_pending = False
                self._drop_client()

                # Back on the join screen: keep looking so the player
                # can tap a different host.
                if self._screen_name == "join":
                    self.start_lobby_scan()
                elif self._screen_name not in ("menu",):
                    self._return_to_join(
                        i18n.t("Connection lost.")
                    )

            # DISCONNECTED
            elif event_type == "disconnected":

                self.client_selection_pending = False

                self._drop_client()

                host_closed = event.get("reason") == "host_closed"

                notice = (
                    i18n.t("The host closed the lobby.")
                    if host_closed
                    else i18n.t("Disconnected from host.")
                )

                if hasattr(self, "client_info_label"):

                    self.client_info_label.text = notice

                elif (
                    hasattr(self, "join_status_label")
                    and self._join_notice is None
                ):

                    self.join_status_label.text = notice

                if self._screen_name == "join":
                    self.start_lobby_scan()
                elif self._screen_name not in ("menu",):
                    self._return_to_join(notice)

            # HOST SENT A LOBBY UPDATE
            elif event_type == "lobby_update":

                self.client_lobby = event.get(
                    "lobby",
                    []
                )

                self.spymaster_mode = int(
                    event.get(
                        "spymaster_mode",
                        self.spymaster_mode,
                    )
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
                            "role"
                        )

                        break

                self.update_team_selection_lobby()

            # HOST SENT EVERYONE BACK TO THE LOBBY
            elif event_type == "lobby_return":

                self.game_over = False
                self.client_game_over = False
                self.client_selection_pending = False
                self._game_over_shown = False
                self.game_over_root = None
                self._overlay_root = None
                self.client_revealed = set()
                self.client_revealed_roles = {}
                self.client_cards = []
                self.spymaster_cards = []
                self.client_words = []
                self.client_key_roles = []
                self.reset_match_log()

                self.show_screen(
                    self.create_team_selection_screen,
                    "team_selection",
                )

                self.update_team_selection_lobby()

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
                self.client_info_label.text = i18n.t("GAME OVER")

            elif self.client_role != "operative":
                self.client_info_label.text = i18n.t(
                    "Spymaster: watch the board and give clues."
                )

            elif self.client_selection_pending:
                self.client_info_label.text = i18n.t(
                    "Waiting for host..."
                )

            elif not can_play:
                self.client_info_label.text = i18n.t(
                    "Waiting for {team} team...",
                    team=i18n.t(
                        str(self.client_current_team).upper()
                    ),
                )

            else:
                self.client_info_label.text = i18n.t(
                    "Your team's turn!"
                )
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
                "LEAVE" if for_client else "LOBBY",
                self.confirm_leave_match,
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
                "LOBBY",
                self.confirm_leave_match,
                badge_text=str(self.host_team or "").upper(),
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
                i18n.t(
                    "{name} guessed {word} · {team}",
                    name=safe_markup(guesser),
                    word=f"[b]{safe_markup(word)}[/b]",
                    team=(
                        f"[color=#{TEAM_HEX.get(role, '8B96A8')}]"
                        f"{i18n.t(str(role).upper())}[/color]"
                    ),
                ),
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

                game_message = ("red", "words")

            elif self.current_team != "red":

                self.switch_team()

        # BLUE CARD
        elif role == "blue":

            self.remaining["blue"] -= 1

            if self.remaining["blue"] <= 0:

                game_message = ("blue", "words")

            elif self.current_team != "blue":

                self.switch_team()

        # NEUTRAL CARD
        elif role == "neutral":

            self.switch_team()

        # ASSASSIN
        elif role == "assassin":

            game_message = (
                ("blue", "assassin")
                if self.current_team == "red"
                else ("red", "assassin")
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
                "guesser": guesser or i18n.t("Player"),
                "remaining": dict(self.remaining),
                "current_team": self.current_team,
                "game_over": game_message is not None,
            }

            # At the end the full key is public so every screen can
            # reveal the remaining cards.
            if game_message:
                payload["winner"] = game_message[0]
                payload["reason"] = game_message[1]
                payload["roles"] = list(self.roles)

            self.network_server.broadcast(payload)

        # FINISH GAME AFTER BROADCASTING THE RESULT
        if game_message:

            self.end_game(*game_message)

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

            def reject(reason, **extra):

                payload = {
                    "type": "selection_rejected",
                    "name": sender_name,
                    "player_key": sender_key,
                    "reason": reason,
                }
                payload.update(extra)

                self.network_server.send_to_player(sender_key, payload)

            # TEAMS AND ROLES ARE LOCKED ONCE THE GAME STARTS
            if message_type == "choose_team":

                reject("game_started")
                continue

            if message_type not in ("reveal_card", "end_turn"):
                continue

            if self.game_over:

                reject("game_over")
                continue

            # Only operatives touch the cards
            if sender_role != "operative":

                reject("not_operative")

                continue

            # Reject actions from the wrong team
            if sender_team != self.current_team:

                reject(
                    "wrong_turn",
                    current_team=self.current_team,
                )

                continue

            # CARD SELECTION
            if message_type == "reveal_card":

                index = message.get("index")

                if not self.reveal_card(index, guesser=sender_name):

                    reject("invalid_card")

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

        self._refresh_client_role_labels()

        # The host may switch the spymaster mode while we sit in the
        # lobby; rebuild the picker so the grid always matches.
        buttons = getattr(self, "team_buttons", None)

        if buttons is not None and hasattr(self, "team_selection_hint"):
            expected = set()

            if self.spymaster_mode == 1:
                expected = {
                    (SHARED_TEAM, "spymaster"),
                    ("red", "operative"),
                    ("blue", "operative"),
                    (None, None),
                }
            else:
                expected = {
                    ("red", "spymaster"),
                    ("red", "operative"),
                    ("blue", "spymaster"),
                    ("blue", "operative"),
                    (None, None),
                }

            if set(buttons) != expected:
                self.show_screen(
                    self.create_team_selection_screen,
                    "team_selection",
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
        leave_button.bind(on_press=self.confirm_leave_lobby)

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
                f"[color=#8B96A8][size=11]{i18n.t('PLAYER')}[/size][/color]  "
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

        self.team_selection_hint = self._label(
            (
                "One shared spymaster sees the key for both teams; "
                "everyone else is an operative."
                if self.spymaster_mode == 1
                else "Each team has one spymaster (sees the key) and "
                "any number of operatives (pick cards)."
            ),
            dp(11) if compact else dp(12),
            MUTED,
            height=dp(32) if compact else dp(36),
        )
        left.add_widget(self.team_selection_hint)

        left.add_widget(
            self._label(
                "PICK A SLOT",
                dp(10),
                MUTED,
                bold=True,
                height=dp(14),
            )
        )

        self.team_buttons = {}

        role_box = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            spacing=dp(6) if compact else dp(8),
        )

        role_box.height = self._fill_role_picker(
            role_box,
            compact,
            self.team_buttons,
            self.select_team,
            self.client_team,
            self.client_role,
        )

        left.add_widget(role_box)

        self.team_selection_status = self._label(
            self._client_assignment_text(),
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

            if team is None or role is None:
                self.client_team = None
                self.client_role = None

                if hasattr(self, "team_buttons"):
                    self._refresh_role_buttons(
                        self.team_buttons, None, None
                    )

                if hasattr(self, "team_selection_status"):
                    self.team_selection_status.text = i18n.t(
                        "No role selected."
                    )

                return

            if hasattr(self, "team_buttons"):
                self._refresh_role_buttons(
                    self.team_buttons,
                    team,
                    role,
                )

            if hasattr(self, "team_selection_status"):

                team_text = (
                    i18n.t("SHARED")
                    if team == SHARED_TEAM
                    else i18n.t(str(team).upper())
                )

                self.team_selection_status.text = i18n.t(
                    "Requesting {team} {role}...",
                    team=team_text,
                    role=i18n.t(str(role).upper()),
                )

        else:

            if hasattr(self, "team_selection_status"):

                self.team_selection_status.text = i18n.t(
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
    def end_game(self, winner="red", reason="words"):

        winner = "blue" if winner == "blue" else "red"

        self.game_over = True

        if self.network_client:
            self.client_game_over = True

        self._reveal_full_board()

        # The client already has its own overlay from apply_client_reveal.
        if getattr(self, "_game_over_shown", False):
            return

        self._game_over_shown = True

        winner_colour = TEAM_COLORS.get(winner, TEXT)

        is_assassin = reason == "assassin"

        title_text = i18n.t(
            "{team} TEAM WINS",
            team=i18n.t(winner.upper()),
        )

        if is_assassin:
            detail_text = i18n.t(
                "{team} wins because the other team found the assassin.",
                team=i18n.t(winner.upper()),
            )
        else:
            detail_text = i18n.t(
                "{team} revealed all of its words.",
                team=i18n.t(winner.upper()),
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

        lobby_button = FlatButton(
            text="BACK TO LOBBY",
            variant="primary",
            font_size=dp(13) if compact else dp(14),
            size_hint=(1, None),
            height=dp(46) if compact else dp(50),
        )
        lobby_button.bind(on_press=self.return_to_lobby)
        panel.add_widget(lobby_button)

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
        rules.text = self._rules_markup()
        panel.add_widget(rules)

        root.add_widget(panel)

        return root

    def _rules_markup(self):
        """How-to-play body, translated paragraph by paragraph."""

        def heading(text):
            return (
                f"[b][color=#2DD4BF]{i18n.t(text)}[/color][/b]"
            )

        def bullet(colour, text):
            return f"[color=#{colour}]● {i18n.t(text)}[/color]"

        sections = [
            heading("OBJECTIVE"),
            i18n.t(
                "Two teams race to find all of their words first."
            ),
            "",
            heading("THE BOARD"),
            i18n.t(
                "The board holds 25 random words. Each word secretly "
                "belongs to one of four categories:"
            ),
            bullet("E14B4B", "RED — a Red Team word"),
            bullet("4C8DF6", "BLUE — a Blue Team word"),
            bullet("8B96A8", "NEUTRAL — belongs to neither team"),
            bullet("C05A5A", "ASSASSIN — picking it loses the game"),
            "",
            heading("ROLES"),
            i18n.t(
                "SPYMASTER — sees the secret key and gives clues "
                "verbally. Spymasters cannot select cards."
            ),
            i18n.t(
                "OPERATIVE — does not see the key and selects the cards."
            ),
            i18n.t(
                "Each team has one spymaster and at least one operative."
            ),
            i18n.t(
                "With one shared spymaster, the same player gives clues "
                "for both teams."
            ),
            "",
            heading("TAKING A TURN"),
            i18n.t(
                "The current team guesses a card by tapping it."
            ),
            i18n.t(
                "Revealing your own word keeps the turn going."
            ),
            i18n.t(
                "Revealing a neutral or enemy word passes the turn."
            ),
            "",
            heading("THE ASSASSIN"),
            i18n.t(
                "If a team chooses the Assassin, that team immediately "
                "loses."
            ),
            "",
            heading("WINNING"),
            i18n.t(
                "A team wins by revealing all of its own cards first."
            ),
        ]

        return "\n".join(sections)
    # RETURN TO LOBBY / MENU
    def return_to_lobby(self, instance=None):
        """Leave the board but keep the group: back to the lobby intact."""

        name = self._screen_name

        host_side = name in ("host_game", "host_spymaster")

        self.game_over = False
        self.client_game_over = False
        self.client_selection_pending = False
        self._game_over_shown = False
        self.game_over_root = None
        self._overlay_root = None
        self.reset_match_log()

        # Match state only; teams and roles survive so the next round
        # starts with the same players in the same seats.
        self.revealed_cards = set()
        self.cards = []
        self.spymaster_cards = []
        self.words = []
        self.roles = []

        if host_side:

            if self.network_server is None:
                self.return_to_menu(None)
                return

            self.network_server.broadcast({"type": "lobby_return"})
            self.network_server.send_lobby_update()

            if self.host_poll_event:
                self.host_poll_event.cancel()

            self.host_poll_event = Clock.schedule_interval(
                self.update_host_lobby, 0.2
            )

            self.show_screen(self.create_host_screen, "host")

            return

        # Clients keep their seat in the lobby and wait for the host.
        self.client_revealed = set()
        self.client_revealed_roles = {}
        self.client_cards = []
        self.spymaster_cards = []
        self.client_words = []
        self.client_key_roles = []

        if self.network_client is not None:

            self.show_screen(
                self.create_team_selection_screen,
                "team_selection",
            )

            self.update_team_selection_lobby()

            return

        self.return_to_menu(None)

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