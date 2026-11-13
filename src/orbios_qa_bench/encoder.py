"""CPU-only MiniLM encoder used by the Telemetry Lab."""
from pathlib import Path
from platform import machine
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .tq_contract import Parameter


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Capability(ClosedModel):
    id: str
    description: str
    aliases: list[str]
    unit: str | None
    kind: Literal["number", "string", "boolean", "array"]
    path: str
    provenance: str
    operations: list[Literal["read", "mean", "delta", "latest"]] = ["read"]
    filter_fields: list[str] = []


def bridge(catalog: dict[str, Parameter]) -> dict[str, Capability]:
    kinds = {"number":"number","state":"string","event":"array","apps":"array"}
    return {mid:Capability.model_validate({"id":mid,"description":p.description,"aliases":p.aliases,"kind":kinds[p.kind],"unit":p.unit,"path":mid,"provenance":p.provenance}) for mid,p in catalog.items()}


class EncoderPlanner:
    def __init__(self, catalog: dict[str, Capability], model_dir: Path, mode: str):
        import onnxruntime as ort  # type: ignore[import-untyped]
        from tokenizers import Tokenizer
        self.mode = mode
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        model = "onnx/model.onnx"
        if mode == "embedding":
            export = "model_quint8_avx2.onnx" if machine() == "x86_64" else "model_qint8_arm64.onnx"
            model = f"onnx/{export}"
        token_file = "tokenizer.json" if mode == "embedding" else "onnx/tokenizer.json"
        self.session = ort.InferenceSession(str(model_dir / model), sess_options=options, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(model_dir / token_file))
        self.tokenizer.enable_truncation(max_length=192)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.update(catalog)

    def encode(self, texts):
        import numpy as np
        tokens = self.tokenizer.encode_batch(texts)
        inputs = {"input_ids": np.asarray([t.ids for t in tokens], dtype=np.int64), "attention_mask": np.asarray([t.attention_mask for t in tokens], dtype=np.int64)}
        if self.mode == "embedding":
            inputs["token_type_ids"] = np.asarray([t.type_ids for t in tokens], dtype=np.int64)
        out = np.asarray(self.session.run(None, inputs)[0])
        if self.mode == "nli":
            logits = out - out.max(axis=1, keepdims=True)
            probabilities = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
            return probabilities[:, 0]  # Local config label 0 = entailment.
        mask = inputs["attention_mask"][..., None]
        pooled = (out * mask).sum(axis=1) / mask.sum(axis=1)
        return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9)

    def update(self, catalog: dict[str, Capability]) -> None:
        self.catalog = catalog
        self.ids = list(catalog)
        if self.mode == "embedding":
            self.vectors = self.encode([catalog[mid].description + "; " + "; ".join(catalog[mid].aliases) for mid in self.ids])
