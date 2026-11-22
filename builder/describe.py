"""A document's one-line description, drawn from the letter itself rather than its letterhead, address block or stamps."""
from __future__ import annotations

import re

# A letterhead says nothing about what the document is about, and runs to the next blank line.
_LETTERHEAD = re.compile(r"^\s*\[letterhead\]", re.IGNORECASE)
# Stamps, notations and signatures are single added lines.
_ADDED = re.compile(r"^\s*\[(?:stamp|file notation|signatures?|signed[^\]]*|marginal note|handwritten note[^\]]*)\]", re.IGNORECASE)
_TAG = re.compile(r"^\s*\[[^\]]*\]\s*")
_PAGE = re.compile(r"^\[page\s+\d+\]$", re.IGNORECASE)
_SALUTATION = re.compile(r"^(?:my\s+)?(?:dear|gentlemen|gents|sirs?|madam|dear\s+friend)\b[^:,\n]{0,40}(?::-?|,|—|\.-)\s*", re.IGNORECASE)
_WORD = re.compile(r"[^\s]*\w[^\s]*")
# The publisher's own rule, kept exactly so a description it generated can be recognised.
_PUBLISHER_SENTENCE =re.compile(r"^(.{40,240}?(?<!\b[A-Z])(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bCo)(?<!\bSt)(?<!\bInc)[.!?])(?:\s|$)",
                                 re.DOTALL)
# The publisher's rule, which also lets a sentence run past a month or title abbreviation such as "Jan." or "Esq.".
_SENTENCE =re.compile(r"^(.{40,240}?(?<!\b[A-Z])(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bCo)(?<!\bSt)(?<!\bInc)(?<!\bJan)(?<!\bFeb)(?<!\bMar)"
                       r"(?<!\bApr)(?<!\bAug)(?<!\bSep)(?<!\bSept)(?<!\bOct)(?<!\bNov)(?<!\bDec)(?<!\bNo)(?<!\bEsq)(?<!\bJr)(?<!\bSr)"
                       r"(?<!\bMessrs)(?<!\bProf)(?<!\bRev)(?<!\bGen)[.!?])(?:\s|$)", re.DOTALL)
PARAGRAPH_MIN_WORDS = 25


def _words(text: str) -> int:
    return len(_WORD.findall(text))


def _blocks(text: str, *, skip_printed: bool = True) -> list[list[str]]:
    """Blank-line-separated blocks as the reader lays them out, with printed matter left out up to the next blank line."""
    out = []
    for chunk in re.split(r"\n[ \t]*\n+", text or ""):
        run: list[str] = []
        for line in chunk.split("\n"):
            if skip_printed and _LETTERHEAD.match(line):
                break
            if _PAGE.match(line.strip()):
                if run:
                    out.append(run)
                    run = []
                continue
            # A stamp as long as a paragraph is a pasted notice, which is content.
            if skip_printed and _ADDED.match(line) and _words(line) < PARAGRAPH_MIN_WORDS:
                continue
            if _words(line) >= PARAGRAPH_MIN_WORDS:
                if run:
                    out.append(run)
                out.append([line])
                run = []
            elif line.strip():
                run.append(line)
        if run:
            out.append(run)
    return out


def _prose(lines: list[str], minimum: int) -> str:
    """The block as one line when it reads as prose, else an empty string."""
    lines = [_TAG.sub("", line).strip() for line in lines]
    lines = [line for line in lines if line]
    joined = re.sub(r"\s+", " ", " ".join(lines)).strip()
    words = _words(joined)
    # Short lines that all begin with a capital are a heading or an address; prose carries on in lower case or runs long.
    stacked = len(lines) > 1 and words < 8 * len(lines) and not any(line[0].islower() for line in lines)
    if words < minimum or stacked or _shouting(joined):
        return ""
    return joined


def opening_sentence(text: str) -> str:
    """The first sentence of the body: after the salutation when there is one, skipping letterheads, stamps and addresses."""
    text = _line_endings(text)
    blocks = _blocks(text)
    candidates = blocks
    # Only a salutation on the first page opens the letter; a later one belongs to an enclosure or another clipping.
    later_page = re.search(r"\S[\s\S]*?\n\s*\[page\s+\d+\]", text or "", re.IGNORECASE)
    first_page = len(_blocks(text[:later_page.end()])) if later_page else len(blocks)
    found = next(((n, k) for n, block in enumerate(blocks[:first_page]) for k, line in enumerate(block)
                  if _SALUTATION.match(_TAG.sub("", line).strip())), None)
    if found:
        n, k = found
        line = _TAG.sub("", blocks[n][k]).strip()
        after = [line[_SALUTATION.match(line).end():], *blocks[n][k + 1:]]
        candidates = ([after] if any(part.strip() for part in after) else []) + blocks[n + 1:]
    for minimum in (15, 6):
        for block in candidates:
            line = _prose(block, minimum)
            # A block that opens in lower case carries on a sentence from above, so it is a last resort.
            if line and (minimum < 15 or not line[0].islower()):
                chosen = _excerpt(line, _SENTENCE)
                if not _shouting(chosen):
                    return chosen
    return ""


def _shouting(text: str) -> bool:
    """Whether the text is mostly capitals, as printed headings and table rules are."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) > 0.6 * len(letters)


def _plain(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def _line_endings(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _excerpt(line: str, sentence_pattern: re.Pattern) -> str:
    sentence = sentence_pattern.match(line)
    return sentence.group(1) if sentence else (line[:237].rstrip() + "…" if len(line) > 240 else line)


def _generated_description(current: str, text: str) -> bool:
    """Matches the publisher's fallback or a tagged block of printed matter."""
    blocks = _blocks(_line_endings(text), skip_printed=False)
    normalized = _plain(current)
    paragraphs = [" ".join(" ".join(_TAG.sub("", line) for line in block).split()) for block in blocks]
    if any((_LETTERHEAD.match(block[0]) or _ADDED.match(block[0])) and _plain(line) == normalized
           for block, line in zip(blocks, paragraphs)):
        return True
    for minimum in (15, 6):
        for line in paragraphs:
            if len(line.split()) >= minimum and not _shouting(line):
                return _plain(_excerpt(line, _PUBLISHER_SENTENCE)) == normalized
    return False


def describe(record: dict) -> str:
    """The record's description, redrawn from the body when the publisher took it from the transcription; abstracts and notes stay."""
    current = record.get("description") or ""
    text = record.get("transcription") or ""
    notes = re.sub(r"<[^>]+>", "", record.get("editors_notes") or "").strip()
    if not text or (current and (record.get("source") == "edisondigital" or _plain(current) == _plain(notes)
                                 or not _generated_description(current, text))):
        return current
    return opening_sentence(text) or current
