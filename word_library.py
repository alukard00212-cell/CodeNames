import random

import i18n


# The word lists live next to the language packs in `Data/Lang/<code>.json`
# (older builds used `Data/Words`; i18n still reads those as a fallback).
# Android file systems are case-sensitive, so the lookup lives in i18n.py
# where every known spelling is tried.
class WordLibrary:

    def __init__(self, language=None):
        self.language = language or i18n.code()
        self.words = self.load_words()

    def load_words(self):
        words = i18n.word_list(self.language)

        if not words:
            raise FileNotFoundError(
                f"Word library is empty. Add words to {self.source} "
                f"(at least {i18n.MIN_WORDS})."
            )

        if len(words) < i18n.MIN_WORDS:
            raise ValueError(
                f"{self.source} has only {len(words)} words. "
                f"Add {i18n.MIN_WORDS - len(words)} more "
                f"(at least {i18n.MIN_WORDS} are needed for one board)."
            )

        return words

    @property
    def source(self):
        return str(i18n.word_source(self.language))

    def get_random_words(self, amount=25):
        if len(self.words) < amount:
            raise ValueError(
                f"Not enough words in {self.source}: "
                f"{len(self.words)} available, {amount} needed."
            )

        return random.sample(self.words, amount)
