"""Unit tests for Scripts/panel.py and Scripts/interpret.py without Unreal or Kimodo.

The `unreal` module is replaced by a MagicMock, so only Python 3.11+ is needed:
    python -m unittest discover -s Tests/unit -v
"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "Scripts"


def load_panel():
    """panel.py를 모듈로 불러온다(runpy와 달리 모듈 전역을 테스트에서 바꿀 수 있다)."""
    sys.modules["unreal"] = MagicMock()
    spec = importlib.util.spec_from_file_location("kimodo_panel_under_test", SCRIPTS / "panel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ASSETS = MagicMock()
    module.ASSETS.does_asset_exist.return_value = False
    return module


panel = load_panel()


def new_panel():
    p = panel.Panel()
    p.load_take = lambda take, config: None
    p.unload_extra = lambda: None
    return p


def session_config(numbers):
    """이전 세션 목록(session_takes.json)을 만든 작업 폴더 설정."""
    work = Path(tempfile.mkdtemp())
    glb = work / "a.glb"
    glb.write_bytes(b"x")
    entries = [{"number": n, "prompt": f"p{n}", "seed": n, "source": {"glb": str(glb)}} for n in numbers]
    (work / "session_takes.json").write_text(json.dumps({"selected_number": None, "takes": entries}), encoding="utf-8")
    return {"work_dir": str(work)}


class TakeNumbering(unittest.TestCase):
    def test_new_takes_continue_after_pending_session(self):
        p = new_panel()
        config = session_config(range(1, 13))
        p.check_last_preview(config)
        self.assertEqual(p.take_counter, 12)
        p.add_take(None, None, prompt="new")
        p.add_take(None, None, prompt="new2")
        self.assertEqual([t["number"] for t in p.takes], [13, 14])
        p.restore_last_preview(config)
        numbers = [t["number"] for t in p.takes]
        self.assertEqual(numbers, list(range(1, 15)))

    def test_duplicates_saved_by_1_0_0_are_renumbered(self):
        p = new_panel()
        config = session_config([1, 2, 3, 1, 2])
        p.check_last_preview(config)
        p.restore_last_preview(config)
        self.assertEqual([t["number"] for t in p.takes], [1, 2, 3, 4, 5])
        p.add_take(None, None, prompt="after")
        self.assertEqual(p.takes[-1]["number"], 6)

    def test_restored_list_is_sorted_by_number(self):
        # 1.0.0 버그로 겹쳤다가 옮겨진 #17, #18이 목록 중간에 남아 있던 실제 사례
        p = new_panel()
        config = session_config(list(range(1, 13)) + [17, 18, 13, 14, 15, 16, 19, 20])
        p.check_last_preview(config)
        p.restore_last_preview(config)
        self.assertEqual([t["number"] for t in p.takes], list(range(1, 21)))
        p.add_take(None, None, prompt="next")
        self.assertEqual(p.takes[-1]["number"], 21)


class TakeLimit(unittest.TestCase):
    def test_starred_takes_do_not_count_toward_the_limit(self):
        p = new_panel()
        starred = [p.add_take(MagicMock(), f"s{i}", prompt=f"star {i}", duration=1.0) for i in range(5)]
        for take in starred:
            p.toggle_star(take["id"])
        for i in range(panel.MAX_TAKES + 1):
            p.add_take(MagicMock(), f"u{i}", prompt=f"plain {i}", duration=1.0)
        unstarred = [t for t in p.takes if not t["starred"]]
        self.assertEqual(len(unstarred), panel.MAX_TAKES)
        self.assertTrue(all(t in p.takes for t in starred), "every starred take is kept")
        self.assertEqual(unstarred[0]["prompt"], "plain 1", "the oldest unstarred take was removed")
        self.assertEqual(len(p.takes), panel.MAX_TAKES + 5)


class RemoveTake(unittest.TestCase):
    def test_status_line_after_removing_takes(self):
        p = new_panel()
        a = p.add_take(MagicMock(), "dir-a", prompt="a", seed=1, duration=1.0)
        b = p.add_take(MagicMock(), "dir-b", prompt="b", seed=2, duration=1.0)
        p.message = "Take #2 · 1.00s · seed 2"
        p.remove_take(b["id"])
        self.assertTrue(p.message.startswith("Take #1"), p.message)
        p.remove_take(a["id"])
        self.assertEqual(p.message, panel.IDLE_MESSAGE)


class TakeKeys(unittest.TestCase):
    def test_keys_are_per_take_and_saved_in_session(self):
        p = new_panel()
        used = [{"time": 0.5, "bone": "left_hand", "offset": [0.1, 0.2, -0.05]}]
        keyed = p.add_take(MagicMock(), "dir-a", prompt="wave", hand_keys=used)
        plain = p.add_take(MagicMock(), "dir-b", prompt="walk")
        # 키로 만든 테이크는 이미 옮겨진 포즈라 만들 때 쓴 키를 다시 보여 주지 않는다(오프셋이 두 번 들어감)
        self.assertEqual(panel.panel_keys(keyed), [])
        self.assertEqual(panel.panel_keys(plain), [])
        self.assertEqual(keyed["hand_keys"], used)  # 기록으로는 남는다
        # 편집한 키는 그 테이크에만 남고, 읽을 수 없는 항목은 버린다. 예전 "hand" 필드도 읽는다
        p.set_take_keys(plain["id"], [{"time": 1.2, "bone": "hips", "offset": [0, -20, 0]},
                                      {"time": "x", "bone": "hips", "offset": [0, 0, 0]},
                                      {"time": 2.0, "hand": "left", "offset": [0, 5, 0]}])
        self.assertEqual(panel.panel_keys(plain), [{"time": 1.2, "bone": "hips", "offset": [0.0, -20.0, 0.0]},
                                                   {"time": 2.0, "bone": "left_hand", "offset": [0.0, 5.0, 0.0]}])
        self.assertEqual(panel.panel_keys(keyed), [])
        self.assertEqual(panel.Panel.take_entry(plain)["edit_keys"], panel.panel_keys(plain))


class Batch(unittest.TestCase):
    def run_batch(self, fixed):
        p = new_panel()
        launched = []
        p.generate = lambda: launched.append((p.job["seed"], p.job["folder"], p.batch_label(), p.job["staging"]))
        first = Path(tempfile.mkdtemp()) / "job"
        first.mkdir()
        (first / "interpretation.json").write_text('{"english": "A person waves.", "korean": ""}', encoding="utf-8")
        p.job = {"folder": first, "seed": 100, "take_root": "/KimodoTakes", "output_folder": "/Game/Kimodo",
                 "staging": "/KimodoTakes/_Import/aaa", "stage": "Conversion",
                 "batch": {"index": 1, "total": 3, "fixed_seed": fixed, "base_seed": 100}}
        self.assertEqual(p.batch_label(), "Take 1/3")
        results = [p.continue_batch(), p.continue_batch(), p.continue_batch()]
        self.assertEqual(results, [True, True, False])
        self.assertEqual([label for _, _, label, _ in launched], ["Take 2/3", "Take 3/3"])
        folders = [folder for _, folder, _, _ in launched]
        self.assertEqual(len(set(folders)), 2)
        for folder in folders:
            self.assertNotEqual(folder, first)
            self.assertTrue((folder / "interpretation.json").is_file())
        stagings = [staging for _, _, _, staging in launched]
        self.assertTrue(all(s.startswith("/KimodoTakes/_Import/") and s != "/KimodoTakes/_Import/aaa" for s in stagings))
        return [seed for seed, _, _, _ in launched]

    def test_fixed_seed_counts_up(self):
        self.assertEqual(self.run_batch(fixed=True), [101, 102])

    def test_random_seeds_differ(self):
        seeds = self.run_batch(fixed=False)
        self.assertEqual(len(set(seeds + [100])), 3)

    def test_single_take_has_no_label(self):
        p = new_panel()
        p.job = {"batch": {"index": 1, "total": 1}}
        self.assertEqual(p.batch_label(), "")


class FakeWorker:
    instances = []

    def __init__(self, key, python, env, model, log_path):
        self.key, self.log_path, self.stopped, self.last_used = key, log_path, False, time.monotonic()
        self.process = MagicMock(returncode=None)
        self.pid = 1234
        FakeWorker.instances.append(self)

    def alive(self):
        return not self.stopped

    def loaded(self):
        return True

    def submit(self, request):
        self.requests = getattr(self, "requests", []) + [request]

    def stop(self):
        self.stopped = True


class ResidentWorker(unittest.TestCase):
    def setUp(self):
        FakeWorker.instances.clear()
        self.real = panel.KimodoWorker
        panel.KimodoWorker = FakeWorker

    def tearDown(self):
        panel.KimodoWorker = self.real

    def job(self, model="Kimodo-SOMA-RP-v1.2", device="stream"):
        return {"python": "py.exe", "model": model, "work_dir": tempfile.mkdtemp(),
                "env": {"KIMODO_TEXT_ENCODER": device, "HF_HUB_CACHE": "cache"}}

    def test_worker_is_reused_until_settings_change(self):
        p = new_panel()
        p.job = self.job()
        first = p.ensure_worker()
        self.assertIs(p.ensure_worker(), first)
        p.job = self.job(device="stream-exact")
        second = p.ensure_worker()
        self.assertIsNot(second, first)
        self.assertTrue(first.stopped)

    def test_idle_worker_is_unloaded(self):
        p = new_panel()
        p.job = self.job()
        worker = p.ensure_worker()
        p.job = None
        p.stop_idle_worker()
        self.assertIs(p.worker, worker)
        worker.last_used -= panel.WORKER_IDLE_SECONDS + 1
        p.stop_idle_worker()
        self.assertIsNone(p.worker)
        self.assertTrue(worker.stopped)

    def test_cancel_keeps_worker_and_close_unloads_it(self):
        p = new_panel()
        p.job = self.job()
        worker = p.ensure_worker()
        p.job.update(stage="Kimodo", batch={"index": 1, "total": 1})
        p.process = panel.WorkerTask(worker, Path(tempfile.mkdtemp()) / "Kimodo.done.json")
        p.cancel()
        self.assertIsNone(p.process)
        self.assertIs(p.worker, worker)
        self.assertFalse(worker.stopped)
        p.close()
        self.assertIsNone(p.worker)
        self.assertTrue(worker.stopped)

    def test_stage_label_before_the_request_log_exists(self):
        p = new_panel()
        p.job = self.job()
        worker = p.ensure_worker()
        worker.abandoned = None
        folder = Path(tempfile.mkdtemp())
        p.job.update(stage="Kimodo", folder=folder)
        p.process = panel.WorkerTask(worker, folder / "Kimodo.done.json")
        self.assertEqual(p.progress()[0], panel.STAGE_PREPARING)
        worker.abandoned = Path(tempfile.mkdtemp()) / "old.done.json"  # 취소한 요청이 아직 안 끝남
        self.assertEqual(p.progress()[0], "Finishing the cancelled take")
        worker.abandoned.write_text('{"ok": true}', encoding="utf-8")
        self.assertEqual(p.progress()[0], panel.STAGE_PREPARING)
        # 워커가 처음 뜨는 중: PyTorch import → 모델 로딩
        worker.loaded = lambda: False
        worker.log_path = Path(tempfile.mkdtemp()) / "KimodoWorker.log"
        worker.log_path.write_text("FutureWarning: ...\n", encoding="utf-8")
        self.assertEqual(p.progress()[0], panel.STAGE_STARTING_KIMODO)
        worker.log_path.write_text("Using device: cuda:0\n", encoding="utf-8")
        self.assertEqual(p.progress()[0], panel.STAGE_LOADING_MODEL)

    def test_generate_sends_steps_to_the_worker(self):
        p = new_panel()
        folder = Path(tempfile.mkdtemp())
        (folder / "interpretation.json").write_text('{"english": "A person waves.", "korean": ""}', encoding="utf-8")
        p.job = dict(self.job(), folder=folder, duration=4.0, seed=42, steps=20, stage="Interpretation")
        p.generate()
        request = p.worker.requests[-1]
        self.assertEqual((request["seed"], request["steps"], request["duration"]), (42, 20, 4.0))
        self.assertEqual(request["prompt"], "A person waves")

    def keyed_panel(self, npz=True):
        p = new_panel()
        job = Path(tempfile.mkdtemp())
        if npz:
            (job / "SOMA.npz").write_bytes(b"x")
        take = p.add_take(MagicMock(), "dir", prompt="wave", english="A person waves", seed=4, steps=20, seconds=4.0,
                          duration=3.97, source={"glb": str(job / "SOMA_Motion.glb")})
        started = {}
        p.start = lambda prompt, duration, revision, config, hand_keys=None, label="": started.update(
            prompt=prompt, duration=duration, config=config, hand_keys=hand_keys, label=label)
        return p, take, job, started

    def test_keyed_uses_the_take_and_sends_keys(self):
        p, take, job, started = self.keyed_panel()
        p.start_keyed({"hand_keys": [{"time": 0.9, "hand": "right", "offset": [30, 5, 0]},
                                     {"time": 1.5, "hand": "L", "offset": [-30, 0, 10]}], "steps": 100})
        self.assertEqual((started["prompt"], started["duration"]), ("A person waves", "4.0"))
        self.assertEqual((started["config"]["seed"], started["config"]["count"], started["config"]["steps"]), ("4", 1, 20))
        self.assertEqual(started["hand_keys"]["base"], str(job / "SOMA.npz"))
        self.assertEqual(started["hand_keys"]["keys"], [
            {"time": 0.9, "bone": "right_hand", "offset": [0.3, 0.05, 0.0]},
            {"time": 1.5, "bone": "left_hand", "offset": [-0.3, 0.0, 0.1]}])
        self.assertIn("2 keys", started["label"])

    def test_body_key_parts(self):
        keys = panel.parse_hand_keys([
            {"time": 1, "bone": "hips", "offset": [0, -20, 0]},
            {"time": 1, "bone": "Left-Foot", "offset": [10, 0, 0]},
            {"time": 2, "bone": "RF", "offset": [0, 0, 5]},
            {"time": 3, "hand": "left", "offset": [0, 0, 0]},
            {"time": 4, "offset": [0, 0, 0]},
        ])
        self.assertEqual([(k["bone"], k["time"]) for k in keys],
                         [("right_hand", 4.0), ("left_hand", 3.0), ("right_foot", 2.0), ("left_foot", 1.0), ("hips", 1.0)])
        self.assertEqual(keys[-1]["offset"], [0.0, -0.2, 0.0])
        with self.assertRaisesRegex(ValueError, "Unknown key part"):
            panel.parse_hand_keys([{"time": 1, "bone": "head", "offset": [0, 0, 0]}])

    def test_keyed_errors(self):
        p, take, job, _ = self.keyed_panel()
        with self.assertRaisesRegex(ValueError, "at least one"):
            p.start_keyed({"hand_keys": []})
        with self.assertRaisesRegex(ValueError, "inside the take"):
            p.start_keyed({"hand_keys": [{"time": 4.0, "offset": [0, 0, 0]}]})
        with self.assertRaisesRegex(ValueError, "at most"):
            p.start_keyed({"hand_keys": [{"time": i * 0.1, "offset": [0, 0, 0]} for i in range(20)]})
        p2, _, _, _ = self.keyed_panel(npz=False)
        with self.assertRaisesRegex(FileNotFoundError, "no Kimodo motion file"):
            p2.start_keyed({"hand_keys": [{"time": 1.0, "offset": [0, 0, 0]}]})
        p3 = new_panel()
        with self.assertRaisesRegex(ValueError, "Select the take"):
            p3.start_keyed({"hand_keys": [{"time": 1.0, "offset": [0, 0, 0]}]})

    def test_generate_sends_hand_keys_to_the_worker(self):
        p = new_panel()
        folder = Path(tempfile.mkdtemp())
        (folder / "interpretation.json").write_text('{"english": "A person waves.", "korean": ""}', encoding="utf-8")
        keys = {"base": "x.npz", "keys": [{"time": 1.0, "hand": "right", "offset": [0.3, 0, 0]}]}
        p.job = dict(self.job(), folder=folder, duration=4.0, seed=4, steps=100, stage="Interpretation", hand_keys=keys)
        p.generate()
        self.assertEqual(p.worker.requests[-1]["hand_keys"], keys)

    def test_cancel_remembers_the_abandoned_request(self):
        p = new_panel()
        p.job = self.job()
        worker = p.ensure_worker()
        done = Path(tempfile.mkdtemp()) / "Kimodo.done.json"
        p.job.update(stage="Kimodo", batch={"index": 1, "total": 1})
        p.process = panel.WorkerTask(worker, done)
        p.cancel()
        self.assertEqual(worker.abandoned, done)

    def test_worker_task_reports_done_file_and_dead_worker(self):
        worker = FakeWorker(None, None, None, None, Path("worker.log"))
        done = Path(tempfile.mkdtemp()) / "Kimodo.done.json"
        task = panel.WorkerTask(worker, done)
        self.assertIsNone(task.poll())
        done.write_text('{"ok": false, "error": "RuntimeError: boom"}', encoding="utf-8")
        self.assertEqual(task.poll(), 1)
        self.assertIn("boom", task.error)
        done.write_text('{"ok": true}', encoding="utf-8")
        self.assertEqual(task.poll(), 0)
        dead = panel.WorkerTask(worker, Path(tempfile.mkdtemp()) / "none.json")
        worker.stopped = True
        self.assertEqual(dead.poll(), 1)
        self.assertIn("worker stopped", dead.error)


class Helpers(unittest.TestCase):
    def test_names(self):
        self.assertEqual(panel.prompt_core("A person waves the right hand."), "WavesRightHand")
        self.assertEqual(panel.prompt_core("..."), "Motion")
        self.assertEqual(panel.preview_name(3, "Walk forward four steps", 7, "a1b2c3"), "T03_WalkForwardFour_s7_a1b2")
        self.assertEqual(panel.suggest_name({"prompt": "Wave the right hand", "seed": 11}, "/Game/Kimodo"),
                         "AS_Kimodo_WaveRightHand_s11")

    def test_suggest_name_skips_existing(self):
        existing = {"/Game/Kimodo/AS_Kimodo_Jump_s1", "/Game/Kimodo/AS_Kimodo_Jump_s1_2"}
        panel.ASSETS.does_asset_exist.side_effect = lambda path: path in existing
        try:
            self.assertEqual(panel.suggest_name({"prompt": "Jump", "seed": 1}, "/Game/Kimodo"), "AS_Kimodo_Jump_s1_3")
        finally:
            panel.ASSETS.does_asset_exist.side_effect = None

    def test_save_folder_and_agents(self):
        self.assertEqual(panel.save_folder_of({"save_folder": "/Game/Anims/", "output_folder": "/Game/X"}), "/Game/Anims")
        self.assertEqual(panel.save_folder_of({"save_folder": "", "output_folder": "/Game/X"}), "/Game/X")
        self.assertEqual(panel.save_folder_of({"save_folder": "bad path"}), "")
        self.assertEqual(panel.selected_agents({"agents": [" Claude", "unknown", "claude", "codex"]}), ["claude", "codex"])

    def test_steps_parsing_and_labels(self):
        self.assertEqual([panel.parse_steps(v) for v in (None, "", "abc", "20", 0, 5000, 100)], [100, 100, 100, 20, 1, 1000, 100])
        take = {"number": 4, "duration": 4.0, "seed": 42, "steps": 20}
        self.assertEqual(panel.take_summary(take), "Take #4 · 4.00s · seed 42 · 20 steps")
        self.assertEqual(panel.take_summary(dict(take, steps=100)), "Take #4 · 4.00s · seed 42")
        self.assertEqual(panel.take_summary(dict(take, steps=None)), "Take #4 · 4.00s · seed 42")

    def test_clean_text_strips_ansi(self):
        self.assertEqual(panel.clean_text("\x1b[32mok\x1b[0m\tdone\n"), "okdone\n")

    def test_kimodo_progress_stages(self):
        log = Path(tempfile.mkdtemp()) / "Kimodo.log"
        self.assertEqual(panel.kimodo_progress(log), (panel.STAGE_LOADING_MODEL, -1.0))
        log.write_text("Using device: cuda:0\n", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log)[0], panel.STAGE_LOADING_MODEL)
        # 상주 워커 요청은 모델을 불러오지 않으므로 로딩이 아니라 준비 단계다.
        log.write_text("Using device: cuda:0\nLoaded model: kimodo-soma-rp-v1.2 (resident worker)\n", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log), (panel.STAGE_PREPARING, -1.0))
        log.write_text("Loaded model\nText encoder: low-memory streaming on cpu (exact)\n", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log)[0], panel.STAGE_LOADING_ENCODER)
        log.write_text("Loaded model\nText encoder: low-memory streaming\nText encoder ready in 1.8s\n", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log)[0], panel.STAGE_ENCODING)
        log.write_text("Text encoder: cached embedding\n 37%|###| 37/100 [00:01<00:02]\r 50%|###| 50/100 [", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log), (panel.STAGE_GENERATING, 0.5))
        log.write_text("Text encoder ready in 1.8s\nEncoded prompt in 36.9s\n", encoding="utf-8")
        self.assertEqual(panel.kimodo_progress(log), (panel.STAGE_GENERATING, 0.0))

    def test_last_log_line_handles_carriage_returns(self):
        log = Path(tempfile.mkdtemp()) / "x.log"
        log.write_text("first\n 10%|#| 10/100\r 20%|#| 20/100\r\n\n", encoding="utf-8")
        self.assertEqual(panel.last_log_line(log), "20%|#| 20/100")

    def test_setup_state_reports_missing_python(self):
        self.assertEqual(panel.setup_state({"python": r"C:\nope\python.exe", "model_cache": "x"}),
                         ("missing", "Python environment"))

    def test_revise_without_agent_appends_request(self):
        interpret = {}
        exec(compile((SCRIPTS / "interpret.py").read_text(encoding="utf-8"), "interpret.py", "exec"), interpret)
        result = interpret["direct_description"]({"request": "Jump  higher", "previous_motion": "A person hops."})
        self.assertEqual(result["english"], "A person hops. Jump higher")


if __name__ == "__main__":
    unittest.main(verbosity=2)
