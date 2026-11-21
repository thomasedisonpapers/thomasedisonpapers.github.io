"""Mirrors edisondigital's catalogue of documents (metadata, transcriptions, scan addresses) into the dataset, gently and resumably."""
from __future__ import annotations

import argparse
import email.utils
import html as html_module
import json
import logging
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger("harvest")

BASE = "https://edisondigital.rutgers.edu"
PER_PAGE = 100
# Each request re-reads this many items already seen, so a deletion before the cursor cannot make the sweep skip one.
OVERLAP = 5
MIN_DELAY_SECONDS = 1.0
MAX_WAIT_SECONDS = 900
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5
# Template 1 is the archive's documents; people, organisations and folders are other templates and are not documents.
DOCUMENT_TEMPLATE = 1
# Lifted from edison-pipeline notebooks.py: Rutgers' outage page and the site's crawler gate are never data.
OUTAGE_HOST = "sorry.rutgers.edu"
BOT_CHALLENGE_PATH = "/bot-challenge"
PLACEHOLDER = "/res/placeholder"
_ARCHIVAL_FOLDER = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")
_ID_PREFIX = re.compile(r"^\s*\[(?=[^\]\s]*\d)[^\]\s]+\]\s*,?\s*")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MARKUP = re.compile(r"<(br|p|div|span|em|strong|i|b)\b", re.IGNORECASE)


class Outage(Exception):
    """edisondigital is down or gating crawlers; the run stops and resumes later from its checkpoint."""


class HarvestRefused(Exception):
    """The harvest looks wrong, so the last good snapshot stays live."""


def unescape(text: str) -> str:
    """The catalogue stores some quotes escaped (\\' and \\"), which the pipeline strips everywhere it reads them."""
    return str(text).replace('\\"', '"').replace("\\'", "'")


def plain(text: str) -> str:
    """Stored HTML as the reader sees it (lifted from edison-pipeline notebooks._plain)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("&nbsp;", " ")
    text = re.sub(r'"{2,}', '"', text)
    text = html_module.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _values(item: dict, term: str) -> list[str]:
    out = []
    for value in item.get(term) or []:
        if not isinstance(value, dict):
            continue
        text = str(value.get("@value") or value.get("o:label") or value.get("display_title") or value.get("@id") or "")
        text = plain(text) if value.get("type") == "html" or _MARKUP.search(text) else unescape(text).strip()
        if text:
            out.append(text)
    return out


def _first(item: dict, term: str) -> str:
    values = _values(item, term)
    return values[0] if values else ""


def _retry_after(value: str | None, attempt: int) -> float:
    """Seconds to wait, from a Retry-After of seconds or an HTTP date, capped so one answer cannot stall the job."""
    if value:
        if value.strip().isdigit():
            return min(MAX_WAIT_SECONDS, float(value))
        try:
            when = email.utils.parsedate_to_datetime(value)
            return min(MAX_WAIT_SECONDS, max(0.0, (when - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            pass
    return min(MAX_WAIT_SECONDS, 30 * 2 ** attempt)


class Client:
    """One polite connection to edisondigital: a request at a time, a pause between them, backing off when told to."""

    def __init__(self, contact: str, session=None, sleep=time.sleep, delay: float = MIN_DELAY_SECONDS):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"EdisonPapersArchiveHarvester/1.0 (+{contact})"
        self.sleep = sleep
        self.delay = max(MIN_DELAY_SECONDS, delay)
        self.requests = 0

    def get(self, path: str, params: dict):
        """The parsed JSON list and the reported total; an outage or a gate raises Outage instead of returning data."""
        for attempt in range(MAX_ATTEMPTS):
            if self.requests:
                self.sleep(self.delay)
            self.requests += 1
            try:
                response = self.session.get(BASE + path, params=params, timeout=TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                logger.warning("request failed (%s); backing off", exc)
                self.sleep(min(MAX_WAIT_SECONDS, 30 * 2 ** attempt) + random.uniform(0, 5))
                continue
            urls = [getattr(r, "url", "") or "" for r in [*getattr(response, "history", []), response]]
            if any(OUTAGE_HOST in url for url in urls) or any(BOT_CHALLENGE_PATH in url for url in urls):
                raise Outage("edisondigital is showing its outage or bot-challenge page")
            if response.status_code in (403, 429) or response.status_code >= 500:
                # A firewall's 403 and a 429 mean "not now", the same as a server error: wait, then stop for the day if it persists.
                pause = _retry_after(response.headers.get("Retry-After"), attempt)
                logger.warning("edisondigital answered %s; waiting %.0fs", response.status_code, pause)
                self.sleep(pause + random.uniform(0, 5))
                continue
            if response.status_code != 200:
                raise HarvestRefused(f"{path} answered {response.status_code}")
            if "json" not in response.headers.get("Content-Type", ""):
                raise Outage(f"{path} answered with {response.headers.get('Content-Type')!r}, not JSON")
            try:
                rows = response.json()
            except ValueError as exc:
                raise Outage(f"{path} sent a body that is not valid JSON") from exc
            total = response.headers.get("Omeka-S-Total-Results")
            if not isinstance(rows, list) or total is None or not str(total).isdigit():
                raise Outage(f"{path} answered without a list or a total-results header")
            return rows, int(total)
        raise Outage(f"{path} kept failing after {MAX_ATTEMPTS} attempts")


def page_entry(position: int, media: dict) -> dict | None:
    """A page numbered by its place among the item's media, so a skipped placeholder does not renumber the rest."""
    original = media.get("original") or ""
    if not original or PLACEHOLDER in original:
        return None
    return {"n": position, "width": None, "height": None, "thumb": media.get("medium") or media.get("large") or original,
            "view": media.get("large") or original, "full": original}


def item_record(item: dict, media_by_id: dict) -> dict | None:
    """One catalogue document in the public record's shape, or None when it has no usable identifier."""
    identifier = _first(item, "dcterms:identifier")
    if not _SAFE_ID.match(identifier or ""):
        return None
    folder_raw = _first(item, "dcterms:isPartOf")
    match = _ARCHIVAL_FOLDER.match(folder_raw)
    folder = {"id": match.group(1).strip(), "title": match.group(2).strip()} if match else {"id": "", "title": folder_raw}
    if folder["id"] and not _SAFE_ID.match(folder["id"]):
        folder = {"id": "", "title": folder_raw}
    transcription = "\n\n".join(_values(item, "scripto:transcription"))
    references = item.get("o:media") or []
    pages = [entry for position, reference in enumerate(references, start=1)
             if (entry := page_entry(position, media_by_id.get(reference.get("o:id"), {})))]
    modified = item.get("o:modified") or item.get("o:created") or {}
    has_format = _first(item, "dcterms:hasFormat")
    return {
        "schema": 1, "id": identifier, "workflow": "edisondigital", "source": "edisondigital", "relation": "edisondigital",
        "folder": folder, "status": "edition" if transcription.strip() else "none",
        "status_label": "Transcription from the Edison Papers digital edition" if transcription.strip() else "Not yet transcribed",
        "title": _ID_PREFIX.sub("", unescape(item.get("o:title") or "")).strip() or identifier,
        "type": _first(item, "dcterms:type"), "date": _first(item, "dcterms:date"), "author": "; ".join(_values(item, "dcterms:creator")),
        "recipient": "; ".join(_values(item, "bibo:recipient")), "place": _first(item, "dcterms:spatial"),
        "mentioned": _values(item, "dcterms:relation"), "subjects": _values(item, "dcterms:subject"), "names": [], "places": [],
        "marginal_notes": [], "microfilm": "", "description": _first(item, "dcterms:abstract"), "editors_notes": "",
        "transcription": transcription, "pages": pages, "pdf": None,
        "edisondigital": {"document": f"{BASE}/document/{identifier}", "manifest": f"{BASE}/iiif/2/{identifier}/manifest",
                          "item": f"{BASE}/api/items/{item.get('o:id')}",
                          # Scans can live outside Omeka's media (notebooks on archive.org); this says where to look.
                          "scans_elsewhere": bool(references) is False, "has_format": has_format},
        "updated": modified.get("@value", "") if isinstance(modified, dict) else "",
    }


class LocalMirror:
    """Keeps the checkpoint on this machine only; a CI run swaps in HubMirror so a new runner can resume."""

    def pull(self, work: Path) -> None:
        return None

    def push(self, work: Path, paths: list[Path]) -> None:
        return None


class HubMirror:
    """Copies each checkpoint file to the Hugging Face dataset as it is written, so the 6-hour job limit costs nothing."""

    def __init__(self, repo: str, token: str | None = None, prefix: str = "edisondigital/work"):
        from huggingface_hub import HfApi
        self.api = HfApi(token=token)
        self.repo = repo
        self.prefix = prefix

    def pull(self, work: Path) -> None:
        from huggingface_hub import snapshot_download
        snapshot_download(self.repo, repo_type="dataset", allow_patterns=[f"{self.prefix}/**"], local_dir=work.parent.parent,
                          token=self.api.token)

    def push(self, work: Path, paths: list[Path]) -> None:
        for path in paths:
            self.api.upload_file(path_or_fileobj=str(path), repo_id=self.repo, repo_type="dataset",
                                 path_in_repo=f"{self.prefix}/{path.relative_to(work).as_posix()}")


class Harvest:
    """A sweep of documents then media, one checkpoint file per page, so an outage or a killed job costs one page at most."""

    def __init__(self, client: Client, dataset: Path, mirror=None, accept_shrink: bool = False):
        self.client = client
        self.root = dataset / "edisondigital"
        self.work = self.root / "work"
        self.state_path = self.work / "state.json"
        self.mirror = mirror or LocalMirror()
        self.accept_shrink = accept_shrink

    def _write(self, path: Path, value) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        temporary.write_text(json.dumps(value, ensure_ascii=False), "utf-8")
        temporary.replace(path)
        return path

    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text("utf-8"))
        return {"started": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), "items": {"seen": 0, "last_id": 0, "page": 0,
                "done": False, "total": None}, "media": {"seen": 0, "last_id": 0, "page": 0, "done": False, "total": None}}

    def _sweep(self, state: dict, kind: str, path: str, extra: dict) -> None:
        """Pages through `path` in id order with overlapping windows, stepping back whenever deletions shifted the list."""
        progress = state[kind]
        while not progress["done"]:
            offset = max(0, progress["seen"] - OVERLAP)
            rows, total = self.client.get(path, {**extra, "sort_by": "id", "sort_order": "asc", "offset": offset,
                                                 "limit": PER_PAGE + OVERLAP})
            progress["total"] = total
            ids = [row.get("o:id") for row in rows if isinstance(row, dict)]
            if progress["last_id"] and ids and ids[0] > progress["last_id"]:
                # The last item we kept is no longer in the window: earlier items were deleted, so step back and read again.
                progress["seen"] = max(0, progress["seen"] - PER_PAGE)
                self._save(state)
                if progress["seen"] == 0 and offset == 0:
                    raise HarvestRefused(f"{path} lost its place at the start of the list")
                continue
            fresh = [row for row in rows if isinstance(row, dict) and row.get("o:id", 0) > progress["last_id"]]
            if rows and not fresh and len(rows) >= PER_PAGE:
                raise Outage(f"{path} returned the same items again; the server may be ignoring the offset")
            if fresh:
                progress["page"] += 1
                written = self._write(self.work / kind / f"{progress['page']:06d}.json", fresh)
                progress["last_id"] = fresh[-1]["o:id"]
                progress["seen"] += len(fresh)
                self.mirror.push(self.work, [written])
            progress["done"] = len(rows) < PER_PAGE + OVERLAP
            self._save(state)
            logger.info("%s: %d kept of %s", kind, progress["seen"], total)

    def _save(self, state: dict) -> None:
        self.mirror.push(self.work, [self._write(self.state_path, state)])

    def run(self) -> dict:
        """Harvests, validates and publishes a snapshot; resumes from the checkpoint if one exists."""
        self.mirror.pull(self.work)
        self.work.mkdir(parents=True, exist_ok=True)
        state = self._state()
        self._save(state)
        self._sweep(state, "items", "/api/items", {"resource_template_id": DOCUMENT_TEMPLATE})
        self._sweep(state, "media", "/api/media", {})
        try:
            return self.publish(state)
        except HarvestRefused:
            # A refused sweep is kept for inspection but moved aside, so the next run starts afresh instead of re-refusing it.
            quarantine = self.root / "quarantine" / state["started"]
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self.work), str(quarantine))
            raise

    def _files(self, kind: str):
        for path in sorted((self.work / kind).glob("*.json")):
            yield from json.loads(path.read_text("utf-8"))

    def publish(self, state: dict) -> dict:
        # Only the three addresses of each page are kept, not every media object, so a million scans fit in memory.
        media_by_id = {}
        media_rows = 0
        for media in self._files("media"):
            media_rows += 1
            sizes = media.get("o:thumbnail_urls") or {}
            media_by_id[media.get("o:id")] = {"original": media.get("o:original_url") or "", "large": sizes.get("large") or "",
                                              "medium": sizes.get("medium") or ""}
        records, seen, skipped, duplicates = [], set(), 0, 0
        for item in self._files("items"):
            record = item_record(item, media_by_id)
            if record is None:
                skipped += 1
                continue
            if record["id"] in seen:
                duplicates += 1
                continue
            seen.add(record["id"])
            records.append(record)
        self.validate(state, records, skipped, media_rows)
        snapshot = state["started"]
        directory = self.root / snapshot
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "records.jsonl").open("w", encoding="utf-8") as out:
            for record in records:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
        summary = {"snapshot": snapshot, "records": len(records), "skipped": skipped, "duplicates": duplicates,
                   "items_total": state["items"]["total"], "media_total": state["media"]["total"], "media_rows": media_rows,
                   "with_transcription": sum(1 for r in records if r["status"] == "edition"),
                   "with_pages": sum(1 for r in records if r["pages"]), "published_at": datetime.now(timezone.utc).isoformat()}
        (directory / "meta.json").write_text(json.dumps(summary, indent=1), "utf-8")
        # The pointer moves last and atomically, so a reader sees the old snapshot or the new one, never half of one.
        pointer = self.root / "current.json"
        temporary = pointer.with_suffix(".part")
        temporary.write_text(json.dumps(summary, indent=1), "utf-8")
        temporary.replace(pointer)
        shutil.rmtree(self.work, ignore_errors=True)
        logger.info("published snapshot %s with %d records", snapshot, len(records))
        return summary

    def validate(self, state: dict, records: list, skipped: int, media_rows: int) -> None:
        total = state["items"]["total"] or 0
        if not total or not records:
            raise HarvestRefused("the site reported no documents, or none could be read")
        harvested = len(records) + skipped
        # Missing documents are the danger; a few extra are documents deleted during the sweep, gone again next week.
        if total - harvested > max(5, 0.005 * total) or harvested - total > max(10, 0.02 * total):
            raise HarvestRefused(f"harvested {harvested} documents but the site reports {total}")
        if skipped > 0.01 * total:
            raise HarvestRefused(f"{skipped} documents had no usable identifier")
        media_total = state["media"]["total"] or 0
        if media_total and abs(media_rows - media_total) > max(5, 0.005 * media_total):
            raise HarvestRefused(f"harvested {media_rows} media but the site reports {media_total}")
        with_pages = sum(1 for r in records if r["pages"])
        if not with_pages:
            raise HarvestRefused("no document has a single scan, so the media sweep went wrong")
        pointer = self.root / "current.json"
        if pointer.exists() and not self.accept_shrink:
            previous = json.loads(pointer.read_text("utf-8"))
            if len(records) < 0.98 * previous["records"]:
                raise HarvestRefused(f"only {len(records)} records, down from {previous['records']}")
            for key, now in (("with_transcription", sum(1 for r in records if r["status"] == "edition")), ("with_pages", with_pages)):
                if previous.get(key) and now < 0.95 * previous[key]:
                    raise HarvestRefused(f"{key} fell from {previous[key]} to {now}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--contact", required=True, help="A URL or address for edisondigital's operators, sent in the User-Agent")
    parser.add_argument("--delay", type=float, default=MIN_DELAY_SECONDS, help="Seconds between requests (at least 1)")
    parser.add_argument("--hf-repo", default="", help="Hugging Face dataset that keeps the checkpoint between runs")
    parser.add_argument("--accept-shrink", action="store_true", help="Publish even though documents disappeared since the last snapshot")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mirror = HubMirror(args.hf_repo) if args.hf_repo else None
    try:
        Harvest(Client(args.contact, delay=args.delay), Path(args.dataset), mirror, args.accept_shrink).run()
    except Outage as exc:
        logger.warning("stopped for now, will resume from the checkpoint: %s", exc)
        return 75
    except HarvestRefused as exc:
        logger.error("harvest refused; the last good snapshot stays live: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
