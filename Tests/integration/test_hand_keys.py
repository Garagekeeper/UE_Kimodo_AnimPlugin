"""Integration test for hand keys (Kimodo hand constraints) in Scripts/kimodo_worker.py.

Base take -> regenerate with right-hand keys through the resident worker. At each key frame the hand should be near
the base hand position moved by the offset, and "+right" should point to the character's right.
Run with the Kimodo venv Python (see test_worker.py). About 1 minute.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from test_worker import CACHE, MODEL, NO_WINDOW, PYTHON, SCRIPTS, installed

PROMPT = "A person waves hello with big slow side to side arm movements, right arm raised high"
KEYS = [(0.9, 0.30), (1.5, -0.30), (2.1, 0.30), (2.7, -0.30)]
# Kimodo는 후처리 없이는 제약을 정확히 맞추지 않는다(공식 문서). 측정: 2~8 cm. 30 cm 이동 대비 충분히 작은 오차.
TOLERANCE = 0.10  # m


@unittest.skipUnless(installed(), f"Kimodo is not installed ({PYTHON})")
class HandKeys(unittest.TestCase):
    def test_hand_reaches_keys(self):
        out = Path(tempfile.mkdtemp(prefix="kimodo_hand_keys_"))
        env = dict(os.environ, HF_HUB_CACHE=str(CACHE), HF_HUB_OFFLINE="1", TEXT_ENCODER_MODE="local",
                   TEXT_ENCODER_DEVICE="cpu", KIMODO_TEXT_ENCODER="stream",
                   KIMODO_EMBEDDING_CACHE=str(CACHE / "kimodo-text-embeddings"), PYTHONUNBUFFERED="1")
        log = open(out / "worker.log", "wb")
        worker = subprocess.Popen([str(PYTHON), str(SCRIPTS / "kimodo_worker.py"), "--model", MODEL], env=env,
                                  stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  creationflags=NO_WINDOW)

        def run(name, hand_keys=None):
            stem = out / name
            done = Path(str(stem) + ".done.json")
            worker.stdin.write(json.dumps({"prompt": PROMPT, "duration": 4.0, "seed": 4, "steps": 100,
                                           "hand_keys": hand_keys, "output": str(stem), "log": str(stem) + ".log",
                                           "done": str(done)}) + "\n")
            worker.stdin.flush()
            started = time.monotonic()
            while not done.exists():
                self.assertIsNone(worker.poll(), f"worker exited, see {out / 'worker.log'}")
                self.assertLess(time.monotonic() - started, 300)
                time.sleep(0.1)
            self.assertEqual(json.loads(done.read_text(encoding="utf-8")), {"ok": True},
                             Path(str(stem) + ".log").read_text(encoding="utf-8"))
            return np.load(str(stem) + ".npz")

        try:
            base = run("base")
            keyed = run("keyed", {"base": str(out / "base.npz"),
                                  "keys": [{"time": t, "hand": "right", "offset": [r, 0.0, 0.0]} for t, r in KEYS]})
            worker.stdin.close()
            worker.wait(timeout=60)
        finally:
            if worker.poll() is None:
                worker.kill()
            log.close()

        names = joint_names()
        hand, l_sh, r_sh = names.index("RightHand"), names.index("LeftShoulder"), names.index("RightShoulder")
        b, k = base["posed_joints"], keyed["posed_joints"]
        errors, along = [], []
        for seconds, right in KEYS:
            f = round(seconds * 30)
            side = b[f, r_sh] - b[f, l_sh]
            side[1] = 0
            side /= np.linalg.norm(side)  # 캐릭터의 오른쪽(왼어깨 → 오른어깨, 수평)
            moved = k[f, hand] - b[f, hand]
            along.append(float(np.dot(moved, side)) * np.sign(right))
            errors.append(float(np.linalg.norm(k[f, hand] - (b[f, hand] + side * right))))
        print(f"\nhand key errors (cm): {[round(e * 100, 1) for e in errors]}, moved the asked way (cm): "
              f"{[round(s * 100, 1) for s in along]}")
        self.assertTrue(all(s > 0.15 for s in along), "the hand should move at least 15 cm the asked way")
        self.assertLess(max(errors), TOLERANCE, "the hand should be within 10 cm of each key")


@unittest.skipUnless(installed(), f"Kimodo is not installed ({PYTHON})")
class FeetAndHipsKeys(unittest.TestCase):
    def test_hips_lower_and_foot_moves(self):
        out = Path(tempfile.mkdtemp(prefix="kimodo_body_keys_"))
        env = dict(os.environ, HF_HUB_CACHE=str(CACHE), HF_HUB_OFFLINE="1", TEXT_ENCODER_MODE="local",
                   TEXT_ENCODER_DEVICE="cpu", KIMODO_TEXT_ENCODER="stream",
                   KIMODO_EMBEDDING_CACHE=str(CACHE / "kimodo-text-embeddings"), PYTHONUNBUFFERED="1")
        log = open(out / "worker.log", "wb")
        worker = subprocess.Popen([str(PYTHON), str(SCRIPTS / "kimodo_worker.py"), "--model", MODEL], env=env,
                                  stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  creationflags=NO_WINDOW)

        def run(name, keys=None):
            stem = out / name
            done = Path(str(stem) + ".done.json")
            worker.stdin.write(json.dumps({"prompt": "A person stands still", "duration": 4.0, "seed": 3, "steps": 100,
                                           "hand_keys": {"base": str(out / "base.npz"), "keys": keys} if keys else None,
                                           "output": str(stem), "log": str(stem) + ".log", "done": str(done)}) + "\n")
            worker.stdin.flush()
            started = time.monotonic()
            while not done.exists():
                self.assertIsNone(worker.poll(), f"worker exited, see {out / 'worker.log'}")
                self.assertLess(time.monotonic() - started, 300)
                time.sleep(0.1)
            self.assertEqual(json.loads(done.read_text(encoding="utf-8")), {"ok": True},
                             Path(str(stem) + ".log").read_text(encoding="utf-8"))
            return np.load(str(stem) + ".npz")

        try:
            base = run("base")
            keyed = run("keyed", [{"time": 2.0, "bone": "hips", "offset": [0.0, -0.20, 0.0]},
                                  {"time": 3.0, "bone": "right_foot", "offset": [0.0, 0.0, 0.20]}])
            worker.stdin.close()
            worker.wait(timeout=60)
        finally:
            if worker.poll() is None:
                worker.kill()
            log.close()

        names = joint_names()
        hips, foot, l_sh, r_sh = (names.index(n) for n in ("Hips", "RightFoot", "LeftShoulder", "RightShoulder"))
        b, k = base["posed_joints"], keyed["posed_joints"]
        drop = float(b[60, hips, 1] - k[60, hips, 1])
        side = b[90, r_sh] - b[90, l_sh]
        side[1] = 0
        side /= np.linalg.norm(side)
        forward = np.array([-side[2], 0.0, side[0]])  # 수평에서 오른쪽을 90° 돌린 방향(y 위)
        if np.dot(forward, b[90, foot] - b[90, hips]) < -0.5:  # 혹시 반대면 뒤집기(발끝 방향과 무관, 부호만 확인용)
            forward = -forward
        step = float(np.dot(k[90, foot] - b[90, foot], forward))
        print(f"\nhips lowered {drop * 100:.1f} cm at 2.0 s, right foot moved {abs(step) * 100:.1f} cm at 3.0 s")
        self.assertGreater(drop, 0.12, "hips key should lower the pelvis")
        self.assertGreater(abs(step), 0.12, "foot key should move the right foot")


def joint_names():
    code = ("from kimodo.skeleton import SOMASkeleton30; s=SOMASkeleton30().somaskel77; "
            "print(','.join(s.bone_order_names if hasattr(s,'bone_order_names') else s.joint_names))")
    out = subprocess.run([str(PYTHON), "-c", code], capture_output=True, text=True, creationflags=NO_WINDOW, check=True)
    return out.stdout.strip().split(",")


if __name__ == "__main__":
    unittest.main(verbosity=2)
