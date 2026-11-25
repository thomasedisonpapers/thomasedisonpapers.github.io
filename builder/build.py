"""Merges our published documents with the edisondigital mirror and writes everything the static site reads."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath

from PIL import Image

import describe
import textindex
from passage import key_of, passages

logger = logging.getLogger("build")

SCHEMA = 1
# A page of results is spread across the years, so small files keep it to a few hundred kilobytes with 90,000 entries.
SUMMARIES_PER_FILE = 200
THUMB_WIDTH = 240
STATUSES = ["approved", "draft", "edition", "none"]
# Fields our record fills first and edisondigital fills where ours is empty.
MERGED_FIELDS = ("title", "type", "date", "author", "recipient", "place", "mentioned", "subjects", "names", "places",
                 "description", "microfilm")
_YEAR = re.compile(r"\b(1[89]\d\d)\b")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class BuildError(Exception):
    """A build that would publish something wrong; the live site is left as it was."""


def read_json(path: Path):
    return json.loads(path.read_text("utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), "utf-8")
    temporary.replace(path)


def safe_id(value: str) -> str:
    if not _SAFE_ID.match(value or ""):
        raise BuildError(f"unsafe id {value!r}")
    return value


def year_of(date: str):
    match = _YEAR.search(date or "")
    return int(match.group(1)) if match else None


def load_local(dataset: Path) -> tuple[dict, dict]:
    """Our published records, with page and PDF paths made relative to the dataset root; withdrawn ones left out."""
    manifest_path = dataset / "local" / "manifest.json"
    if not manifest_path.exists():
        return {}, {}
    manifest = read_json(manifest_path)
    withdrawn = set(manifest.get("withdrawn", []))
    records = {}
    for folder in manifest.get("folders", {}).values():
        for document_id in folder.get("documents", {}):
            if document_id in withdrawn:
                continue
            safe_id(document_id)
            record = read_json(dataset / "local" / document_id / "record.json")
            if record.get("id") != document_id:
                raise BuildError(f"local/{document_id}/record.json says it is {record.get('id')!r}")
            for page in record.get("pages", []):
                for size in ("thumb", "view", "full"):
                    page[size] = f"local/{document_id}/{page[size]}"
            if record.get("pdf"):
                record["pdf"] = f"local/{document_id}/{record['pdf']}"
            records[document_id] = record
    return records, manifest


ACCOUNT_TYPE = "Account book"
NOTEBOOK_TYPE = "Notebook"
# Each kind of bound volume, by the collection the publisher names it with: (record kind, document type).
VOLUME_KINDS = {"accounts": ("account", ACCOUNT_TYPE), "notebooks": ("notebook", NOTEBOOK_TYPE)}
SCAN_KINDS = frozenset(kind for kind, _ in VOLUME_KINDS.values())
_IIIF = re.compile(r"^https://iiif\.archive\.org/[^\s\\]+$")
# The marks the drafting prompt uses for a ledger's ticks and crossings, which are not words a reader searches for.
_LEDGER_MARKS = re.compile(r"\[(?:tick|posting cross|cancelled|unclear)\]", re.IGNORECASE)
_READING = re.compile(r"\[(?:unclear|struck out|insertion|margin):\s*([^\]]*)\]", re.IGNORECASE)
# Amounts and folio numbers fill a ledger but are never searched for, and indexing them tripled the index; years are kept.
_FIGURE = re.compile(r"(?<![\w.])\d+(?:[.,/]\d+)*(?![\w])")


def load_volumes(dataset: Path, *, collections: set[str] | None = None) -> dict[str, dict]:
    """The published account books and notebooks by ID, withdrawn ones left out, or nothing before the first publish."""
    manifest_path = dataset / "volumes" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = read_json(manifest_path)
    withdrawn = set(manifest.get("withdrawn", []))
    volumes = {}
    for volume_id, entry in sorted(manifest.get("volumes", {}).items()):
        if volume_id in withdrawn:
            continue
        if collections is not None and entry.get("collection") and entry["collection"] not in collections:
            continue
        safe_id(volume_id)
        volume = read_json(dataset / "volumes" / volume_id / "volume.json")
        if volume.get("id") != volume_id:
            raise BuildError(f"volumes/{volume_id}/volume.json says it is {volume.get('id')!r}")
        if collections is None or (volume.get("collection") or "accounts") in collections:
            volumes[volume_id] = volume
    return volumes


def _plain_marks(text: str) -> str:
    return " ".join(_LEDGER_MARKS.sub(" ", _READING.sub(r"\1", text or "")).split())


def opening_text(pages: list[dict], *, keep_figures: bool = False) -> str:
    """An opening's words in reading order (lines, each table's caption and rows, drawings), as search and meaning read them."""
    def keep_year(match: re.Match) -> str:
        value = match[0]
        return value if len(value) == 4 and value.startswith(("18", "19")) and value.isdecimal() else " "

    lines = _opening_lines(pages)
    if not keep_figures:
        lines = [" ".join(_FIGURE.sub(keep_year, line).split()) for line in lines]
    return "\n".join(line for line in lines if line)


def _opening_lines(pages: list[dict]) -> list[str]:
    out = []
    for page in pages or []:
        out += [_plain_marks(line) for line in page.get("lines") or []]
        for table in page.get("tables") or []:
            out.append(_plain_marks(table.get("caption", "")))
            out += [_plain_marks(" ".join(cell for cell in row if cell)) for row in table.get("rows") or []]
        for drawing in page.get("drawings") or []:
            out.append(_plain_marks(" ".join([drawing.get("summary", ""), *drawing.get("labels", [])])))
    return out


def opening_description(pages: list[dict]) -> str:
    """What an opening shows at a glance: the first account heading, else its first line of words."""
    captions = [_plain_marks(t.get("caption", "")) for p in pages or [] for t in p.get("tables") or []]
    lines = [_plain_marks(line) for p in pages or [] for line in p.get("lines") or []]
    for text in [*captions, *lines]:
        if len(re.findall(r"[A-Za-z]{2,}", text)) >= 2:
            return text if len(text) <= 160 else text[:157].rstrip() + "…"
    return ""


_ARCHIVE_ITEM = re.compile(r"^https://iiif\.archive\.org/image/iiif/3/(taepnotebook-[A-Za-z0-9_-]+)%2F")


def bookreader_page(iiif: str, n: int) -> str | None:
    """archive.org's book-viewer image of scan `n`, less its size: page index n-1 is the manifest's canvas n-1, as our scans are."""
    match = _ARCHIVE_ITEM.match(iiif or "")
    return f"https://archive.org/download/{match.group(1)}/page/n{n - 1}" if match and isinstance(n, int) and n >= 1 else None


def opening_records(volumes: dict[str, dict], has_file=lambda path: False) -> dict[str, dict]:
    """Each account-book opening as a record the search, topics and meaning index read like any document's."""
    records = {}
    for volume_id, volume in volumes.items():
        years = volume.get("years") or []
        kind, kind_type = VOLUME_KINDS.get(volume.get("collection"), VOLUME_KINDS["accounts"])
        for opening in volume.get("openings") or []:
            n, opening_id = opening["n"], safe_id(opening["id"])
            if opening_id in records:
                raise BuildError(f"duplicate account-book opening ID {opening_id}")
            pages = opening.get("pages") or []
            thumb, full = f"scans/{volume_id}/thumb/{n:04d}.jpg", f"scans/{volume_id}/full/{n:04d}.jpg"
            iiif = opening.get("iiif") if _IIIF.fullmatch(str(opening.get("iiif") or "")) else None
            if iiif:
                # A notebook scan is linked from archive.org, never copied: its book viewer's page images, which answered
                # in about a second on 23 November while the IIIF server timed out on many first requests.
                page_base = bookreader_page(iiif, n)
                scan_pages = [{"n": 1, "width": opening.get("width"), "height": opening.get("height"),
                               "thumb": f"{page_base}_w400.jpg" if page_base else f"{iiif}/full/400,/0/default.jpg",
                               "view": f"{page_base}_w1600.jpg" if page_base else f"{iiif}/full/1600,/0/default.jpg",
                               "full": f"{iiif}/full/1600,/0/default.jpg"}]
            else:
                scan_pages = ([{"n": 1, "width": opening.get("width"), "height": opening.get("height"), "thumb": thumb, "view": full, "full": full}]
                              if has_file(thumb) and has_file(full) else [])
            records[opening_id] = {
                "id": opening_id, "kind": kind, "volume": volume_id, "scan": n,
                "title": f"{volume.get('title') or volume_id}, scan {n}", "type": kind_type,
                "date": volume.get("date", ""), "year": years[0] if years else None,
                "status": opening.get("status", "draft"), "folder": {"id": volume_id, "title": volume.get("title") or volume_id},
                "subjects": list(volume.get("subjects") or []),
                "names": list(dict.fromkeys(p for page in pages for p in page.get("people") or [])),
                "places": list(dict.fromkeys(p for page in pages for p in page.get("places") or [])),
                "description": opening_description(pages),
                "transcription": opening_text(pages, keep_figures=kind == "notebook") or _plain_marks(opening.get("latex", "")),
                # A mirrored scan is shown from the mirror only once it is there; until then the reader falls back to edisondigital.
                "pages": scan_pages,
            }
    return records


def add_openings(records: dict, openings: dict) -> None:
    duplicates = records.keys() & openings.keys()
    if duplicates:
        raise BuildError(f"account-book and notebook openings share IDs with documents: {', '.join(sorted(duplicates)[:10])}")
    records.update(openings)


def number_ranges(numbers: list[int]) -> str:
    """Scan numbers as "1-10,15", so the reader asks the mirror only for scans it has, in a few bytes per book."""
    out, numbers = [], sorted(set(numbers))
    for n in numbers:
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ",".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def volume_entry(volume: dict, members: list[dict], summaries_of: dict[str, dict], has_file) -> dict:
    """One account book or notebook as the collections page lists it: title, dates, subjects, counts and a cover from its first scan."""
    volume_id = volume["id"]
    cover = next((summaries_of[m["id"]] for m in members if summaries_of[m["id"]]["thumb"]), None)
    pdf = f"volumes/{volume_id}/{volume['pdf']}" if volume.get("pdf") else None
    # Only scans copied to the data host count as mirrored; a notebook's scans are linked from archive.org instead.
    mirrored = [m["scan"] for m in members if m.get("pages") and str(m["pages"][0].get("view", "")).startswith("scans/")]
    return {"mirrored": number_ranges(mirrored), "id": volume_id, "collection": volume.get("collection") or "accounts", "title": volume.get("title") or volume_id, "date": volume.get("date", ""), "years": volume.get("years") or [],
            "subjects": volume.get("subjects") or [], "series": volume.get("series") or [], "description": volume.get("description", ""),
            "count": len(members), "approved": sum(1 for m in members if m.get("status") == "approved"),
            "status": volume.get("status", "draft"), "pdf": pdf if pdf and has_file(pdf) else None,
            "thumb": cover and cover["thumb"], "thumb_from": cover and cover["thumb_from"], "ratio": cover and cover["ratio"]}


# Related documents come from the strongest model whose results are present, so a better model takes over once run.
RELATED_MODELS = ("qwen3-embedding-0.6b", "arctic-embed-s")
RELATED_COUNT = 6


def load_related(dataset: Path) -> dict[str, list[str]]:
    """Each document's nearest documents by meaning as `embed.py related` wrote them, or nothing before the first embedding run."""
    related = {}
    for model in RELATED_MODELS:
        path = dataset / "embeddings" / model / "related.json"
        if not path.exists():
            continue
        raw = read_json(path)
        if not isinstance(raw, dict):
            raise BuildError(f"{path} is not a map of documents to related documents")
        for doc, near in raw.items():
            if isinstance(near, list):
                candidates = [pair[0] for pair in near if isinstance(pair, list) and pair and isinstance(pair[0], str)]
                if candidates:
                    related.setdefault(doc, candidates)
    return related


def related_ordinals(document_id: str, related: dict[str, list[str]], ordinal_of_id: dict[str, int]) -> list[int]:
    """A document's related documents that are in this build, by ordinal; itself and anything withdrawn or unpublished are left out."""
    out: list[int] = []
    for other in related.get(document_id, []):
        ordinal = ordinal_of_id.get(other)
        if other != document_id and ordinal is not None and ordinal not in out:
            out.append(ordinal)
        if len(out) == RELATED_COUNT:
            break
    return out


# The model visitors' browsers run for search by meaning; its revision must match the host's encoder (checked by a test).
SEARCH_MODEL = {"name": "arctic-embed-s", "hf": "Snowflake/snowflake-arctic-embed-s", "revision": "e596f507467533e48a2e17c007f0e1dacc837b33",
                "dtype": "q8", "dim": 384, "query_prefix": "Represent this sentence for searching relevant passages: "}
_PART_NAME = re.compile(r"^part-\d{5}$")


def write_vectors(dataset: Path, target: Path, records: dict, ordinal_of_id: dict[str, int]) -> int:
    """Writes vectors that still match published passages, with their document ordinals."""
    import numpy as np

    root = dataset / "embeddings" / SEARCH_MODEL["name"]
    if not (root / "manifest.json").exists() or not (root / "documents.jsonl").exists():
        return 0
    keys, blocks = [], []
    for part in read_json(root / "manifest.json").get("parts", []):
        name = str(part.get("name", ""))
        if not _PART_NAME.match(name):
            raise BuildError(f"unexpected embedding part name {name!r}")
        part_keys = (root / "parts" / f"{name}.keys").read_text(encoding="utf-8").split()
        vectors = np.load(root / "parts" / f"{name}.npy", allow_pickle=False)
        if (not isinstance(vectors, np.ndarray) or vectors.ndim != 2 or len(part_keys) != len(vectors)
                or vectors.shape[1] != SEARCH_MODEL["dim"] or not np.isfinite(vectors).all()):
            raise BuildError(f"embedding part {name} has {vectors.shape} vectors for {len(part_keys)} keys")
        keys += part_keys
        blocks.append(vectors)
    row_of = {key: n for n, key in enumerate(keys)}
    if len(row_of) != len(keys):
        raise BuildError("the semantic index contains duplicate passage vectors")
    rows, owners = [], []
    with (root / "documents.jsonl").open(encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            document = json.loads(line)
            document_id = document.get("id")
            ordinal = ordinal_of_id.get(document_id)
            if ordinal is None:
                continue
            current = {key_of(text) for text in passages(records[document_id])}
            for key in document.get("keys", []):
                if key not in row_of:
                    raise BuildError(f"{document_id} refers to a missing semantic passage vector")
                if key in current:
                    rows.append(row_of[key])
                    owners.append(ordinal)
    if not rows:
        return 0
    matrix = np.concatenate(blocks)[rows].astype(np.float32)
    target.mkdir(parents=True, exist_ok=True)
    base = {**{k: SEARCH_MODEL[k] for k in ("hf", "revision", "dtype", "dim", "query_prefix")},
            "model": SEARCH_MODEL["name"], "count": len(rows), "scale": 127}
    owners = np.asarray(owners, dtype="<u4")
    if len(rows) <= VECTOR_FLAT_LIMIT:
        # Unit vectors fit in a signed byte at a scale of 127, a quarter the size of 32-bit floats with no change to the ranking that matters.
        (target / "vectors.i8").write_bytes(_bytes_of(matrix))
        (target / "ordinals.u32").write_bytes(owners.tobytes())
        write_json(target / "meta.json", {**base, "layout": "flat", "files": {"vectors": "vectors.i8", "ordinals": "ordinals.u32"}})
        return len(rows)
    assign, means = cluster_vectors(matrix, size=CLUSTER_SIZE)
    # Clusters are chosen by their centre's direction (on all 201,184 passages this found 87% of the top 40 for 6 MB,
    # against 82% by average similarity); each centre's length turns that back into the average for the cutoff.
    norms = np.linalg.norm(means, axis=1)
    (target / "centroids.i8").write_bytes(_bytes_of(means / np.maximum(norms, 1e-9)[:, None]))
    counts = []
    cluster_owners = []
    for cluster in range(len(means)):
        members = np.flatnonzero(assign == cluster)
        counts.append(len(members))
        # One file per cluster: its passages' vectors, then the document ordinal of each, so one request brings both.
        path = target / "clusters" / f"{cluster:05d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        ordinals = owners[members].tobytes()
        path.write_bytes(_bytes_of(matrix[members]) + ordinals)
        cluster_owners.append(ordinals)
    # A filtered search needs to know which clusters contain its documents before it downloads their vectors.
    (target / "cluster-ordinals.u32").write_bytes(b"".join(cluster_owners))
    write_json(target / "meta.json", {**base, "layout": "ivf", "clusters": counts, "probe": {"vectors": PROBE_VECTORS},
                                      "centre_norms": [round(float(n), 4) for n in norms],
                                      "files": {"centroids": "centroids.i8", "clusters": "clusters/", "ordinals": "cluster-ordinals.u32"}})
    return len(rows)


# Above this many passages, one file of every vector is too much to download, so they are split into clusters.
VECTOR_FLAT_LIMIT = 20_000
# About 200 passages a cluster, and the clusters nearest a query until this many passages are read: on 24 November, with
# every notebook encoded (201,184 passages), this found 85% of the single file's top 10 and 87% of its top 40 for 6 MB.
CLUSTER_SIZE = 200
PROBE_VECTORS = 16_000


def _bytes_of(vectors) -> bytes:
    import numpy as np

    return np.clip(np.rint(vectors * 127), -127, 127).astype(np.int8).tobytes()


def cluster_vectors(matrix, *, size: int = CLUSTER_SIZE, iterations: int = 20, seed: int = 0):
    """Spherical k-means with a fixed seed, so an unchanged set of vectors always gives the same files: each row's cluster and each cluster's mean."""
    import numpy as np

    k = max(1, round(len(matrix) / size))
    rng = np.random.default_rng(seed)
    centres = matrix[rng.choice(len(matrix), k, replace=False)].copy()

    def nearest(centres):
        return np.concatenate([np.argmax(matrix[s:s + 16384] @ centres.T, axis=1) for s in range(0, len(matrix), 16384)])

    def cluster_means(assign):
        sums = np.zeros_like(centres)
        np.add.at(sums, assign, matrix)
        counts = np.bincount(assign, minlength=k)
        return sums / np.maximum(counts, 1).astype(np.float32)[:, None], counts

    for _ in range(iterations):
        assign = nearest(centres)
        means, counts = cluster_means(assign)
        norms = np.maximum(np.linalg.norm(means, axis=1), 1e-9)
        occupied = counts > 0
        updated = means / norms[:, None]
        if np.array_equal(centres[occupied], updated[occupied]):
            break
        centres[occupied] = updated[occupied]
    assign = nearest(centres)
    # The stored centre is the plain mean, whose dot product with a query is its cluster's average similarity.
    means, _ = cluster_means(assign)
    return assign, means


def load_edisondigital(dataset: Path) -> dict:
    """The current validated snapshot of the edisondigital mirror, one record per line, or nothing before the first harvest."""
    pointer = dataset / "edisondigital" / "current.json"
    if not pointer.exists():
        return {}
    snapshot = read_json(pointer)["snapshot"]
    safe_id(snapshot)
    records = {}
    with (dataset / "edisondigital" / snapshot / "records.jsonl").open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                record = json.loads(line)
                records[safe_id(record["id"])] = record
    return records


def merge(local: dict, remote: dict) -> tuple[dict, list[str]]:
    """One record per DocID: ours first, field by field and file by file, edisondigital where we have nothing."""
    merged, conflicts = {}, []
    for document_id in sorted(set(local) | set(remote)):
        ours, theirs = local.get(document_id), remote.get(document_id)
        if ours and theirs:
            if ours.get("relation") != "supplements-edisondigital":
                # A new document whose DocID is already a different item on edisondigital must never be merged into it.
                conflicts.append(document_id)
                continue
            record = dict(ours)
            for name in MERGED_FIELDS:
                if not record.get(name) and theirs.get(name):
                    record[name] = theirs[name]
            # An approved transcription of ours wins; the edition's beats our unreviewed draft or nothing.
            if ours.get("status") in ("draft", "none") and theirs.get("status") == "edition":
                record["transcription"] = theirs.get("transcription", "")
                record["status"] = "edition"
                record["status_label"] = theirs.get("status_label", "")
                # Our PDF prints our draft, so it no longer matches the text shown.
                record["pdf"] = None
            if not record.get("pages"):
                record["pages"] = theirs.get("pages", [])
            record["edisondigital"] = theirs.get("edisondigital") or ours.get("edisondigital")
            record["source"] = "local+edisondigital"
        else:
            record = dict(ours or theirs)
        record["year"] = year_of(record.get("date", ""))
        merged[document_id] = record
    return merged, conflicts


def description_for(record: dict, harvested: dict) -> str:
    """Resolve the description shared by the public catalogue and its embeddings."""
    original = harvested.get("description")
    if original and record.get("description") == original:
        return original
    return describe.describe(record)


def document_terms(record: dict) -> dict[str, int]:
    folder = record.get("folder") or {}
    return textindex.document_terms({
        "id": record["id"], "title": record.get("title", ""), "author": record.get("author", ""),
        "recipient": record.get("recipient", ""), "names": list(record.get("names") or []) + list(record.get("mentioned") or []),
        "subjects": list(record.get("subjects") or []), "type": record.get("type", ""),
        "place": [record.get("place", "")] + list(record.get("places") or []),
        "folder": [folder.get("id", ""), folder.get("title", "")], "description": record.get("description", ""),
        "text": record.get("transcription", ""),
    })


def make_thumb(dataset: Path, record: dict, site_data: Path) -> str | None:
    """A small WebP of the first page, served from the site itself so result lists never wait on the data host."""
    pages = record.get("pages") or []
    if not pages or str(pages[0].get("thumb", "")).startswith("http"):
        return None
    source = dataset / pages[0]["thumb"]
    if not source.exists():
        return None
    target = site_data / "thumbs" / f"{record['id']}.webp"
    if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            image.convert("RGB").save(target, "WEBP", quality=72, method=6)
    return f"data/thumbs/{record['id']}.webp"


def summary(record: dict, thumb: str | None) -> dict:
    """What a result row needs, and nothing more: the full record is fetched only when a document is opened."""
    folder = record.get("folder") or {}
    first = (record.get("pages") or [{}])[0]
    return {"id": record["id"], "title": record.get("title") or record["id"], "date": record.get("date", ""),
            "year": record.get("year"), "type": record.get("type", ""), "author": record.get("author", ""),
            "recipient": record.get("recipient", ""), "folder": folder.get("id", ""), "folder_title": folder.get("title", ""),
            "status": record.get("status") if record.get("status") in STATUSES else "none", "description": record.get("description", ""),
            "thumb": thumb or first.get("thumb"), "thumb_from": "site" if thumb else ("web" if str(first.get("thumb", "")).startswith("http") else "dataset"),
            "ratio": round((first.get("height") or 4) / (first.get("width") or 3), 3), "pages": len(record.get("pages") or []),
            # An account-book opening opens in the account reader at its scan rather than as a document.
            **({"kind": record["kind"], "volume": record["volume"], "scan": record["scan"]} if record.get("kind") in SCAN_KINDS else {})}


def shared_scans(records: dict) -> list[dict]:
    """Groups of documents that show the same page scan, with whether each group's documents show exactly the same pages."""
    owners: dict[str, set[str]] = defaultdict(set)
    for record in records.values():
        for page in record.get("pages") or []:
            if page.get("sha256"):
                owners[page["sha256"]].add(record["id"])
    # Documents joined through any shared page form one group, so a page range that swallows several neighbours is reported once.
    parent: dict[str, str] = {}

    def root(document_id: str) -> str:
        while parent.setdefault(document_id, document_id) != document_id:
            parent[document_id] = parent[parent[document_id]]
            document_id = parent[document_id]
        return document_id

    for ids in owners.values():
        first, *rest = sorted(ids)
        for other in rest:
            parent[root(other)] = root(first)
    groups: dict[str, list[str]] = defaultdict(list)
    for document_id in parent:
        groups[root(document_id)].append(document_id)
    out = []
    for members in sorted(groups.values(), key=min):
        if len(members) < 2:
            continue
        signatures = {tuple(p.get("sha256") for p in records[m].get("pages") or []) for m in members}
        out.append({"documents": sorted(members), "identical": len(signatures) == 1 and all(next(iter(signatures)))})
    return out


def report_shared_scans(groups: list[dict]) -> None:
    """Warns about documents sharing a scan, which the build cannot fix: their page ranges are set in the review platform."""
    if not groups:
        return
    count = sum(len(g["documents"]) for g in groups)
    logger.warning("%d documents in %d groups share a page scan with another document; check their page ranges in the review platform",
                   count, len(groups))
    lines = [f"{', '.join(g['documents'])}{' (the same pages)' if g['identical'] else ''}" for g in groups]
    for line in lines:
        logger.warning("shared scan: %s", line)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        # The run's summary page is where an editor will see this, not the log.
        try:
            with open(summary_file, "a", encoding="utf-8") as handle:
                handle.write(f"### {count} documents share a page scan\n\nCheck these page ranges in the review platform.\n\n"
                             + "".join(f"- {line}\n" for line in lines) + "\n")
        except OSError as exc:
            logger.warning("could not write the shared scan job summary: %s", exc)


def folder_description(records: list[dict]) -> str:
    """A plain account of what the folder holds, for folders the archive has not described."""
    return f"{len(records)} document{'s' if len(records) != 1 else ''}{_holdings(records)}."


def topic_description(title: str, records: list[dict]) -> str:
    """What a generated topic holds, naming the editors' heading it comes from."""
    return f"Documents the editors filed under “{title}”{_holdings(records)}."


def _holdings(records: list[dict]) -> str:
    years = sorted(y for y in (r.get("year") for r in records) if y)
    span = f", {years[0]}–{years[-1]}" if years and years[0] != years[-1] else (f", {years[0]}" if years else "")
    # A bracketed type such as "[Unedited]" is a cataloguing state, not a kind of document.
    kinds = [plural_kind(kind) for kind, _ in Counter(r.get("type") for r in records
                                                       if r.get("type") and not r["type"].startswith("[")).most_common(3)]
    listed = f", mostly {', '.join(kinds[:-1])}{' and ' if len(kinds) > 1 else ''}{kinds[-1]}" if kinds else ""
    return f"{span}{listed}"


def plural_kind(kind: str) -> str:
    """A document type in the plural, as "mostly letters and minutes" reads."""
    kind = kind.lower()
    words = kind.split() or [kind]
    # "Letters to the editor" is plural already, in its first word.
    if words[-1].endswith("s") or (len(words) > 1 and words[0].endswith("s")):
        return kind
    if words[-1].endswith(("x", "ch", "sh")):
        return kind + "es"
    last = words[-1]
    if last.endswith("y") and last[-2:-1] not in "aeiou":
        return kind[:-1] + "ies"
    return kind + "s"


TOPIC_MIN_DOCUMENTS = 10
_TOPIC_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def heading_key(text: str) -> tuple[str, ...]:
    """A subject heading as stemmed words, so capitals, plurals and entities never split one heading in two."""
    return tuple(textindex.stem(word) for word in textindex.tokenize(html.unescape(text or "")))


def _clean_heading(text: str) -> str:
    return " ".join(html.unescape(text or "").split()).strip()


def _names_subject(heading: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    return bool(phrase) and any(heading[i:i + len(phrase)] == phrase for i in range(len(heading) - len(phrase) + 1))


def _subject_headings(record: dict):
    split = False
    for subject in record.get("subjects") or []:
        text = _clean_heading(subject)
        # A semicolon in an HTML entity can leave multiple pieces of one heading in the export.
        continues = subject.rstrip().lower().endswith("&amp")
        piece, split = split or continues, continues
        if not piece and (key := heading_key(text)):
            yield key, text


def subject_index(ordered: list[dict]) -> dict[tuple[str, ...], list[int]]:
    index = defaultdict(list)
    for ordinal, record in enumerate(ordered):
        for key in {key for key, _ in _subject_headings(record)}:
            index[key].append(ordinal)
    return index


def topic_documents(topic: dict, subjects: dict[tuple[str, ...], list[int]], terms_by_doc: list[dict[str, int]],
                    variants: dict[str, list[str]]) -> list[int]:
    """The ordinals of a topic's documents: by the editors' subject headings when it names them, else by its words."""
    if not topic.get("subjects"):
        return [n for n, terms in enumerate(terms_by_doc) if topic_matches(topic, terms, variants)]
    phrases = {heading_key(s) for s in topic["subjects"]}
    if topic.get("generated"):
        return sorted({n for phrase in phrases for n in subjects.get(phrase, [])})
    return sorted({n for heading, ordinals in subjects.items() if any(_names_subject(heading, p) for p in phrases)
                   for n in ordinals})


def generated_topics(ordered: list[dict], curated: list[dict], minimum: int | None = None) -> list[dict]:
    """A topic for every subject heading the editors gave at least `minimum` documents, unless a curated topic names it."""
    minimum = TOPIC_MIN_DOCUMENTS if minimum is None else minimum
    taken = {heading_key(s) for topic in curated for s in topic.get("subjects", [])}
    counts, spellings = Counter(), defaultdict(Counter)
    for record in ordered:
        keys = set()
        for key, text in _subject_headings(record):
            keys.add(key)
            spellings[key][text] += 1
        counts.update(keys)
    out, ids = [], {topic["id"] for topic in curated if isinstance(topic.get("id"), str)}
    for key in sorted(counts, key=lambda k: " ".join(k)):
        if counts[key] < minimum or key in taken:
            continue
        title = max(spellings[key].items(), key=lambda item: (item[1], item[0]))[0]
        slug = re.sub(r"[^a-z0-9]+", "-", textindex.fold(title)).strip("-") or "subject"
        prefix = f"subject-{slug}"
        topic_id = prefix[:80].rstrip("-")
        number = 1
        while topic_id in ids:
            number += 1
            suffix = f"-{number}"
            topic_id = prefix[:80 - len(suffix)].rstrip("-") + suffix
        ids.add(topic_id)
        out.append({"id": topic_id, "title": title, "subjects": [title], "generated": True})
    return out


def topic_matches(topic: dict, terms: dict[str, int], variants: dict[str, list[str]] | None = None) -> bool:
    mask = 0
    for name in topic.get("fields") or []:
        mask |= textindex.FIELDS[name]
    for phrase in topic.get("any", []):
        # The topic page searches each word with its variants (phonograph also finds phono), so the count must too.
        words = textindex.tokenize(phrase)
        for wording in [words, *(textindex.tokenize(v) for v in (variants or {}).get(" ".join(words), []))]:
            # The same rule as the site's phrase search, common words left out.
            keys = textindex.phrase_keys(wording)
            found = mask or ~0
            for key in keys:
                found &= terms.get(key, 0)
            if keys and found:
                return True
    return False


ASSET_DIRS = ("css", "js", "fonts", "img", "vendor")
ASSET_REVISION = re.compile(r"[0-9a-f]{12}")


def copy_site(web: Path, out: Path) -> str:
    """Copies the site, with scripts, styles and fonts under a folder named for their content, so no browser keeps an old copy."""
    digest = hashlib.sha256()
    for name in ASSET_DIRS:
        for path in sorted((web / name).rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(web)).encode())
                digest.update(path.read_bytes())
    revision = digest.hexdigest()[:12]
    out.mkdir(parents=True, exist_ok=True)
    assets = out / "assets"
    if assets.exists():
        # The previous revision stays one build longer, for pages already open when the new one lands.
        for old in sorted(assets.iterdir(), key=lambda p: p.stat().st_mtime)[:-1]:
            if old.name != revision:
                shutil.rmtree(old)
    for name in ASSET_DIRS:
        if (web / name).is_dir():
            shutil.copytree(web / name, assets / revision / name, dirs_exist_ok=True)
    for item in web.iterdir():
        if item.name in ASSET_DIRS + ("data",):
            continue
        if item.is_dir():
            shutil.copytree(item, out / item.name, dirs_exist_ok=True)
        elif item.suffix == ".html":
            html = item.read_text("utf-8")
            for name in ASSET_DIRS:
                html = html.replace(f'"{name}/', f'"assets/{revision}/{name}/')
            (out / item.name).write_text(html, "utf-8")
        else:
            shutil.copy2(item, out / item.name)
    return revision


def archive_assets(out: Path, revision: str) -> str:
    """Publishes a deterministic bundle of static files so the next deploy can reuse them."""
    root = out / "assets" / revision
    bundles = out / "asset-bundles"
    bundles.mkdir(exist_ok=True)
    archive = bundles / f"{revision}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                bundle.writestr(zipfile.ZipInfo(name), path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    for old in bundles.glob("*.zip"):
        if old != archive:
            old.unlink()
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def restore_assets(out: Path, previous: dict, current: str, site_url: str) -> None:
    """Restores only the previous deploy's code and design assets."""
    import requests

    revision = previous.get("asset_revision")
    if revision is None or revision == current:
        return
    if not isinstance(revision, str) or not ASSET_REVISION.fullmatch(revision):
        raise BuildError("invalid previous asset revision")
    try:
        response = requests.get(f"{site_url.rstrip('/')}/asset-bundles/{revision}.zip", timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BuildError(f"could not download the previous asset bundle: {exc}") from exc
    if hashlib.sha256(response.content).hexdigest() != previous.get("asset_bundle_sha256"):
        raise BuildError("the previous asset bundle did not match its checksum")
    root = out / "assets" / revision
    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        for name in bundle.namelist():
            path = PurePosixPath(name)
            if (len(path.parts) < 2 or path.parts[0] not in ASSET_DIRS
                    or ".." in path.parts or "\\" in name or ":" in name):
                raise BuildError(f"unexpected previous asset path: {name}")
            target = root.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(name))


def build(dataset: Path, web: Path, out: Path, *, allow_shrink: bool = False, dataset_url: str | None = None,
          previous_site: str | None = None, available: set[str] | None = None) -> dict:
    """Writes the site and its data to `out`, checking page files against `available` (the dataset's listing) when given, and raises BuildError rather than publish a bad build."""
    local, manifest = load_local(dataset)
    remote = load_edisondigital(dataset)
    records, conflicts = merge(local, remote)
    # A withdrawal removes the document whichever source it came from, the edisondigital mirror included.
    for document_id in manifest.get("withdrawn", []):
        records.pop(document_id, None)
    if conflicts:
        raise BuildError(f"DocIDs used by a new document and a different edisondigital item: {', '.join(conflicts[:10])}")
    def has_file(path: str) -> bool:
        if path.startswith(("http://", "https://")):
            return True
        return path in available if available is not None else (dataset / path).is_file()

    volumes = load_volumes(dataset)
    openings = opening_records(volumes, has_file)
    add_openings(records, openings)
    if dataset_url and not records and not (allow_shrink and manifest.get("withdrawn")):
        raise BuildError("the public dataset contains no documents")
    for record in records.values():
        safe_id(record["id"])
        folder_id = (record.get("folder") or {}).get("id", "")
        if folder_id:
            safe_id(folder_id)
        for page in record.get("pages", []):
            for size in ("thumb", "view", "full"):
                path = page[size]
                if not has_file(path):
                    raise BuildError(f"{record['id']} page {page.get('n')} has no {size} file at {path}")
        if record.get("pdf") and not has_file(record["pdf"]):
            raise BuildError(f"{record['id']} has no PDF file at {record['pdf']}")
        if record.get("kind") not in SCAN_KINDS:
            record["description"] = description_for(record, remote.get(record["id"], {}))
    report_shared_scans(shared_scans(records))

    previous_pointer = out / "data" / "current.json"
    previous = read_json(previous_pointer) if previous_pointer.exists() else {}
    if previous.get("documents") and len(records) < 0.98 * previous["documents"] and not allow_shrink:
        raise BuildError(f"the build has {len(records)} documents, down from {previous['documents']}; rerun with --allow-shrink")
    approved = sum(1 for r in records.values() if r.get("status") == "approved")
    if previous.get("approved") and approved < previous["approved"] - max(5, previous["approved"] // 10) and not allow_shrink:
        raise BuildError(f"approved documents fell from {previous['approved']} to {approved}")

    asset_revision = copy_site(web, out)
    if previous_site:
        restore_assets(out, previous, asset_revision, previous_site)
    if dataset_url:
        write_json(out / "sources.json", {"dataset": [dataset_url]})
    data_root = out / "data"
    # Everything but the thumbnails is written to a staging folder and becomes data/{revision}/ only when complete.
    site_data = data_root / ".staging"
    if site_data.exists():
        shutil.rmtree(site_data)
    site_data.mkdir(parents=True)
    topics = read_json(web / "data" / "topics.json") if (web / "data" / "topics.json").exists() else []
    synonyms = read_json(web / "data" / "synonyms.json") if (web / "data" / "synonyms.json").exists() else {}

    # Date order, so the position of a document in every list is also its place in time.
    ordered = sorted(records.values(), key=lambda r: (r.get("year") or 9999, r.get("date") or "", r["id"]))
    summaries, terms_by_doc = [], []
    for record in ordered:
        # Account-book scans keep their mirror's thumbnail on the data host, so thousands of them never fill the site's own space.
        thumb = None if record.get("kind") in SCAN_KINDS else make_thumb(dataset, record, data_root)
        summaries.append(summary(record, thumb))
        terms_by_doc.append(document_terms(record))

    for start in range(0, len(summaries), SUMMARIES_PER_FILE):
        write_json(site_data / "docs" / f"{start // SUMMARIES_PER_FILE}.json", summaries[start:start + SUMMARIES_PER_FILE])

    types = sorted({s["type"] for s in summaries if s["type"]})
    folder_ids = sorted({s["folder"] for s in summaries if s["folder"]})
    write_json(site_data / "facets.json", {
        "year": [s["year"] or 0 for s in summaries],
        "type": [types.index(s["type"]) if s["type"] else -1 for s in summaries],
        "folder": [folder_ids.index(s["folder"]) if s["folder"] else -1 for s in summaries],
        "status": [STATUSES.index(s["status"] if s["status"] in STATUSES else "none") for s in summaries],
        # Each document's place in DocID order, so "sort by ID" needs no extra download.
        "idrank": [rank for rank, _ in sorted(enumerate(sorted(range(len(summaries)), key=lambda n: summaries[n]["id"])),
                                              key=lambda pair: pair[1])],
        "types": types, "folders": folder_ids, "statuses": STATUSES,
    })

    shards: dict[str, dict[str, list[int]]] = defaultdict(dict)
    last_seen: dict[str, int] = {}
    for ordinal, terms in enumerate(terms_by_doc):
        for term, mask in terms.items():
            # Each posting stores the gap from the previous document with the term, which keeps the numbers short.
            shards[textindex.shard_of(term)].setdefault(term, []).extend((ordinal - last_seen.get(term, 0), mask))
            last_seen[term] = ordinal
    for shard, postings in shards.items():
        write_json(site_data / "index" / f"{shard}.json", postings)
    variants = textindex.variant_map(synonyms)
    write_json(site_data / "index" / "meta.json", {"schema": SCHEMA, "documents": len(summaries), "fields": textindex.FIELDS,
                                                   "weights": textindex.WEIGHTS, "shards": sorted(shards),
                                                   "stopwords": sorted(textindex.STOPWORDS), "pair_shards": textindex.PAIR_SHARDS,
                                                   "stemmer": "porter-1980", "synonyms": variants,
                                                   "per_file": SUMMARIES_PER_FILE})

    ordinal_of_id = {s["id"]: n for n, s in enumerate(summaries)}
    related = load_related(dataset)

    def with_related(record: dict) -> dict:
        ordinals = related_ordinals(record["id"], related, ordinal_of_id)
        return {**record, "related": ordinals} if ordinals else record

    by_folder: dict[str, list[dict]] = defaultdict(list)
    by_volume: dict[str, list[dict]] = defaultdict(list)
    for record in ordered:
        folder_id = (record.get("folder") or {}).get("id", "")
        if record.get("kind") in SCAN_KINDS:
            # The reader fetches a whole account book from the data host, so only its listing is written here.
            by_volume[record["volume"]].append(record)
        elif folder_id:
            by_folder[folder_id].append(record)
        else:
            write_json(site_data / "unfiled" / f"{record['id']}.json", with_related(record))
    folder_list = []
    for folder_id, members in sorted(by_folder.items()):
        safe_id(folder_id)
        members = sorted(members, key=lambda r: r["id"])
        listing = [summaries[ordinal_of_id[r["id"]]] for r in members]
        write_json(site_data / "folders" / f"{folder_id}.json", {"id": folder_id, "title": members[0]["folder"].get("title", folder_id),
                                                                 "description": folder_description(members),
                                                                 "documents": [with_related(r) for r in members],
                                                                 "summaries": listing})
        first = next((s for s in summaries if s["folder"] == folder_id), None)
        folder_list.append({"id": folder_id, "title": members[0]["folder"].get("title", folder_id),
                            "description": folder_description(members), "count": len(members),
                            "approved": sum(1 for r in members if r.get("status") == "approved"),
                            "thumb": first and first["thumb"], "thumb_from": first and first["thumb_from"],
                            "ratio": first and first["ratio"]})
    summaries_of = {s["id"]: s for s in summaries}
    volume_list = [volume_entry(volume, sorted(by_volume.get(volume_id, []), key=lambda r: r["scan"]), summaries_of, has_file)
                   for volume_id, volume in volumes.items()]
    # The reader needs only its own volume's entry, so each has a small file; the list keeps short descriptions.
    for entry in volume_list:
        write_json(site_data / "volumes" / f"{safe_id(entry['id'])}.json", entry)
    write_json(site_data / "volumes.json", [{**v, "description": v["description"][:200]} for v in volume_list])

    ordinal_of = {s["id"]: n for n, s in enumerate(summaries)}
    is_scan = [r.get("kind") in SCAN_KINDS for r in ordered]
    topic_list = []
    subjects = subject_index(ordered)
    topic_ids = set()
    for topic in [*topics, *generated_topics(ordered, topics)]:
        topic_id = topic.get("id")
        if not isinstance(topic_id, str) or not _TOPIC_ID.fullmatch(topic_id):
            raise BuildError(f"invalid topic id {topic_id!r}")
        if topic_id in topic_ids:
            raise BuildError(f"duplicate topic id {topic_id!r}")
        topic_ids.add(topic_id)
        matched = topic_documents(topic, subjects, terms_by_doc, variants)
        if not topic.get("generated"):
            # A featured topic promises letters and papers on its subject; thousands of ledger and notebook scans would bury them.
            matched = [n for n in matched if not is_scan[n]]
        cover = topic.get("cover") if topic.get("cover") in ordinal_of else None
        if cover is None and matched:
            cover = next((summaries[n]["id"] for n in matched if summaries[n]["status"] == "approved" and summaries[n]["thumb"]),
                         summaries[matched[0]]["id"])
        cover_summary = summaries[ordinal_of[cover]] if cover else None
        # Each topic's documents are listed once here, so the topic page shows exactly what its count says.
        write_json(site_data / "topics" / f"{topic['id']}.json", {"ordinals": matched})
        topic_list.append({"id": topic["id"], "title": topic["title"],
                           "description": topic.get("description") or topic_description(topic["title"], [ordered[n] for n in matched]),
                           "count": len(matched), "featured": not topic.get("generated"),
                           "thumb": cover_summary and cover_summary["thumb"], "thumb_from": cover_summary and cover_summary["thumb_from"]})
    write_json(site_data / "topics.json", topic_list)

    decades = Counter((s["year"] // 10) * 10 for s in summaries if s["year"])
    recent = sorted((r for r in records.values() if r.get("status") == "approved"), key=lambda r: r.get("updated") or "", reverse=True)
    # The landing page opens on a real verified scan, the most recently verified one with a page to show.
    hero_record = (next((r for r in recent if r.get("pages") and r.get("kind") not in SCAN_KINDS), None)
                   or next((r for r in ordered if r.get("pages") and r.get("kind") not in SCAN_KINDS), None))
    hero = hero_record and {"id": hero_record["id"], "title": hero_record.get("title", ""), "view": hero_record["pages"][0]["view"]}
    built_at = datetime.now(timezone.utc).isoformat()
    catalog = {"schema": SCHEMA, "built_at": built_at, "documents": len(summaries), "approved": approved,
               "drafts": sum(1 for s in summaries if s["status"] == "draft"),
               "folders": folder_list, "topics": [t for t in topic_list if t["featured"]], "topic_count": len(topic_list),
               "volumes": [{"id": v["id"], "title": v["title"]} for v in volume_list], "openings": len(openings),
               "decades": dict(sorted(decades.items())),
               "recent": [ordinal_of[r["id"]] for r in recent[:12]], "hero": hero,
               "sources": {"local": len(local), "edisondigital": len(remote)},
               "local_published_at": manifest.get("generated_at", "")}
    vector_count = write_vectors(dataset, site_data / "vectors", records, ordinal_of_id)
    if vector_count:
        logger.info("wrote %d passage vectors for search by meaning", vector_count)
    write_json(site_data / "catalog.json", catalog)
    revision = hashlib.sha256(json.dumps([catalog, sorted(shards)], sort_keys=True).encode()).hexdigest()[:16]
    final = data_root / revision
    if final.exists():
        shutil.rmtree(final)
    site_data.rename(final)
    pointer = {"revision": revision, "built_at": built_at, "documents": len(summaries), "approved": approved}
    if dataset_url:
        pointer["asset_revision"] = asset_revision
        pointer["asset_bundle_sha256"] = archive_assets(out, asset_revision)
    # The pointer moves last; the build before stays on disk so pages already open keep reading a consistent set.
    write_json(data_root / "current.json", pointer)
    keep = {revision, previous.get("revision")}
    for old in data_root.iterdir():
        if old.is_dir() and old.name not in keep and old.name not in ("thumbs", ".staging"):
            shutil.rmtree(old)
    logger.info("built %d documents (%d approved) in %d folders, %d index files", len(summaries), approved, len(folder_list), len(shards))
    return pointer


# What the build reads; scans, viewer images and PDFs stay on Hugging Face and are checked against the file listing instead.
RECORD_PATTERNS = ["local/manifest.json", "local/*/record.json", "volumes/manifest.json", "volumes/*/volume.json",
                   "edisondigital/current.json", "edisondigital/*/records.jsonl",
                   "embeddings/*/related.json", "embeddings/arctic-embed-s/manifest.json", "embeddings/arctic-embed-s/documents.jsonl",
                   "embeddings/arctic-embed-s/parts/*"]


def first_thumbnails(dataset: Path, listing: set[str]) -> list[str]:
    """Each document's first-page thumbnail in the dataset, the only image the build reads (to make the site's own thumbnail)."""
    local, _ = load_local(dataset)
    paths = []
    for record in [*local.values(), *load_edisondigital(dataset).values()]:
        pages = record.get("pages") or []
        thumb = str(pages[0].get("thumb", "")) if pages else ""
        if thumb and not thumb.startswith("http") and thumb in listing:
            paths.append(thumb)
    return sorted(set(paths))


def download_public_dataset(repo: str, cache: Path) -> tuple[Path, str, set[str]]:
    """Uses the same immutable public dataset revision for the build and browser downloads, fetching records and first thumbnails only."""
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    info = api.dataset_info(repo, token=False)
    if info.private or info.gated or not info.sha:
        raise BuildError("Pages needs a public, ungated Hugging Face dataset with a published revision")
    listing = set(api.list_repo_files(info.id, repo_type="dataset", revision=info.sha, token=False))
    fetch = dict(repo_type="dataset", revision=info.sha, cache_dir=cache, token=False)
    directory = Path(snapshot_download(info.id, allow_patterns=RECORD_PATTERNS, **fetch))
    thumbs = first_thumbnails(directory, listing)
    if thumbs:
        snapshot_download(info.id, allow_patterns=thumbs, **fetch)
    logger.info("downloaded the records and %d first thumbnails of %d files", len(thumbs), len(listing))
    return directory, f"https://huggingface.co/datasets/{info.id}/resolve/{info.sha}/", listing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="The dataset directory, or the download cache when using --hf-repo")
    parser.add_argument("--web", default=str(Path(__file__).resolve().parents[1] / "web"), help="The site's source files")
    parser.add_argument("--out", required=True, help="Where the built site goes")
    parser.add_argument("--hf-repo", help="Build from this public Hugging Face dataset and use its revision for page files")
    parser.add_argument("--previous-site", help="Restore the previous public site's static assets using its saved pointer")
    parser.add_argument("--allow-shrink", action="store_true", help="Publish even though documents were removed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        dataset = Path(args.dataset)
        dataset_url = None
        available = None
        if args.hf_repo:
            dataset, dataset_url, available = download_public_dataset(args.hf_repo, dataset)
        build(dataset, Path(args.web), Path(args.out), allow_shrink=args.allow_shrink, dataset_url=dataset_url,
              previous_site=args.previous_site, available=available)
    except BuildError as exc:
        logger.error("build refused: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
