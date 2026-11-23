"""Kimodo SOMA BVH를 Blender 없이 Unreal이 가져올 수 있는 스킨 glTF(.glb)로 변환한다.

Kimodo 가상환경의 numpy만 사용한다. BVH는 센티미터·Y-up이고 glTF는 미터·Y-up이므로
축은 그대로 두고 단위만 바꾼다. 위치 채널은 BVH 규칙대로 해당 관절의 OFFSET을 대체한다.
"""

import json
from pathlib import Path
import struct
import sys

import numpy as np


CENTIMETERS_TO_METERS = 0.01
# IK Rig 미리보기에 필요한 스킨 메시용으로 본마다 붙이는 작은 사면체 크기(미터)
MARKER_SIZE = 0.008
ANIMATION_NAME = "KimodoMotion"


def parse_bvh(path):
    tokens = Path(path).read_text(encoding="utf-8").split()
    joints = []
    stack = []
    index = 0
    while tokens[index] != "MOTION":
        token = tokens[index]
        if token in ("ROOT", "JOINT"):
            parent = stack[-1] if stack else -1
            joints.append({"name": tokens[index + 1], "parent": parent, "offset": None, "channels": []})
            index += 2
        elif token == "End":
            raise ValueError("Kimodo BVH files are not expected to contain End Site blocks.")
        elif token == "{":
            stack.append(len(joints) - 1)
            index += 1
        elif token == "}":
            stack.pop()
            index += 1
        elif token == "OFFSET":
            joints[stack[-1]]["offset"] = np.array([float(v) for v in tokens[index + 1:index + 4]])
            index += 4
        elif token == "CHANNELS":
            count = int(tokens[index + 1])
            joints[stack[-1]]["channels"] = tokens[index + 2:index + 2 + count]
            index += 2 + count
        else:
            index += 1

    frame_count = int(tokens[index + 2])
    frame_time = float(tokens[index + 5])
    channel_count = sum(len(joint["channels"]) for joint in joints)
    values = np.array(tokens[index + 6:], dtype=np.float64)
    if values.size != frame_count * channel_count:
        raise ValueError(f"BVH has {values.size} motion values; expected {frame_count * channel_count}.")
    return joints, values.reshape(frame_count, channel_count), frame_time


def axis_rotations(axis, degrees):
    radians = np.radians(degrees)
    c, s = np.cos(radians), np.sin(radians)
    matrices = np.zeros((len(degrees), 3, 3))
    first, second = {"X": (1, 2), "Y": (2, 0), "Z": (0, 1)}[axis]
    third = 3 - first - second
    matrices[:, third, third] = 1.0
    matrices[:, first, first] = c
    matrices[:, first, second] = -s
    matrices[:, second, first] = s
    matrices[:, second, second] = c
    return matrices


def matrices_to_quaternions(matrices):
    """회전 행렬 (N, 3, 3)을 glTF 순서 (x, y, z, w) 쿼터니언으로 바꾸고 프레임 간 부호를 맞춘다."""
    m = matrices
    trace = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    candidates = np.stack([
        trace, m[:, 0, 0], m[:, 1, 1], m[:, 2, 2],
    ], axis=1)
    choice = candidates.argmax(axis=1)
    quaternions = np.zeros((len(m), 4))
    for row in range(len(m)):
        r = m[row]
        if choice[row] == 0:
            s = np.sqrt(trace[row] + 1.0) * 2
            q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
        elif choice[row] == 1:
            s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
            q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
        elif choice[row] == 2:
            s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
            q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
        else:
            s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
            q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
        quaternions[row] = q
    quaternions /= np.linalg.norm(quaternions, axis=1, keepdims=True)

    # 보간이 먼 길로 돌지 않도록 이전 프레임과 같은 반구를 유지한다.
    for row in range(1, len(quaternions)):
        if np.dot(quaternions[row], quaternions[row - 1]) < 0:
            quaternions[row] *= -1
    return quaternions


def joint_tracks(joints, motion):
    """관절별 로컬 회전 행렬 (T, 3, 3)과 위치 채널(센티미터, 없으면 None)을 계산한다."""
    frame_count = motion.shape[0]
    column = 0
    tracks = []
    for joint in joints:
        rotation = np.broadcast_to(np.eye(3), (frame_count, 3, 3)).copy()
        position = None
        xyz = {}
        for channel in joint["channels"]:
            values = motion[:, column]
            column += 1
            if channel.endswith("position"):
                xyz[channel[0]] = values
            else:
                # BVH는 나열된 채널 순서대로 오른쪽에 곱한다 (Z Y X → Rz·Ry·Rx).
                rotation = rotation @ axis_rotations(channel[0], values)
        if xyz:
            position = np.stack([xyz.get(axis, np.zeros(frame_count)) for axis in "XYZ"], axis=1)
        tracks.append((rotation, position))
    return tracks


class BinaryBuffer:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def add(self, array, component_type, accessor_type, target=None, bounds=False):
        while len(self.data) % 4:
            self.data.append(0)
        raw = array.tobytes()
        view = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if target:
            view["target"] = target
        self.data.extend(raw)
        self.views.append(view)

        accessor = {
            "bufferView": len(self.views) - 1, "componentType": component_type,
            "count": int(array.shape[0]), "type": accessor_type,
        }
        if bounds:
            flat = array.reshape(array.shape[0], -1)
            accessor["min"] = [float(v) for v in flat.min(axis=0)]
            accessor["max"] = [float(v) for v in flat.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def convert(source, output):
    joints, motion, frame_time = parse_bvh(source)
    tracks = joint_tracks(joints, motion)
    buffer = BinaryBuffer()
    FLOAT, UNSIGNED_SHORT, UNSIGNED_INT = 5126, 5123, 5125

    # 휴지 자세: 회전 없이 OFFSET만 누적한다.
    rest_world = []
    for joint in joints:
        offset = joint["offset"] * CENTIMETERS_TO_METERS
        parent = joint["parent"]
        rest_world.append(offset if parent < 0 else rest_world[parent] + offset)

    nodes = []
    for joint_index, joint in enumerate(joints):
        children = [i for i, other in enumerate(joints) if other["parent"] == joint_index]
        node = {"name": joint["name"], "translation": [float(v) for v in joint["offset"] * CENTIMETERS_TO_METERS]}
        if children:
            node["children"] = children
        nodes.append(node)

    # 본마다 작은 사면체를 붙여 단일 가중치로 스키닝한다.
    corners = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float64) * MARKER_SIZE
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.uint32)
    positions = np.concatenate([rest_world[i] + corners for i in range(len(joints))]).astype(np.float32)
    indices = np.concatenate([faces + 4 * i for i in range(len(joints))]).reshape(-1).astype(np.uint32)
    bone_ids = np.zeros((len(positions), 4), dtype=np.uint16)
    bone_ids[:, 0] = np.repeat(np.arange(len(joints)), 4)
    weights = np.zeros((len(positions), 4), dtype=np.float32)
    weights[:, 0] = 1.0

    inverse_bind = np.repeat(np.eye(4, dtype=np.float32)[None], len(joints), axis=0)
    for i, world in enumerate(rest_world):
        # glTF 행렬은 열 우선이므로 이동값을 마지막 열 대신 마지막 행 위치에 넣는다.
        inverse_bind[i, 3, :3] = -world

    mesh = {"name": "SOMA_SourceGeometry", "primitives": [{
        "attributes": {
            "POSITION": buffer.add(positions, FLOAT, "VEC3", 34962, bounds=True),
            "JOINTS_0": buffer.add(bone_ids, UNSIGNED_SHORT, "VEC4", 34962),
            "WEIGHTS_0": buffer.add(weights, FLOAT, "VEC4", 34962),
        },
        "indices": buffer.add(indices, UNSIGNED_INT, "SCALAR", 34963),
    }]}
    skin = {
        "name": "SOMA", "joints": list(range(len(joints))), "skeleton": 0,
        "inverseBindMatrices": buffer.add(inverse_bind, FLOAT, "MAT4"),
    }
    mesh_node = len(nodes)
    nodes.append({"name": "SOMA_SourceGeometry", "mesh": 0, "skin": 0})

    times = (np.arange(motion.shape[0]) * frame_time).astype(np.float32)
    time_accessor = buffer.add(times, FLOAT, "SCALAR", bounds=True)
    samplers = []
    channels = []
    for joint_index, (rotation, position) in enumerate(tracks):
        quaternions = matrices_to_quaternions(rotation).astype(np.float32)
        samplers.append({"input": time_accessor, "output": buffer.add(quaternions, FLOAT, "VEC4"),
                         "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": joint_index, "path": "rotation"}})
        if position is None:
            continue

        translation = (position * CENTIMETERS_TO_METERS).astype(np.float32)
        samplers.append({"input": time_accessor, "output": buffer.add(translation, FLOAT, "VEC3"),
                         "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": joint_index, "path": "translation"}})

    document = {
        "asset": {"version": "2.0", "generator": "KimodoAnimation bvh_to_gltf"},
        "scene": 0, "scenes": [{"nodes": [0, mesh_node]}],
        "nodes": nodes, "meshes": [mesh], "skins": [skin],
        "animations": [{"name": ANIMATION_NAME, "samplers": samplers, "channels": channels}],
        "buffers": [{"byteLength": len(buffer.data)}],
        "bufferViews": buffer.views, "accessors": buffer.accessors,
    }
    write_glb(output, document, bytes(buffer.data))


def write_glb(path, document, binary):
    content = json.dumps(document, separators=(",", ":")).encode("utf-8")
    content += b" " * (-len(content) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(content) + 8 + len(binary)
    with Path(path).open("wb") as file:
        file.write(struct.pack("<4sII", b"glTF", 2, total))
        file.write(struct.pack("<I4s", len(content), b"JSON"))
        file.write(content)
        file.write(struct.pack("<I4s", len(binary), b"BIN\0"))
        file.write(binary)


if __name__ == "__main__":
    source_path, output_path = map(Path, sys.argv[1:3])
    convert(source_path, output_path)
    if output_path.stat().st_size <= 0:
        raise SystemExit("The glTF file is empty.")
