"""Helpers for headless editor tests (run inside UnrealEditor-Cmd -run=pythonscript).

Each test file creates an EditorTest, drives Scripts/panel.py through dispatch() exactly like the C++ panel does,
records checks, and cleans up its content folder and work folder. Results are appended as JSON lines to the file in
KIMODO_TEST_RESULTS (set by Tests/run_tests.ps1) and also logged as "KIMODO_TEST ..." warnings.

Needs: Kimodo installed (plugin venv + models) and a project that has SKM_Manny_Simple (e.g. the UE5 templates).
"""
import base64
import json
import os
import runpy
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import unreal

PLUGIN = Path(os.environ.get("KIMODO_PLUGIN_DIR") or Path(__file__).resolve().parents[2])
PANEL = PLUGIN / "Scripts" / "panel.py"
ROOT = Path(os.environ.get("LOCALAPPDATA", "")) / "KimodoAnimation"
RESULTS = os.environ.get("KIMODO_TEST_RESULTS", "")
ASSETS = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
TAKE_ROOT = "/KimodoTakes"
TAKE_DIR = Path(unreal.Paths.project_saved_dir()).resolve() / "Kimodo" / "TakeAssets"
# 처음 보는 프롬프트는 인코딩에 수십 초가 걸리므로 테스트는 같은 프롬프트를 재사용한다(임베딩 캐시).
PROMPT = "A person waves the right hand."


def find_mesh(name="SKM_Manny_Simple"):
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for data in registry.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "SkeletalMesh")):
        if str(data.asset_name) == name:
            return f"{data.package_name}.{data.asset_name}"
    raise RuntimeError(f"{name} not found in this project")


class EditorTest:
    def __init__(self, name, **config):
        self.name = name
        self.folder = f"/Game/KimodoTest_{name}"
        self.work = Path(tempfile.mkdtemp(prefix=f"kimodo_{name}_"))
        self.failed = 0
        self.base = {
            "value": "", "duration": "2.0", "seed": "", "target_mesh": find_mesh(), "target_rig": "", "retargeter": "",
            "output_folder": self.folder, "save_folder": self.folder, "model": "Kimodo-SOMA-RP-v1.2",
            "align_bones": [], "python": str(ROOT / "venv" / "Scripts" / "python.exe"),
            "model_cache": str(ROOT / "model-cache"), "bootstrap_python": "", "work_dir": str(self.work),
            "agents": [], "text_encoder_device": "stream", "take_root": TAKE_ROOT, "take_root_dir": str(TAKE_DIR),
            "count": 1, "root_motion": False,
        }
        self.base.update(config)
        self.mod = None
        self.reload()

    # -- panel ---------------------------------------------------------------------
    def reload(self):
        """에디터를 다시 연 것처럼 panel.py를 새로 불러온다(이전 인스턴스의 워커는 내린다)."""
        if self.mod is not None:
            self.mod["close_panel"]()
        self.mod = runpy.run_path(str(PANEL))
        self.panel = self.mod["panel"]
        return self.call("poll")

    def call(self, action, value="", **extra):
        payload = dict(self.base, action=action, value=value, **extra)
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return json.loads(base64.b64decode(self.mod["dispatch"](encoded)).decode("utf-8"))

    def wait(self, state, stop=None, timeout=600, **extra):
        """작업이 끝날 때까지 poll한다. stop(state)가 참이면 그 자리에서 멈춘다. (state, events, stopped)."""
        events, started = [], time.monotonic()
        while state["busy"]:
            if stop and stop(state):
                return state, events, True
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"{self.name}: still busy after {timeout}s: {state['message']}")
            time.sleep(0.25)
            state = self.call("poll", **extra)
            if state["event"]:
                events.append(state["event"])
        return state, events, False

    # -- results -------------------------------------------------------------------
    def check(self, check, ok, detail=""):
        ok = bool(ok)
        self.failed += not ok
        unreal.log_warning(f"KIMODO_TEST {self.name} {'PASS' if ok else 'FAIL'} {check} {detail}")
        self.record({"test": self.name, "check": check, "ok": ok, "detail": str(detail)})
        return ok

    def record(self, entry):
        if RESULTS:
            with open(RESULTS, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def finish(self):
        """테이크 캐시·저장 에셋·작업 폴더를 지우고 워커를 내린다."""
        try:
            for take in list(self.call("poll")["takes"]):
                self.call("discard", take["id"])
            self.call("poll")
            self.mod["close_panel"]()
            if ASSETS.does_directory_exist(self.folder):
                ASSETS.delete_directory(self.folder)
            shutil.rmtree(self.work, ignore_errors=True)
        except Exception:
            self.check("cleanup", False, traceback.format_exc())
        self.record({"test": self.name, "done": True, "failed": self.failed})


def run(name, body, **config):
    """테스트 본문을 실행하고, 예외도 실패로 기록한 뒤 정리한다."""
    test = EditorTest(name, **config)
    try:
        body(test)
    except Exception:
        test.check("exception", False, traceback.format_exc())
    finally:
        test.finish()
