"""The builder: one tokenizer with the site, ours-first merging, and refusing any build that would publish something wrong."""
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

import build
import textindex

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "tokenize.json").read_text("utf-8"))


@pytest.mark.parametrize("case", FIXTURE, ids=[c["text"] or "empty" for c in FIXTURE])
def test_the_builder_tokenizes_exactly_as_the_site_does(case):
    assert textindex.tokenize(case["text"]) == case["terms"]


def test_a_docid_with_a_hyphen_is_findable_whole():
    assert "x018c9-0915" in textindex.document_terms({"id": "X018C9-0915"})


def test_a_word_pair_carries_the_field_it_was_found_in():
    assert textindex.document_terms({"title": "Storage battery"})["storag batteri"] == textindex.FIELDS["title"]


STEMS = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "stems.json").read_text("utf-8"))


def test_the_builder_stems_every_word_of_our_transcriptions_as_the_fixture_records():
    from stemmer import stem
    assert {w: s for w, s in STEMS.items() if stem(w) != s} == {}


def test_porters_own_examples_are_stemmed_as_porter_published_them():
    from stemmer import stem
    assert [stem(w) for w in ("caresses", "ponies", "relational", "generalization", "hopping")] == ["caress", "poni", "relat", "gener", "hop"]


def test_a_document_is_indexed_by_stems_so_plurals_find_singulars():
    assert "laboratori" in textindex.document_terms({"text": "the laboratories"})


def test_the_variant_list_works_in_both_directions():
    assert textindex.variant_map({"lab": ["laboratory"]})["laboratory"] == ["lab"]


def _record(document_id, **fields):
    return {"id": document_id, "title": "A letter", "status": "approved", "relation": "new", "pages": [],
            "folder": {"id": "E2002", "title": "Advice"}, **fields}


def test_our_field_wins_over_edisondigitals():
    merged, _ = build.merge({"D1": _record("D1", relation="supplements-edisondigital", date="1880")}, {"D1": _record("D1", date="1881")})
    assert merged["D1"]["date"] == "1880"


def test_edisondigital_fills_a_field_we_left_empty():
    merged, _ = build.merge({"D1": _record("D1", relation="supplements-edisondigital", place="")}, {"D1": _record("D1", place="Menlo Park")})
    assert merged["D1"]["place"] == "Menlo Park"


def test_the_editions_transcription_beats_our_unreviewed_draft():
    ours = _record("D1", relation="supplements-edisondigital", status="draft", transcription="Draft.")
    merged, _ = build.merge({"D1": ours}, {"D1": _record("D1", status="edition", transcription="Edition.")})
    assert merged["D1"]["transcription"] == "Edition."


def test_our_approved_transcription_beats_the_editions():
    ours = _record("D1", relation="supplements-edisondigital", status="approved", transcription="Ours.")
    merged, _ = build.merge({"D1": ours}, {"D1": _record("D1", status="edition", transcription="Edition.")})
    assert merged["D1"]["transcription"] == "Ours."


def test_a_new_document_sharing_an_edisondigital_id_is_a_conflict():
    _, conflicts = build.merge({"D1": _record("D1", relation="new")}, {"D1": _record("D1")})
    assert conflicts == ["D1"]


def test_the_year_is_read_from_the_date():
    assert build.year_of("ca. March 5, 1911") == 1911


def test_a_topic_phrase_of_three_words_needs_each_word_pair():
    terms = textindex.document_terms({"title": "the storage battery company"})
    assert build.topic_matches({"any": ["storage battery company"]}, terms)


def test_a_topic_limited_to_a_field_ignores_other_fields():
    terms = textindex.document_terms({"text": "Mina wrote"})
    assert not build.topic_matches({"any": ["mina"], "fields": ["author"]}, terms)


def test_a_topic_counts_a_document_that_uses_only_a_variant_spelling_regression_phonograph_314_vs_317_21_november():
    terms = textindex.document_terms({"text": "the new phono records"})
    variants = textindex.variant_map({"phono": ["phonograph"]})
    assert build.topic_matches({"any": ["phonograph"]}, terms, variants)


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset" / "local"
    documents = {}
    for n, status in enumerate(["approved", "draft", "draft"]):
        document_id = f"E2002AA{'ABC'[n]}"
        directory = root / document_id
        for size in ("thumb", "view", "full"):
            (directory / size).mkdir(parents=True)
            Image.new("RGB", (30, 40), "white").save(directory / size / "0001.jpg")
        record = _record(document_id, status=status, date=f"18{80 + n}", transcription="Portland cement letter.",
                         pages=[{"n": 1, "width": 30, "height": 40, "thumb": "thumb/0001.jpg", "view": "view/0001.jpg",
                                 "full": "full/0001.jpg"}], pdf=None, updated=f"2026-11-0{n + 1}")
        (directory / "record.json").write_text(json.dumps(record), "utf-8")
        documents[document_id] = {"status": status}
    (root / "manifest.json").write_text(json.dumps({"folders": {"E2002": {"documents": documents}}, "withdrawn": []}), "utf-8")
    web = tmp_path / "web"
    for name in ("css", "js", "fonts", "data"):
        (web / name).mkdir(parents=True)
    (web / "index.html").write_text('<link href="css/site.css"><script src="js/home.js"></script>', "utf-8")
    (web / "css" / "site.css").write_text("body{}", "utf-8")
    (web / "js" / "home.js").write_text("// home", "utf-8")
    (web / "data" / "topics.json").write_text(json.dumps([{"id": "cement", "title": "Cement", "any": ["portland cement"]}]), "utf-8")
    return tmp_path


def _build(tmp, **options):
    return build.build(tmp / "dataset", tmp / "web", tmp / "site", **options)


def _data(tmp, path):
    revision = json.loads((tmp / "site" / "data" / "current.json").read_text("utf-8"))["revision"]
    return json.loads((tmp / "site" / "data" / revision / path).read_text("utf-8"))


def test_a_build_counts_every_published_document(dataset):
    assert _build(dataset)["documents"] == 3


def test_the_html_points_at_the_versioned_assets(dataset):
    _build(dataset)
    assert '"assets/' in (dataset / "site" / "index.html").read_text("utf-8")


def test_a_topic_counts_its_matching_documents(dataset):
    _build(dataset)
    assert _data(dataset, "catalog.json")["topics"][0]["count"] == 3


def test_documents_are_numbered_in_date_order(dataset):
    _build(dataset)
    assert [d["id"] for d in _data(dataset, "docs/0.json")] == ["E2002AAA", "E2002AAB", "E2002AAC"]


@pytest.mark.parametrize("folder", [None, {"id": "", "title": ""}])
def test_a_document_without_a_collection_has_a_reader_record(dataset, folder):
    path = dataset / "dataset" / "local" / "E2002AAA" / "record.json"
    record = json.loads(path.read_text("utf-8"))
    record["folder"] = folder
    path.write_text(json.dumps(record), "utf-8")

    _build(dataset)
    assert _data(dataset, "unfiled/E2002AAA.json")["id"] == "E2002AAA"


def test_a_public_build_serves_page_files_from_its_dataset_revision(dataset):
    url = "https://huggingface.co/datasets/edison/archive/resolve/abc123/"
    _build(dataset, dataset_url=url)
    assert build.read_json(dataset / "site" / "sources.json") == {"dataset": [url]}


def test_a_fresh_public_build_keeps_the_asset_urls_from_the_previous_html(dataset, monkeypatch):
    source = "https://huggingface.co/datasets/edison/archive/resolve/abc123/"
    vendor = dataset / "web" / "vendor"
    vendor.mkdir()
    (vendor / "runtime.mjs").write_text("// previous runtime", "utf-8")
    previous = _build(dataset, dataset_url=source)
    revision = previous["asset_revision"]
    archive = dataset / "site" / "asset-bundles" / f"{revision}.zip"
    response = requests.Response()
    response.status_code = 200
    response._content = archive.read_bytes()
    monkeypatch.setattr(requests, "get", lambda url, **_: response)

    out = dataset / "fresh-site"
    build.write_json(out / "data" / "current.json", previous)
    (dataset / "web" / "js" / "home.js").write_text("// updated home", "utf-8")
    build.build(dataset / "dataset", dataset / "web", out, dataset_url=source, previous_site="https://example.test")

    old_assets = out / "assets" / revision
    assets = ("js/home.js", "vendor/runtime.mjs")
    assert {name: (old_assets / name).read_text("utf-8") for name in assets} == {
        "js/home.js": "// home", "vendor/runtime.mjs": "// previous runtime",
    }


def test_a_withdrawn_document_is_left_out(dataset):
    manifest_path = dataset / "dataset" / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["withdrawn"] = ["E2002AAC"]
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    assert _build(dataset)["documents"] == 2


def test_a_build_that_loses_documents_is_refused(dataset):
    _build(dataset)
    manifest_path = dataset / "dataset" / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["withdrawn"] = ["E2002AAB", "E2002AAC"]
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)


def test_an_explicit_withdrawal_build_can_publish_an_empty_public_catalogue(dataset):
    url = "https://huggingface.co/datasets/edison/archive/resolve/abc123/"
    _build(dataset, dataset_url=url)
    path = dataset / "dataset" / "local" / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["withdrawn"] = list(manifest["folders"]["E2002"]["documents"])
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert _build(dataset, dataset_url=url, allow_shrink=True)["documents"] == 0


def test_a_page_pointing_at_a_missing_file_is_refused(dataset):
    (dataset / "dataset" / "local" / "E2002AAA" / "view" / "0001.jpg").unlink()
    with pytest.raises(build.BuildError):
        _build(dataset)


@pytest.mark.parametrize("use_listing", [False, True])
def test_a_record_pointing_at_a_missing_pdf_is_refused(dataset, use_listing):
    source = dataset / "dataset"
    record_path = source / "local" / "E2002AAA" / "record.json"
    record = json.loads(record_path.read_text("utf-8"))
    record["pdf"] = "E2002AAA.pdf"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    listing = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    with pytest.raises(build.BuildError, match="has no PDF file"):
        _build(dataset, available=listing if use_listing else None)


def test_an_unsafe_folder_id_is_refused(dataset):
    record_path = dataset / "dataset" / "local" / "E2002AAA" / "record.json"
    record = json.loads(record_path.read_text("utf-8"))
    record["folder"]["id"] = "../escape"
    record_path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)


def test_a_common_word_is_not_indexed():
    assert "the" not in textindex.document_terms({"text": "the phonograph"})


def test_a_pair_with_a_common_word_is_not_indexed():
    assert "the phonograph" not in textindex.document_terms({"text": "the phonograph"})


def test_a_phrase_of_common_and_uncommon_words_is_looked_up_by_its_uncommon_words():
    assert textindex.phrase_keys(["edison", "and", "company"]) == ["edison", "compani"]


def test_word_pairs_are_spread_over_hashed_files():
    assert textindex.shard_of("storage battery") == f"p{textindex.fnv1a('storage battery') % textindex.PAIR_SHARDS:03x}"


def test_a_short_word_is_padded_to_its_three_letter_file():
    assert textindex.shard_of("x1") == "x1_"


def test_postings_store_the_gap_from_the_previous_document(dataset):
    _build(dataset)
    assert _data(dataset, f"index/{textindex.shard_of('cement')}.json")["cement"] == [0, 512, 1, 512, 1, 512]


def test_a_withdrawal_also_removes_the_edisondigital_copy(dataset):
    snapshot = dataset / "dataset" / "edisondigital" / "S1"
    snapshot.mkdir(parents=True)
    (snapshot / "records.jsonl").write_text(json.dumps(_record("D9999AAA", status="edition")) + "\n", "utf-8")
    (dataset / "dataset" / "edisondigital" / "current.json").write_text(json.dumps({"snapshot": "S1"}), "utf-8")
    manifest_path = dataset / "dataset" / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["withdrawn"] = ["D9999AAA"]
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    assert _build(dataset)["documents"] == 3


def test_a_refused_build_leaves_the_previous_build_live(dataset):
    first = _build(dataset)
    record_path = dataset / "dataset" / "local" / "E2002AAA" / "record.json"
    record = json.loads(record_path.read_text("utf-8"))
    record["folder"]["id"] = "../escape"
    record_path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)
    assert json.loads((dataset / "site" / "data" / "current.json").read_text("utf-8"))["revision"] == first["revision"]


def test_a_record_whose_id_differs_from_its_folder_name_is_refused(dataset):
    record_path = dataset / "dataset" / "local" / "E2002AAA" / "record.json"
    record = json.loads(record_path.read_text("utf-8"))
    record["id"] = "E2002ZZZ"
    record_path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)


def _listing(tmp):
    root = tmp / "dataset"
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_a_build_from_the_file_listing_needs_no_scans_on_disk(dataset):
    listing = _listing(dataset)
    for scan in [*(dataset / "dataset").rglob("full/*.jpg"), *(dataset / "dataset").rglob("view/*.jpg")]:
        scan.unlink()
    assert _build(dataset, available=listing)["documents"] == 3


def test_a_page_missing_from_the_file_listing_is_refused(dataset):
    listing = _listing(dataset) - {"local/E2002AAB/full/0001.jpg"}
    with pytest.raises(build.BuildError):
        _build(dataset, available=listing)


def test_only_each_documents_first_thumbnail_is_downloaded(dataset):
    assert build.first_thumbnails(dataset / "dataset", _listing(dataset)) == [
        f"local/E2002AA{c}/thumb/0001.jpg" for c in "ABC"]


def test_the_deploy_downloads_records_and_first_thumbnails_but_no_scans(dataset, monkeypatch):
    import huggingface_hub
    asked = []
    listing = sorted(_listing(dataset))

    class FakeApi:
        def dataset_info(self, repo, token):
            return type("Info", (), {"id": repo, "sha": "abc123", "private": False, "gated": False})()

        def list_repo_files(self, repo, repo_type, revision, token):
            return listing

    def fake_snapshot(repo, allow_patterns, **options):
        asked.extend(allow_patterns)
        return str(dataset / "dataset")

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot)
    build.download_public_dataset("org/repo", dataset / "cache")
    assert not [p for p in asked if "/view/" in p or "/full/" in p or p.endswith((".pdf", "**"))]


def _related(tmp, model="arctic-embed-s", near=None):
    path = tmp / "dataset" / "embeddings" / model / "related.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    near = near or {"E2002AAA": [["E2002AAA", 1.0], ["E2002ZZZ", 0.9], ["E2002AAC", 0.8], ["E2002AAB", 0.7]]}
    path.write_text(json.dumps(near), "utf-8")


def _related_of(tmp, document_id):
    documents = _data(tmp, "folders/E2002.json")["documents"]
    return next(d for d in documents if d["id"] == document_id).get("related")


def _ordinal(tmp, document_id):
    return next(n for n, s in enumerate(_data(tmp, "docs/0.json")) if s["id"] == document_id)


def test_a_document_lists_its_related_documents_in_order_by_ordinal(dataset):
    _related(dataset)
    _build(dataset)
    assert _related_of(dataset, "E2002AAA") == [_ordinal(dataset, "E2002AAC"), _ordinal(dataset, "E2002AAB")]


def test_a_related_document_missing_from_the_build_is_left_out(dataset):
    _related(dataset, near={"E2002AAA": [["E2002ZZZ", 0.9]]})
    _build(dataset)
    assert _related_of(dataset, "E2002AAA") is None


def test_the_stronger_models_related_documents_are_preferred(dataset):
    _related(dataset, "arctic-embed-s", {"E2002AAA": [["E2002AAB", 0.9]]})
    _related(dataset, "qwen3-embedding-0.6b", {"E2002AAA": [["E2002AAC", 0.9]]})
    _build(dataset)
    assert _related_of(dataset, "E2002AAA") == [_ordinal(dataset, "E2002AAC")]


def test_a_document_missing_from_the_preferred_model_uses_the_other_model(dataset):
    _related(dataset, "arctic-embed-s", {"E2002AAA": [["E2002AAB", 0.9]]})
    _related(dataset, "qwen3-embedding-0.6b", {"E2002AAC": [["E2002AAB", 0.9]]})
    _build(dataset)
    assert _related_of(dataset, "E2002AAA") == [_ordinal(dataset, "E2002AAB")]


def test_a_build_without_embeddings_lists_no_related_documents(dataset):
    _build(dataset)
    assert _related_of(dataset, "E2002AAA") is None


def test_no_more_than_six_related_documents_are_listed():
    ids = {f"D{n}": n for n in range(10)}
    assert len(build.related_ordinals("D0", {"D0": list(ids)}, ids)) == build.RELATED_COUNT


def _store(tmp, ids=("E2002AAA", "E2002AAB", "E2002ZZZ"), dim=384):
    import numpy as np
    root = tmp / "dataset" / "embeddings" / "arctic-embed-s"
    (root / "parts").mkdir(parents=True, exist_ok=True)
    keys = [f"k{n}" for n in range(len(ids))]
    vectors = np.zeros((len(ids), dim), dtype=np.float16)
    vectors[:, 0] = 1.0
    np.save(root / "parts" / "part-00000.npy", vectors)
    (root / "parts" / "part-00000.keys").write_text("\n".join(keys) + "\n", "utf-8")
    (root / "manifest.json").write_text(json.dumps({"parts": [{"name": "part-00000", "count": len(ids)}]}), "utf-8")
    (root / "documents.jsonl").write_text("".join(json.dumps({"id": i, "keys": [k]}) + "\n" for i, k in zip(ids, keys)), "utf-8")


def _vector_meta(tmp):
    return _data(tmp, "vectors/meta.json")


def test_a_build_writes_a_vector_for_each_passage_of_its_documents(dataset):
    _store(dataset)
    _build(dataset)
    assert _vector_meta(dataset)["count"] == 2


def test_passage_vectors_are_stored_as_signed_bytes(dataset):
    _store(dataset)
    _build(dataset)
    revision = json.loads((dataset / "site" / "data" / "current.json").read_text("utf-8"))["revision"]
    assert (dataset / "site" / "data" / revision / "vectors" / "vectors.i8").read_bytes()[0] == 127


def test_a_build_without_embeddings_has_no_search_by_meaning(dataset):
    _build(dataset)
    revision = json.loads((dataset / "site" / "data" / "current.json").read_text("utf-8"))["revision"]
    assert not (dataset / "site" / "data" / revision / "vectors").exists()


def test_vectors_of_the_wrong_width_are_refused(dataset):
    _store(dataset, dim=12)
    with pytest.raises(build.BuildError):
        _build(dataset)


def test_a_document_referencing_a_missing_vector_is_refused(dataset):
    _store(dataset)
    root = dataset / "dataset" / "embeddings" / "arctic-embed-s"
    path = root / "documents.jsonl"
    path.write_text(json.dumps({"id": "E2002AAA", "keys": ["missing"]}) + "\n", encoding="utf-8")
    with pytest.raises(build.BuildError, match="missing semantic passage vector"):
        _build(dataset)


def test_the_browsers_query_model_is_the_hosts_document_model():
    import embed
    assert (build.SEARCH_MODEL["revision"], build.SEARCH_MODEL["query_prefix"]) == (
        embed.MODELS["arctic-embed-s"]["onnx"]["revision"], embed.MODELS["arctic-embed-s"]["query_prefix"])
