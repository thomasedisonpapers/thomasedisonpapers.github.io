"""How text becomes searchable terms; web/js/search.js tokenizes the same way, held together by tests/fixtures/tokenize.json."""
from __future__ import annotations

import re
import unicodedata

from stemmer import stem

# One bit per searchable field, so a posting says where a term was found and field searches stay exact.
FIELDS = {"title": 1, "author": 2, "recipient": 4, "names": 8, "subjects": 16, "type": 32, "place": 64,
          "folder": 128, "description": 256, "text": 512, "id": 1024}
# How much a match in each field counts towards the ranking.
WEIGHTS = {"title": 8, "author": 5, "recipient": 5, "names": 4, "subjects": 3, "type": 3, "place": 3, "folder": 2,
           "description": 2, "text": 1, "id": 10}
# Words so common that indexing them would make the biggest index files most of the index; the site reads this list from meta.json.
STOPWORDS = frozenset("""an and are as at be been but by can could did do for from had has have he her his if in is it its
me my no not of on or our she so than that the their them then there these they this those to us was we were which who will
with would you your""".split())
# Word pairs are spread over this many files by a hash of the pair, so no common first word makes one file huge.
PAIR_SHARDS = 512

# Letters that do not decompose into a base letter are spelled out first, so "Straße" and "Ætna" stay searchable.
_SPELLED = str.maketrans({"ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l",
                          "đ": "d", "Đ": "d", "þ": "th", "Þ": "th"})
_WORD = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lower case with accents removed, so "Édison" and "edison" are one word; every mark goes, as JS's \\p{M} does."""
    decomposed = unicodedata.normalize("NFKD", (text or "").translate(_SPELLED))
    return "".join(c for c in decomposed if unicodedata.category(c)[0] != "M").lower()


def tokenize(text: str) -> list[str]:
    """Words of two or more characters, or any number; "&" and punctuation separate words."""
    return [w for w in _WORD.findall(fold(text)) if len(w) > 1 or w.isdigit()]


def fnv1a(text: str) -> int:
    """A 32-bit FNV-1a hash, chosen because it is the same few lines in Python and JS."""
    value = 2166136261
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


def shard_of(term: str) -> str:
    """The index file a term lives in: a word by its first three characters, a word pair by a hash of the pair."""
    if " " in term:
        return f"p{fnv1a(term) % PAIR_SHARDS:03x}"
    return term[:3] if len(term) >= 3 else term + "_" * (3 - len(term))


def phrase_keys(words: list[str]) -> list[str]:
    """What a phrase is looked up by: its neighbouring pairs of uncommon words, stemmed, else those words' stems alone."""
    pairs = [f"{stem(a)} {stem(b)}" for a, b in zip(words, words[1:]) if a not in STOPWORDS and b not in STOPWORDS]
    return pairs or [stem(w) for w in words if w not in STOPWORDS]


def document_terms(fields: dict[str, list[str] | str]) -> dict[str, int]:
    """Every uncommon word's stem and every neighbouring pair of them, each with the mask of the fields it appears in."""
    terms: dict[str, int] = {}
    for name, value in fields.items():
        bit = FIELDS[name]
        values = value if isinstance(value, list) else [value]
        for item in values:
            if name == "id" and item:
                # A DocID such as "X018C9-0915" is also findable whole, as typed.
                whole = fold(item).strip()
                terms[whole] = terms.get(whole, 0) | bit
            words = tokenize(item)
            for word in words:
                if word not in STOPWORDS:
                    key = stem(word)
                    terms[key] = terms.get(key, 0) | bit
            for left, right in zip(words, words[1:]):
                if left in STOPWORDS or right in STOPWORDS:
                    continue
                pair = f"{stem(left)} {stem(right)}"
                terms[pair] = terms.get(pair, 0) | bit
    return terms


def variant_map(synonyms: dict) -> dict[str, list[str]]:
    """The editors' variant list made symmetric: if "lab" finds "laboratory", "laboratory" finds "lab" too."""
    out: dict[str, set[str]] = {}
    for key, variants in synonyms.items():
        if key.startswith("_") or not isinstance(variants, list):
            continue
        group = {" ".join(tokenize(key))} | {" ".join(tokenize(v)) for v in variants}
        group.discard("")
        for word in group:
            out.setdefault(word, set()).update(group - {word})
    return {word: sorted(others) for word, others in sorted(out.items())}
