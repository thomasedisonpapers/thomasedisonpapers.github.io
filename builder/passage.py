"""Public passage text and its content keys, shared by the encoder and the site builder."""

import hashlib
import re

# About 250 tokens of 1900s English per passage, overlapping so a sentence cut at one edge is whole in the next.
WINDOW_WORDS = 180
STRIDE_WORDS = 144
_DOCID_PREFIX = re.compile(r"^\s*\[[A-Za-z0-9_-]+\],?\s*")


def key_of(text: str) -> str:
    """A passage's identity is its exact text, so unchanged passages are never encoded twice, whichever document moved."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def heading(record: dict) -> str:
    """The context every passage carries, so a passage from page three still says who wrote to whom and when."""
    title = _DOCID_PREFIX.sub("", record.get("title") or "")
    people = " to ".join(p for p in (record.get("author"), record.get("recipient")) if p)
    return ". ".join(p for p in (title, record.get("date") or "", people) if p).strip() + "."


def passages(record: dict) -> list[str]:
    """One catalogue passage (heading, description, people, subjects, places), then overlapping windows of the transcription."""
    head = heading(record)
    names = [*(record.get("names") or []), *(record.get("mentioned") or [])]
    places = [*([record["place"]] if record.get("place") else []), *(record.get("places") or [])]
    parts = [head, record.get("description") or "", f"People: {'; '.join(dict.fromkeys(names))}." if names else "",
             f"Subjects: {'; '.join(record.get('subjects') or [])}." if record.get("subjects") else "",
             f"Places: {'; '.join(dict.fromkeys(places))}." if places else ""]
    out = [" ".join(p for p in parts if p)]
    words = (record.get("transcription") or "").split()
    for start in range(0, len(words), STRIDE_WORDS):
        out.append(f"{head}\n{' '.join(words[start:start + WINDOW_WORDS])}")
        if start + WINDOW_WORDS >= len(words):
            break
    return out
