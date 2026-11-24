"""Run `kimodo.scripts.generate` with the plugin's cached / low-memory text encoder.

Usage: python kimodo_generate.py <generate args...>
Environment: KIMODO_TEXT_ENCODER = stream (default) | stream-exact | cpu | cuda | auto
             KIMODO_EMBEDDING_CACHE = folder for cached prompt embeddings (optional)
"""

import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib  # noqa: E402

# `kimodo.model.load_model` is shadowed by the function of the same name in kimodo.model,
# so fetch the module itself; load_model() looks up _select_text_encoder_conf in its globals.
load_model_module = importlib.import_module("kimodo.model.load_model")


def _select_text_encoder_conf(text_encoder_url, text_encoder_fp32=False):
    return {
        "_target_": "kimodo_text_encoder.CachedTextEncoder",
        "mode": os.environ.get("KIMODO_TEXT_ENCODER", "stream"),
        "cache_dir": os.environ.get("KIMODO_EMBEDDING_CACHE") or None,
    }


load_model_module._select_text_encoder_conf = _select_text_encoder_conf

if __name__ == "__main__":
    sys.argv = ["kimodo.scripts.generate"] + sys.argv[1:]
    runpy.run_module("kimodo.scripts.generate", run_name="__main__", alter_sys=True)
