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


KEY_BONES = ("right_hand", "left_hand", "right_foot", "left_foot", "hips")


def key_bone(key):
    """키의 부위. 1.2 이전 요청의 "hand": "right"|"left"도 받는다."""
    bone = key.get("bone") or {"left": "left_hand"}.get(key.get("hand"), "right_hand")
    return bone if bone in KEY_BONES else "right_hand"


def hand_constraints(model, device, spec, total_frames):
    """Body keys(손·발·골반 키프레임) → Kimodo end-effector 제약.

    spec: {"base": <기준 테이크의 Kimodo 결과 .npz>, "keys": [{"time": 초, "bone": KEY_BONES 중 하나,
           "offset": [오른쪽, 위, 앞] (m, 캐릭터 기준)}]}
    기준 테이크의 그 프레임 전신 자세에서 그 부위만 offset만큼 옮긴 위치·회전을 제약으로 건다(Kimodo가 위치를 받는
    부위는 양손·양발·골반뿐이다). 골반 키는 루트 수평 위치·높이도 같이 옮겨 몸 전체가 따라오게 한다.
    Kimodo는 프롬프트별로 제약을 잘라(crop_move) 인덱스 텐서를 CPU에 새로 만들고, 조건 생성은 인덱스만 모델 장치로
    옮기므로 인덱스는 CPU, 값 텐서는 모델 장치에 둔다(kimodo CLI의 제약 파일 경로와 같은 배치)."""
    if not spec or not spec.get("keys"):
        return []
    import numpy as np
    from kimodo.constraints import (EndEffectorConstraintSet, LeftFootConstraintSet, LeftHandConstraintSet,
                                    RightFootConstraintSet, RightHandConstraintSet,
                                    _convert_constraint_local_rots_to_skeleton)
    from kimodo.skeleton import SOMASkeleton30

    classes = {
        "right_hand": RightHandConstraintSet, "left_hand": LeftHandConstraintSet,
        "right_foot": RightFootConstraintSet, "left_foot": LeftFootConstraintSet,
        "hips": lambda *args, **kwargs: EndEffectorConstraintSet(*args, joint_names=["Hips"], **kwargs),
    }
    base = np.load(spec["base"])
    skeleton = SOMASkeleton30()

    def frame_of(key):
        return min(total_frames - 1, max(0, round(float(key["time"]) * model.fps)))

    def world_delta(offset, heading_row):
        right, up, forward = (float(v) for v in offset)
        cos, sin = float(heading_row[0]), float(heading_row[1])  # 정면 +z를 이만큼 돌린 방향
        return np.array([-cos * right + sin * forward, up, sin * right + cos * forward])

    # 골반 키가 있는 프레임은 모든 제약의 루트(수평 위치·높이)를 같이 옮겨 서로 어긋나지 않게 한다.
    hips_delta = {frame_of(k): world_delta(k.get("offset", [0, 0, 0]), base["global_root_heading"][frame_of(k)])
                  for k in spec["keys"] if key_bone(k) == "hips"}

    constraints = []
    for bone in KEY_BONES:
        keys = [k for k in spec["keys"] if key_bone(k) == bone]
        offsets = {frame_of(k): k.get("offset", [0, 0, 0]) for k in sorted(keys, key=lambda k: float(k["time"]))}
        frames = sorted(offsets)
        if not frames:
            continue
        local = _convert_constraint_local_rots_to_skeleton(torch.from_numpy(base["local_rot_mats"][frames]), skeleton)
        rots, positions, _ = skeleton.fk(local, torch.from_numpy(base["root_positions"][frames]))
        smooth = torch.from_numpy(base["smooth_root_pos"][frames][:, [0, 2]].copy())
        constraint = classes[bone](skeleton, frame_indices=torch.tensor(frames), global_joints_positions=positions,
                                   global_joints_rots=rots, smooth_root_2d=smooth)
        heading = base["global_root_heading"][frames]
        root_y = constraint.global_joints_positions[:, skeleton.root_idx, 1].clone()
        for row, frame in enumerate(frames):
            delta = torch.tensor(world_delta(offsets[frame], heading[row]), dtype=positions.dtype)
            constraint.global_joints_positions[row, constraint.pos_indices] += delta
            if frame in hips_delta:
                shift = hips_delta[frame]
                constraint.smooth_root_2d[row] += torch.tensor([shift[0], shift[2]], dtype=positions.dtype)
                root_y[row] += float(shift[1])
        constraint.root_y_pos = root_y
        for name in ("global_joints_positions", "global_joints_rots", "smooth_root_2d", "root_y_pos", "global_root_heading"):
            setattr(constraint, name, getattr(constraint, name).to(device))
        constraints.append(constraint)
        print(f"Body keys ({bone}): frames {frames}")
    return constraints


def generate(model, device, request):
    """kimodo.scripts.generate.main()의 단일 샘플·BVH 경로와 같은 순서로 생성한다(같은 시드면 같은 결과)."""
    texts, num_frames = get_texts_and_num_frames_from_prompt(request["prompt"], str(request["duration"]), model.fps)
    print(f"Diffusion steps: {int(request.get('steps') or 100)}")
    print("Will generate motions with the following prompts")
    for text, frames in zip(texts, num_frames):
        print(f"    '{text}' with {frames} frames")
    constraints = hand_constraints(model, device, request.get("hand_keys"), sum(num_frames))
    seed_everything(int(request["seed"]))
    output = model(
        texts, num_frames, constraint_lst=constraints, num_denoising_steps=int(request.get("steps") or 100), num_samples=1,
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
