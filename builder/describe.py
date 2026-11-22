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
# The conditions telegraph companies printed on every form, which transcribers rarely tag.
_FORM_PRINT = re.compile("|".join([
    r"subject to the (?:terms|conditions)[^.]{0,60}(?:printed|on (?:the )?back)", r"conditions limiting its liability",
    r"errors can be guarded against", r"to guard against mistakes", r"unrepeated message", r"message blanks provided",
    r"no inquiry respecting this message", r"filing time as shown", r"^\s*class of service\b", r"deferred character is indicated",
    r"patrons are requested", r"by using postal telegraph", r"quickest,? surest and safest", r"read the notice and agreement",
    r"offices in america", r"cable service to all the world", r"greatest telegraph and cable system",
    r"under the conditions named above", r"^\s*night lettergram\s*[.:]?\s*$", r"unless otherwise indicated by signal"]), re.IGNORECASE)
# Field labels and routing lines of a telegraph form, which carry codes rather than the message.
_FORM_FIELD = re.compile(r"\bno\.? of (?:words|wds)\b|\btime (?:received|rec'?d|filed)\b|\breceived by\b|\bsent by\b|"
                         r"\breceiver'?s no\b|^\s*received at\b|\bcare of\b|\bc/o\b", re.IGNORECASE)
_CAPITALS_ROUTING = re.compile(r"^CARE\b|\bCARE (?:[A-Z]\.? ){1,2}[A-Z]{2,}\b")
_TELEGRAM_TYPE = re.compile(r"\b(?:telegram|cable(?:gram)?)s?\b", re.IGNORECASE)
_TELEGRAPH_MASTHEAD = re.compile(r"\b(?:western union|postal telegraph|commercial cables?|anglo-american telegraph)\b", re.IGNORECASE)
_LATER_PAGE = re.compile(r"\S[\s\S]*?(\n\s*\[page\s+\d+\])", re.IGNORECASE)
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


def _blocks(text: str, *, skip_printed: bool = True, telegram: bool = False) -> list[list[str]]:
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
            if telegram and _FORM_PRINT.search(line):
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


def opening_sentence(text: str, *, telegram: bool = False) -> str:
    """The first sentence of the body: after the salutation when there is one, skipping letterheads, stamps and addresses."""
    text = _line_endings(text)
    telegram = telegram or _is_telegraph_form(text)
    blocks = _blocks(text, telegram=telegram)
    candidates = blocks
    # Only a salutation on the first page opens the letter; a later one belongs to an enclosure or another clipping.
    later_page = _LATER_PAGE.search(text)
    first_page = len(_blocks(text[:later_page.end()], telegram=telegram)) if later_page else len(blocks)
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


def _is_telegraph_form(text: str) -> bool:
    first_lines = _line_endings(text).split("\n", 6)[:6]
    return any(_TELEGRAPH_MASTHEAD.search(line) for line in first_lines) and any(_FORM_PRINT.search(line) for line in first_lines)


def telegram_message(text: str) -> str:
    """The message of a telegram on the first page, past the company's masthead, conditions, codes and address."""
    text = _line_endings(text)
    later = _LATER_PAGE.search(text)
    lines = [line.strip() for line in (text[:later.start(1)] if later else text).split("\n")]

    def usable(line: str) -> bool:
        return bool(_WORD.search(line)) and not _TAG.match(line) and not _FORM_PRINT.search(line) and not _FORM_FIELD.search(line)

    def lowercase_words(line: str) -> int:
        return sum(word[0].islower() for word in _WORD.findall(line))

    prose = [n for n, line in enumerate(lines) if usable(line) and _words(line) >= 3 and lowercase_words(line) >= 2]
    # A message sent in capitals comes after the date line, and has no figures of its own.
    dated = next((n for n, line in enumerate(lines) if re.search(r"1[89]\d\d", line)), None)
    if dated is None:
        dated = next((n for n, line in enumerate(lines) if re.search(r"\d", line)), len(lines))
    capitals = [n for n, line in enumerate(lines) if n > dated and usable(line) and _words(line) >= 6
                and not re.search(r"\d", line) and not _CAPITALS_ROUTING.search(line) and _shouting(line)]
    start = (prose or capitals or [None])[0]
    if start is None:
        return ""
    # A line above that stops mid-sentence, on a lower-case word, is the message's own first line.
    while (start > 0 and usable(lines[start - 1]) and not lines[start - 1].startswith("To ")
           and _WORD.findall(lines[start - 1])[-1][0].islower()):
        start -= 1
    message = [lines[start].rstrip("=")]
    for line in lines[start + 1:]:
        # A short line with no lower-case word after the message is the sender's name.
        if not usable(line) or (_words(line) <= 3 and not lowercase_words(line)):
            break
        message.append(line.rstrip("="))
    joined = re.sub(r"\s+", " ", " ".join(message)).strip()
    # The form's "To" label sometimes runs straight into the message when the addressee was not transcribed.
    joined = re.sub(r"^To (?=[A-Z][a-z])", "", joined)
    return _excerpt(joined, _SENTENCE).rstrip(",;: ")


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
    """Matches the publisher's fallback or opening tagged printed matter."""
    normalized = _plain(current)
    opening = _blocks(_line_endings(text), skip_printed=False)
    if opening:
        first = opening[0]
        if _LETTERHEAD.match(first[0]) or _ADDED.match(first[0]):
            printed = " ".join(_TAG.sub("", line) for line in first)
            if _plain(printed) == normalized:
                return True
    # Replay the publisher's paragraph grouping with the stored line endings.
    blocks = _blocks(text, skip_printed=False)
    paragraphs = [" ".join(" ".join(_TAG.sub("", line) for line in block).split()) for block in blocks]
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
    telegram = bool(_TELEGRAM_TYPE.search(record.get("type") or "")) or _is_telegraph_form(text)
    return (telegram and telegram_message(text)) or opening_sentence(text, telegram=telegram) or current
