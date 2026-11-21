"""Embeddings: public text only, each passage encoded once, and a store that is either whole or untouched."""
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import numpy as np
import pytest

import embed

MODEL = "arctic-embed-s"
DIM = embed.MODELS[MODEL]["dim"]


def _record(document_id, status="approved", words=40, **fields):
    return {"id": document_id, "title": f"[{document_id}], Letter about batteries", "date": "1919-12-29", "author": "Traiser, Louis M",
            "recipient": "Edison, Thomas Alva", "status": status, "transcription": " ".join(f"w{document_id}{n}" for n in range(words)),
            "folder": {"id": "E2002", "title": "Advice"}, "pages": [], **fields}


def _dataset(tmp_path, records, withdrawn=()):
    local = tmp_path / "dataset" / "local"
    for record in records:
        (local / record["id"]).mkdir(parents=True)
        (local / record["id"] / "record.json").write_text(json.dumps(record), "utf-8")
    documents = {r["id"]: {} for r in records}
    (local / "manifest.json").write_text(json.dumps({"folders": {"E2002": {"documents": documents}}, "withdrawn": list(withdrawn)}), "utf-8")
    return tmp_path / "dataset"


def _fake_encoder(calls):
    """A stand-in model that records what it was asked to encode and returns a unit vector per text."""
    def encode(texts):
        calls.extend(texts)
        rng = np.random.default_rng(len(texts))
        v = rng.normal(size=(len(texts), DIM)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)
    return encode


def _run(tmp_path, records, calls=None, **plan_options):
    dataset = _dataset(tmp_path, records) if not (tmp_path / "dataset").exists() else tmp_path / "dataset"
    store = tmp_path / "store"
    embed.plan(dataset, store, MODEL, **plan_options)
    embed.encode_chunks(store, MODEL, _fake_encoder([] if calls is None else calls))
    return embed.collect(store, MODEL)


def test_a_passage_heading_drops_the_bracketed_docid_from_the_title():
    assert embed.heading(_record("E2002AAA")).startswith("Letter about batteries.")


def test_a_passage_heading_names_writer_and_recipient():
    assert "Traiser, Louis M to Edison, Thomas Alva" in embed.heading(_record("E2002AAA"))


def test_a_document_without_a_transcription_still_gets_its_catalogue_passage():
    assert len(embed.passages(_record("E2002AAA", words=0))) == 1


def test_a_long_transcription_is_cut_into_overlapping_windows():
    assert len(embed.passages(_record("E2002AAA", words=400))) == 1 + 3


def test_every_window_of_a_transcription_carries_the_documents_heading():
    record = _record("E2002AAA", words=400)
    assert all(p.startswith(embed.heading(record)) for p in embed.passages(record)[1:])


def test_the_approved_scope_leaves_drafts_out(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA"), _record("E2002AAB", status="draft")])
    assert list(embed.wanted(dataset, "approved")) == ["E2002AAA"]


def test_a_withdrawn_document_is_never_embedded(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA"), _record("E2002AAB")], withdrawn=["E2002AAB"])
    assert list(embed.wanted(dataset, "all")) == ["E2002AAA"]


def test_reviewer_fields_never_reach_a_passage(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA", reviewer="Secret Reviewer", editors_notes="internal")])
    assert "Secret Reviewer" not in json.dumps(embed.wanted(dataset, "all"))


def test_a_collected_store_lists_every_document(tmp_path):
    assert _run(tmp_path, [_record("E2002AAA"), _record("E2002AAB")])["documents"] == 2


def test_a_second_plan_with_nothing_changed_has_nothing_to_encode(tmp_path):
    _run(tmp_path, [_record("E2002AAA")])
    assert embed.plan(tmp_path / "dataset", tmp_path / "store", MODEL)["to_encode"] == 0


def test_an_edited_document_re_encodes_only_its_changed_passages(tmp_path):
    _run(tmp_path, [_record("E2002AAA", words=400)])
    record = json.loads((tmp_path / "dataset/local/E2002AAA/record.json").read_text("utf-8"))
    record["transcription"] += " postscript"
    (tmp_path / "dataset/local/E2002AAA/record.json").write_text(json.dumps(record), "utf-8")
    assert embed.plan(tmp_path / "dataset", tmp_path / "store", MODEL)["to_encode"] == 1


def test_collect_refuses_while_a_chunk_is_not_yet_encoded(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    embed.plan(dataset, tmp_path / "store", MODEL)
    with pytest.raises(embed.EmbedError):
        embed.collect(tmp_path / "store", MODEL)


def test_a_refused_collect_leaves_no_manifest(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    embed.plan(dataset, tmp_path / "store", MODEL)
    with pytest.raises(embed.EmbedError):
        embed.collect(tmp_path / "store", MODEL)
    assert not (tmp_path / "store" / MODEL / "manifest.json").exists()


def test_an_encoded_chunk_is_skipped_when_a_job_runs_again(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    embed.plan(dataset, tmp_path / "store", MODEL)
    embed.encode_chunks(tmp_path / "store", MODEL, _fake_encoder([]))
    assert embed.encode_chunks(tmp_path / "store", MODEL, _fake_encoder([])) == 0


def test_a_replan_can_collect_changed_passages_after_an_earlier_chunk_was_encoded(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    store = tmp_path / "store"
    encoder = _fake_encoder([])
    embed.plan(dataset, store, MODEL)
    embed.encode_chunks(store, MODEL, encoder)
    path = dataset / "local" / "E2002AAA" / "record.json"
    record = json.loads(path.read_text("utf-8"))
    record["transcription"] += " postscript"
    path.write_text(json.dumps(record), "utf-8")

    embed.plan(dataset, store, MODEL)
    embed.encode_chunks(store, MODEL, encoder)
    assert embed.collect(store, MODEL)["passages"] == 2


def test_a_worker_finishing_after_a_replan_does_not_add_duplicate_passages(tmp_path, monkeypatch):
    store = tmp_path / "store"
    encoder = _fake_encoder([])
    monkeypatch.setattr(embed, "wanted", lambda *_: {"D1": [(key, key) for key in "abcd"]})
    embed.plan(tmp_path, store, MODEL, chunk=2)

    def finish_old_chunk(texts):
        monkeypatch.setattr(embed, "wanted", lambda *_: {"D1": [(key, key) for key in "bcde"]})
        embed.plan(tmp_path, store, MODEL, chunk=2)
        embed.encode_chunks(store, MODEL, encoder)
        return encoder(texts)

    embed.encode_chunks(store, MODEL, finish_old_chunk, only=0)
    embed.collect(store, MODEL)
    assert sorted(embed.load_vectors(store / MODEL)[0]) == list("bcde")


def test_a_replan_waits_for_collection_and_keeps_its_new_chunk(tmp_path, monkeypatch):
    store = tmp_path / "store"
    encoder = _fake_encoder([])
    monkeypatch.setattr(embed, "wanted", lambda *_: {"D1": [("a", "a")]})
    embed.plan(tmp_path, store, MODEL)
    embed.encode_chunks(store, MODEL, encoder)
    collecting, replanning, release = Event(), Event(), Event()
    write_json = embed._write_json

    def pause_collection(path, value):
        if path.name == "manifest.json":
            collecting.set()
            assert release.wait(timeout=5)
        write_json(path, value)

    def replan():
        replanning.set()
        return embed.plan(tmp_path, store, MODEL)

    monkeypatch.setattr(embed, "_write_json", pause_collection)
    monkeypatch.setattr(embed, "wanted", lambda *_: {"D1": [("a", "a"), ("b", "b")]})
    with ThreadPoolExecutor(max_workers=2) as workers:
        old = workers.submit(embed.collect, store, MODEL)
        try:
            assert collecting.wait(timeout=5)
            new = workers.submit(replan)
            assert replanning.wait(timeout=5)
            with pytest.raises(TimeoutError):
                new.result(timeout=0.1)
        finally:
            release.set()
        old.result(timeout=5)
        new.result(timeout=5)

    assert embed.encode_chunks(store, MODEL, encoder) == 1


def test_a_retry_reuses_a_legacy_chunk_when_its_passage_keys_match(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    store = tmp_path / "store"
    root = store / MODEL
    embed.plan(dataset, store, MODEL)
    chunk = root / "todo" / "chunk-00000.jsonl"
    keys = [json.loads(row)["key"] for row in chunk.read_text("utf-8").splitlines()]
    embed._save_rows(root / "done" / chunk.stem, keys, _fake_encoder([])(keys))

    assert embed.encode_chunks(store, MODEL, _fake_encoder([])) == 0


def test_collect_repairs_duplicate_rows_left_in_an_existing_store(tmp_path, monkeypatch):
    store = tmp_path / "store"
    root = store / MODEL
    keys = ["a", "b", "b", "c", "d"]
    embed._save_rows(root / "parts" / "part-00000", keys, _fake_encoder([])(keys))
    embed._write_json(root / "manifest.json", {"parts": [{"name": "part-00000", "count": len(keys)}]})
    monkeypatch.setattr(embed, "wanted", lambda *_: {"D1": [(key, key) for key in "abcd"]})
    embed.plan(tmp_path, store, MODEL)

    embed.collect(store, MODEL)
    assert sorted(embed.load_vectors(root)[0]) == list("abcd")


def test_a_job_array_task_encodes_only_its_own_chunk(tmp_path):
    dataset = _dataset(tmp_path, [_record(f"E2002AA{c}") for c in "ABCD"])
    embed.plan(dataset, tmp_path / "store", MODEL, chunk=2)
    assert embed.encode_chunks(tmp_path / "store", MODEL, _fake_encoder([]), only=1) == 2


def test_a_model_returning_the_wrong_width_is_refused(tmp_path):
    dataset = _dataset(tmp_path, [_record("E2002AAA")])
    embed.plan(dataset, tmp_path / "store", MODEL)
    with pytest.raises(embed.EmbedError):
        embed.encode_chunks(tmp_path / "store", MODEL, lambda texts: np.zeros((len(texts), 3), np.float32))


def test_stored_vectors_are_half_precision(tmp_path):
    _run(tmp_path, [_record("E2002AAA")])
    assert embed.load_vectors(tmp_path / "store" / MODEL)[1].dtype == np.float16


def test_withdrawing_most_documents_compacts_the_dead_rows_away(tmp_path):
    _run(tmp_path, [_record(f"E2002AA{c}") for c in "ABCD"])
    manifest = json.loads((tmp_path / "dataset/local/manifest.json").read_text("utf-8"))
    manifest["withdrawn"] = ["E2002AAB", "E2002AAC", "E2002AAD"]
    (tmp_path / "dataset/local/manifest.json").write_text(json.dumps(manifest), "utf-8")
    result = _run(tmp_path, [])
    assert sum(p["count"] for p in result["parts"]) == result["passages"]


def test_withdrawing_one_document_removes_its_vectors_and_invalidates_related_documents(tmp_path):
    ids = [f"E2002AA{suffix}" for suffix in "ABCDEF"]
    _run(tmp_path, [_record(doc, title=f"Letter {doc}") for doc in ids])
    root = tmp_path / "store" / MODEL
    related = root / "related.json"
    related.write_text(json.dumps(embed.related(root)), encoding="utf-8")
    manifest_path = tmp_path / "dataset" / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["withdrawn"] = [ids[-1]]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run(tmp_path, [])
    assert (sum(part["count"] for part in result["parts"]), related.exists()) == (result["passages"], False)


def test_a_document_is_never_its_own_related_document(tmp_path):
    _run(tmp_path, [_record(f"E2002AA{c}") for c in "ABC"])
    assert all(doc not in [other for other, _ in near] for doc, near in embed.related(tmp_path / "store" / MODEL, k=2).items())


def test_a_single_document_has_no_related_documents(tmp_path):
    _run(tmp_path, [_record("E2002AAA")])
    assert embed.related(tmp_path / "store" / MODEL) == {"E2002AAA": []}


def test_semantic_search_returns_no_hits_when_all_documents_are_withdrawn(tmp_path):
    _run(tmp_path, [_record("E2002AAA")])
    manifest_path = tmp_path / "dataset" / "local" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["withdrawn"] = ["E2002AAA"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _run(tmp_path, [])

    assert embed.search(tmp_path / "store" / MODEL, np.ones(DIM, dtype=np.float32)) == []


def test_a_model_without_an_onnx_export_is_refused_for_the_host():
    with pytest.raises(embed.EmbedError):
        embed.load_onnx_encoder("qwen3-embedding-0.6b")


def test_the_host_model_is_pinned_to_an_exact_revision():
    assert len(embed.MODELS["arctic-embed-s"]["onnx"]["revision"]) == 40
