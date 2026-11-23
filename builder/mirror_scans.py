"""Copies each published account-book scan from edisondigital into the dataset, gently and resumably, so the site never depends on it."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import random
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from harvest import BOT_CHALLENGE_PATH, MAX_ATTEMPTS, MAX_WAIT_SECONDS, MIN_DELAY_SECONDS, OUTAGE_HOST, TIMEOUT_SECONDS, Outage, \
    _retry_after

logger = logging.getLogger("mirror")

SCAN_HOSTS = {"edisondigital.rutgers.edu", "iiif.archive.org"}
THUMB_WIDTH = 400
MAX_BYTES = 40 * 1024 * 1024
STATE = ".mirror-state/scans.json"
# A scan edisondigital would not give is asked for again a day later, not at the head of every run.
RETRY_FAILED_SECONDS = 24 * 3600
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# JPEG segments that carry camera, location and editing metadata rather than the picture (as the publisher strips them).
_METADATA_MARKERS = {0xE1, 0xED}


def strip_jpeg_metadata(content: bytes) -> bytes:
    """The scan without its EXIF and Photoshop segments, byte for byte otherwise (lifted from edison-pipeline public_export)."""
    if content[:2] != b"\xff\xd8":
        return content
    out = bytearray(b"\xff\xd8")
    i = 2
    while i + 4 <= len(content) and content[i] == 0xFF:
        marker = content[i + 1]
        if marker == 0xDA:
            out += content[i:]
            return bytes(out)
        length = int.from_bytes(content[i + 2:i + 4], "big")
        if marker not in _METADATA_MARKERS:
            out += content[i:i + 2 + length]
        i += 2 + length
    return content


def thumbnail(full: bytes) -> bytes:
    with Image.open(io.BytesIO(full)) as image:
        image = image.convert("RGB")
        if image.width > THUMB_WIDTH:
            image = image.resize((THUMB_WIDTH, max(1, round(image.height * THUMB_WIDTH / image.width))), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=80, optimize=True, progressive=True)
        return out.getvalue()


class Fetcher:
    """One polite connection: a scan at a time, a pause between them, and a stop at the first sign of an outage or a gate."""

    def __init__(self, contact: str, session=None, sleep=time.sleep, delay: float = MIN_DELAY_SECONDS):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"EdisonPapersArchiveMirror/1.0 (+{contact})"
        self.sleep, self.delay, self.requests = sleep, max(MIN_DELAY_SECONDS, delay), 0

    def get(self, url: str, remaining=None) -> bytes | None:
        """The scan's bytes; None when this one scan cannot be had, and Outage when the whole site should be left alone.

        `remaining()` gives the run's seconds left, so a struggling site never holds the publish past its budget.
        """
        def wait(seconds: float) -> None:
            if remaining is not None and remaining() <= seconds:
                raise Outage("edisondigital is struggling and this run's time is spent")
            self.sleep(seconds)

        for attempt in range(MAX_ATTEMPTS):
            if self.requests:
                self.sleep(self.delay)
            self.requests += 1
            try:
                response = self.session.get(url, timeout=TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                logger.warning("request failed (%s); backing off", exc)
                wait(min(MAX_WAIT_SECONDS, 30 * 2 ** attempt) + random.uniform(0, 5))
                continue
            urls = [getattr(r, "url", "") or "" for r in [*getattr(response, "history", []), response]]
            if any(OUTAGE_HOST in u or BOT_CHALLENGE_PATH in u for u in urls):
                raise Outage("edisondigital is showing its outage or bot-challenge page")
            if response.status_code in (403, 429) or response.status_code >= 500:
                pause = _retry_after(response.headers.get("Retry-After"), attempt)
                logger.warning("%s answered %s; waiting %.0fs", urlparse(url).hostname, response.status_code, pause)
                wait(pause + random.uniform(0, 5))
                continue
            if response.status_code != 200:
                logger.warning("%s answered %s; skipped this run", url, response.status_code)
                return None
            data = response.content
            if not response.headers.get("Content-Type", "").startswith("image/jpeg") or data[:2] != b"\xff\xd8" or len(data) > MAX_BYTES:
                logger.warning("%s did not send a JPEG scan; skipped this run", url)
                return None
            return data
        raise Outage(f"{urlparse(url).hostname} kept failing after {MAX_ATTEMPTS} attempts")


def published_openings(dataset: Path) -> list[tuple[str, int, str]]:
    """(volume, scan number, source) for every published opening, each volume's first scan first so every book soon has a cover."""
    manifest_path = dataset / "volumes" / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text("utf-8"))
    withdrawn = set(manifest.get("withdrawn", []))
    firsts, rest = [], []
    for volume_id, entry in sorted(manifest.get("volumes", {}).items()):
        if volume_id in withdrawn or entry.get("collection") == "notebooks":
            continue
        if not _SAFE_ID.fullmatch(volume_id):
            raise ValueError(f"unsafe volume id {volume_id!r}")
        volume = json.loads((dataset / "volumes" / volume_id / "volume.json").read_text("utf-8"))
        if volume.get("collection") == "notebooks":
            continue
        for opening in sorted(volume.get("openings", []), key=lambda o: o["n"]):
            if opening.get("iiif"):
                # archive.org serves this scan at any size itself, so the site links to it and nothing is copied.
                continue
            source, n = opening.get("source") or "", opening.get("n")
            parsed = urlparse(source)
            if not isinstance(n, int) or not 1 <= n <= 9999 or parsed.scheme != "https" or parsed.hostname not in SCAN_HOSTS:
                continue
            (firsts if not firsts or firsts[-1][0] != volume_id else rest).append((volume_id, n, source))
    return firsts + rest


def scan_paths(dataset: Path, volume_id: str, n: int) -> dict[str, Path]:
    return {size: dataset / "scans" / volume_id / size / f"{n:04d}.jpg" for size in ("full", "thumb")}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)


def remove_unpublished(dataset: Path, openings: list[tuple[str, int, str]]) -> int:
    """Takes away scans of volumes or openings no longer published, so the upload never offers them."""
    wanted = {(v, n) for v, n, _ in openings}
    volumes = {v for v, _ in wanted}
    removed = 0
    root = dataset / "scans"
    if not root.is_dir():
        return 0
    # A write cut short leaves a .part file, which the upload would refuse as a stray.
    for leftover in root.rglob("*.part"):
        leftover.unlink()
        removed += 1
    for directory in root.iterdir():
        if directory.is_dir() and directory.name not in volumes:
            shutil.rmtree(directory)
            removed += 1
            continue
        for path in directory.glob("*/*.jpg"):
            if not path.stem.isdigit() or (directory.name, int(path.stem)) not in wanted:
                path.unlink()
                removed += 1
    return removed


def _is_jpeg(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(2) == b"\xff\xd8"


def mirror(dataset: Path, fetcher: Fetcher, *, minutes: float = 10, limit: int | None = None, clock=time.monotonic,
           now=time.time) -> dict:
    """Mirrors scans not yet copied (or whose source changed) until the time or count budget is spent, saving progress as it goes."""
    state_path = dataset / STATE
    try:
        state = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
    except ValueError:
        state = {}
    if not (dataset / "volumes" / "manifest.json").exists():
        return {"copied": 0, "skipped": 0, "removed": 0, "mirrored": 0, "openings": 0, "outage": ""}
    openings = published_openings(dataset)
    removed = remove_unpublished(dataset, openings)
    deadline = clock() + minutes * 60
    copied, skipped, outage = 0, 0, ""
    todo = []
    current = {}
    for volume_id, n, source in openings:
        key = f"{volume_id}/{n}"
        paths = scan_paths(dataset, volume_id, n)
        previous = state.get(key, {})
        whole = all(path.is_file() for path in paths.values())
        if previous.get("source") == source:
            if previous.get("sha256") and whole:
                current[key] = previous
                continue
            if previous.get("failed") and now() - previous["failed"] < RETRY_FAILED_SECONDS:
                current[key] = previous
                continue
        elif not previous and whole and _is_jpeg(paths["full"]):
            # A scan already on disk is kept when the record of it was lost, rather than deleted and fetched again.
            current[key] = {"source": source, "sha256": hashlib.sha256(paths["full"].read_bytes()).hexdigest()}
            continue
        # An old scan must stop being served even when the new source cannot be fetched this run.
        for path in paths.values():
            if path.is_file():
                path.unlink()
                removed += 1
        todo.append((volume_id, n, source))
    try:
        for volume_id, n, source in todo:
            if clock() >= deadline or (limit is not None and copied >= limit):
                break
            key = f"{volume_id}/{n}"
            data = fetcher.get(source, remaining=lambda: deadline - clock())
            thumb = None
            if data is not None:
                full = strip_jpeg_metadata(data)
                try:
                    thumb = thumbnail(full)
                except (OSError, ValueError, Image.DecompressionBombError):
                    logger.warning("cannot read scan for %s, opening %s", volume_id, n)
            if thumb is None:
                current[key] = {"source": source, "failed": now()}
                skipped += 1
                continue
            paths = scan_paths(dataset, volume_id, n)
            _write(paths["full"], full)
            _write(paths["thumb"], thumb)
            current[key] = {"source": source, "sha256": hashlib.sha256(full).hexdigest()}
            copied += 1
            if copied % 25 == 0:
                _write(state_path, json.dumps(current, sort_keys=True).encode("utf-8"))
    except Outage as exc:
        outage = str(exc)
        logger.warning("stopped: %s", exc)
    _write(state_path, json.dumps(current, sort_keys=True).encode("utf-8"))
    done = sum(1 for entry in current.values() if entry.get("sha256"))
    result = {"copied": copied, "skipped": skipped, "removed": removed, "mirrored": done, "openings": len(openings), "outage": outage}
    logger.info("mirror: %s", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="The publisher's output folder, holding volumes/")
    parser.add_argument("--contact", required=True, help="A web page or address for the User-Agent, so edisondigital knows who is asking")
    parser.add_argument("--minutes", type=float, default=10, help="Stop starting new scans after this long")
    parser.add_argument("--limit", type=int, help="Stop after copying this many scans")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mirror(args.dataset, Fetcher(args.contact), minutes=args.minutes, limit=args.limit)
    # An outage is a normal pause, not a failure: the next run resumes where this one stopped.
    return 0


if __name__ == "__main__":
    sys.exit(main())
