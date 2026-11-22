"""The uploader validates the boundary, publishes one revision, then verifies it."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import upload

JPEG = b"\xff\xd8\xff\xdb\x00\x04\x00\x00\xff\xd9"
JPEG_WITH_EXIF = b"\xff\xd8\xff\xe1\x00\x08Exif\x00\x00\xff\xd9"
KEY = "a" * 20


def _record(document_id, **extra):
    return {"schema": 1, "id": document_id, "title": "Letter", "status": "approved", "pdf": f"{document_id}.pdf",
            "pages": [{"n": 1, "width": 3, "height": 4, "thumb": "thumb/0001.jpg", "view": "view/0001.jpg", "full": "full/0001.jpg"}],
            **extra}


def _dataset(tmp_path, documents=("E2002AAA",), folder="new/E2002", withdrawn=(), records=None):
    local = tmp_path / "local"
    for document_id in documents:
        root = local / document_id
        for size in ("thumb", "view", "full"):
            (root / size).mkdir(parents=True)
            (root / size / "0001.jpg").write_bytes(JPEG)
        (root / f"{document_id}.pdf").write_bytes(b"%PDF-1.7 " + document_id.encode())
        record = (records or {}).get(document_id) or _record(document_id)
        (root / "record.json").write_text(json.dumps(record), "utf-8")
    manifest = {"folders": {folder: {"documents": {d: {} for d in documents}}}, "withdrawn": list(withdrawn)}
    (local / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    (tmp_path / ".publish-state").mkdir()
    (tmp_path / ".publish-state" / "state.json").write_text("{}", "utf-8")
    return tmp_path


def _embeddings(dataset, ids=("E2002AAA",), model_name="arctic-embed-s"):
    model = dataset / "embeddings" / model_name
    (model / "parts").mkdir(parents=True)
    np.save(model / "parts" / "part-00000.npy", np.ones((1, 3), dtype=np.float16))
    (model / "parts" / "part-00000.keys").write_text(f"{KEY}\n", "utf-8")
    (model / "documents.jsonl").write_text("".join(json.dumps({"id": i, "keys": [KEY]}) + "\n" for i in ids), "utf-8")
    (model / "manifest.json").write_text(json.dumps({"dim": 3, "parts": [{"name": "part-00000", "count": 1}]}), "utf-8")
    (model / "plan.json").write_text("{}", "utf-8")
    (model / "queries.json").write_text("[]", "utf-8")
    return model


class FakeHub:
    """A stand-in Hugging Face repository that stores files in memory and records each commit's message and paths."""

    def __init__(self, private=True, files=None, drop=None, cache=None):
        self.private, self.files, self.commits, self.drop = private, dict(files or {}), [], drop
        self.preuploads = []
        self.oid = "0" * 40
        self.cache = cache

    def repo_info(self, repo, repo_type, revision):
        assert revision == "main"
        return SimpleNamespace(private=self.private, sha=self.oid)

    def list_repo_tree(self, repo, recursive, repo_type, revision):
        assert revision == self.oid
        for path, data in self.files.items():
            text = path.endswith((".json", ".jsonl", ".keys"))
            yield SimpleNamespace(path=path, blob_id=hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(),
                                  lfs=None if text else SimpleNamespace(sha256=hashlib.sha256(data).hexdigest()))

    def hf_hub_download(self, repo, filename, repo_type, revision):
        assert revision == self.oid
        path = self.cache / revision / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.files[filename])
        return str(path)

    def preupload_lfs_files(self, repo, additions, repo_type, revision):
        assert revision == "main"
        self.preuploads.append([op.path_in_repo for op in additions])

    def create_commit(self, repo, operations, repo_type, revision, parent_commit, commit_message):
        if revision != "main" or parent_commit != self.oid:
            raise upload.UploadError("the main branch changed")
        paths = []
        files = dict(self.files)
        for op in operations:
            if hasattr(op, "path_or_fileobj"):
                if op.path_in_repo != self.drop:
                    files[op.path_in_repo] = Path(op.path_or_fileobj).read_bytes()
            else:
                files.pop(op.path_in_repo, None)
            paths.append(op.path_in_repo)
        self.files = files
        self.commits.append((commit_message, paths))
        self.oid = f"{int(self.oid, 16) + 1:040x}"
        return SimpleNamespace(oid=self.oid)


def test_a_clean_published_dataset_passes_the_boundary_check(tmp_path):
    assert upload.check(_dataset(tmp_path)) == []


def test_a_record_with_a_field_outside_the_allowlist_is_refused(tmp_path):
    dataset = _dataset(tmp_path, records={"E2002AAA": _record("E2002AAA", reviewer="A. Reviewer")})
    assert any("outside the allowlist" in p for p in upload.check(dataset))


def test_a_page_with_a_field_outside_the_allowlist_is_refused(tmp_path):
    record = _record("E2002AAA")
    record["pages"][0]["cost_estimate"] = 0.02
    assert any("page 1" in p for p in upload.check(_dataset(tmp_path, records={"E2002AAA": record})))


@pytest.mark.parametrize("field,value", [
    ("folder", {"id": "E2002", "title": "Advice", "private_note": "internal"}),
    ("edisondigital", {"document": "public link", "private_note": "internal"}),
    ("subjects", [{"private_note": "internal"}]),
])
def test_metadata_objects_and_lists_are_checked_recursively(tmp_path, field, value):
    dataset = _dataset(tmp_path, records={"E2002AAA": _record("E2002AAA", **{field: value})})
    assert any(field in problem for problem in upload.check(dataset))


@pytest.mark.parametrize("target", ["manifest", "folder", "document"])
def test_the_manifest_rejects_unlisted_fields_at_each_level(tmp_path, target):
    dataset = _dataset(tmp_path)
    path = dataset / "local" / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    folder = manifest["folders"]["new/E2002"]
    targets = {"manifest": manifest, "folder": folder, "document": folder["documents"]["E2002AAA"]}
    targets[target]["private_note"] = "internal"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("outside the allowlist: private_note" in p for p in upload.check(dataset))


def test_a_test_folder_is_refused(tmp_path):
    assert any("test folder" in p for p in upload.check(_dataset(tmp_path, folder="new/ZLONGTEST")))


def test_a_withdrawn_documents_files_are_refused(tmp_path):
    dataset = _dataset(tmp_path, documents=("E2002AAA", "E2002AAB"), withdrawn=("E2002AAB",))
    assert any(p.startswith("local/E2002AAB/") for p in upload.check(dataset))


def test_a_stray_file_beside_the_published_ones_is_refused(tmp_path):
    dataset = _dataset(tmp_path)
    (dataset / "local" / "E2002AAA" / "notes.txt").write_text("internal", "utf-8")
    assert any("notes.txt" in p for p in upload.check(dataset))


def test_a_scan_that_still_carries_exif_is_refused(tmp_path):
    dataset = _dataset(tmp_path)
    (dataset / "local" / "E2002AAA" / "full" / "0001.jpg").write_bytes(JPEG_WITH_EXIF)
    assert any("image metadata" in p for p in upload.check(dataset))


def test_a_record_from_a_different_manifest_revision_is_refused(tmp_path):
    dataset = _dataset(tmp_path)
    manifest_path = dataset / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["folders"]["new/E2002"]["documents"]["E2002AAA"]["status"] = "draft"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("does not match its manifest entry" in p for p in upload.check(dataset))


def test_a_scan_changed_since_the_record_was_written_is_refused(tmp_path):
    record = _record("E2002AAA")
    record["pages"][0]["sha256"] = hashlib.sha256(JPEG + b"old").hexdigest()
    dataset = _dataset(tmp_path, records={"E2002AAA": record})
    assert any("does not match its published page checksum" in p for p in upload.check(dataset))


def test_a_page_path_that_climbs_out_of_its_folder_is_refused(tmp_path):
    record = _record("E2002AAA")
    record["pages"][0]["full"] = "../../secrets.txt"
    assert any("outside its folder" in p for p in upload.check(_dataset(tmp_path, records={"E2002AAA": record})))


def test_embeddings_of_an_unpublished_document_are_refused(tmp_path):
    dataset = _dataset(tmp_path)
    _embeddings(dataset, ids=("E2002AAA", "E2002ZZZ"))
    assert any("not published" in p for p in upload.check(dataset))


def test_a_vector_without_a_published_document_reference_is_refused(tmp_path):
    dataset = _dataset(tmp_path)
    model = _embeddings(dataset)
    (model / "parts" / "part-00000.keys").write_text(f"{KEY}\n{'b' * 20}\n", encoding="utf-8")
    np.save(model / "parts" / "part-00000.npy", np.ones((2, 3), dtype=np.float16))
    (model / "manifest.json").write_text(json.dumps({"dim": 3, "parts": [{"name": "part-00000", "count": 2}]}), "utf-8")
    assert any("unreferenced vector rows" in p for p in upload.check(dataset))


def test_a_vector_file_cannot_hide_extra_data_after_its_declared_rows(tmp_path):
    dataset = _dataset(tmp_path)
    model = _embeddings(dataset)
    with (model / "parts" / "part-00000.npy").open("ab") as vector_file:
        vector_file.write(b"extra")
    assert any("does not match its passage rows" in p for p in upload.check(dataset))


def test_related_documents_cannot_expose_a_withdrawn_id(tmp_path):
    dataset = _dataset(tmp_path, withdrawn=("E2002OLD",))
    model = _embeddings(dataset)
    (model / "related.json").write_text(json.dumps({"E2002AAA": [["E2002OLD", 0.5]]}), encoding="utf-8")
    assert any("invalid or unpublished references" in p for p in upload.check(dataset))


def test_embedding_plans_and_evaluation_queries_stay_on_the_machine(tmp_path):
    dataset = _dataset(tmp_path)
    _embeddings(dataset)
    assert not [p for p in upload.local_files(dataset) if p.endswith(("plan.json", "queries.json"))]


def test_the_publishers_bookkeeping_is_never_uploaded(tmp_path):
    assert not [p for p in upload.local_files(_dataset(tmp_path)) if ".publish-state" in p]


def test_the_first_upload_to_a_public_repository_is_refused(tmp_path):
    with pytest.raises(upload.UploadError):
        upload.upload(_dataset(tmp_path), "org/repo", FakeHub(private=False))


def test_records_manifests_and_removals_land_in_one_commit(tmp_path):
    dataset = _dataset(tmp_path, documents=("E2002AAA", "E2002AAB"))
    _embeddings(dataset, ids=("E2002AAA",))
    hub = FakeHub(files={"local/E2002OLD/record.json": b"{}"})
    upload.upload(dataset, "org/repo", hub)
    assert hub.commits == [("Publish archive dataset", [
        *[path for path in upload.local_files(dataset) if not upload.POINTER.match(path)],
        *[path for path in upload.local_files(dataset) if upload.POINTER.match(path)],
        "local/E2002OLD/record.json",
    ])]


def test_interrupting_the_preupload_leaves_the_previous_revision_whole(tmp_path, monkeypatch):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    upload.upload(dataset, "org/repo", hub)
    old = dict(hub.files)
    path = dataset / "local" / "E2002AAA" / "record.json"
    path.write_text(json.dumps(_record("E2002AAA", title="Updated letter")), encoding="utf-8")

    def interrupt(*args, **kwargs):
        raise upload.UploadError("interrupted preupload")

    monkeypatch.setattr(hub, "preupload_lfs_files", interrupt)
    with pytest.raises(upload.UploadError, match="interrupted preupload"):
        upload.upload(dataset, "org/repo", hub)
    assert hub.files == old


def test_an_edit_after_validation_cannot_change_the_bytes_uploaded(tmp_path, monkeypatch):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    record_path = dataset / "local" / "E2002AAA" / "record.json"
    validated = record_path.read_bytes()
    preupload = hub.preupload_lfs_files

    def edit_after_check(*args, **kwargs):
        record_path.write_text(json.dumps(_record("E2002AAA", private_note="internal")), encoding="utf-8")
        preupload(*args, **kwargs)

    monkeypatch.setattr(hub, "preupload_lfs_files", edit_after_check)
    upload.upload(dataset, "org/repo", hub)
    assert hub.files["local/E2002AAA/record.json"] == validated


def test_a_dataset_that_changes_while_it_is_being_copied_is_refused(tmp_path, monkeypatch):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    copyfile = upload.shutil.copyfile

    def modify_during_copy(source, destination):
        copyfile(source, destination)
        if source.name == "record.json":
            source.write_text(json.dumps(_record("E2002AAA", title="Another title")), encoding="utf-8")

    monkeypatch.setattr(upload.shutil, "copyfile", modify_during_copy)
    with pytest.raises(upload.UploadError, match="changed while taking the upload snapshot"):
        upload.upload(dataset, "org/repo", hub)
    assert hub.preuploads == []


def test_a_concurrent_commit_prevents_replacing_the_revision_that_was_checked(tmp_path, monkeypatch):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    preupload = hub.preupload_lfs_files

    def concurrent(*args, **kwargs):
        preupload(*args, **kwargs)
        hub.oid = "1" * 40

    monkeypatch.setattr(hub, "preupload_lfs_files", concurrent)
    with pytest.raises(upload.UploadError, match="main branch changed"):
        upload.upload(dataset, "org/repo", hub)
    assert hub.commits == []


def test_an_upload_without_embeddings_leaves_the_published_embeddings_in_place(tmp_path):
    hub = FakeHub(files={"embeddings/arctic-embed-s/related.json": b"{}"}, cache=tmp_path / "hf-cache")
    upload.upload(_dataset(tmp_path), "org/repo", hub)
    assert "embeddings/arctic-embed-s/related.json" in hub.files


def test_an_upload_with_one_models_embeddings_leaves_another_models_in_place(tmp_path):
    dataset = _dataset(tmp_path)
    _embeddings(dataset)
    stored = _embeddings(tmp_path / "remote", model_name="qwen3-embedding-0.6b")
    files = {p.relative_to(tmp_path / "remote").as_posix(): p.read_bytes() for p in stored.rglob("*")
             if p.is_file() and upload.EMBEDDING_FILE.fullmatch(p.relative_to(tmp_path / "remote").as_posix())}
    hub = FakeHub(files=files, cache=tmp_path / "hf-cache")
    upload.upload(dataset, "org/repo", hub)
    assert "embeddings/qwen3-embedding-0.6b/manifest.json" in hub.files


@pytest.mark.parametrize("model_on_host", [True, False])
def test_a_records_only_withdrawal_removes_stale_remote_vectors_in_the_same_commit(tmp_path, model_on_host):
    old_id = "E2002OLD"
    dataset = _dataset(tmp_path, withdrawn=(old_id,))
    prefix = "embeddings/arctic-embed-s/"
    if model_on_host:
        _embeddings(dataset, ids=(old_id,))
    hub = FakeHub(files={
        prefix + "documents.jsonl": (json.dumps({"id": old_id, "keys": [KEY]}) + "\n").encode(),
        prefix + "manifest.json": b"{}",
        prefix + "parts/part-00000.npy": b"old",
        prefix + "parts/part-00000.keys": KEY.encode(),
        "local/E2002OLD/record.json": b"{}",
    }, cache=tmp_path / "hf-cache")

    upload.upload(dataset, "org/repo", hub, embedding_model="none")

    assert len(hub.commits) == 1 and not any(path.startswith(prefix) or old_id in path for path in hub.files)


def test_an_upload_removes_a_retained_model_with_a_dead_passage_row(tmp_path):
    dataset = _dataset(tmp_path)
    prefix = "embeddings/arctic-embed-s/"
    hub = FakeHub(files={
        prefix + "documents.jsonl": (json.dumps({"id": "E2002AAA", "keys": [KEY]}) + "\n").encode(),
        prefix + "manifest.json": b'{"parts":[{"name":"part-00000","count":2}]}',
        prefix + "parts/part-00000.npy": b"old",
        prefix + "parts/part-00000.keys": f"{KEY}\n{'b' * 20}\n".encode(),
    }, cache=tmp_path / "hf-cache")

    upload.upload(dataset, "org/repo", hub, embedding_model="none")

    assert not any(path.startswith(prefix) for path in hub.files)


def test_an_upload_with_a_models_embeddings_removes_that_models_stale_parts(tmp_path):
    dataset = _dataset(tmp_path)
    _embeddings(dataset)
    hub = FakeHub(files={"embeddings/arctic-embed-s/parts/part-00007.npy": b"old"})
    upload.upload(dataset, "org/repo", hub)
    assert "embeddings/arctic-embed-s/parts/part-00007.npy" not in hub.files


def test_the_harvesters_files_are_never_removed(tmp_path):
    hub = FakeHub(files={"edisondigital/current.json": b"{}"})
    upload.upload(_dataset(tmp_path), "org/repo", hub)
    assert "edisondigital/current.json" in hub.files


def test_a_second_upload_with_nothing_changed_makes_no_commits(tmp_path):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    upload.upload(dataset, "org/repo", hub)
    hub.commits.clear()
    upload.upload(dataset, "org/repo", hub)
    assert hub.commits == []


@pytest.mark.parametrize("aliases,expected_commits", [({}, 0), ({"E2002OLD": "E2002AAA"}, 1)])
def test_a_manifest_timestamp_is_ignored_when_the_published_contents_are_unchanged(
        tmp_path, monkeypatch, aliases, expected_commits):
    dataset = _dataset(tmp_path)
    hub = FakeHub()
    manifest_path = dataset / "local" / "manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    before.update(generated_at="2026-11-21T15:00:00Z", aliases={})
    manifest_path.write_text(json.dumps(before), encoding="utf-8")
    upload.upload(dataset, "org/repo", hub)
    hub.commits.clear()
    cached = tmp_path / "remote-manifest.json"
    cached.write_bytes(hub.files["local/manifest.json"])

    def download(repo, filename, *, repo_type, revision):
        assert revision == hub.oid
        return str(cached)

    monkeypatch.setattr(hub, "hf_hub_download", download, raising=False)
    before.update(generated_at="2026-11-21T15:15:00Z", aliases=aliases)
    manifest_path.write_text(json.dumps(before), encoding="utf-8")
    upload.upload(dataset, "org/repo", hub)
    assert len(hub.commits) == expected_commits


def test_a_file_that_did_not_land_fails_the_verification(tmp_path):
    with pytest.raises(upload.UploadError):
        upload.upload(_dataset(tmp_path), "org/repo", FakeHub(drop="local/E2002AAA/record.json"))


def test_a_dry_run_changes_nothing(tmp_path):
    hub = FakeHub()
    upload.upload(_dataset(tmp_path), "org/repo", hub, dry_run=True)
    assert hub.commits == []


def test_a_large_upload_preuploads_files_in_batches(tmp_path):
    dataset = _dataset(tmp_path, documents=tuple(f"E2002A{n:02d}" for n in range(30)))
    hub = FakeHub()
    upload.upload(dataset, "org/repo", hub)
    assert max(map(len, hub.preuploads)) <= upload.BATCH
