"""The builder: one tokenizer with the site, ours-first merging, and refusing any build that would publish something wrong."""
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

import build
import textindex
from passage import key_of, passages

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


def scanned(document_id, *hashes):
    return {"id": document_id, "pages": [{"sha256": h} for h in hashes]}


def test_two_documents_showing_the_same_scan_are_reported_as_the_same_pages_regression_ce85002_ce85003_22_november():
    records = {r["id"]: r for r in [scanned("CE85002", "a"), scanned("CE85003", "a"), scanned("CE85004", "b")]}
    assert build.shared_scans(records) == [{"documents": ["CE85002", "CE85003"], "identical": True}]


def test_a_page_range_that_swallows_its_neighbours_is_reported_as_one_group():
    records = {r["id"]: r for r in [scanned("E2037AAA", "a", "b", "c"), scanned("E2037AAB", "b"), scanned("E2037AAC", "c")]}
    assert build.shared_scans(records) == [{"documents": ["E2037AAA", "E2037AAB", "E2037AAC"], "identical": False}]


def test_pages_without_a_hash_are_never_reported_as_shared():
    records = {r["id"]: r for r in [{"id": "A1", "pages": [{}]}, {"id": "A2", "pages": [{}]}]}
    assert build.shared_scans(records) == []


def test_shared_scans_with_unknown_pages_are_not_reported_as_identical():
    records = {r["id"]: r for r in [scanned("A1", "a", None), scanned("A2", "a", None)]}
    assert build.shared_scans(records) == [{"documents": ["A1", "A2"], "identical": False}]


def test_an_unwritable_scan_summary_does_not_stop_the_build(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))
    build.report_shared_scans([{"documents": ["A1", "A2"], "identical": True}])
    assert "could not write the shared scan job summary" in caplog.text


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


def _store(tmp, ids=("E2002AAA", "E2002AAB", "E2002ZZZ"), dim=384, *, include_transcription=False):
    import numpy as np
    root = tmp / "dataset" / "embeddings" / "arctic-embed-s"
    (root / "parts").mkdir(parents=True, exist_ok=True)
    records, _ = build.load_local(tmp / "dataset")
    documents = []
    for document_id in ids:
        if document_id in records:
            record = records[document_id]
            record["description"] = build.description_for(record, {})
            texts = passages(record)
            keys = [key_of(text) for text in (texts if include_transcription else texts[:1])]
        else:
            keys = [key_of(document_id)]
        documents.append({"id": document_id, "keys": keys})
    keys = list(dict.fromkeys(key for document in documents for key in document["keys"]))
    vectors = np.zeros((len(keys), dim), dtype=np.float16)
    vectors[:, 0] = 1.0
    np.save(root / "parts" / "part-00000.npy", vectors)
    (root / "parts" / "part-00000.keys").write_text("\n".join(keys) + "\n", "utf-8")
    (root / "manifest.json").write_text(json.dumps({"parts": [{"name": "part-00000", "count": len(keys)}]}), "utf-8")
    (root / "documents.jsonl").write_text("".join(json.dumps(document) + "\n" for document in documents), "utf-8")


def _vector_meta(tmp):
    return _data(tmp, "vectors/meta.json")


def test_a_build_writes_a_vector_for_each_passage_of_its_documents(dataset):
    _store(dataset)
    _build(dataset)
    assert _vector_meta(dataset)["count"] == 2


def test_an_edited_description_leaves_only_the_unchanged_transcription_vector(dataset):
    _store(dataset, ids=("E2002AAA",), include_transcription=True)
    path = dataset / "dataset" / "local" / "E2002AAA" / "record.json"
    record = json.loads(path.read_text("utf-8"))
    record["description"] = "A new editorial abstract."
    path.write_text(json.dumps(record), "utf-8")

    _build(dataset)
    assert _vector_meta(dataset)["count"] == 1


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


def _filed(*headings_per_document):
    return [_record(f"D{n}", subjects=list(headings)) for n, headings in enumerate(headings_per_document)]


def test_a_subject_heading_on_enough_documents_becomes_a_topic():
    topics = build.generated_topics(_filed(*[["Radio"]] * 3), [], minimum=3)
    assert [t["title"] for t in topics] == ["Radio"]


def test_a_subject_heading_on_too_few_documents_is_not_a_topic_yet():
    assert build.generated_topics(_filed(*[["Radio"]] * 2), [], minimum=3) == []


def test_capitals_and_plurals_do_not_split_a_heading_into_two_topics():
    topics = build.generated_topics(_filed(["Incandescent Lamp"], ["incandescent lamps"], ["Incandescent lamp"]), [], minimum=3)
    assert len(topics) == 1


def test_a_heading_a_curated_topic_names_is_not_repeated_as_its_own_topic():
    curated = [{"id": "radio", "title": "Wireless", "subjects": ["radio"]}]
    assert build.generated_topics(_filed(*[["Radio"]] * 3), curated, minimum=3) == []


def test_fragments_of_a_heading_split_at_an_ampersand_are_not_topics():
    filed = _filed(*[["Awards &amp", "honors/clubs &amp", "societies"]] * 3)
    assert build.generated_topics(filed, [], minimum=3) == []


def test_a_generated_topic_holds_only_its_exact_heading_not_longer_ones():
    filed = _filed(["Electric light"], ["Electric light and power"])
    topic = {"id": "subject-electric-light", "title": "Electric light", "subjects": ["Electric light"], "generated": True}
    assert build.topic_documents(topic, build.subject_index(filed), [], {}) == [0]


def test_a_curated_topic_takes_longer_headings_that_contain_its_words():
    filed = _filed(["Marine lighting"], ["Moonlighting"], ["Arc lighting"])
    topic = {"id": "electric-light", "title": "Electric light", "subjects": ["lighting"]}
    assert build.topic_documents(topic, build.subject_index(filed), [], {}) == [0, 2]


def test_a_generated_topic_does_not_include_a_fragment_of_another_heading():
    filed = _filed(["Societies"], ["Clubs &amp", "societies", "Radio"])
    topic = {"subjects": ["Societies"], "generated": True}
    assert build.topic_documents(topic, build.subject_index(filed), [], {}) == [0]


def test_long_headings_get_unique_topic_ids_that_fit_the_topic_route():
    prefix = "Electrical testing of industrial devices using several experiments and materials"
    filed = _filed([f"{prefix} in France", f"{prefix} in Germany", f"{prefix} in Italy"])
    curated = [{"id": "subject-electrical-testing-of-industrial-devices-using-several-experiments-and-m"}]
    topics = build.generated_topics(filed, curated, minimum=1)
    ids = {topic["id"] for topic in topics if build._TOPIC_ID.fullmatch(topic["id"])}
    assert len(ids - {curated[0]["id"]}) == 3


def test_every_kind_of_document_is_named_in_the_plural():
    records = [{"type": "Letter"}, {"type": "Letter"}, {"type": "Legal Testimony"}, {"type": "Speech"}]
    assert build.folder_description(records).endswith("mostly letters, legal testimonies and speeches.")


def test_a_bracketed_cataloguing_state_is_not_named_as_a_kind_of_document():
    assert build.folder_description([{"type": "[Unedited]"}, {"type": "Letter"}]).endswith("mostly letters.")


def test_a_topic_page_lists_exactly_the_documents_its_count_says(dataset):
    _build(dataset)
    topic = _data(dataset, "topics.json")[0]
    assert len(_data(dataset, f"topics/{topic['id']}.json")["ordinals"]) == topic["count"]


def test_the_catalogue_carries_only_featured_topics_and_the_number_of_all_of_them(dataset, monkeypatch):
    for n, path in enumerate(sorted((dataset / "dataset" / "local").glob("*/record.json"))):
        record = json.loads(path.read_text("utf-8"))
        path.write_text(json.dumps({**record, "subjects": ["Radio"]}), "utf-8")
    monkeypatch.setattr(build, "TOPIC_MIN_DOCUMENTS", 3)
    _build(dataset)
    catalog = _data(dataset, "catalog.json")
    assert ([t["id"] for t in catalog["topics"]], catalog["topic_count"]) == (["cement"], 2)


def test_a_topic_id_that_could_break_a_url_stops_the_build(dataset):
    (dataset / "web" / "data" / "topics.json").write_text(json.dumps([{"id": "../cement", "title": "Cement", "any": ["cement"]}]), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)


def test_duplicate_topic_ids_cannot_overwrite_another_topics_membership(dataset):
    path = dataset / "web" / "data" / "topics.json"
    path.write_text(json.dumps([{"id": "cement", "title": name, "any": [name]} for name in ("Cement", "Radio")]), "utf-8")
    with pytest.raises(build.BuildError, match="duplicate topic id"):
        _build(dataset)


def test_a_generated_topic_is_described_by_the_heading_it_comes_from():
    assert build.topic_description("Radio", [{"type": "Letter", "year": 1920}]) == "Documents the editors filed under “Radio”, 1920, mostly letters."


LEDGER_PAGE = {"position": "single", "content": "table", "lines": ["Gold & Stock Page 2"], "people": ["Gault, Jacob"], "places": ["Newark"],
               "drawings": [], "tables": [{"caption": "In account with James Hammit", "columns": ["Month", "Day", "Particulars", "Amount"],
                                           "rows": [["1873", "Jan", "By Cash [tick]", "125.00"], ["", "", "[struck out: Interest] Balance due", "70 00"]]}]}


def _account_book(dataset, volume_id="A234A", scans=2, subjects=("Edison & Murray",), mirrored=()):
    """Publishes one account book of `scans` openings into the test dataset, with mirrored scans for the numbers given."""
    root = dataset / "dataset"
    openings = [{"n": n, "id": f"{volume_id}-{n - 1:04d}", "width": 30, "height": 40, "status": "draft",
                 "source": f"https://edisondigital.rutgers.edu/files/original/{volume_id}/au{n}.jpg", "pages": [LEDGER_PAGE]}
                for n in range(1, scans + 1)]
    volume = {"id": volume_id, "collection": "accounts", "title": "Account, Edison and Murray, 1873", "date": "1873",
              "years": [1873, 1873], "subjects": list(subjects), "series": ["Edison & Murray"], "status": "draft", "openings": openings,
              "pdf": None}
    (root / "volumes" / volume_id).mkdir(parents=True)
    (root / "volumes" / volume_id / "volume.json").write_text(json.dumps(volume), "utf-8")
    (root / "volumes" / "manifest.json").write_text(json.dumps({"volumes": {volume_id: {"openings": scans}}, "withdrawn": []}), "utf-8")
    for n in mirrored:
        for size in ("thumb", "full"):
            (root / "scans" / volume_id / size).mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (30, 40), "white").save(root / "scans" / volume_id / size / f"{n:04d}.jpg")
    return dataset


def test_ledger_amounts_are_left_out_of_the_searchable_text_but_years_stay():
    assert build.opening_text([LEDGER_PAGE]).split("\n")[2] == "1873 Jan By Cash"


def test_a_year_followed_by_punctuation_stays_searchable_in_a_ledger():
    assert build.opening_text([{"lines": ["March 1909, by cash."]}]) == "March 1909, by cash."


def test_a_struck_out_word_is_still_searchable_as_written():
    assert "Interest Balance due" in build.opening_text([LEDGER_PAGE])


def test_an_opening_is_described_by_its_account_heading():
    assert build.opening_description([LEDGER_PAGE]) == "In account with James Hammit"


def test_every_account_book_scan_becomes_a_searchable_entry(dataset):
    assert _build(_account_book(dataset))["documents"] == 5


def test_an_account_book_scan_opens_in_the_account_reader_at_its_scan(dataset):
    _build(_account_book(dataset))
    summary = next(s for s in _data(dataset, "docs/0.json") if s["id"] == "A234A-0001")
    assert (summary["kind"], summary["volume"], summary["scan"]) == ("account", "A234A", 2)


def test_an_account_book_scan_is_found_by_a_name_in_its_ledger(dataset):
    _build(_account_book(dataset))
    shard = _data(dataset, f"index/{textindex.shard_of(textindex.stem('hammit'))}.json")
    assert textindex.stem("hammit") in shard


def test_a_scan_not_yet_mirrored_has_no_thumbnail(dataset):
    _build(_account_book(dataset))
    assert next(s for s in _data(dataset, "docs/0.json") if s["id"] == "A234A-0000")["thumb"] is None


def test_a_mirrored_scan_uses_the_mirrors_thumbnail_from_the_data_host(dataset):
    _build(_account_book(dataset, mirrored=(1,)))
    summary = next(s for s in _data(dataset, "docs/0.json") if s["id"] == "A234A-0000")
    assert (summary["thumb"], summary["thumb_from"]) == ("scans/A234A/thumb/0001.jpg", "dataset")


def test_the_book_list_gives_each_account_book_its_scan_count_and_cover(dataset):
    _build(_account_book(dataset, mirrored=(1,)))
    assert [(v["id"], v["count"], v["thumb"]) for v in _data(dataset, "volumes.json")] == [("A234A", 2, "scans/A234A/thumb/0001.jpg")]


def test_the_catalogue_names_each_account_book_for_the_collection_filter(dataset):
    _build(_account_book(dataset))
    assert _data(dataset, "catalog.json")["volumes"] == [{"id": "A234A", "title": "Account, Edison and Murray, 1873"}]


def test_an_account_books_subject_becomes_a_topic_once_it_holds_enough_scans(dataset, monkeypatch):
    monkeypatch.setattr(build, "TOPIC_MIN_DOCUMENTS", 2)
    _build(_account_book(dataset))
    assert any(t["title"] == "Edison & Murray" and t["count"] == 2 for t in _data(dataset, "topics.json"))


def test_a_featured_topic_leaves_out_ledger_scans_filed_under_its_subject(dataset):
    (dataset / "web" / "data" / "topics.json").write_text(json.dumps([{"id": "cement", "title": "Cement", "subjects": ["cement"]}]), "utf-8")
    _build(_account_book(dataset, subjects=("Edison Portland Cement Company",)))
    assert next(t for t in _data(dataset, "topics.json") if t["id"] == "cement")["count"] == 0


def test_a_withdrawn_account_book_is_left_out_of_the_build(dataset):
    _account_book(dataset)
    path = dataset / "dataset" / "volumes" / "manifest.json"
    path.write_text(json.dumps({"volumes": {"A234A": {}}, "withdrawn": ["A234A"]}), "utf-8")
    assert _build(dataset)["documents"] == 3


def test_an_opening_that_shares_an_id_with_a_document_stops_the_build(dataset):
    _account_book(dataset, volume_id="E2002AAA")
    path = dataset / "dataset" / "volumes" / "E2002AAA" / "volume.json"
    volume = json.loads(path.read_text("utf-8"))
    volume["openings"][0]["id"] = "E2002AAB"
    path.write_text(json.dumps(volume), "utf-8")
    with pytest.raises(build.BuildError):
        _build(dataset)


def test_the_book_list_says_which_scans_the_mirror_holds_as_ranges(dataset):
    _build(_account_book(dataset, scans=4, mirrored=(1, 2, 4)))
    assert _data(dataset, "volumes.json")[0]["mirrored"] == "1-2,4"
