import re

import unreal as u


# Kimodo는 항상 SOMA 스켈레톤을 출력하므로 소스 쪽 본 이름은 고정이다.
SOMA_RETARGET_ROOT = "Hips"
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Pinky")


def has_toe(bone):
    bone = bone.lower()
    return "ball" in bone or "toe" in bone


def split_side(chain_name):
    """LeftArm, arm_l, L_Arm, Arm_Left 같은 체인 이름을 (Left|Right|None, 'arm')로 나눈다."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", chain_name).lower()) if t]
    if not tokens:
        return None, ""
    sides = {"l": "Left", "left": "Left", "r": "Right", "right": "Right"}
    if tokens[0] in sides and len(tokens) > 1:
        return sides[tokens[0]], "".join(tokens[1:])
    if tokens[-1] in sides and len(tokens) > 1:
        return sides[tokens[-1]], "".join(tokens[:-1])
    return None, "".join(tokens)


def endpoints_from_chain_name(name, start, end):
    """Epic 표준(UE5 자동 캐릭터화) 체인 이름으로 SOMA 시작·끝 본을 정한다."""
    side, part = split_side(str(name))
    start, end = str(start), str(end)
    if side is None:
        central = {
            "root": ("Root", "Root"),
            "pelvis": ("Hips", "Hips"),
            "hips": ("Hips", "Hips"),
            "spine": ("Spine1", "Chest"),
            "neck": ("Neck1", "Head" if "head" in end.lower() else "Neck2"),
            "head": ("Head", "Head"),
        }
        return central.get(part)

    if part in ("clavicle", "shoulder"):
        bones = ("Shoulder", "Shoulder")
    elif part == "arm":
        bones = ("Arm", "Hand")
    elif part == "hand":
        bones = ("Hand", "Hand")
    elif part == "leg":
        bones = ("Leg", "ToeBase" if has_toe(end) else "Foot")
    elif part == "foot":
        if has_toe(start):
            bones = ("ToeBase", "ToeBase")
        else:
            bones = ("Foot", "ToeBase" if has_toe(end) else "Foot")
    elif part in ("toe", "toes", "ball"):
        bones = ("ToeBase", "ToeBase")
    else:
        for finger in FINGERS:
            if part == finger.lower():
                first = "Hand" + finger
                return side + first + "1", side + first + "3"
        # 중수골·IK 목표 체인 등은 SOMA에 대응하지 않으므로 매핑하지 않는다.
        return None
    return tuple(side + bone for bone in bones)


def endpoints_from_bones(start, end):
    """UE5 마네킹 본 이름(pelvis, spine_01, upperarm_l…)으로 SOMA 시작·끝 본을 정한다."""
    start, end = str(start).lower(), str(end).lower()
    central = {
        "root": ("Root", "Root"),
        "pelvis": ("Hips", "Hips"),
        "spine_01": ("Spine1", "Chest"),
        "neck_01": ("Neck1", "Head" if end == "head" else "Neck2"),
        "head": ("Head", "Head"),
    }
    if start in central:
        return central[start]

    side = {"l": "Left", "r": "Right"}.get(start[-1:])
    if not side or not start.endswith(("_l", "_r")):
        return None
    limbs = {
        "clavicle": ("Shoulder", "Shoulder"),
        "upperarm": ("Arm", "Hand"),
        "thigh": ("Leg", "Foot"),
        "ball": ("ToeBase", "ToeBase"),
        "foot": ("Foot", "ToeBase" if end.startswith("ball") else "Foot"),
        "hand": ("Hand", "Hand"),
    }
    joint = start[:-2]
    if joint in limbs:
        return tuple(side + bone for bone in limbs[joint])
    for finger in FINGERS:
        if joint == finger.lower() + "_01":
            first = "Hand" + finger
            return side + first + "1", side + first + "3"
    return None


def target_bone_names(target):
    try:
        pose = u.AnimPoseExtensions.get_reference_pose(target.get_editor_property("skeleton"))
        return {str(bone) for bone in u.AnimPoseExtensions.get_bone_names(pose)}
    except Exception:
        u.log_warning("Could not read the target bone names. Using AutoAlignBones without filtering.")
        return None


def build_retargeter(source, target, target_rig, align_bones):
    # 대상 체인과 같은 이름의 SOMA 체인을 만들어 EXACT로 연결한다.
    source_rig = u.new_object(u.IKRigDefinition)
    sc = u.IKRigController.get_controller(source_rig)
    assert sc.set_skeletal_mesh(source)
    sc.set_retarget_root(SOMA_RETARGET_ROOT)

    tc = u.IKRigController.get_controller(target_rig)
    arm_starts = []
    for chain in tc.get_retarget_chains():
        start = chain.get_editor_property("start_bone").get_editor_property("bone_name")
        end = chain.get_editor_property("end_bone").get_editor_property("bone_name")

        # 체인 이름을 먼저 보고, 이름이 비표준이면 UE5 마네킹 본 이름으로 추정한다.
        bones = endpoints_from_chain_name(chain.chain_name, start, end)
        if not bones:
            bones = endpoints_from_bones(start, end)
        if not bones:
            continue

        sc.add_retarget_chain(chain.chain_name, bones[0], bones[1], "")
        if bones[0] in ("LeftArm", "RightArm"):
            arm_starts.append(str(start))
    if not sc.get_retarget_chains():
        raise RuntimeError(
            f"No retarget chain of {target_rig.get_name()} matched the SOMA skeleton. "
            "Use standard chain names (Spine, Head, LeftArm…) or set a Retargeter.")

    settings = u.new_object(u.IKRetargeter)
    rc = u.IKRetargeterController.get_controller(settings)
    rc.set_ik_rig(u.RetargetSourceOrTarget.SOURCE, source_rig)
    rc.set_ik_rig(u.RetargetSourceOrTarget.TARGET, target_rig)
    rc.add_default_ops()
    rc.auto_map_chains(u.AutoMapChainType.EXACT, True)

    # 대상의 다리 기준 포즈는 유지하고 팔의 A/T 포즈 차이만 맞춘다.
    existing = target_bone_names(target)
    bones = list(align_bones)
    if existing is not None:
        bones = [bone for bone in bones if bone in existing]
    if not bones:
        bones = arm_starts
    if bones:
        rc.auto_align_bones(bones, u.RetargetAutoAlignMethod.CHAIN_TO_CHAIN, u.RetargetSourceOrTarget.TARGET)
    return settings


def retarget(assets, source, animation, target, target_rig, staging, align_bones=(), retargeter=None):
    if retargeter is None:
        retargeter = build_retargeter(source, target, target_rig, align_bones)

    inputs = u.IKRetargetBatchOperationInputs()
    inputs.set_editor_properties({
        "assets_to_retarget": [assets.find_asset_data(animation.get_path_name())],
        "source_mesh": source, "target_mesh": target, "ik_retarget_asset": retargeter,
        "search": animation.get_name(), "replace": "KimodoPreview",
        "target_path": staging, "include_referenced_assets": False,
        "overwrite_existing_files": False,
    })
    result = u.IKRetargetBatchOperation.run_batch_retarget(inputs)
    assert len(result) == 1, result
    return result[0].get_asset()
