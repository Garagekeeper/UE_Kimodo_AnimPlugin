"""Integration test for Scripts/kimodo_worker.py against the one-shot Scripts/kimodo_generate.py CLI.

Needs an installed Kimodo (the plugin's venv and model cache). Run with that venv's Python:
    %LOCALAPPDATA%\\KimodoAnimation\\venv\\Scripts\\python.exe -m unittest discover -s Tests/integration -v
Environment overrides: KIMODO_PYTHON (venv python), KIMODO_MODEL_CACHE.
Takes about 1-2 minutes (the CLI run loads the model; the worker loads it once).
"""
import filecmp
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "Scripts"
ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "KimodoAnimation"
PYTHON = Path(os.environ.get("KIMODO_PYTHON") or ROOT / "venv" / "Scripts" / "python.exe")
CACHE = Path(os.environ.get("KIMODO_MODEL_CACHE") or ROOT / "model-cache")
MODEL = "Kimodo-SOMA-RP-v1.2"
PROMPT = "A person waves the right hand"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def installed():
    if not PYTHON.is_file() or not CACHE.is_dir():
        return False
    return subprocess.run([str(PYTHON), "-c", "import kimodo"], capture_output=True,
                          creationflags=NO_WINDOW).returncode == 0


@unittest.skipUnless(installed(), f"Kimodo is not installed ({PYTHON})")
class WorkerMatchesCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="kimodo_worker_test_"))
        cls.env = dict(os.environ, HF_HUB_CACHE=str(CACHE), HF_HUB_OFFLINE="1", TEXT_ENCODER_MODE="local",
                       TEXT_ENCODER_DEVICE="cpu", KIMODO_TEXT_ENCODER="stream",
                       KIMODO_EMBEDDING_CACHE=str(CACHE / "kimodo-text-embeddings"), PYTHONUNBUFFERED="1")

    def run_cli(self, seed, steps=100):
        stem = self.out / f"cli_{seed}_{steps}"
        started = time.monotonic()
        with open(str(stem) + ".log", "wb") as log:
            subprocess.run([str(PYTHON), str(SCRIPTS / "kimodo_generate.py"), "--model", MODEL, "--duration", "2.0",
                            "--num_samples", "1", "--seed", str(seed), "--diffusion_steps", str(steps),
                            "--bvh", "--bvh_standard_tpose",
                            "--no-postprocess", "--output", str(stem), "--", PROMPT],
                           env=self.env, stdout=log, stderr=subprocess.STDOUT, check=True, creationflags=NO_WINDOW)
        return Path(str(stem) + ".bvh"), time.monotonic() - started

    def test_same_seed_same_bvh_and_resident_is_faster(self):
        # 임베딩 캐시를 먼저 채워 CLI와 워커가 같은 조건(캐시된 프롬프트)에서 비교되게 한다.
        self.run_cli(6)
        cli_bvh, cli_seconds = self.run_cli(7)
        cli20_bvh, _ = self.run_cli(7, steps=20)

        log = open(self.out / "worker.log", "wb")
        worker = subprocess.Popen([str(PYTHON), str(SCRIPTS / "kimodo_worker.py"), "--model", MODEL], env=self.env,
                                  stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", creationflags=NO_WINDOW)
        try:
            results = []
            for index, (seed, steps) in enumerate(((7, 100), (8, 100), (7, 100), (7, 20))):
                stem = self.out / f"w{index}_{seed}_{steps}"
                done = Path(str(stem) + ".done.json")
                started = time.monotonic()
                worker.stdin.write(json.dumps({"prompt": PROMPT, "duration": 2.0, "seed": seed, "steps": steps,
                                               "output": str(stem),
                                               "log": str(stem) + ".log", "done": str(done)}) + "\n")
                worker.stdin.flush()
                while not done.exists():
                    self.assertIsNone(worker.poll(), f"worker exited, see {self.out / 'worker.log'}")
                    self.assertLess(time.monotonic() - started, 300, "worker request timed out")
                    time.sleep(0.1)
                self.assertEqual(json.loads(done.read_text(encoding="utf-8")), {"ok": True})
                results.append((Path(str(stem) + ".bvh"), time.monotonic() - started))
            worker.stdin.close()
            self.assertEqual(worker.wait(timeout=60), 0, "worker should exit when stdin closes")
        finally:
            if worker.poll() is None:
                worker.kill()
            log.close()

        self.assertTrue(filecmp.cmp(cli_bvh, results[0][0], shallow=False), "worker seed 7 differs from CLI seed 7")
        self.assertTrue(filecmp.cmp(cli_bvh, results[2][0], shallow=False), "seed 7 after another request differs")
        self.assertFalse(filecmp.cmp(results[0][0], results[1][0], shallow=False), "seeds 7 and 8 should differ")
        self.assertTrue(filecmp.cmp(cli20_bvh, results[3][0], shallow=False), "worker 20 steps differs from CLI 20 steps")
        self.assertFalse(filecmp.cmp(results[0][0], results[3][0], shallow=False), "20 and 100 steps should differ")
        resident = (results[1][1] + results[2][1]) / 2
        print(f"\nCLI {cli_seconds:.1f}s, worker first {results[0][1]:.1f}s, resident avg {resident:.1f}s", file=sys.stderr)
        self.assertLess(resident, cli_seconds, "resident requests should be faster than a CLI run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
