"""Kimodo를 메모리에 띄워 두고 생성 요청을 차례로 처리하는 상주 프로세스.

매번 kimodo_generate.py를 새로 실행하면 프로세스 시작·torch import·모델 로딩에 생성마다 약 10초가 든다.
이 워커는 모델을 한 번만 불러 두고, 패널이 stdin으로 보내는 요청(JSON 한 줄)마다 생성만 한다.

Usage: python kimodo_worker.py --model <name>
       환경변수는 kimodo_generate.py와 같다(KIMODO_TEXT_ENCODER, KIMODO_EMBEDDING_CACHE, HF_HUB_CACHE …).
요청:  {"prompt": str, "duration": float, "seed": int, "steps": int(확산 스텝, 기본 100), "output": <확장자 없는 출력 경로>,
        "log": <이 생성의 로그 파일>, "done": <끝나면 쓸 결과 파일>}
결과:  done 파일에 {"ok": true} 또는 {"ok": false, "error": "..."} (임시 파일에 쓴 뒤 이름을 바꿔 원자적으로).
stdin이 닫히면(패널이 종료하거나 에디터가 꺼짐) 종료한다.
"""

import argparse
import contextlib
import gc
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# kimodo_generate를 import하면 텍스트 인코더가 이 플러그인의 CachedTextEncoder로 바뀐다(main은 실행하지 않음).
import kimodo_generate  # noqa: E402,F401
import torch  # noqa: E402
from kimodo import load_model  # noqa: E402
from kimodo.exports.motion_io import save_kimodo_npz  # noqa: E402
from kimodo.model.registry import get_model_info  # noqa: E402
from kimodo.scripts.generate import get_texts_and_num_frames_from_prompt  # noqa: E402
from kimodo.tools import seed_everything  # noqa: E402

# 모델 전체를 올리는 인코더(약 16 GB)는 요청이 끝나면 내린다. 스트리밍 인코더(1~2 GB)는 다음 새 프롬프트를 위해 둔다.
RELEASE_ENCODER_MODES = ("cpu", "cuda", "auto")


def write_done(path, result):
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    os.replace(temp, path)


def generate(model, device, request):
    """kimodo.scripts.generate.main()의 단일 샘플·BVH 경로와 같은 순서로 생성한다(같은 시드면 같은 결과)."""
    texts, num_frames = get_texts_and_num_frames_from_prompt(request["prompt"], str(request["duration"]), model.fps)
    print(f"Diffusion steps: {int(request.get('steps') or 100)}")
    print("Will generate motions with the following prompts")
    for text, frames in zip(texts, num_frames):
        print(f"    '{text}' with {frames} frames")
    seed_everything(int(request["seed"]))
    output = model(
        texts, num_frames, constraint_lst=[], num_denoising_steps=int(request.get("steps") or 100), num_samples=1,
        multi_prompt=True,
        num_transition_frames=5, post_processing=False, return_numpy=True,
    )

    stem = request["output"]
    single = {k: (v[0] if hasattr(v, "shape") and len(v.shape) > 0 and v.shape[0] == 1 else v) for k, v in output.items()}
    print(f"Saving the npz output to {stem}.npz")
    save_kimodo_npz(stem + ".npz", single)

    from kimodo.exports.bvh import save_motion_bvh
    from kimodo.skeleton import SOMASkeleton30, global_rots_to_local_rots

    skeleton = model.skeleton
    if "somaskel" not in skeleton.name:
        raise RuntimeError("BVH export needs a SOMA model. Set Model Name to a Kimodo-SOMA model.")
    if isinstance(skeleton, SOMASkeleton30):
        skeleton = skeleton.somaskel77.to(device)
    joints_pos = torch.from_numpy(output["posed_joints"][0]).to(device)
    joints_rot = torch.from_numpy(output["global_rot_mats"][0]).to(device)
    local_rot_mats = global_rots_to_local_rots(joints_rot, skeleton)
    print(f"Saving the BVH output to {stem}.bvh")
    save_motion_bvh(stem + ".bvh", local_rot_mats, joints_pos[:, skeleton.root_idx, :], skeleton=skeleton,
                    fps=model.fps, standard_tpose=True)


def release_encoder(model):
    encoder = getattr(model, "text_encoder", None)
    if getattr(encoder, "mode", "") in RELEASE_ENCODER_MODES and getattr(encoder, "_encoder", None) is not None:
        encoder._encoder = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8")

    started = time.monotonic()
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}", flush=True)
    model, resolved = load_model(args.model, device=device, default_family="Kimodo", return_resolved_name=True)
    info = get_model_info(resolved)
    print(f"Loaded model: {info.display_name if info else resolved} ({resolved}) in {time.monotonic() - started:.1f}s",
          flush=True)
    print("WORKER READY", flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        started = time.monotonic()
        result = {"ok": True}
        try:
            request = json.loads(line)
            with open(request["log"], "w", encoding="utf-8", buffering=1) as log, \
                    contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                print(f"Using device: {device}")
                print(f"Loaded model: {resolved} (resident worker)")
                try:
                    generate(model, device, request)
                except Exception as error:  # 워커는 살려 두고 이 요청만 실패로 알린다.
                    traceback.print_exc()
                    result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
                print(f"Request finished in {time.monotonic() - started:.1f}s")
            release_encoder(model)
            write_done(request["done"], result)
        except Exception:
            # 취소한 요청의 작업 폴더가 그사이 지워졌을 때 등. 워커는 계속 다음 요청을 받는다.
            traceback.print_exc()
            sys.stdout.flush()
            continue
        print(f"Request {'done' if result['ok'] else 'failed'} in {time.monotonic() - started:.1f}s: {request['output']}",
              flush=True)


if __name__ == "__main__":
    main()
