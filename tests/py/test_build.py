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

    assert (out / "assets" / revision / "js" / "home.js").read_text("utf-8") == "// home"


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


def test_a_page_pointing_at_a_missing_file_is_refused(dataset):
    (dataset / "dataset" / "local" / "E2002AAA" / "view" / "0001.jpg").unlink()
    with pytest.raises(build.BuildError):
        _build(dataset)


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
