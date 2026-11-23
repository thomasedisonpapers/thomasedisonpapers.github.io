"""The Porter stemmer (1980), so "laboratory", "laboratories" and "laboratory's" are one word; web/js/stemmer.js is its twin."""
from __future__ import annotations

from functools import lru_cache

_VOWELS = set("aeiou")


def _consonant(word: str, i: int) -> bool:
    if word[i] in _VOWELS:
        return False
    if word[i] == "y":
        return i == 0 or not _consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """How many vowel-then-consonant sequences the stem holds: Porter's m."""
    count, i, n = 0, 0, len(stem)
    while i < n and _consonant(stem, i):
        i += 1
    while i < n:
        while i < n and not _consonant(stem, i):
            i += 1
        if i >= n:
            break
        count += 1
        while i < n and _consonant(stem, i):
            i += 1
    return count


def _has_vowel(stem: str) -> bool:
    return any(not _consonant(stem, i) for i in range(len(stem)))


def _double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _consonant(word, len(word) - 1)


def _cvc(word: str) -> bool:
    """Consonant, vowel, consonant at the end, the last not w, x or y: Porter's *o."""
    return (len(word) >= 3 and _consonant(word, len(word) - 1) and not _consonant(word, len(word) - 2)
            and _consonant(word, len(word) - 3) and word[-1] not in "wxy")


def _replace(word: str, rules: list[tuple[str, str]], minimum: int) -> str:
    for suffix, replacement in rules:
        if word.endswith(suffix):
            stem = word[: len(word) - len(suffix)]
            return stem + replacement if _measure(stem) > minimum else word
    return word


_STEP2 = [("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"), ("izer", "ize"), ("abli", "able"),
          ("alli", "al"), ("entli", "ent"), ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"), ("ator", "ate"),
          ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"),
          ("biliti", "ble")]
_STEP3 = [("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"), ("ful", ""), ("ness", "")]
_STEP4 = ["al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion", "ou", "ism", "ate", "iti",
          "ous", "ive", "ize"]
# Longest suffix first, so "ement" is tried before "ment" and "ent", as Porter's rule of the longest match requires.
_STEP2.sort(key=lambda rule: -len(rule[0]))
_STEP3.sort(key=lambda rule: -len(rule[0]))
_STEP4.sort(key=len, reverse=True)


# A ledger repeats the same few hundred words thousands of times, and stemming each again took most of the build.
@lru_cache(maxsize=200_000)
def stem(word: str) -> str:
    """A word's stem; numbers, identifiers and words of two letters or fewer are left as they are."""
    if len(word) <= 2 or not word.isalpha() or not word.isascii():
        return word
    # Step 1a: plurals.
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-2]
    elif word.endswith("ss"):
        pass
    elif word.endswith("s"):
        word = word[:-1]
    # Step 1b: -eed, -ed, -ing.
    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
    else:
        trimmed = None
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and _has_vowel(word[: -len(suffix)]):
                trimmed = word[: -len(suffix)]
                break
        if trimmed is not None:
            word = trimmed
            if word.endswith(("at", "bl", "iz")):
                word += "e"
            elif _double_consonant(word) and word[-1] not in "lsz":
                word = word[:-1]
            elif _measure(word) == 1 and _cvc(word):
                word += "e"
    # Step 1c: y to i.
    if word.endswith("y") and _has_vowel(word[:-1]):
        word = word[:-1] + "i"
    word = _replace(word, _STEP2, 0)
    word = _replace(word, _STEP3, 0)
    # Step 4: remove a suffix when the stem is long enough; -ion only after s or t.
    for suffix in _STEP4:
        if word.endswith(suffix):
            stem_part = word[: len(word) - len(suffix)]
            if _measure(stem_part) > 1 and (suffix != "ion" or stem_part.endswith(("s", "t"))):
                word = stem_part
            break
    # Step 5a: a final e.
    if word.endswith("e"):
        stem_part = word[:-1]
        if _measure(stem_part) > 1 or (_measure(stem_part) == 1 and not _cvc(stem_part)):
            word = stem_part
    # Step 5b: a final double l.
    if _measure(word) > 1 and _double_consonant(word) and word.endswith("l"):
        word = word[:-1]
    return word
