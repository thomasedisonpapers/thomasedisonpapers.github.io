"""Passage embeddings for meaning-based search: planned here, encoded wherever a GPU is (Amarel), folded into an append-only store.

The store for one model lives at {store}/{model}/ and holds only public text, because passages come from the published records:
  manifest.json          the model, its settings and the immutable parts, written last so a half-finished run is never read
  documents.jsonl        each document's passage keys in reading order
  parts/part-NNNNN.npy   unit-length float16 vectors, one row per key in part-NNNNN.keys
  todo/chunk-NNNNN.jsonl passages still to encode, one file per job-array task
  done/chunk-NNNNN.npy   a finished chunk, waiting for `collect`
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

import numpy as np
from filelock import FileLock

import build

logger = logging.getLogger("embed")

# The models we can embed with; the query side must use the same model, pooling and prefixes.
MODELS = {
    "arctic-embed-s": {"hf": "Snowflake/snowflake-arctic-embed-s", "pooling": "cls", "dim": 384, "max_tokens": 512, "document_prefix": "",
                       "query_prefix": "Represent this sentence for searching relevant passages: ",
                       # Snowflake's own ONNX export, pinned so documents on the host and queries in the browser use the same weights.
                       "onnx": {"revision": "e596f507467533e48a2e17c007f0e1dacc837b33", "file": "onnx/model.onnx"}},
    "qwen3-embedding-0.6b": {"hf": "Qwen/Qwen3-Embedding-0.6B", "pooling": "last", "dim": 1024, "max_tokens": 1024, "document_prefix": "",
                             "query_prefix": "Instruct: Given a search query, retrieve letters and documents from the Thomas A. Edison Papers that answer it\nQuery: "},
}
# About 250 tokens of 1900s English per passage, overlapping so a sentence cut at one edge is whole in the next.
WINDOW_WORDS = 180
STRIDE_WORDS = 144
CHUNK_PASSAGES = 4096
_DOCID_PREFIX = re.compile(r"^\s*\[[A-Za-z0-9_-]+\],?\s*")


class EmbedError(Exception):
    """A store that would be wrong or incomplete; nothing is changed."""


def key_of(text: str) -> str:
    """A passage's identity is its exact text, so unchanged passages are never encoded twice, whichever document moved."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def heading(record: dict) -> str:
    """The context every passage carries, so a passage from page three still says who wrote to whom and when."""
    title = _DOCID_PREFIX.sub("", record.get("title") or "")
    people = " to ".join(p for p in (record.get("author"), record.get("recipient")) if p)
    return ". ".join(p for p in (title, record.get("date") or "", people) if p).strip() + "."


def passages(record: dict) -> list[str]:
    """One catalogue passage (heading, description, people, subjects, places), then overlapping windows of the transcription."""
    head = heading(record)
    names = [*(record.get("names") or []), *(record.get("mentioned") or [])]
    places = [*([record["place"]] if record.get("place") else []), *(record.get("places") or [])]
    parts = [head, record.get("description") or "", f"People: {'; '.join(dict.fromkeys(names))}." if names else "",
             f"Subjects: {'; '.join(record.get('subjects') or [])}." if record.get("subjects") else "",
             f"Places: {'; '.join(dict.fromkeys(places))}." if places else ""]
    out = [" ".join(p for p in parts if p)]
    words = (record.get("transcription") or "").split()
    for start in range(0, len(words), STRIDE_WORDS):
        out.append(f"{head}\n{' '.join(words[start:start + WINDOW_WORDS])}")
        if start + WINDOW_WORDS >= len(words):
            break
    return out


def in_scope(record: dict, scope: str) -> bool:
    """`approved` is the exported, editor-verified collections; `transcribed` adds drafts and editions; `all` adds catalogue-only items."""
    if scope == "approved":
        return record.get("status") == "approved"
    if scope == "transcribed":
        return bool(record.get("transcription"))
    return True


def wanted(dataset: Path, scope: str) -> dict[str, list[tuple[str, str]]]:
    """Each in-scope document's passages as (key, text), read from the same merged public records the site is built from."""
    local, manifest = build.load_local(dataset)
    records, _ = build.merge(local, build.load_edisondigital(dataset))
    for document_id in manifest.get("withdrawn", []):
        records.pop(document_id, None)
    return {doc: [(key_of(t), t) for t in passages(r)] for doc, r in sorted(records.items()) if in_scope(r, scope)}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _save_rows(path: Path, keys: list[str], vectors: np.ndarray) -> None:
    """Vectors and their keys side by side, each renamed into place only when whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".npy.tmp"), "wb") as f:
        np.save(f, vectors.astype(np.float16))
    path.with_suffix(".keys.tmp").write_text("\n".join(keys) + "\n", encoding="utf-8")
    os.replace(path.with_suffix(".keys.tmp"), path.with_suffix(".keys"))
    os.replace(path.with_suffix(".npy.tmp"), path.with_suffix(".npy"))


def _load_rows(path: Path) -> tuple[list[str], np.ndarray]:
    keys = path.with_suffix(".keys").read_text(encoding="utf-8").split()
    vectors = np.load(path.with_suffix(".npy"))
    if len(keys) != len(vectors):
        raise EmbedError(f"{path.name} has {len(vectors)} vectors for {len(keys)} keys")
    return keys, vectors


def read_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"parts": []}


def part_keys(root: Path, parts: list[dict]) -> set[str]:
    keys = set()
    for part in parts:
        keys.update((root / "parts" / f"{part['name']}.keys").read_text(encoding="utf-8").split())
    return keys


def stored_keys(root: Path) -> set[str]:
    """Every key already encoded, in a part or in a finished chunk."""
    keys = part_keys(root, read_manifest(root)["parts"])
    for done in (root / "done").glob("chunk-*.npy"):
        key_file = done.with_suffix(".keys")
        if key_file.exists():
            keys.update(key_file.read_text(encoding="utf-8").split())
    return keys


def plan(dataset: Path, store: Path, model: str, *, scope: str = "approved", chunk: int = CHUNK_PASSAGES) -> dict:
    """Writes the passages not yet encoded as job-sized chunks, and the document list that `collect` will make current."""
    if model not in MODELS:
        raise EmbedError(f"unknown model {model!r}; choose from {', '.join(MODELS)}")
    root = store / model
    with FileLock(root / ".plan-collect.lock"):
        return _plan(dataset, root, model, scope, chunk)


def _plan(dataset: Path, root: Path, model: str, scope: str, chunk: int) -> dict:
    documents = wanted(dataset, scope)
    have = stored_keys(root)
    todo, seen = [], set()
    for pairs in documents.values():
        for key, text in pairs:
            if key not in have and key not in seen:
                seen.add(key)
                todo.append({"key": key, "text": text})
    for old in (root / "todo").glob("chunk-*.jsonl"):
        old.unlink()
    for n, start in enumerate(range(0, len(todo), chunk)):
        path = root / "todo" / f"chunk-{n:05d}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in todo[start:start + chunk]), encoding="utf-8")
    _write_json(root / "plan.json", {"model": model, "scope": scope, "documents": {d: [k for k, _ in p] for d, p in documents.items()}})
    summary = {"documents": len(documents), "passages": sum(len(p) for p in documents.values()), "to_encode": len(todo),
               "chunks": -(-len(todo) // chunk)}
    logger.info("planned %s", summary)
    return summary


def load_onnx_encoder(model: str, batch: int = 32):
    """The same encoder on ONNX Runtime and the tokenizers library, light enough for the host's CPU with no PyTorch."""
    spec = MODELS[model]
    if "onnx" not in spec:
        raise EmbedError(f"{model} has no ONNX export; encode it on Amarel with PyTorch")
    import onnxruntime
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    revision, name = spec["onnx"]["revision"], spec["onnx"]["file"]
    tokenizer = Tokenizer.from_file(hf_hub_download(spec["hf"], "tokenizer.json", revision=revision, token=False))
    tokenizer.enable_truncation(max_length=spec["max_tokens"])
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id("[PAD]") or 0, pad_token="[PAD]")
    session = onnxruntime.InferenceSession(hf_hub_download(spec["hf"], name, revision=revision, token=False),
                                           providers=["CPUExecutionProvider"])
    wanted = {i.name for i in session.get_inputs()}

    def encode(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), spec["dim"]), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        for start in range(0, len(order), batch):
            ids = order[start:start + batch]
            encoded = tokenizer.encode_batch([texts[i] for i in ids])
            feeds = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                     "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                     "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64)}
            hidden = session.run(["last_hidden_state"], {k: v for k, v in feeds.items() if k in wanted})[0]
            pooled = hidden[:, 0] if spec["pooling"] == "cls" else hidden[:, -1]
            out[ids] = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
        return out

    return encode


def load_encoder(model: str, device: str | None = None, batch: int = 64):
    """A function from texts to unit vectors, using the model's own pooling; torch and transformers load only here."""
    import torch
    from transformers import AutoModel, AutoTokenizer

    spec = MODELS[model]
    # A copy fetched ahead of time (Amarel's compute nodes are offline) is used when present.
    local = Path(os.environ.get("EMBED_MODELS", "")) / spec["hf"].split("/")[1]
    source = str(local) if os.environ.get("EMBED_MODELS") and local.is_dir() else spec["hf"]
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(source)
    if spec["pooling"] == "last":
        tokenizer.padding_side = "left"
    net = AutoModel.from_pretrained(source, torch_dtype=torch.float16 if device == "cuda" else torch.float32).to(device).eval()

    @torch.inference_mode()
    def encode(texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), spec["dim"]), dtype=np.float32)
        # Longest first in each batch, so padding is small and an out-of-memory batch shows up at once.
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        for start in range(0, len(order), batch):
            ids = order[start:start + batch]
            enc = tokenizer([texts[i] for i in ids], padding=True, truncation=True, max_length=spec["max_tokens"], return_tensors="pt").to(device)
            hidden = net(**enc).last_hidden_state
            if spec["pooling"] == "cls":
                pooled = hidden[:, 0]
            elif spec["pooling"] == "last":
                pooled = hidden[:, -1]
            else:
                mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1)
            pooled = torch.nn.functional.normalize(pooled.float(), dim=-1)
            out[ids] = pooled.cpu().numpy()
        return out

    return encode


def encode_chunks(store: Path, model: str, encoder, *, only: int | None = None) -> int:
    """Encodes each todo chunk that has no finished output yet, so a pre-empted job array simply runs again."""
    root = store / model
    chunks = sorted((root / "todo").glob("chunk-*.jsonl"))
    if only is not None:
        chunks = [c for c in chunks if c.stem == f"chunk-{only:05d}"]
    count = 0
    for path in chunks:
        chunk = path.read_bytes()
        # The same task number can refer to different passages after replanning.
        target = root / "done" / f"chunk-{hashlib.sha256(chunk).hexdigest()}.npy"
        if target.exists() and target.with_suffix(".keys").exists():
            continue
        rows = [json.loads(line) for line in chunk.decode("utf-8").splitlines() if line.strip()]
        keys = [row["key"] for row in rows]
        legacy = root / "done" / path.with_suffix(".npy").name
        legacy_keys = legacy.with_suffix(".keys")
        if legacy.exists() and legacy_keys.exists() and legacy_keys.read_text(encoding="utf-8").split() == keys:
            continue
        vectors = encoder([MODELS[model]["document_prefix"] + r["text"] for r in rows])
        if vectors.shape != (len(rows), MODELS[model]["dim"]) or not np.isfinite(vectors).all():
            raise EmbedError(f"{path.name}: the model returned {vectors.shape} or non-finite vectors")
        _save_rows(target, keys, vectors)
        count += len(rows)
        logger.info("encoded %s (%d passages)", path.name, len(rows))
    return count


def _take_indices(keys: list[str], remaining: set[str]) -> list[int]:
    indices = []
    for index, key in enumerate(keys):
        if key in remaining:
            remaining.remove(key)
            indices.append(index)
    return indices


def _take_rows(keys: list[str], vectors: np.ndarray, remaining: set[str]) -> tuple[list[str], np.ndarray]:
    indices = _take_indices(keys, remaining)
    return [keys[index] for index in indices], vectors[indices]


def collect(store: Path, model: str) -> dict:
    """Folds finished chunks into one new part and makes the plan's documents current; refuses while any passage is missing."""
    root = store / model
    with FileLock(root / ".plan-collect.lock"):
        return _collect(root, model)


def _collect(root: Path, model: str) -> dict:
    planned = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    parts = read_manifest(root)["parts"]
    published = part_keys(root, parts)
    live = {k for keys in planned["documents"].values() for k in keys}
    missing = live - published
    done = sorted((root / "done").glob("chunk-*.npy"))
    new_keys, new_vectors = [], []
    for path in done:
        keys, vectors = _load_rows(path)
        keys, vectors = _take_rows(keys, vectors, missing)
        if keys:
            new_keys.extend(keys)
            new_vectors.append(vectors)
    if missing:
        raise EmbedError(f"{len(missing)} planned passages have no vector yet; run encode for every chunk first")
    if new_keys:
        name = f"part-{max([int(p['name'][5:]) for p in parts], default=-1) + 1:05d}"
        _save_rows(root / "parts" / name, new_keys, np.concatenate(new_vectors))
        parts.append({"name": name, "count": len(new_keys)})
    total = sum(p["count"] for p in parts)
    compacted = total > len(live)
    if compacted:
        parts = _compact(root, parts, live)
    spec = MODELS[model]
    documents_path = root / "documents.jsonl"
    documents = "".join(json.dumps({"id": d, "keys": k}) + "\n" for d, k in planned["documents"].items())
    if compacted or not documents_path.exists() or documents_path.read_text(encoding="utf-8") != documents:
        (root / "related.json").unlink(missing_ok=True)
    tmp = documents_path.with_name("documents.jsonl.tmp")
    tmp.write_text(documents, encoding="utf-8")
    os.replace(tmp, documents_path)
    for part in parts:
        part["sha256"] = hashlib.sha256((root / "parts" / f"{part['name']}.npy").read_bytes()).hexdigest()
    result = {"model": model, "hf": spec["hf"], "dim": spec["dim"], "pooling": spec["pooling"], "query_prefix": spec["query_prefix"],
              "dtype": "float16", "scope": planned["scope"], "documents": len(planned["documents"]), "passages": len(live), "parts": parts}
    # The manifest moves last, so a reader sees the old store or the new one, never a mix.
    _write_json(root / "manifest.json", result)
    for path in done:
        path.unlink()
        path.with_suffix(".keys").unlink()
    for path in (root / "todo").glob("chunk-*.jsonl"):
        path.unlink()
    keep = {p["name"] for p in parts}
    for old in (root / "parts").glob("part-*.npy"):
        if old.stem not in keep:
            old.unlink()
            old.with_suffix(".keys").unlink(missing_ok=True)
    logger.info("collected: %d documents, %d passages in %d parts", result["documents"], result["passages"], len(parts))
    return result


def _compact(root: Path, parts: list[dict], live: set[str]) -> list[dict]:
    """Rewrites only affected parts and keeps one vector per live passage."""
    remaining = live.copy()
    # New names only, never reused, so the parts the current manifest lists stay intact until it is replaced.
    next_part = max(int(p["name"][5:]) for p in parts) + 1
    out = []
    for part in parts:
        path = root / "parts" / part["name"]
        keys = path.with_suffix(".keys").read_text(encoding="utf-8").split()
        indices = _take_indices(keys, remaining)
        if len(indices) == len(keys):
            out.append(part)
        elif indices:
            _, vectors = _load_rows(path)
            name = f"part-{next_part:05d}"
            _save_rows(root / "parts" / name, [keys[i] for i in indices], vectors[indices])
            out.append({"name": name, "count": len(indices)})
            next_part += 1
    return out


def load_vectors(root: Path, parts: list[dict] | None = None, only: set[str] | None = None) -> tuple[list[str], np.ndarray]:
    """Every stored row, or one row per key in `only`."""
    parts = read_manifest(root)["parts"] if parts is None else parts
    remaining = only.copy() if only is not None else None
    keys, blocks = [], []
    for part in parts:
        k, v = _load_rows(root / "parts" / part["name"])
        if remaining is not None:
            k, v = _take_rows(k, v, remaining)
        keys += k
        blocks.append(v)
    return keys, (np.concatenate(blocks) if blocks else np.zeros((0, 1), np.float16))


def document_vectors(root: Path) -> tuple[list[str], np.ndarray]:
    """One unit vector per document, the mean of its passages, for "related documents"."""
    keys, vectors = load_vectors(root)
    row = {k: i for i, k in enumerate(keys)}
    ids, out = [], []
    for line in (root / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        doc = json.loads(line)
        mean = vectors[[row[k] for k in doc["keys"]]].astype(np.float32).mean(0)
        ids.append(doc["id"])
        out.append(mean / (np.linalg.norm(mean) or 1))
    return ids, np.array(out, dtype=np.float32)


def related(root: Path, k: int = 8, block: int = 4096) -> dict[str, list[list]]:
    """Each document's k nearest other documents with their cosine similarity, computed in blocks so memory stays bounded."""
    ids, vectors = document_vectors(root)
    limit = max(0, min(k, len(ids) - 1))
    out = {}
    for start in range(0, len(ids), block):
        scores = vectors[start:start + block] @ vectors.T
        for i in range(scores.shape[0]):
            scores[i, start + i] = -np.inf
        top = np.argsort(-scores, axis=1)[:, :limit]
        for i, row in enumerate(top):
            out[ids[start + i]] = [[ids[j], round(float(scores[i, j]), 4)] for j in row]
    return out


def encode_queries(store: Path, model: str, encoder, queries: list[str]) -> None:
    """Encodes evaluation queries with the model's query prefix, so they can be tried later on a machine with no model."""
    root = store / model
    vectors = encoder([MODELS[model]["query_prefix"] + q for q in queries])
    _save_rows(root / "queries", [key_of(q) for q in queries], vectors)
    _write_json(root / "queries.json", queries)


def search(root: Path, query_vector: np.ndarray, k: int = 10) -> list[tuple[str, float]]:
    """Documents ranked by their best-matching passage, the way the site's vector search will rank them."""
    keys, vectors = load_vectors(root)
    if not keys:
        return []
    best = dict(zip(keys, (vectors.astype(np.float32) @ query_vector.astype(np.float32)).tolist()))
    scores = []
    for line in (root / "documents.jsonl").read_text(encoding="utf-8").splitlines():
        doc = json.loads(line)
        scores.append((doc["id"], max(best[k] for k in doc["keys"])))
    return sorted(scores, key=lambda pair: -pair[1])[:k]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["plan", "encode", "collect", "related", "queries", "search"])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--dataset", type=Path, help="needed by plan")
    parser.add_argument("--scope", choices=["approved", "transcribed", "all"], default="approved")
    parser.add_argument("--chunk", type=int, help="encode only this chunk number (a Slurm array index)")
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--backend", choices=["torch", "onnx"], default="torch",
                        help="torch on Amarel's GPUs, onnx for the host's CPU with no PyTorch")
    parser.add_argument("--out", type=Path, help="where related writes its JSON")
    parser.add_argument("--queries", type=Path, help="a text file of evaluation queries, one per line")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        if args.step == "plan":
            print(json.dumps(plan(args.dataset, args.store, args.model, scope=args.scope)))
        elif args.step == "encode":
            encoder = (load_onnx_encoder(args.model, batch=args.batch) if args.backend == "onnx"
                       else load_encoder(args.model, batch=args.batch))
            print(encode_chunks(args.store, args.model, encoder, only=args.chunk))
        elif args.step == "collect":
            print(json.dumps(collect(args.store, args.model)))
        elif args.step == "queries":
            lines = [q.strip() for q in args.queries.read_text(encoding="utf-8").splitlines() if q.strip() and not q.startswith("#")]
            encode_queries(args.store, args.model, load_encoder(args.model, batch=args.batch), lines)
        elif args.step == "search":
            root = args.store / args.model
            queries = json.loads((root / "queries.json").read_text(encoding="utf-8"))
            _, vectors = _load_rows(root / "queries")
            for query, vector in zip(queries, vectors):
                print()
                print(query)
                for doc, score in search(root, vector):
                    print(f"  {score:.3f}  {doc}")
        else:
            result = related(args.store / args.model)
            _write_json(args.out or args.store / args.model / "related.json", result)
    except EmbedError as error:
        logger.error("refused: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
