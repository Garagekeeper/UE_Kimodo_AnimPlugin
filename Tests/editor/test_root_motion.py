"""Root motion off keeps the root bone at the origin; on moves it with the pelvis and sets Enable Root Motion."""
import os
import sys

import unreal

sys.path.insert(0, os.path.join(os.environ.get("KIMODO_PLUGIN_DIR", ""), "Tests", "editor"))
from common import run  # noqa: E402

PROMPT = "A person walks forward four steps."


def root_travel(path):
    clip = unreal.EditorAssetLibrary.load_asset(path)
    options = unreal.AnimPoseEvaluationOptions()
    positions = []
    for time in (0.0, clip.get_play_length()):
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(clip, time, options)
        positions.append(unreal.AnimPoseExtensions.get_bone_pose(pose, "root", unreal.AnimPoseSpaces.WORLD).translation)
    start, end = positions
    distance = ((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5
    return clip.get_editor_property("enable_root_motion"), distance, end


def body(t):
    for flag in (False, True):
        s, _, _ = t.wait(t.call("generate", PROMPT, seed="7", duration="3.0", root_motion=flag), root_motion=flag)
        enabled, distance, end = root_travel(s["path"])
        if flag:
            t.check("root motion on: Enable Root Motion set", enabled)
            t.check("root motion on: root travels", distance > 50, f"{distance:.0f} cm")
            t.check("root motion on: status mentions it", "root motion" in s["message"], s["message"])
        else:
            t.check("root motion off: Enable Root Motion unset", not enabled)
            t.check("root motion off: root stays at origin", distance < 1 and abs(end.x) < 1 and abs(end.y) < 1,
                    f"{distance:.2f} cm")


run("root_motion", body)
