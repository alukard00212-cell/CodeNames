import json
import random
from pathlib import Path


# The project ships its word data in `Data/Words`. Android file systems are
# case-sensitive, so never rely on the desktop's case-insensitive lookup:
# anchor everything to this module's directory and try the known spellings.
BASE_DIR = Path(__file__).resolve().parent


class WordLibrary:

    def __init__(self, language="en"):
        self.language = language
        self.words = self.load_words()

    def _candidate_paths(self):
        names = ("Data/Words", "Data/words", "data/Words", "data/words")

        return [BASE_DIR / name / f"{self.language}.json" for name in names]

    def load_words(self):
        file_path = next(
            (path for path in self._candidate_paths() if path.exists()),
            None,
        )

        if file_path is None:
            tried = ", ".join(str(path) for path in self._candidate_paths())

            raise FileNotFoundError(
                f"Word library not found. Tried: {tried}"
            )

        with open(file_path, "r", encoding="utf-8") as file:
            words = json.load(file)

        if not isinstance(words, list):
            raise ValueError("Word library must contain a list of words.")

        return words

    def get_random_words(self, amount=25):
        if len(self.words) < amount:
            raise ValueError(
                f"Not enough words in {self.language} library."
            )

        return random.sample(self.words, amount)
