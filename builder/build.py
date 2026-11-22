"""Merges our published documents with the edisondigital mirror and writes everything the static site reads."""
from __future__ import annotations

import argparse
import hashlib
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

logger = logging.getLogger("build")

SCHEMA = 1
SUMMARIES_PER_FILE = 1000
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


def write_vectors(dataset: Path, target: Path, ordinal_of_id: dict[str, int]) -> int:
    """Writes every passage vector of the documents in this build as signed bytes, with each passage's document ordinal; returns the count."""
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
            ordinal = ordinal_of_id.get(document.get("id"))
            if ordinal is None:
                continue
            for key in document.get("keys", []):
                if key not in row_of:
                    raise BuildError(f"{document['id']} refers to a missing semantic passage vector")
                rows.append(row_of[key])
                owners.append(ordinal)
    if not rows:
        return 0
    matrix = np.concatenate(blocks).astype(np.float32)
    target.mkdir(parents=True, exist_ok=True)
    # Unit vectors fit in a signed byte at a scale of 127, a quarter the size of 32-bit floats with no change to the ranking that matters.
    (target / "vectors.i8").write_bytes(np.clip(np.rint(matrix[rows] * 127), -127, 127).astype(np.int8).tobytes())
    (target / "ordinals.u32").write_bytes(np.asarray(owners, dtype="<u4").tobytes())
    write_json(target / "meta.json", {**{k: SEARCH_MODEL[k] for k in ("hf", "revision", "dtype", "dim", "query_prefix")},
                                      "model": SEARCH_MODEL["name"], "count": len(rows), "scale": 127, "layout": "flat",
                                      "files": {"vectors": "vectors.i8", "ordinals": "ordinals.u32"}})
    return len(rows)


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
            "ratio": round((first.get("height") or 4) / (first.get("width") or 3), 3), "pages": len(record.get("pages") or [])}


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
    return [{"documents": sorted(members),
             "identical": len({tuple(p.get("sha256") for p in records[m].get("pages") or []) for m in members}) == 1}
            for members in sorted(groups.values(), key=min) if len(members) > 1]


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
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(f"### {count} documents share a page scan\n\nCheck these page ranges in the review platform.\n\n"
                         + "".join(f"- {line}\n" for line in lines) + "\n")


def folder_description(records: list[dict]) -> str:
    """A plain account of what the folder holds, for folders the archive has not described."""
    years = sorted(y for y in (r.get("year") for r in records) if y)
    span = f", {years[0]}–{years[-1]}" if years and years[0] != years[-1] else (f", {years[0]}" if years else "")
    kinds = [kind for kind, _ in Counter(r.get("type") for r in records if r.get("type")).most_common(3)]
    listed = f", mostly {', '.join(k.lower() for k in kinds[:-1])}{' and ' if len(kinds) > 1 else ''}{kinds[-1].lower()}s" if kinds else ""
    return f"{len(records)} document{'s' if len(records) != 1 else ''}{span}{listed}."


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
    if dataset_url and not records and not (allow_shrink and manifest.get("withdrawn")):
        raise BuildError("the public dataset contains no documents")
    if conflicts:
        raise BuildError(f"DocIDs used by a new document and a different edisondigital item: {', '.join(conflicts[:10])}")
    def has_file(path: str) -> bool:
        if path.startswith(("http://", "https://")):
            return True
        return path in available if available is not None else (dataset / path).is_file()

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
        record["description"] = describe.describe(record)
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
        summaries.append(summary(record, make_thumb(dataset, record, data_root)))
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
    for record in ordered:
        folder_id = (record.get("folder") or {}).get("id", "")
        if folder_id:
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

    ordinal_of = {s["id"]: n for n, s in enumerate(summaries)}
    topic_list = []
    for topic in topics:
        matched = [n for n, terms in enumerate(terms_by_doc) if topic_matches(topic, terms, variants)]
        cover = topic.get("cover") if topic.get("cover") in ordinal_of else None
        if cover is None and matched:
            cover = next((summaries[n]["id"] for n in matched if summaries[n]["status"] == "approved" and summaries[n]["thumb"]),
                         summaries[matched[0]]["id"])
        cover_summary = summaries[ordinal_of[cover]] if cover else None
        topic_list.append({**{k: topic[k] for k in ("id", "title", "description", "any") if k in topic},
                           "fields": topic.get("fields") or [], "count": len(matched),
                           "thumb": cover_summary and cover_summary["thumb"], "thumb_from": cover_summary and cover_summary["thumb_from"]})

    decades = Counter((s["year"] // 10) * 10 for s in summaries if s["year"])
    recent = sorted((r for r in records.values() if r.get("status") == "approved"), key=lambda r: r.get("updated") or "", reverse=True)
    # The landing page opens on a real verified scan, the most recently verified one with a page to show.
    hero_record = next((r for r in recent if r.get("pages")), None) or next((r for r in ordered if r.get("pages")), None)
    hero = hero_record and {"id": hero_record["id"], "title": hero_record.get("title", ""), "view": hero_record["pages"][0]["view"]}
    built_at = datetime.now(timezone.utc).isoformat()
    catalog = {"schema": SCHEMA, "built_at": built_at, "documents": len(summaries), "approved": approved,
               "drafts": sum(1 for s in summaries if s["status"] == "draft"),
               "folders": folder_list, "topics": topic_list, "decades": dict(sorted(decades.items())),
               "recent": [ordinal_of[r["id"]] for r in recent[:12]], "hero": hero,
               "sources": {"local": len(local), "edisondigital": len(remote)},
               "local_published_at": manifest.get("generated_at", "")}
    passages = write_vectors(dataset, site_data / "vectors", ordinal_of_id)
    if passages:
        logger.info("wrote %d passage vectors for search by meaning", passages)
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
RECORD_PATTERNS = ["local/manifest.json", "local/*/record.json", "edisondigital/current.json", "edisondigital/*/records.jsonl",
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
