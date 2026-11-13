"""Fetch and verify the two runtime assets from the pinned public model revision."""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

BASE = ("https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        "/resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/")
TOKENIZER = "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"
MODELS = {
    "arm64": ("model_qint8_arm64.onnx", "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672"),
    "amd64": ("model_quint8_avx2.onnx", "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f"),
}


def fetch(remote: str, local: Path, sha256: str) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler({}))
    with opener.open(BASE + remote, timeout=90) as response, local.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    with local.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != sha256:
        raise ValueError(f"SHA-256 mismatch for {remote}: {actual}")


def main(arch: str, directory: Path) -> None:
    if arch not in MODELS:
        raise ValueError(f"Unsupported model architecture: {arch}")
    model, checksum = MODELS[arch]
    fetch("tokenizer.json", directory / "tokenizer.json", TOKENIZER)
    fetch("onnx/" + model, directory / "onnx" / model, checksum)
    shutil.copyfile("/usr/share/common-licenses/Apache-2.0", directory / "LICENSE")
    (directory / "MODEL_SOURCE.txt").write_text(
        f"MiniLM exported by Sentence Transformers\n{BASE}\n"
        f"Apache-2.0\n{model} {checksum}\ntokenizer.json {TOKENIZER}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
