"""Low-memory Kimodo text encoder with an on-disk embedding cache.

Kimodo conditions motion on LLM2Vec-Llama-3-8B embeddings. Loading the whole
encoder needs about 16 GB of RAM (bf16). This module computes the same
embedding while holding only one decoder layer at a time: each layer's weights
are read from the Hugging Face safetensors, the MNTP LoRA is merged into them,
the supervised LoRA is applied at runtime like PEFT does, and the layer is
freed again before the next one.

Embeddings are cached per prompt, so regenerating with another seed or
duration does not run the encoder at all.

Instantiated by Kimodo through Hydra (`_target_`), see kimodo_generate.py.
"""

import glob
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

BASE_REPO = "meta-llama/Meta-Llama-3-8B-Instruct"
MNTP_REPO = "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp"
SUPERVISED_REPO = "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised"
LLM_DIM = 4096
DTYPE = torch.bfloat16
CACHE_VERSION = "llm2vec-llama3-8b-v1"
LINEAR_NAMES = (
    "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
    "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj",
)


def _snapshot(repo):
    """Local snapshot folder of a Hugging Face repo in HF_HUB_CACHE (no download)."""
    root = os.environ.get("HF_HUB_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    matches = sorted(glob.glob(os.path.join(root, "models--" + repo.replace("/", "--"), "snapshots", "*")))
    if not matches:
        raise FileNotFoundError(f"{repo} is not in the model cache ({root}). Run Install Kimodo first.")
    return Path(matches[-1])


def _lora_scaling(folder):
    config = json.loads((folder / "adapter_config.json").read_text(encoding="utf-8"))
    return config["lora_alpha"] / config["r"]


class _RowEmbedding(nn.Module):
    """Token embedding that reads only the rows it needs from safetensors (avoids a 1 GB table)."""

    def __init__(self, path, device):
        super().__init__()
        self.path = path
        self.device = device

    def forward(self, input_ids):
        from safetensors import safe_open

        flat = input_ids.reshape(-1).tolist()
        unique = sorted(set(flat))
        with safe_open(self.path, framework="pt") as handle:
            table = handle.get_slice("model.embed_tokens.weight")
            rows = {token: table[token:token + 1] for token in unique}
        out = torch.cat([rows[token] for token in flat], dim=0).to(device=self.device, dtype=DTYPE)
        return out.reshape(*input_ids.shape, -1)


class StreamingLLM2Vec:
    """LLM2Vec encoder whose decoder layers are loaded one at a time."""

    def __init__(self, device="cpu", merge_mntp=False):
        from accelerate import init_empty_weights
        from transformers import AutoTokenizer, LlamaConfig

        from kimodo.model.llm2vec.llm2vec import LLM2Vec
        from kimodo.model.llm2vec.models.bidirectional_llama import LlamaBiModel

        self.device = torch.device(device)
        self.merge_mntp = merge_mntp
        base = _snapshot(BASE_REPO)
        mntp = _snapshot(MNTP_REPO)
        supervised = _snapshot(SUPERVISED_REPO)

        index = json.loads((base / "model.safetensors.index.json").read_text(encoding="utf-8"))
        self.weight_files = {name: str(base / file) for name, file in index["weight_map"].items()}
        self.mntp_file = str(mntp / "adapter_model.safetensors")
        self.mntp_scale = _lora_scaling(mntp)
        self.supervised_file = str(supervised / "adapter_model.safetensors")
        self.supervised_scale = _lora_scaling(supervised)

        # LLM2Vec reads the model name from the MNTP config to pick the Llama 3 chat template.
        config = LlamaConfig.from_pretrained(str(base))
        mntp_config = json.loads((mntp / "config.json").read_text(encoding="utf-8"))
        config._name_or_path = mntp_config.get("_name_or_path", BASE_REPO)
        config._attn_implementation = "sdpa"
        config.torch_dtype = DTYPE

        with init_empty_weights(include_buffers=False):
            model = LlamaBiModel(config)
        model.config._name_or_path = config._name_or_path
        model.embed_tokens = _RowEmbedding(self.weight_files["model.embed_tokens.weight"], self.device)
        model.rotary_emb.to(self.device)
        self._assign(model.norm, "weight", self._base_tensor("model.norm.weight"))
        for index_, layer in enumerate(model.layers):
            layer.register_forward_pre_hook(self._make_loader(index_))
            layer.register_forward_hook(self._make_unloader())
        model.eval()

        tokenizer = AutoTokenizer.from_pretrained(str(mntp))
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "left"

        self.llm2vec = LLM2Vec(model=model, tokenizer=tokenizer)
        # Weights live on the meta device between layers, so LLM2Vec must not move the module.
        self.llm2vec.to = lambda *args, **kwargs: self.llm2vec
        self._lora_hooks = []

    # -- weight streaming -------------------------------------------------
    def _read(self, path, name):
        from safetensors import safe_open

        with safe_open(path, framework="pt") as handle:
            return handle.get_tensor(name)

    def _base_tensor(self, name):
        return self._read(self.weight_files[name], name)

    def _assign(self, module, name, tensor):
        module._parameters[name] = nn.Parameter(tensor.to(device=self.device, dtype=DTYPE), requires_grad=False)

    def _make_loader(self, index):
        def load(layer, args):
            from safetensors import safe_open

            prefix = f"model.layers.{index}."
            lora = f"base_model.model.layers.{index}."
            with safe_open(self.mntp_file, framework="pt") as mntp, safe_open(self.supervised_file, framework="pt") as sup:
                for name, _ in list(layer.named_parameters(recurse=True)):
                    module_name, _, param = name.rpartition(".")
                    module = layer.get_submodule(module_name)
                    weight = self._base_tensor(prefix + name)
                    if module_name in LINEAR_NAMES:
                        if self.merge_mntp:
                            # Exact path: merge MNTP into the base weight like PEFT merge_and_unload (slow on CPU).
                            a = mntp.get_tensor(lora + module_name + ".lora_A.weight")
                            b = mntp.get_tensor(lora + module_name + ".lora_B.weight")
                            weight = (weight.float() + (b @ a) * self.mntp_scale).to(DTYPE)
                        else:
                            # Fast path: apply MNTP as a runtime LoRA too (differs only by bf16 rounding).
                            ma = mntp.get_tensor(lora + module_name + ".lora_A.weight").to(self.device)
                            mb = mntp.get_tensor(lora + module_name + ".lora_B.weight").to(self.device)
                            self._lora_hooks.append(module.register_forward_hook(self._make_lora(ma, mb, self.mntp_scale)))
                        # Supervised adapter stays unmerged and is applied in the forward pass.
                        sa = sup.get_tensor(lora + module_name + ".lora_A.weight").to(self.device)
                        sb = sup.get_tensor(lora + module_name + ".lora_B.weight").to(self.device)
                        self._lora_hooks.append(module.register_forward_hook(self._make_lora(sa, sb, self.supervised_scale)))
                    self._assign(module, param, weight)
                    del weight

        return load

    def _make_lora(self, lora_a, lora_b, scale):
        def apply(module, inputs, output):
            # Same arithmetic as PEFT LoRA with fp32 adapters on a bf16 base layer.
            x = inputs[0].to(lora_a.dtype)
            return (output + (x @ lora_a.T @ lora_b.T) * scale).to(output.dtype)

        return apply

    def _make_unloader(self):
        def unload(layer, args, output):
            for hook in self._lora_hooks:
                hook.remove()
            self._lora_hooks.clear()
            for name, param in list(layer.named_parameters(recurse=True)):
                module_name, _, attr = name.rpartition(".")
                module = layer.get_submodule(module_name)
                module._parameters[attr] = nn.Parameter(torch.empty(param.shape, dtype=DTYPE, device="meta"), requires_grad=False)
            if self.device.type == "cuda":
                torch.cuda.empty_cache()
            return output

        return unload

    def encode(self, text):
        with torch.no_grad():
            result = self.llm2vec.encode([text], batch_size=1, show_progress_bar=False, device=str(self.device))
        return result.to(torch.float32).cpu().numpy()  # [1, 4096]


class CachedTextEncoder:
    """Kimodo text encoder interface: __call__(texts) -> (tensor [B, 1, 4096], lengths)."""

    def __init__(self, mode="stream", cache_dir=None, **_ignored):
        self.mode = (mode or "stream").lower()
        cache_root = cache_dir or os.path.join(os.environ.get("HF_HUB_CACHE", "."), "kimodo-text-embeddings")
        self.cache_dir = Path(cache_root)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._device = "cpu"
        self._encoder = None

    # Kimodo calls these on its text encoder.
    def to(self, device=None, dtype=None):
        if device is not None:
            self._device = str(device)
        return self

    def eval(self):
        return self

    def get_device(self):
        return self._device

    def _variant(self):
        """Modes that produce bit-identical embeddings share cache entries; others are kept apart."""
        if self.mode in ("cpu", "stream-exact"):
            return "exact-cpu"  # original full model on CPU == exact streaming (verified bit for bit)
        if self.mode == "auto":
            return "cuda" if torch.cuda.is_available() else "exact-cpu"
        return self.mode  # "stream" (fast, bf16 rounding differs slightly) or "cuda"

    def _cache_path(self, text):
        key = hashlib.sha256(f"{CACHE_VERSION}\n{self._variant()}\n{text.strip()}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.npy"

    def _encode_uncached(self, text):
        if self._encoder is None:
            started = time.monotonic()
            if self.mode in ("stream", "stream-exact"):
                # Streaming is bound by per-layer weight loading, and measured faster on CPU than
                # copying every layer to the GPU, so it always runs on CPU (peak RAM about 1-2 GB).
                # stream-exact merges the MNTP adapter like the original (identical output, slower).
                exact = self.mode == "stream-exact"
                print(f"Text encoder: low-memory streaming on cpu ({'exact' if exact else 'fast'})", flush=True)
                self._encoder = StreamingLLM2Vec("cpu", merge_mntp=exact)
            else:
                # Full in-memory LLM2Vec (about 16 GB), the original Kimodo path.
                from kimodo.model.llm2vec import LLM2VecEncoder

                print(f"Text encoder: full model on {self.mode}", flush=True)
                full = LLM2VecEncoder(
                    base_model_name_or_path=MNTP_REPO, peft_model_name_or_path=SUPERVISED_REPO,
                    dtype="bfloat16", llm_dim=LLM_DIM, device=self.mode,
                )
                self._encoder = full
            print(f"Text encoder ready in {time.monotonic() - started:.1f}s", flush=True)

        started = time.monotonic()
        if isinstance(self._encoder, StreamingLLM2Vec):
            embedding = self._encoder.encode(text)
        else:
            tensor, _ = self._encoder([text])
            embedding = tensor[:, 0].float().cpu().numpy()
        print(f"Encoded prompt in {time.monotonic() - started:.1f}s", flush=True)
        return embedding.astype(np.float32)

    def __call__(self, texts):
        is_string = isinstance(texts, str)
        if is_string:
            texts = [texts]

        rows = []
        for text in texts:
            if not text.strip():
                rows.append(np.zeros((1, LLM_DIM), dtype=np.float32))
                continue
            path = self._cache_path(text)
            if path.is_file():
                print("Text encoder: cached embedding", flush=True)
                rows.append(np.load(path))
                continue
            embedding = self._encode_uncached(text)
            temp = path.with_suffix(".tmp.npy")
            np.save(temp, embedding)
            os.replace(temp, path)
            rows.append(embedding)

        encoded = torch.tensor(np.stack(rows))  # [B, 1, 4096]
        lengths = [1] * len(texts)
        if is_string:
            return encoded[0].to(self._device), lengths[0]
        return encoded.to(self._device), lengths
