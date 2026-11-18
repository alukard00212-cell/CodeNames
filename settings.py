"""Small JSON settings store for the app (language + player name).

Android's APK assets are read-only, so user settings must live in the
app's private data directory (`App.user_data_dir`). On desktop that maps
to the normal per-user config folder.
"""

import json
import os
from pathlib import Path


DEFAULTS = {
    "language": "en",
    "player_name": "",
}

NAME_MAX_LENGTH = 24


def clean_player_name(value, fallback="Player"):
    """Trim a name and clamp it to the length the lobby can show."""

    text = " ".join(str(value or "").split())
    text = text[:NAME_MAX_LENGTH]

    return text or fallback


class Settings:

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else Path.home() / ".codenames"
        self.path = self.folder / "settings.json"
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))

        except (OSError, ValueError):
            return

        if not isinstance(raw, dict):
            return

        for key in DEFAULTS:
            if key in raw:
                self.data[key] = raw[key]

    def save(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)

            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

        except OSError:
            # A read-only or full disk should never crash the game.
            pass

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        if self.data.get(key) == value:
            return

        self.data[key] = value
        self.save()

    @property
    def player_name(self):
        return clean_player_name(self.data.get("player_name"))

    @property
    def language(self):
        return str(self.data.get("language") or DEFAULTS["language"])


def settings_for_app(app):
    """Create a Settings object inside the app's private data folder."""

    folder = None

    if app is not None:
        try:
            folder = app.user_data_dir

        except Exception:
            folder = None

    return Settings(folder)
