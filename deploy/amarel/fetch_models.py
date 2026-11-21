"""Fetches an embedding model's weights and tokenizer to ~/edison-amarel/embed/models on the login node, with only the standard library."""
import json
import sys
import urllib.request
from pathlib import Path

WANTED = (".json", ".txt", ".safetensors", ".model")
target_root = Path.home() / "edison-amarel" / "embed" / "models"
for repo in sys.argv[1:]:
    target = target_root / repo.split("/")[1]
    files = [s["rfilename"] for s in json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}"))["siblings"]]
    # Only the top-level PyTorch weights and tokenizer; ONNX and other exports are for other runtimes.
    for name in [f for f in files if "/" not in f and f.endswith(WANTED)]:
        path = target / name
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(name + ".part")
        urllib.request.urlretrieve(f"https://huggingface.co/{repo}/resolve/main/{name}", part)
        part.rename(path)
        print(repo, name, path.stat().st_size)
