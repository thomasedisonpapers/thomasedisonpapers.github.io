"""Validates the published dataset, uploads one atomic revision, then verifies it file by file.

Only `local/` (from the publisher) and `embeddings/` are managed here; `edisondigital/` belongs to the harvester and is never touched.
The token comes from the HF_TOKEN environment variable or the machine's Hugging Face login, and is never printed or passed on the command line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
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
# Folders made to test the review platform, which must never reach the public.
TEST_FOLDER = re.compile(r"TEST|^SPLITUI$", re.IGNORECASE)
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
PART_NAME = re.compile(r"part-\d{5}")
PASSAGE_KEY = re.compile(r"[0-9a-f]{20}")
MANAGED = ("local/", "embeddings/")
# The embedding files the site reads; plans and evaluation queries stay on the machine that made them.
EMBEDDING_FILE = re.compile(r"^embeddings/[a-z0-9.-]+/(manifest\.json|documents\.jsonl|related\.json|parts/part-\d{5}\.(npy|keys))$")
POINTER = re.compile(r"^(local/manifest\.json|embeddings/[a-z0-9.-]+/manifest\.json)$")
# Bound the number of files in each pre-upload request.
BATCH = 100


class UploadError(Exception):
    """A release that failed validation or could not be verified."""


def local_files(dataset: Path) -> dict[str, Path]:
    """Every file that belongs in the repository, by its path there; hidden bookkeeping such as `.publish-state` is never included."""
    files = {}
    for top in ("local", "embeddings"):
        root = dataset / top
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(dataset).as_posix()
            if not path.is_file() or any(part.startswith(".") for part in rel.split("/")):
                continue
            if top == "embeddings" and not EMBEDDING_FILE.match(rel):
                continue
            files[rel] = path
    return files


def _snapshot(dataset: Path, destination: Path) -> None:
    """Copies the release inputs and detects changes while the copy is being made."""
    root = dataset.resolve()

    def inventory():
        paths = local_files(dataset)
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


def check(dataset: Path) -> list[str]:
    """Everything that stops an upload: fields outside the allowlist, test folders, withdrawn or stray files, image metadata, unpublished embeddings."""
    try:
        return _check(dataset)
    except (OSError, ValueError, EOFError, OverflowError) as exc:
        return [f"could not validate the dataset: {exc}"]


def _check(dataset: Path) -> list[str]:
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
    files = local_files(dataset)
    root = dataset.resolve()
    for rel, path in files.items():
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            problems.append(f"{rel} must be a file inside the dataset")
        if not rel.startswith("local/"):
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
    published_ids = set(published)
    for model, paths in models.items():
        check_embeddings(dataset / "embeddings" / model, paths, published_ids, problems)
    return problems


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
        if not isinstance(related, dict) or any(
                document not in document_ids or not isinstance(pairs, list) or any(
                    not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str)
                    or pair[0] not in document_ids or type(pair[1]) not in (int, float) or not math.isfinite(pair[1])
                    for pair in pairs)
                for document, pairs in related.items()):
            problems.append(f"{label}/related.json contains invalid or unpublished references")
    for extra in sorted(files - expected):
        problems.append(f"{label}/{extra.relative_to(model).as_posix()} is not part of the published embedding store")


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


def _same_public_manifest(path: Path, api, repo: str, revision: str) -> bool:
    published_path = api.hf_hub_download(repo, "local/manifest.json", repo_type="dataset", revision=revision)
    try:
        local, published = read_json(path), read_json(Path(published_path))
    except ValueError:
        return False
    if not isinstance(published, dict):
        return False
    local.pop("generated_at", None)
    published.pop("generated_at", None)
    return local == published


def upload(dataset: Path, repo: str, api, *, dry_run: bool = False) -> dict:
    """Publishes from a private temporary copy so edits cannot bypass validation."""
    if dry_run:
        return _upload(dataset, repo, api, dry_run=True)
    with TemporaryDirectory(prefix="edison-upload-") as directory:
        snapshot = Path(directory)
        _snapshot(dataset, snapshot)
        return _upload(snapshot, repo, api, dry_run=False)


def _upload(dataset: Path, repo: str, api, *, dry_run: bool) -> dict:
    """Pre-uploads large files, then commits the entire validated update to main and verifies that revision."""
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    problems = check(dataset)
    if problems:
        raise UploadError(f"{len(problems)} problem(s), the first: " + "; ".join(problems[:5]))
    info = api.repo_info(repo, repo_type="dataset", revision="main")
    if not info.sha:
        raise UploadError(f"{repo} has no main revision")
    remote = remote_files(api, repo, info.sha)
    if "local/manifest.json" not in remote and not info.private:
        raise UploadError(f"{repo} is public and has never been uploaded to; the first upload must go to a private repository")
    files = local_files(dataset)
    changed = [p for p in files if not POINTER.match(p) and not (p in remote and _same(files[p], remote[p]))]
    pointers = [p for p in files if POINTER.match(p) and not (p in remote and _same(files[p], remote[p]))]
    # Only folders this machine actually publishes are kept in step, so a host without embeddings never deletes them.
    managed = tuple(prefix for prefix in MANAGED if any(path.startswith(prefix) for path in files))
    stale = sorted(p for p in remote if p.startswith(managed) and p not in files)
    manifest = "local/manifest.json"
    timestamp_only = (not changed and not stale and pointers == [manifest] and manifest in remote
                      and _same_public_manifest(files[manifest], api, repo, info.sha))
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
    wrong = [p for p in files if not (timestamp_only and p == manifest)
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.check_only:
        problems = check(args.dataset)
        for problem in problems:
            logger.error("refused: %s", problem)
        logger.info("boundary check: %s", "failed" if problems else "passed")
        return 1 if problems else 0
    from huggingface_hub import HfApi
    try:
        print(json.dumps(upload(args.dataset, args.repo, HfApi(token=os.environ.get("HF_TOKEN") or None), dry_run=args.dry_run)))
    except UploadError as error:
        logger.error("refused: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
