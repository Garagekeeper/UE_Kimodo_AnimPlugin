# Third-party notices

The plugin's own code (C++ and Python under `Source/` and `Scripts/`) is MIT-licensed (see `LICENSE`).
It bundles or downloads the following third-party components, which keep their own licenses.

## Bundled with the plugin

### kimodo (`Wheels/kimodo-1.0.0-cp311-cp311-win_amd64.whl`)

- Source: <https://github.com/nv-tlabs/kimodo> (commit `e1c219c99b4b94ba48be1fb6a1cbc64ecee4c954`), built for Windows / Python 3.11 without changes.
- License: Apache License 2.0. The full text is in `ThirdParty/kimodo/LICENSE` and inside the wheel (`kimodo-1.0.0.dist-info/licenses/LICENSE`).

## Downloaded on the user's machine (not redistributed)

`Install Kimodo` downloads these into the local model cache (default `%LOCALAPPDATA%\KimodoAnimation\`).
They are not part of this repository. Each user accepts their terms on the source site before downloading,
and is responsible for following them, including for the animations produced with them.

| Component | Source | Terms |
|---|---|---|
| Meta Llama 3 8B Instruct | <https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct> | Meta Llama 3 Community License (gated; accept on Hugging Face) |
| LLM2Vec adapters (MNTP, supervised) | <https://huggingface.co/McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp>, <https://huggingface.co/McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised> | See each model card |
| Kimodo motion model checkpoints | Downloaded by `kimodo` (see <https://github.com/nv-tlabs/kimodo>) | See the Kimodo repository and model card |
| PyTorch and other Python packages | PyPI / <https://download.pytorch.org> | Each package's own license |
