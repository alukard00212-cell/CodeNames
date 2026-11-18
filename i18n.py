"""Language pack loading (UI strings + word lists) and font setup.

Every language lives in `Data/Lang/<code>.json`:

    {
      "code": "fa",
      "name": "Persian",
      "native_name": "فارسی",
      "rtl": true,
      "strings": { "PLAY": "شروع بازی" },
      "words": ["سیب", "ماه", ...]
    }

English source strings are used as the lookup keys, so a missing
translation simply falls back to the English text instead of crashing.
"""

import json
from pathlib import Path

from kivy.core.text import LabelBase


BASE_DIR = Path(__file__).resolve().parent

LANG_DIR_CANDIDATES = (
    BASE_DIR / "Data" / "Lang",
    BASE_DIR / "Data" / "lang",
    BASE_DIR / "data" / "Lang",
    BASE_DIR / "data" / "lang",
)

WORD_DIR_CANDIDATES = (
    BASE_DIR / "Data" / "Words",
    BASE_DIR / "Data" / "words",
    BASE_DIR / "data" / "Words",
    BASE_DIR / "data" / "words",
)

FONT_DIR_CANDIDATES = (
    BASE_DIR / "Data" / "Fonts",
    BASE_DIR / "Data" / "fonts",
    BASE_DIR / "data" / "Fonts",
    BASE_DIR / "data" / "fonts",
)

FALLBACK_CODE = "en"
MIN_WORDS = 25

_FONTS_INSTALLED = False

_state = {
    "code": FALLBACK_CODE,
    "name": "English",
    "native_name": "English",
    "rtl": False,
    "strings": {},
    "words": [],
    "path": None,
}


def _first_dir(candidates):
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    return candidates[0]


def lang_dir():
    return _first_dir(LANG_DIR_CANDIDATES)


def font_dir():
    return _first_dir(FONT_DIR_CANDIDATES)


def _read_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))

    except (OSError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    return data


def _lang_path(code):
    safe = str(code or FALLBACK_CODE).strip().lower()
    safe = "".join(
        character for character in safe if character.isalnum() or character in "-_"
    ) or FALLBACK_CODE

    return lang_dir() / f"{safe}.json"


def _apply(code, data, path):
    strings = data.get("strings")

    if not isinstance(strings, dict):
        strings = {}

    words = data.get("words")

    if not isinstance(words, list):
        words = []

    _state.update({
        "code": str(data.get("code") or code),
        "name": str(data.get("name") or code),
        "native_name": str(data.get("native_name") or data.get("name") or code),
        "rtl": bool(data.get("rtl", False)),
        "strings": {
            str(key): str(value)
            for key, value in strings.items()
            if value is not None
        },
        "words": [str(word).strip() for word in words if str(word).strip()],
        "path": path,
    })


def install_fonts():
    """Register the bundled font as Kivy's default family.

    Vazirmatn carries both Latin and Arabic glyphs, so one family keeps
    English and Persian looking consistent (and bold stays real bold).
    """

    global _FONTS_INSTALLED

    if _FONTS_INSTALLED:
        return

    folder = font_dir()
    regular = folder / "Vazirmatn-Regular.ttf"
    bold = folder / "Vazirmatn-Bold.ttf"

    if not regular.exists():
        return

    LabelBase.register(
        name="Roboto",
        fn_regular=str(regular),
        fn_bold=str(bold if bold.exists() else regular),
    )

    _FONTS_INSTALLED = True


def available_languages():
    """Every valid pack in Data/Lang, English first."""

    found = {}

    folder = lang_dir()

    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            data = _read_file(path)

            if data is None:
                continue

            code = str(data.get("code") or path.stem)

            found[code] = {
                "code": code,
                "name": str(data.get("name") or code),
                "native_name": str(
                    data.get("native_name") or data.get("name") or code
                ),
                "rtl": bool(data.get("rtl", False)),
            }

    if FALLBACK_CODE not in found:
        found[FALLBACK_CODE] = {
            "code": FALLBACK_CODE,
            "name": "English",
            "native_name": "English",
            "rtl": False,
        }

    ordered = sorted(found.values(), key=lambda entry: (entry["code"] != FALLBACK_CODE, entry["code"]))

    return ordered


def load(code):
    """Load a language pack, falling back to English when needed."""

    requested = str(code or FALLBACK_CODE)
    path = _lang_path(requested)
    data = _read_file(path)

    if data is None and requested != FALLBACK_CODE:
        requested = FALLBACK_CODE
        path = _lang_path(requested)
        data = _read_file(path)

    if data is None:
        _apply(FALLBACK_CODE, {}, None)
        return FALLBACK_CODE

    _apply(requested, data, path)

    return _state["code"]


def code():
    return _state["code"]


def native_name():
    return _state["native_name"]


def display_name():
    return _state["name"]


def is_rtl():
    return bool(_state["rtl"])


def strings():
    return _state["strings"]


def path():
    return _state["path"]


def words():
    return [shape(word) for word in _state["words"]]


def _has_rtl_text(value):
    for character in value:
        code = ord(character)

        if (
            0x0600 <= code <= 0x06FF
            or 0x0750 <= code <= 0x077F
            or 0x08A0 <= code <= 0x08FF
            or 0xFB50 <= code <= 0xFDFF
            or 0xFE70 <= code <= 0xFEFF
        ):
            return True

    return False


def _is_shaped(value):
    return any(
        0xFB50 <= ord(character) <= 0xFDFF
        or 0xFE70 <= ord(character) <= 0xFEFF
        for character in value
    )


def shape(text):
    """Return text in visual order for right-to-left languages.

    Arabic-script letters need contextual shaping and bidirectional
    reordering to display correctly. If the optional dependencies are
    not installed, or the text does not contain Arabic-script
    characters, the original text is returned unchanged.
    """

    if text is None:
        return ""

    value = str(text)

    if not value or not _has_rtl_text(value) or _is_shaped(value):
        return value

    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

    except Exception:
        return value

    try:
        reshaped = arabic_reshaper.reshape(value)
        return get_display(reshaped)

    except Exception:
        return value


_MARKUP_MARKERS = (
    "[b]",
    "[/b]",
    "[i]",
    "[/i]",
    "[u]",
    "[/u]",
    "[color",
    "[/color]",
    "[size",
    "[/size]",
    "[ref",
    "[/ref]",
    "[anchor",
    "[/anchor]",
    "[sub]",
    "[/sub]",
    "[sup]",
    "[/sup]",
)


def _looks_like_markup(text):
    return any(marker in text for marker in _MARKUP_MARKERS)


def t(text, **kwargs):
    """Translate an English source string, then apply `{placeholders}`."""

    if text is None:
        return ""

    template = _state["strings"].get(str(text), text)

    if kwargs:
        try:
            template = template.format(**kwargs)

        except (KeyError, IndexError, ValueError):
            pass

    if _looks_like_markup(template):
        return template

    return shape(template)


def _word_file_words(code_name):
    for folder in WORD_DIR_CANDIDATES:
        path = folder / f"{code_name}.json"

        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))

        except (OSError, ValueError):
            continue

        if isinstance(data, list):
            return [str(word).strip() for word in data if str(word).strip()]

        if isinstance(data, dict) and isinstance(data.get("words"), list):
            return [
                str(word).strip()
                for word in data["words"]
                if str(word).strip()
            ]

    return []


def word_list(code_name=None):
    """Words for a language: Data/Lang first, Data/Words as fallback."""

    if code_name is None or code_name == _state["code"]:
        words_here = _state["words"]

        if words_here:
            return [shape(word) for word in words_here]

        return [shape(word) for word in _word_file_words(_state["code"])]

    path_here = _lang_path(code_name)
    data = _read_file(path_here)

    if data is not None and isinstance(data.get("words"), list):
        found = [str(word).strip() for word in data["words"] if str(word).strip()]

        if found:
            if code_name == _state["code"]:
                return [shape(word) for word in found]

            return found

    found = _word_file_words(code_name)

    if code_name == _state["code"]:
        return [shape(word) for word in found]

    return found


def word_source(code_name=None):
    """Best guess at the file a player should edit to add words."""

    if code_name is None or code_name == _state["code"]:
        if _state["path"] is not None:
            return _state["path"]

    return _lang_path(code_name or _state["code"])
