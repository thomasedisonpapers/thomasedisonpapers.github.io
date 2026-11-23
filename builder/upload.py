"""Validates the published dataset, uploads one atomic revision, then verifies it file by file.

Only `local/` and `volumes/` (from the publisher), `scans/` (mirrored account-book scans) and `embeddings/` are managed here; `edisondigital/` belongs to the harvester and is never touched.
The token comes from the HF_TOKEN environment variable or the machine's Hugging Face login, and is never printed or passed on the command line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger("upload")

# An independent copy of the publisher's allowlist, so widening what goes public takes a change on both sides.
RECORD_LISTS = ("mentioned", "subjects", "names", "places", "marginal_notes")
RECORD_FIELDS = {
    **dict.fromkeys(("id", "workflow", "source", "relation", "status", "status_label", "title", "type", "date",
                     "author", "recipient", "place", "microfilm", "description", "editors_notes", "transcription",
                     "pdf", "updated", "hash"), (str, type(None))),
    **dict.fromkeys(RECORD_LISTS, (list, type(None))),
    "schema": (int,), "folder": (dict, type(None)), "edisondigital": (dict, type(None)), "pages": (list,),
}
PAGE_FIELDS = {"n": (int,), "width": (int, type(None)), "height": (int, type(None)),
               **dict.fromkeys(("thumb", "view", "full", "sha256"), (str,))}
MANIFEST_FIELDS = {"schema": (int,), "generated_at": (str,), "folders": (dict,), "withdrawn": (list,), "aliases": (dict,)}
FOLDER_FIELDS = {**dict.fromkeys(("id", "workflow", "title", "export_version"), (str,)), "documents": (dict,)}
ENTRY_FIELDS = dict.fromkeys(("status", "hash", "updated"), (str,))
EMBED_MANIFEST_FIELDS = {
    **dict.fromkeys(("model", "hf", "pooling", "query_prefix", "dtype", "scope"), (str,)),
    **dict.fromkeys(("dim", "documents", "passages"), (int,)),
    "parts": (list,),
}
VOLUME_LISTS = ("series", "subjects", "parties", "people", "places")
VOLUME_FIELDS = {
    **dict.fromkeys(("id", "collection", "title", "catalogue_title", "date", "description", "status", "status_label", "pdf",
                     "updated", "hash"), (str, type(None))),
    **dict.fromkeys(VOLUME_LISTS, (list,)),
    "schema": (int,), "years": (list,), "openings": (list,), "edisondigital": (dict, type(None)),
}
OPENING_FIELDS = {"n": (int,), "id": (str,), "width": (int, type(None)), "height": (int, type(None)), "source": (str,),
                  "status": (str,), "pages": (list,), "latex": (str,), "hash": (str,)}
VOLUME_PAGE_FIELDS = {**dict.fromkeys(("position", "content"), (str,)),
                      **dict.fromkeys(("lines", "tables", "drawings", "people", "places"), (list,))}
VOLUMES_MANIFEST_FIELDS = {"schema": (int,), "generated_at": (str,), "volumes": (dict,), "withdrawn": (list,)}
VOLUME_ENTRY_FIELDS = {**dict.fromkeys(("collection", "status", "hash", "updated"), (str,)), "openings": (int,)}
COLLECTIONS = {"accounts"}
STATUSES = {"approved", "draft"}
# The only hosts a scan may be mirrored from, checked again here so a bad record cannot point the mirror elsewhere.
SCAN_SOURCE = re.compile(r"^https://(?:edisondigital\.rutgers\.edu|iiif\.archive\.org)/[^\s\\]*$")
SCAN_FILE = re.compile(r"^scans/([A-Za-z0-9_-]+)/(thumb|full)/(\d{4})\.jpg$")
# Folders made to test the review platform, which must never reach the public.
TEST_FOLDER = re.compile(r"TEST|^SPLITUI$", re.IGNORECASE)
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
PART_NAME = re.compile(r"part-\d{5}")
PASSAGE_KEY = re.compile(r"[0-9a-f]{20}")
MANAGED = ("local/", "volumes/", "scans/", "embeddings/")
# The embedding files the site reads; plans and evaluation queries stay on the machine that made them.
EMBEDDING_FILE = re.compile(r"^embeddings/[a-z0-9.-]+/(manifest\.json|documents\.jsonl|related\.json|parts/part-\d{5}\.(npy|keys))$")
MANIFESTS = ("local/manifest.json", "volumes/manifest.json")
SCAN_REMOVAL_LIMIT = 1000
POINTER = re.compile(r"^((?:local|volumes)/manifest\.json|embeddings/[a-z0-9.-]+/manifest\.json)$")
# Bound the number of files in each pre-upload request.
BATCH = 100


class UploadError(Exception):
    """A release that failed validation or could not be verified."""


def local_files(dataset: Path, *, embedding_model: str | None = None) -> dict[str, Path]:
    """Every file that belongs in the repository, by its path there; hidden bookkeeping such as `.publish-state` is never included."""
    files = {}
    roots = ["local", "volumes", "scans"]
    if embedding_model != "none":
        roots.append(f"embeddings/{embedding_model}" if embedding_model else "embeddings")
    for top in roots:
        root = dataset / top
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(dataset).as_posix()
            # A file still being written (.part) is never part of a release.
            if not path.is_file() or rel.endswith(".part") or any(part.startswith(".") for part in rel.split("/")):
                continue
            if top.startswith("embeddings") and not EMBEDDING_FILE.fullmatch(rel):
                continue
            files[rel] = path
    return files


def _snapshot(dataset: Path, destination: Path, embedding_model: str | None) -> None:
    """Copies the release inputs and detects changes while the copy is being made."""
    root = dataset.resolve()

    def inventory():
        paths = local_files(dataset, embedding_model=embedding_model)
        signatures = {}
        for name, path in paths.items():
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise UploadError(f"{name} must be a file inside the dataset")
            stat = path.stat()
            signatures[name] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return paths, signatures

    try:
        paths, before = inventory()
        for name, path in paths.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        _, after = inventory()
    except OSError as exc:
        raise UploadError(f"could not copy the dataset into a temporary upload snapshot: {exc}") from exc
    if before != after:
        raise UploadError("the local dataset changed while taking the upload snapshot; rerun after the export finishes")


def has_jpeg_metadata(data: bytes) -> bool:
    """Whether a JPEG still carries an EXIF/XMP (APP1) or Photoshop (APP13) block, which can hold camera, location or software details."""
    if not data.startswith(b"\xff\xd8"):
        return False
    at = 2
    while at + 4 <= len(data) and data[at] == 0xFF:
        marker = data[at + 1]
        if marker == 0xDA:
            return False
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        if marker in (0xE1, 0xED):
            return True
        at += 2 + int.from_bytes(data[at + 2:at + 4], "big")
    return False


def checked_fields(value, schema: dict, label: str, problems: list[str]) -> dict:
    if not isinstance(value, dict):
        problems.append(f"{label} must be an object")
        return {}
    checked = {}
    for key, item in value.items():
        if key not in schema:
            problems.append(f"{label} has fields outside the allowlist: {key}")
        elif type(item) not in schema[key]:
            problems.append(f"{label}.{key} has an invalid type")
        else:
            checked[key] = item
    return checked


def safe_id(value) -> bool:
    return isinstance(value, str) and SAFE_ID.fullmatch(value) is not None


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(dataset: Path, *, embedding_model: str | None = None) -> list[str]:
    """Everything that stops an upload: fields outside the allowlist, test folders, withdrawn or stray files, image metadata, unpublished embeddings."""
    try:
        return _check(dataset, embedding_model)
    except (OSError, ValueError, EOFError, OverflowError) as exc:
        return [f"could not validate the dataset: {exc}"]


def _check(dataset: Path, embedding_model: str | None) -> list[str]:
    problems: list[str] = []
    manifest_path = dataset / "local" / "manifest.json"
    if not manifest_path.exists():
        return ["local/manifest.json is missing"]
    manifest = checked_fields(read_json(manifest_path), MANIFEST_FIELDS, "local/manifest.json", problems)
    if "folders" not in manifest:
        problems.append("local/manifest.json needs a folders object")
    withdrawn = set()
    for document_id in manifest.get("withdrawn", []):
        if safe_id(document_id):
            withdrawn.add(document_id)
        else:
            problems.append("local/manifest.json has an invalid withdrawn ID")
    if any(not safe_id(key) or not safe_id(target) for key, target in manifest.get("aliases", {}).items()):
        problems.append("local/manifest.json aliases must map document IDs to document IDs")
    published: dict[str, dict] = {}
    for key, folder in manifest.get("folders", {}).items():
        segments = key.split("/")
        if len(segments) > 2 or not all(safe_id(segment) for segment in segments):
            problems.append(f"local/manifest.json has an invalid folder key: {key!r}")
        if TEST_FOLDER.search(segments[-1]):
            problems.append(f"{key} is a test folder")
        folder = checked_fields(folder, FOLDER_FIELDS, f"manifest folder {key}", problems)
        for document_id, entry in folder.get("documents", {}).items():
            entry = checked_fields(entry, ENTRY_FIELDS, f"manifest document {document_id}", problems)
            if not safe_id(document_id):
                problems.append(f"{document_id!r} is not a safe document ID")
            elif document_id not in withdrawn:
                if document_id in published and entry != published[document_id]:
                    problems.append(f"local/manifest.json has conflicting entries for {document_id}")
                published[document_id] = entry
    allowed = {"local/manifest.json"}
    full_checksums = {}
    for document_id, entry in sorted(published.items()):
        record_path = dataset / "local" / document_id / "record.json"
        if not record_path.exists():
            problems.append(f"local/{document_id}/record.json is missing")
            continue
        record = checked_fields(read_json(record_path), RECORD_FIELDS, document_id, problems)
        if record.get("folder") is not None:
            checked_fields(record["folder"], {"id": (str,), "title": (str,)}, f"{document_id}.folder", problems)
        if record.get("edisondigital") is not None:
            checked_fields(record["edisondigital"], {"document": (str,), "manifest": (str,)},
                           f"{document_id}.edisondigital", problems)
        for field in RECORD_LISTS:
            if any(not isinstance(item, str) for item in record.get(field) or []):
                problems.append(f"{document_id}.{field} must contain only text")
        if record.get("id") != document_id:
            problems.append(f"local/{document_id}/record.json says it is {record.get('id')!r}")
        if any(record.get(field) != value for field, value in entry.items()):
            problems.append(f"{document_id} does not match its manifest entry")
        if record.get("hash"):
            content = {key: value for key, value in record.items() if key not in ("updated", "pdf", "hash")}
            digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if record["hash"] != digest:
                problems.append(f"{document_id} does not match its record checksum")
        allowed.add(f"local/{document_id}/record.json")
        named = [record["pdf"]] if record.get("pdf") else []
        for n, page in enumerate(record.get("pages", []), 1):
            page = checked_fields(page, PAGE_FIELDS, f"{document_id} page {n}", problems)
            named += [page[size] for size in ("thumb", "view", "full") if page.get(size) and not page[size].startswith("http")]
            if page.get("sha256") and page.get("full") and not page["full"].startswith("http"):
                full_checksums[f"local/{document_id}/{Path(page['full']).as_posix()}"] = page["sha256"]
        for name in named:
            if "\\" in name or ":" in name or ".." in Path(name).parts or Path(name).is_absolute():
                problems.append(f"{document_id} points outside its folder: {name!r}")
            else:
                allowed.add(f"local/{document_id}/{Path(name).as_posix()}")
    openings = check_volumes(dataset, problems, allowed)
    files = local_files(dataset, embedding_model=embedding_model)
    root = dataset.resolve()
    for rel, path in files.items():
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            problems.append(f"{rel} must be a file inside the dataset")
        if rel.startswith("scans/"):
            check_scan(rel, path, openings, problems)
            continue
        if not rel.startswith(("local/", "volumes/")):
            continue
        if rel not in allowed:
            problems.append(f"{rel} is not part of any published document")
        else:
            jpeg = rel.lower().endswith((".jpg", ".jpeg"))
            expected = full_checksums.get(rel)
            if jpeg or expected:
                data = path.read_bytes()
                if jpeg and has_jpeg_metadata(data):
                    problems.append(f"{rel} still carries image metadata")
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    problems.append(f"{rel} does not match its published page checksum")
    problems += [f"{rel} is named by a record but missing" for rel in sorted(allowed - files.keys())]
    models = {}
    for name, path in files.items():
        if name.startswith("embeddings/"):
            models.setdefault(name.split("/")[1], set()).add(path)
    published_ids = set(published) | {o for numbers in openings.values() for o in numbers.values()}
    for model, paths in models.items():
        check_embeddings(dataset / "embeddings" / model, paths, published_ids, problems)
    return problems


def check_volumes(dataset: Path, problems: list[str], allowed: set[str]) -> dict[str, dict[int, str]]:
    """Checks every published volume against its own allowlist, and returns each volume's openings by scan number."""
    manifest_path = dataset / "volumes" / "manifest.json"
    if not manifest_path.exists():
        return {}
    allowed.add("volumes/manifest.json")
    manifest = checked_fields(read_json(manifest_path), VOLUMES_MANIFEST_FIELDS, "volumes/manifest.json", problems)
    withdrawn_ids = manifest.get("withdrawn", [])
    withdrawn = {v for v in withdrawn_ids if safe_id(v)}
    if any(not safe_id(v) for v in withdrawn_ids):
        problems.append("volumes/manifest.json has an invalid withdrawn ID")
    openings: dict[str, dict[int, str]] = {}
    for volume_id, entry in manifest.get("volumes", {}).items():
        entry = checked_fields(entry, VOLUME_ENTRY_FIELDS, f"manifest volume {volume_id}", problems)
        if not safe_id(volume_id) or volume_id in withdrawn:
            problems.append(f"{volume_id!r} cannot be published as a volume")
            continue
        path = dataset / "volumes" / volume_id / "volume.json"
        if not path.exists():
            problems.append(f"volumes/{volume_id}/volume.json is missing")
            continue
        raw = read_json(path)
        volume = checked_fields(raw, VOLUME_FIELDS, volume_id, problems)
        label = f"volume {volume_id}"
        if volume.get("id") != volume_id or volume.get("collection") not in COLLECTIONS or volume.get("status") not in STATUSES:
            problems.append(f"{label} has the wrong id, collection or status")
        if any(not isinstance(item, str) for field in VOLUME_LISTS for item in volume.get(field) or []):
            problems.append(f"{label} lists must contain only text")
        if volume.get("edisondigital") is not None:
            checked_fields(volume["edisondigital"], {"item": (str,), "manifest": (str,)}, f"{label}.edisondigital", problems)
        if (any(volume.get(field) != value for field, value in entry.items() if field != "openings")
                or entry.get("openings") != len(volume.get("openings", []))):
            problems.append(f"{label} does not match its manifest entry")
        content = {key: value for key, value in volume.items() if key not in ("updated", "pdf", "hash")}
        if volume.get("hash") != hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest():
            problems.append(f"{label} does not match its checksum")
        numbers = openings.setdefault(volume_id, {})
        for opening in volume.get("openings", []):
            opening = checked_fields(opening, OPENING_FIELDS, f"{label} opening", problems)
            n = opening.get("n")
            if not isinstance(n, int) or not 1 <= n <= 9999 or opening.get("id") != f"{volume_id}-{n - 1:04d}" or n in numbers:
                problems.append(f"{label} has an opening with the wrong number or id")
                continue
            source = opening.get("source")
            if (source != "" and not SCAN_SOURCE.fullmatch(source or "")) or opening.get("status") not in STATUSES:
                problems.append(f"{label} opening {n} has a scan source or status that is not allowed")
            for page in opening.get("pages", []):
                page = checked_fields(page, VOLUME_PAGE_FIELDS, f"{label} opening {n} page", problems)
                check_volume_page(page, f"{label} opening {n}", problems)
            numbers[n] = opening.get("id")
        allowed.add(f"volumes/{volume_id}/volume.json")
        if volume.get("pdf"):
            if volume["pdf"] != f"{volume_id}.pdf":
                problems.append(f"{label} names a PDF other than its own")
            allowed.add(f"volumes/{volume_id}/{volume_id}.pdf")
    return openings


def check_volume_page(page: dict, label: str, problems: list[str]) -> None:
    def texts(values) -> bool:
        return isinstance(values, list) and all(isinstance(v, str) for v in values)

    if not all(texts(page.get(field, [])) for field in ("lines", "people", "places")):
        problems.append(f"{label} page text must be lists of text")
    for table in page.get("tables", []):
        table = checked_fields(table, {"caption": (str,), "columns": (list,), "rows": (list,)}, f"{label} table", problems)
        if not texts(table.get("columns", [])) or not all(texts(row) for row in table.get("rows", [])):
            problems.append(f"{label} table cells must be text")
    for drawing in page.get("drawings", []):
        drawing = checked_fields(drawing, {"summary": (str,), "labels": (list,)}, f"{label} drawing", problems)
        if not texts(drawing.get("labels", [])):
            problems.append(f"{label} drawing labels must be text")


def check_scan(rel: str, path: Path, openings: dict[str, dict[int, str]], problems: list[str]) -> None:
    """A mirrored scan must belong to a published opening and carry no camera or software metadata."""
    match = SCAN_FILE.fullmatch(rel)
    if not match or int(match.group(3)) not in openings.get(match.group(1), {}):
        problems.append(f"{rel} is not a scan of any published opening")
    elif has_jpeg_metadata(path.read_bytes()):
        problems.append(f"{rel} still carries image metadata")


def check_embeddings(model: Path, files: set[Path], published: set[str], problems: list[str]) -> None:
    import numpy as np

    label = f"embeddings/{model.name}"
    manifest_path = model / "manifest.json"
    documents_path = model / "documents.jsonl"
    if not {manifest_path, documents_path}.issubset(files):
        problems.append(f"{label} needs both manifest.json and documents.jsonl")
        return
    manifest = checked_fields(read_json(manifest_path), EMBED_MANIFEST_FIELDS, f"{label}/manifest.json", problems)
    if "parts" not in manifest:
        problems.append(f"{label}/manifest.json needs a parts list")

    document_ids, referenced = set(), set()
    for n, line in enumerate(documents_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        entry_label = f"{label}/documents.jsonl line {n}"
        doc = checked_fields(json.loads(line), {"id": (str,), "keys": (list,)}, entry_label, problems)
        document_id = doc.get("id")
        if document_id not in published:
            problems.append(f"{entry_label} covers a document that is not published")
        if document_id in document_ids:
            problems.append(f"{entry_label} repeats a document")
        document_ids.add(document_id)
        keys = doc.get("keys")
        if not keys or any(not isinstance(key, str) or not PASSAGE_KEY.fullmatch(key) for key in keys):
            problems.append(f"{entry_label} has invalid passage keys")
        else:
            referenced.update(keys)

    stored = set()
    expected = {manifest_path, documents_path}
    for part in manifest.get("parts", []):
        part = checked_fields(part, {"name": (str,), "count": (int,), "sha256": (str,)}, f"{label} part", problems)
        name = part.get("name", "")
        if not PART_NAME.fullmatch(name):
            problems.append(f"{label} has an invalid part name")
            continue
        stem = model / "parts" / name
        key_path, vector_path = stem.with_suffix(".keys"), stem.with_suffix(".npy")
        if key_path in expected:
            problems.append(f"{label}/{name} is listed twice")
            continue
        expected.update((key_path, vector_path))
        if key_path not in files or vector_path not in files:
            problems.append(f"{label}/{name} is missing its keys or vectors")
            continue
        keys = key_path.read_text(encoding="utf-8").split()
        if len(keys) != len(set(keys)) or not stored.isdisjoint(keys):
            problems.append(f"{label}/{name} contains duplicate passage rows")
        stored.update(keys)
        vectors = np.load(vector_path, mmap_mode="r", allow_pickle=False)
        if (not isinstance(vectors, np.memmap) or vectors.ndim != 2 or vectors.dtype != np.float16
                or vectors.shape != (len(keys), manifest.get("dim")) or len(keys) != part.get("count")
                or vectors.offset + vectors.nbytes != vector_path.stat().st_size):
            problems.append(f"{label}/{name} does not match its passage rows and manifest")
        if "sha256" in part:
            with vector_path.open("rb") as vector_file:
                digest = hashlib.file_digest(vector_file, "sha256").hexdigest()
            if digest != part["sha256"]:
                problems.append(f"{label}/{name} does not match its checksum")

    if stored - referenced:
        problems.append(f"{label} contains {len(stored - referenced)} unreferenced vector rows")
    if referenced - stored:
        problems.append(f"{label} is missing {len(referenced - stored)} referenced vector rows")
    if manifest.get("documents", len(document_ids)) != len(document_ids) or manifest.get("passages", len(referenced)) != len(referenced):
        problems.append(f"{label} counts do not match its documents and passages")

    related_path = model / "related.json"
    if related_path in files:
        expected.add(related_path)
        related = read_json(related_path)
        if not _valid_related(related, document_ids):
            problems.append(f"{label}/related.json contains invalid or unpublished references")
    for extra in sorted(files - expected):
        problems.append(f"{label}/{extra.relative_to(model).as_posix()} is not part of the published embedding store")


def _valid_related(related, allowed: set[str]) -> bool:
    return isinstance(related, dict) and all(
        document in allowed and isinstance(pairs, list) and all(
            isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
            and pair[0] in allowed and type(pair[1]) in (int, float) and -1.001 <= pair[1] <= 1.001
            for pair in pairs)
        for document, pairs in related.items())


def _digests(path: Path) -> tuple[str, str]:
    """A file's SHA-256 and its git blob ID, the two ways Hugging Face identifies a stored file."""
    digest = hashlib.sha256()
    blob = hashlib.sha1(b"blob %d\0" % path.stat().st_size)
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
            blob.update(block)
    return digest.hexdigest(), blob.hexdigest()


def _same(path: Path, remote) -> bool:
    sha256, blob = _digests(path)
    lfs = getattr(remote, "lfs", None)
    if lfs:
        return (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)) == sha256
    return getattr(remote, "blob_id", None) == blob


def remote_files(api, repo: str, revision: str) -> dict:
    """Every file at the selected repository commit, by path."""
    return {item.path: item for item in api.list_repo_tree(repo, recursive=True, repo_type="dataset", revision=revision)
            if hasattr(item, "blob_id")}


def _same_public_manifest(path: Path, api, repo: str, revision: str, name: str = "local/manifest.json") -> bool:
    published_path = api.hf_hub_download(repo, name, repo_type="dataset", revision=revision)
    try:
        local, published = read_json(path), read_json(Path(published_path))
    except ValueError:
        return False
    if not isinstance(published, dict):
        return False
    local.pop("generated_at", None)
    published.pop("generated_at", None)
    return local == published


def _affected_remote_models(dataset: Path, api, repo: str, revision: str, remote: dict, managed: set[str]) -> set[str]:
    """Finds retained model outputs that can no longer safely accompany the published catalogue."""
    manifest = read_json(dataset / "local" / "manifest.json")
    published = {doc for folder in manifest["folders"].values() for doc in folder.get("documents", {})}
    published.difference_update(manifest.get("withdrawn", []))
    published.update(o for numbers in check_volumes(dataset, [], set()).values() for o in numbers.values())
    models: dict[str, set[str]] = {}
    for path in remote:
        if path.startswith("embeddings/") and path.count("/") >= 2:
            prefix = "/".join(path.split("/")[:2]) + "/"
            if prefix not in managed:
                models.setdefault(prefix, set()).add(path[len(prefix):])
    affected = set()
    for prefix, names in models.items():
        try:
            if _remote_model_is_current(api, repo, revision, prefix, names, published):
                continue
        except ValueError:
            pass
        logger.warning("removing retained embedding output for %s because it does not match the published catalogue", prefix)
        affected.add(prefix)
    return affected


def _remote_model_is_current(api, repo: str, revision: str, prefix: str, names: set[str], published: set[str]) -> bool:
    def fetch(name: str) -> Path:
        return Path(api.hf_hub_download(repo, prefix + name, repo_type="dataset", revision=revision))

    if names == {"related.json"}:
        return _valid_related(read_json(fetch("related.json")), published)
    if not {"manifest.json", "documents.jsonl"}.issubset(names):
        return False
    documents, referenced = set(), set()
    for line in fetch("documents.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict) or set(entry) != {"id", "keys"}:
            return False
        doc, keys = entry["id"], entry["keys"]
        if (not safe_id(doc) or doc not in published or doc in documents or not isinstance(keys, list)
                or not keys or any(not isinstance(key, str) or not PASSAGE_KEY.fullmatch(key) for key in keys)):
            return False
        documents.add(doc)
        referenced.update(keys)

    manifest = read_json(fetch("manifest.json"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("parts"), list):
        return False
    expected = {"manifest.json", "documents.jsonl"}
    remaining = referenced.copy()
    for part in manifest["parts"]:
        if not isinstance(part, dict) or not isinstance(part.get("name"), str) or not PART_NAME.fullmatch(part["name"]):
            return False
        key_path, vector_path = (f"parts/{part['name']}{suffix}" for suffix in (".keys", ".npy"))
        if key_path in expected or not {key_path, vector_path}.issubset(names):
            return False
        keys = fetch(key_path).read_text(encoding="utf-8").split()
        unique = set(keys)
        if len(unique) != len(keys) or len(keys) != part.get("count") or not unique.issubset(remaining):
            return False
        remaining.difference_update(unique)
        expected.update((key_path, vector_path))
    if "related.json" in names:
        if not _valid_related(read_json(fetch("related.json")), documents):
            return False
        expected.add("related.json")
    return not remaining and names == expected


def upload(dataset: Path, repo: str, api, *, dry_run: bool = False, embedding_model: str | None = None) -> dict:
    """Publishes from a private temporary copy so edits cannot bypass validation."""
    if dry_run:
        return _upload(dataset, repo, api, dry_run=True, embedding_model=embedding_model)
    with TemporaryDirectory(prefix="edison-upload-") as directory:
        snapshot = Path(directory)
        _snapshot(dataset, snapshot, embedding_model)
        return _upload(snapshot, repo, api, dry_run=False, embedding_model=embedding_model)


def _upload(dataset: Path, repo: str, api, *, dry_run: bool, embedding_model: str | None) -> dict:
    """Pre-uploads large files, then commits the entire validated update to main and verifies that revision."""
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    problems = check(dataset, embedding_model=embedding_model)
    if problems:
        raise UploadError(f"{len(problems)} problem(s), the first: " + "; ".join(problems[:5]))
    info = api.repo_info(repo, repo_type="dataset", revision="main")
    if not info.sha:
        raise UploadError(f"{repo} has no main revision")
    remote = remote_files(api, repo, info.sha)
    if "local/manifest.json" not in remote and not info.private:
        raise UploadError(f"{repo} is public and has never been uploaded to; the first upload must go to a private repository")
    files = local_files(dataset, embedding_model=embedding_model)
    changed = [p for p in files if not POINTER.match(p) and not (p in remote and _same(files[p], remote[p]))]
    pointers = [p for p in files if POINTER.match(p) and not (p in remote and _same(files[p], remote[p]))]
    managed = {path.split("/")[0] + "/" if path.startswith(("local/", "volumes/", "scans/")) else "/".join(path.split("/")[:2]) + "/"
               for path in files}
    if "volumes/manifest.json" in files:
        managed.add("scans/")
    managed.update(_affected_remote_models(dataset, api, repo, info.sha, remote, managed))
    managed = tuple(sorted(managed))
    stale = sorted(p for p in remote if p.startswith(managed) and p not in files)
    remote_scans = sum(1 for p in remote if p.startswith("scans/"))
    stale_scans = sum(1 for p in stale if p.startswith("scans/"))
    if stale_scans > SCAN_REMOVAL_LIMIT and stale_scans * 2 > remote_scans:
        # Taking down a book removes a few hundred scans; losing most of them means the local copy is incomplete.
        raise UploadError(f"refusing to remove {stale_scans} of {remote_scans} mirrored scans; the local dataset looks incomplete")
    # A manifest whose only change is its timestamp is not a release, so an idle run makes no commit and no rebuild.
    timestamp_only = (not changed and not stale and bool(pointers) and all(
        p in MANIFESTS and p in remote and _same_public_manifest(files[p], api, repo, info.sha, p) for p in pointers))
    held = set(pointers) if timestamp_only else set()
    if timestamp_only:
        pointers = []
    plan = {"upload": len(changed), "manifests": len(pointers), "remove": len(stale), "files": len(files)}
    logger.info("plan: %s", plan)
    if dry_run:
        return plan
    landed = remote
    if changed or pointers or stale:
        additions = [CommitOperationAdd(p, str(files[p])) for p in changed + pointers]
        for start in range(0, len(additions), BATCH):
            api.preupload_lfs_files(repo, additions[start:start + BATCH], repo_type="dataset", revision="main")
        operations = additions + [CommitOperationDelete(p) for p in stale]
        # A concurrent upload or harvest must not replace the revision used to compute this update.
        commit = api.create_commit(repo, operations, repo_type="dataset", revision="main", parent_commit=info.sha,
                                   commit_message="Publish archive dataset")
        landed = remote_files(api, repo, commit.oid)
    wrong = [p for p in files if p not in held
             and (p not in landed or not _same(files[p], landed[p]))]
    extra = [p for p in landed if p.startswith(managed) and p not in files]
    if wrong or extra:
        raise UploadError(f"the repository does not match what was sent: {len(wrong)} wrong or missing, {len(extra)} extra "
                          f"(first: {(wrong + extra)[0]})")
    plan["verified"] = len(files)
    logger.info("uploaded and verified %d files", len(files))
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, required=True, help="The publisher's output folder (holding local/ and embeddings/)")
    parser.add_argument("--repo", default=os.environ.get("HF_DATASET_REPO", "thomasedisonpapers/edison-papers"))
    parser.add_argument("--check-only", action="store_true", help="Run the boundary check and stop")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be sent, removed and kept, without changing anything")
    parser.add_argument("--embedding-model", choices=("arctic-embed-s", "qwen3-embedding-0.6b", "none"),
                        help="Publish this local model only, or use 'none' to publish records independently")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.check_only:
        problems = check(args.dataset, embedding_model=args.embedding_model)
        for problem in problems:
            logger.error("refused: %s", problem)
        logger.info("boundary check: %s", "failed" if problems else "passed")
        return 1 if problems else 0
    from huggingface_hub import HfApi
    try:
        print(json.dumps(upload(args.dataset, args.repo, HfApi(token=os.environ.get("HF_TOKEN") or None),
                                dry_run=args.dry_run, embedding_model=args.embedding_model)))
    except UploadError as error:
        logger.error("refused: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
