from base64 import b64decode, b64encode
import json
import math
import os
from pathlib import Path
import random
import re
import runpy
import shutil
import subprocess
import time
import traceback
from uuid import uuid4

import unreal as u


SCRIPTS = Path(__file__).resolve().parent
KEPT_JOBS = 20
# 패널에 남겨 둘 테이크 수. 넘으면 별표 없는 가장 오래된 테이크의 임시 에셋부터 지운다.
MAX_TAKES = 8
AGENT_NAMES = ("codex", "claude", "gemini")
ASSETS = u.get_editor_subsystem(u.EditorAssetSubsystem)
retarget = runpy.run_path(str(SCRIPTS / "retarget.py"))["retarget"]
direct_description = runpy.run_path(str(SCRIPTS / "interpret.py"))["direct_description"]
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
TEXT_ENCODER_DEVICES = ("stream", "stream-exact", "cpu", "cuda", "auto")
# 터미널 색상·커서 코드(ESC[...m 등)와 줄바꿈 외 제어 문자. Slate 글꼴에 글리프가 없어 매 프레임 경고가 난다.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def clean_text(text):
    return CONTROL_CHARS.sub("", ANSI_ESCAPE.sub("", text))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def selected_agents(config):
    agents = []
    for name in config.get("agents", []):
        name = name.strip().lower()
        if name in AGENT_NAMES and name not in agents:
            agents.append(name)
    return agents


def load_optional(path, kind):
    if not path:
        return None
    asset = u.load_asset(path)
    if not asset:
        raise RuntimeError(f"The {kind} {path} could not be loaded.")
    return asset


def find_ik_rig(target):
    """대상 메시와 같은 메시(우선) 또는 같은 스켈레톤을 쓰는 IK Rig를 찾는다."""
    skeleton = target.get_editor_property("skeleton")
    registry = u.AssetRegistryHelpers.get_asset_registry()
    same_skeleton = None
    for data in registry.get_assets_by_class(u.TopLevelAssetPath("/Script/IKRig", "IKRigDefinition")):
        rig = data.get_asset()
        if not rig:
            continue
        mesh = u.IKRigController.get_controller(rig).get_skeletal_mesh()
        if not mesh:
            continue
        if mesh == target:
            return rig
        if same_skeleton is None and mesh.get_editor_property("skeleton") == skeleton:
            same_skeleton = rig
    return same_skeleton


def generate_ik_rig(target):
    """UE5 자동 캐릭터화로 표준 이름의 리타게팅 체인을 가진 임시 IK Rig를 만든다."""
    rig = u.new_object(u.IKRigDefinition)
    controller = u.IKRigController.get_controller(rig)
    if not controller.set_skeletal_mesh(target):
        raise RuntimeError(f"Could not create an IK Rig for {target.get_name()}.")
    controller.apply_auto_generated_retarget_definition()
    if not controller.get_retarget_chains():
        raise RuntimeError(f"Unreal could not characterize {target.get_name()}. Choose a Target IK Rig in the panel.")
    return rig


def resolve_target(config):
    target = load_optional(config.get("target_mesh"), "target mesh")
    if not isinstance(target, u.SkeletalMesh):
        raise ValueError("Choose a target skeletal mesh.")

    # Retargeter를 지정하면 IK Rig 선택과 자동 매핑을 건너뛴다.
    retargeter = load_optional(config.get("retargeter"), "retargeter")
    if retargeter:
        return target, None, retargeter, "custom retargeter"

    rig = load_optional(config.get("target_rig"), "target IK Rig")
    if rig:
        if not u.IKRigController.get_controller(rig).is_skeletal_mesh_compatible(target):
            raise ValueError(f"{rig.get_name()} does not match the skeleton of {target.get_name()}.")
        return target, rig, None, rig.get_name()

    rig = find_ik_rig(target)
    if rig:
        return target, rig, None, rig.get_name()
    return target, generate_ik_rig(target), None, "auto-generated IK Rig"


def import_and_retarget(glb, staging, target, rig, align_bones, retargeter):
    """glTF 가져오기와 리타게팅. 에셋 작업이라 게임 스레드에서만 돌 수 있으므로 진행 창을 띄워 멈춘 것처럼 보이지 않게 한다."""
    with u.ScopedSlowTask(2, "Importing the Kimodo motion…") as task:
        task.make_dialog(False)
        task.enter_progress_frame(1, "Importing the Kimodo motion…")
        mesh, motion = import_gltf(glb, staging + "/Source")
        task.enter_progress_frame(1, f"Retargeting to {target.get_name()}…")
        return retarget(ASSETS, mesh, motion, target, rig, staging, list(align_bones or []), retargeter)


def import_gltf(path, destination):
    task = u.AssetImportTask()
    task.set_editor_properties({
        "filename": str(path), "destination_path": destination,
        "automated": True, "replace_existing": False, "save": False,
    })
    u.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    # Interchange glTF 임포터는 하위 폴더와 에셋 이름을 정하므로 종류로 찾는다.
    mesh = None
    clip = None
    for asset_path in ASSETS.list_assets(destination, recursive=True):
        asset = ASSETS.load_asset(asset_path)
        if isinstance(asset, u.SkeletalMesh):
            mesh = asset
        elif isinstance(asset, u.AnimSequence):
            clip = asset
    if not mesh or not clip:
        raise RuntimeError("Unreal could not import the SOMA mesh and animation.")
    return mesh, clip


def prune_jobs(jobs):
    if not jobs.is_dir():
        return

    # 새 작업 폴더를 만들기 전에 최근 작업 로그만 남긴다.
    folders = sorted((p for p in jobs.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders[KEPT_JOBS - 1:]:
        shutil.rmtree(folder, ignore_errors=True)


TEXT_ENCODER_REPOS = (
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp",
    "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised",
)


def model_downloaded(cache, repo):
    """Hugging Face 캐시에 저장소 스냅샷이 완전히 있는지 확인한다 (분할 가중치 포함)."""
    snapshots = cache / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snapshot in snapshots.iterdir():
        if not any(snapshot.iterdir()):
            continue
        index = snapshot / "model.safetensors.index.json"
        if not index.is_file():
            return True

        # 분할 가중치는 내려받기가 끝난 파일만 스냅샷에 연결되므로 목록과 비교한다.
        shards = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
        missing = False
        for shard in shards:
            if not (snapshot / shard).is_file():
                missing = True
        if not missing:
            return True
    return False


# Python 경로별 "kimodo를 실제로 찾을 수 있는가" 결과. 세션마다 한 번만 확인하고, 설치가 끝나면 비운다.
_import_checks = {}


def kimodo_installed(python):
    """venv의 Python이 실제로 실행되고 kimodo를 찾는지 확인한다.

    폴더만 보면 엔진을 옮기거나 업그레이드해 venv의 기반 Python이 사라져도 ready로 보인다.
    find_spec은 torch를 불러오지 않아 빠르다."""
    key = str(python).lower()
    if key not in _import_checks:
        try:
            result = subprocess.run(
                [str(python), "-c", "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('kimodo') else 1)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW, timeout=30,
            )
            _import_checks[key] = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _import_checks[key] = False
    return _import_checks[key]


def setup_state(config):
    """(ready|missing, 이유) — 생성에 필요한 Python, Kimodo 패키지, 모델 캐시를 확인한다."""
    python = Path(config.get("python") or "")
    cache = Path(config.get("model_cache") or "")
    if not config.get("python") or not python.is_file():
        return "missing", "Python environment"
    if not kimodo_installed(python):
        return "missing", "Kimodo package"
    if not config.get("model_cache") or not cache.is_dir():
        return "missing", "model cache"
    model = config.get("model") or "Kimodo-SOMA-RP-v1.2"
    for repo in ("nvidia/" + model,) + TEXT_ENCODER_REPOS:
        if not model_downloaded(cache, repo):
            return "missing", repo
    return "ready", ""


def last_log_line(path, limit=140):
    """진행 중인 단계 로그의 마지막 줄. tqdm 진행바는 \\r로 갱신되므로 \\r도 줄바꿈으로 본다."""
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 4096))
            text = log.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(re.split(r"[\r\n]+", text)):
        line = clean_text(line).strip()
        if line:
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


def read_tail(path, size=8192):
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - size))
            return log.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


TQDM_STEPS = re.compile(r"(\d+)/(\d+) \[")


def kimodo_progress(path):
    """Kimodo 단계 로그에서 (세부 단계 이름, 진행률 0~1 또는 -1)을 읽는다.

    kimodo_generate.py는 모델을 불러온 뒤 텍스트 인코더를 처음 쓸 때 'Text encoder:'를,
    인코딩이 끝나면 'Encoded prompt' 또는 'cached embedding'을 찍고, 그다음 확산 샘플링이 tqdm으로 진행된다."""
    try:
        with open(path, "rb") as log:
            text = log.read().decode("utf-8", errors="replace")
    except OSError:
        return "Loading Kimodo", -1.0
    steps = TQDM_STEPS.findall(text[-8192:])
    if "Encoded prompt" in text or "cached embedding" in text:
        if steps:
            done, total = (int(v) for v in steps[-1])
            return "Generating motion", (done / total if total else -1.0)
        return "Generating motion", 0.0
    if "Text encoder:" in text:
        return "Encoding prompt", -1.0
    return "Loading Kimodo", -1.0


NAME_STOP_WORDS = {
    "a", "an", "the", "person", "someone", "and", "then", "with", "while", "of", "to", "in", "on", "at",
    "his", "her", "their", "both", "one", "two", "it", "is", "slowly", "quickly",
}


def suggest_name(take, folder):
    """AS_Kimodo_<핵심 단어 3개>_s<시드>. 같은 이름이 있으면 _2, _3을 붙인다."""
    words = [w for w in re.findall(r"[A-Za-z]+", take.get("english") or take.get("prompt") or "")
             if w.lower() not in NAME_STOP_WORDS]
    core = "".join(w.capitalize() for w in words[:3]) or "Motion"
    base = f"AS_Kimodo_{core}_s{take.get('seed')}"
    name, index = base, 2
    while folder and ASSETS.does_asset_exist(f"{folder}/{name}"):
        name, index = f"{base}_{index}", index + 1
    return name


def save_folder_of(config):
    folder = (config.get("save_folder") or "").strip().rstrip("/") or config.get("output_folder") or "/Game/Kimodo"
    return folder if re.fullmatch(r"(/[\w\-]+)+", folder) else ""


def folder_size_gb(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                # 심볼릭 링크 스냅샷을 이중으로 세지 않도록 링크 자체의 크기를 쓴다.
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total / 1024**3


class Panel:
    def __init__(self):
        self.process = None
        self.job = None
        self.preview = None
        self.saved_clip = None
        self.preview_dir = None
        # 패널 미리보기가 아직 참조할 수 있어 다음 요청에서 지우는 임시 폴더
        self.stale_dirs = []
        self.seed = None
        self.previous_motion = ""
        self.interpretation = ""
        # Kimodo에 실제로 전달한 영어 프롬프트
        self.english_prompt = ""
        self.message = "Choose a target mesh, then enter a motion prompt and duration."
        self.restored = False
        # 다시 가져와야 하는 직전 미리보기(glTF). 에디터 시작을 막지 않도록 버튼을 눌렀을 때만 복원한다.
        self.restorable = None
        # 생성 결과(테이크) 목록. 새로 생성해도 이전 결과를 지우지 않고 쌓아 두고, 고른 테이크로 수정·저장한다.
        self.takes = []
        self.selected = None
        self.take_counter = 0

    # -- takes -----------------------------------------------------------------
    def find_take(self, take_id):
        return next((t for t in self.takes if t["id"] == take_id), None)

    def selected_take(self):
        return self.find_take(self.selected) if self.selected else None

    def apply_selected(self):
        """선택한 테이크의 정보를 패널 상태(미리보기, 수정 기준 동작, 시드, 해석)로 옮긴다."""
        take = self.selected_take()
        self.preview = take["clip"] if take else None
        self.preview_dir = take["dir"] if take else None
        self.previous_motion = take["motion"] if take else ""
        self.seed = take["seed"] if take else None
        self.interpretation = take["interpretation"] if take else ""
        self.english_prompt = take["english"] if take else ""

    def add_take(self, clip, directory, **info):
        self.take_counter += 1
        take = {
            "id": uuid4().hex[:12], "number": self.take_counter, "clip": clip, "dir": directory,
            "prompt": "", "english": "", "interpretation": "", "motion": "", "seed": None,
            "duration": clip.get_play_length(), "starred": False, "saved": "", "target": "",
        }
        take.update(info)
        self.takes.append(take)
        self.selected = take["id"]
        self.apply_selected()
        self.prune_takes()
        return take

    def prune_takes(self):
        while len(self.takes) > MAX_TAKES:
            victim = next((t for t in self.takes if not t["starred"] and t["id"] != self.selected), None)
            if victim is None:
                return
            self.remove_take(victim["id"])

    def remove_take(self, take_id):
        take = self.find_take(take_id)
        if take is None:
            return
        self.takes.remove(take)
        # C++ 미리보기가 아직 참조할 수 있어 임시 에셋은 다음 요청에서 지운다. 저장한 에셋(dir 없음)은 남긴다.
        if take["dir"]:
            self.stale_dirs.append(take["dir"])
        if self.selected == take_id:
            self.selected = self.takes[-1]["id"] if self.takes else None
            self.apply_selected()

    def select_take(self, take_id):
        if self.process is not None or self.find_take(take_id) is None:
            return
        self.selected = take_id
        self.apply_selected()
        take = self.selected_take()
        self.message = f"Take #{take['number']} · {take['duration']:.2f}s · seed {take['seed']}"

    def toggle_star(self, take_id):
        take = self.find_take(take_id)
        if take is not None:
            take["starred"] = not take["starred"]

    @staticmethod
    def last_preview_file(work_dir):
        return Path(work_dir) / "last_preview.json"

    def save_last_preview_state(self, work_dir, state):
        try:
            path = self.last_preview_file(work_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            u.log_warning(f"Could not save Kimodo last preview metadata: {error}")

    def check_last_preview(self, config):
        """첫 poll에서 호출한다. 저장된 클립은 불러오기만 하면 되므로 바로 복원하고,
        glTF를 다시 가져와야 하는 미리보기는 '직전 미리보기 복원' 버튼으로 미룬다."""
        work_dir = config.get("work_dir")
        if not work_dir:
            return
        state_file = self.last_preview_file(work_dir)
        if not state_file.is_file():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        saved_path = data.get("saved_clip") or ""
        if saved_path and ASSETS.does_asset_exist(saved_path):
            clip = ASSETS.load_asset(saved_path)
            if isinstance(clip, u.AnimSequence):
                # 저장한 에셋은 임시 폴더가 아니므로 dir 없이 테이크로 둔다(테이크를 지워도 에셋은 남는다).
                self.add_take(clip, None, prompt=data.get("prompt", ""), english=data.get("english_prompt", ""),
                              interpretation=data.get("interpretation", ""), motion=data.get("motion", ""),
                              seed=data.get("seed"), saved=saved_path)
                self.message = f"Restored saved clip · {saved_path}"
                return

        if Path(data.get("glb") or "").is_file():
            self.restorable = data
            self.message = "The previous preview can be restored. Click Restore last preview."

    def restore_last_preview(self, config):
        data = self.restorable
        if self.process is not None or not data:
            return
        self.restorable = None

        glb = Path(data.get("glb") or "")
        if not glb.is_file():
            raise FileNotFoundError(f"The previous preview file {glb} no longer exists.")
        target, rig, retargeter, rig_label = resolve_target(data)
        output_folder = data.get("output_folder") or config.get("output_folder") or "/Game/Kimodo"
        staging = f"{output_folder}/_Preview_Restored_{uuid4().hex[:8]}"
        try:
            clip = import_and_retarget(glb, staging, target, rig, data.get("align_bones"), retargeter)
            if clip.get_editor_property("skeleton") != target.get_editor_property("skeleton"):
                raise RuntimeError(f"Retargeting did not produce an animation for {target.get_name()}.")
            clip.set_preview_skeletal_mesh(target)
        except Exception:
            self.delete_staging(staging)
            raise

        self.add_take(clip, staging, prompt=data.get("prompt", ""), english=data.get("english_prompt", ""),
                      interpretation=data.get("interpretation", ""), motion=data.get("motion", ""),
                      seed=data.get("seed"), target=target.get_name())
        self.message = f"Restored preview · {clip.get_play_length():.2f}s · seed {self.seed} · {target.get_name()} · {rig_label}"

    def start(self, prompt, duration_text, revision, config):
        if self.process is not None:
            return
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Enter a motion prompt.")
        if revision and not self.previous_motion:
            raise ValueError("Generate a preview before revising it.")
        try:
            duration = float(duration_text.strip().replace(",", "."))
        except ValueError:
            raise ValueError("Enter the duration in seconds, for example 3.5.") from None
        if not math.isfinite(duration) or duration < 0.1:
            raise ValueError("Enter a duration of at least 0.1 seconds.")

        # 시드를 직접 넣으면 항상 그 값을 쓴다. 비우면 새 미리보기는 다른 변형을 뽑고,
        # 수정은 직전 시드를 유지해 요청한 차이만 반영한다.
        seed_text = str(config.get("seed") or "").strip()
        if seed_text:
            try:
                seed = int(seed_text)
            except ValueError:
                raise ValueError("Enter the seed as a whole number, or leave it empty for a random seed.") from None
            if not 0 <= seed < 2**31:
                raise ValueError(f"Enter a seed between 0 and {2**31 - 1}.")
        elif revision and self.seed is not None:
            seed = self.seed
        else:
            seed = random.randrange(2**31)

        # 해석 CLI가 없으면 프롬프트를 그대로 Kimodo에 전달하므로 영어만 받는다.
        agents = selected_agents(config)
        if not agents and HANGUL.search(prompt):
            raise ValueError("Write the Kimodo prompt in English.")

        state, missing = setup_state(config)
        if state != "ready":
            raise FileNotFoundError(f"Kimodo is not installed on this computer (missing {missing}). Click Install Kimodo.")
        python = Path(config["python"])
        cache = Path(config["model_cache"])
        work_dir = Path(config["work_dir"])

        target, rig, retargeter, rig_label = resolve_target(config)

        job_id = uuid4().hex
        jobs = work_dir / "PanelJobs"
        prune_jobs(jobs)
        folder = jobs / job_id
        folder.mkdir(parents=True)
        self.job = {
            "folder": folder, "work_dir": work_dir, "stage": "Interpretation", "target": target, "rig": rig,
            "retargeter": retargeter, "rig_label": rig_label,
            "target_mesh_path": config.get("target_mesh") or "",
            "target_rig_path": config.get("target_rig") or "",
            "retargeter_path": config.get("retargeter") or "",
            "output_folder": config["output_folder"],
            "align_bones": list(config.get("align_bones", [])), "model": config.get("model") or "Kimodo-SOMA-RP-v1.2",
            "staging": config["output_folder"] + "/_Preview_" + job_id, "started": time.monotonic(),
            "duration": duration, "seed": seed, "request": prompt,
            # 수정은 고른 테이크를 기준으로 하므로, 결과 테이크의 원래 요청을 "기준 + 수정"으로 남긴다.
            "base_prompt": (self.selected_take() or {}).get("prompt", "") if revision else "",
        }
        # Llama 3 8B 텍스트 인코더 방식. 기본 stream은 층을 하나씩 읽어 RAM 1~2 GB로 돌고,
        # cpu/cuda/auto는 모델 전체(약 16 GB)를 올린다. 어느 쪽이든 같은 프롬프트는 캐시를 쓴다.
        device = (config.get("text_encoder_device") or "stream").lower()
        if device not in TEXT_ENCODER_DEVICES:
            device = "stream"
        env = dict(
            os.environ, HF_HUB_CACHE=str(cache), HF_HUB_OFFLINE="1",
            TEXT_ENCODER_MODE="local", TEXT_ENCODER_DEVICE="cpu" if device.startswith("stream") else device,
            KIMODO_TEXT_ENCODER=device, KIMODO_EMBEDDING_CACHE=str(cache / "kimodo-text-embeddings"),
            PYTHONUNBUFFERED="1",
        )
        self.job.update(python=python, env=env)
        request = {
            "request": prompt, "duration_seconds": duration,
            "previous_motion": self.previous_motion if revision else "",
            "agents": agents,
        }
        request_path = folder / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        if not agents:
            result = direct_description(request)
            (folder / "interpretation.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            self.generate()
            return

        self.launch([str(python), str(SCRIPTS / "interpret.py"),
                     str(request_path), str(folder / "interpretation.json")], env)
        self.message = "Interpreting the motion request…"

    def start_setup(self, config, token):
        if self.process is not None:
            return
        bootstrap = Path(config.get("bootstrap_python") or "")
        if not bootstrap.is_file():
            raise FileNotFoundError(f"Unreal's bundled Python was not found at {bootstrap}.")
        if not config.get("python") or not config.get("model_cache"):
            raise ValueError("The Kimodo Python and model cache paths are not set.")

        folder = Path(config["work_dir"]) / "Setup"
        folder.mkdir(parents=True, exist_ok=True)
        progress = folder / "progress.json"
        progress.unlink(missing_ok=True)
        self.job = {
            "folder": folder, "stage": "Setup", "started": time.monotonic(), "duration": 0,
            "progress": progress, "cache": Path(config["model_cache"]),
        }

        # 토큰은 설치 프로세스의 환경변수로만 전달하고 파일이나 로그에 남기지 않는다.
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_PROGRESS_BARS="1")
        env.pop("HF_HUB_OFFLINE", None)
        if token.strip():
            env["HF_TOKEN"] = token.strip()
        self.launch([
            str(bootstrap), str(SCRIPTS / "setup.py"), config["python"], config["model_cache"],
            config.get("model") or "Kimodo-SOMA-RP-v1.2", str(progress),
        ], env)
        self.message = "Installing Kimodo…"

    def setup_progress(self):
        try:
            return json.loads(self.job["progress"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def generate(self):
        folder = self.job["folder"]
        result = json.loads((folder / "interpretation.json").read_text(encoding="utf-8"))
        self.interpretation = result["korean"]
        self.job.update(stage="Kimodo", motion=result["english"])
        # CLI는 마침표마다 길이를 반복하므로 한 개의 프롬프트로 전달한다.
        prompt = ", ".join(part.strip() for part in result["english"].split(".") if part.strip())
        self.english_prompt = prompt
        duration = self.job["duration"]
        self.launch([
            str(self.job["python"]), str(SCRIPTS / "kimodo_generate.py"), "--model", self.job["model"],
            "--duration", str(duration), "--num_samples", "1", "--seed", str(self.job["seed"]), "--bvh",
            "--bvh_standard_tpose", "--no-postprocess", "--output", str(folder / "SOMA"),
            "--", prompt,
        ], self.job["env"])
        self.message = f"Generating {duration:g}s in Kimodo…"

    def launch(self, argv, env=None):
        log = self.job["folder"] / (self.job["stage"] + ".log")
        with log.open("wb") as output:
            self.process = subprocess.Popen(
                argv, cwd=self.job["folder"], env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    def poll(self):
        if self.process is None:
            return
        status = self.process.poll()
        stage = self.job["stage"]
        folder = self.job["folder"]
        if status is None:
            elapsed = int(time.monotonic() - self.job["started"])
            if stage == "Setup":
                progress = self.setup_progress()
                step = f"{progress.get('step', 0)}/{progress.get('total', '?')}"
                size = folder_size_gb(self.job["cache"])
                self.message = (f"Setup {step} · {progress.get('message', 'Starting…')} · "
                                f"models {size:.1f} GB · {elapsed}s elapsed")
                return
            # 단계·진행률·로그 마지막 줄은 progress()로 따로 넘겨 진행 막대와 툴팁에 보여 준다.
            self.message = f"{self.job['duration']:g}s clip · seed {self.job['seed']} · {elapsed}s elapsed"
            return

        self.process = None
        if stage == "Setup":
            progress = self.setup_progress()
            self.job = None
            _import_checks.clear()
            if status:
                detail = progress.get("message") or f"exited with code {status}"
                raise RuntimeError(f"Setup failed: {detail} (log: {folder / 'Setup.log'})")
            self.message = progress.get("message") or "Kimodo is installed."
            return
        if status:
            raise RuntimeError(f"{stage} exited with code {status}. See {folder / (stage + '.log')}")
        if stage == "Interpretation":
            self.generate()
            return
        if stage == "Kimodo":
            if not (folder / "SOMA.bvh").is_file():
                raise RuntimeError(f"Kimodo did not produce a BVH. See {folder / 'Kimodo.log'}")
            # Blender 없이 Kimodo 가상환경의 numpy로 스킨 glTF를 만든다.
            self.job["stage"] = "Conversion"
            self.launch([
                str(self.job["python"]), str(SCRIPTS / "bvh_to_gltf.py"),
                str(folder / "SOMA.bvh"), str(folder / "SOMA_Motion.glb"),
            ], self.job["env"])
            self.message = "Converting the BVH to glTF…"
            return

        self.create_preview()

    def create_preview(self):
        staging = self.job["staging"]
        target = self.job["target"]
        try:
            clip = import_and_retarget(self.job["folder"] / "SOMA_Motion.glb", staging, target, self.job["rig"],
                                       self.job["align_bones"], self.job["retargeter"])
            if clip.get_editor_property("skeleton") != target.get_editor_property("skeleton"):
                raise RuntimeError(f"Retargeting did not produce an animation for {target.get_name()}.")
            clip.set_preview_skeletal_mesh(target)
        except Exception:
            self.delete_staging(staging)
            raise

        self.restorable = None
        request = self.job["request"]
        if self.job["base_prompt"]:
            request = f"{self.job['base_prompt']} → {request}"
        self.add_take(clip, staging, prompt=request, english=self.english_prompt,
                      interpretation=self.interpretation, motion=self.job["motion"],
                      seed=self.job["seed"], target=target.get_name())
        self.save_last_preview_state(self.job["work_dir"], {
            "prompt": request,
            "glb": str(self.job["folder"] / "SOMA_Motion.glb"),
            "target_mesh": self.job["target_mesh_path"] or target.get_path_name(),
            "target_rig": self.job["target_rig_path"],
            "retargeter": self.job["retargeter_path"],
            "align_bones": self.job["align_bones"],
            "output_folder": self.job["output_folder"],
            "motion": self.previous_motion,
            "interpretation": self.interpretation,
            "english_prompt": self.english_prompt,
            "seed": self.seed,
        })
        label = f"{target.get_name()} · {self.job['rig_label']}"
        self.job = None
        self.message = f"Preview ready · {clip.get_play_length():.2f}s · seed {self.seed} · {label}"

    def cancel(self):
        if self.process is None:
            return

        # 해석 단계의 CLI처럼 자식 프로세스가 남지 않도록 프로세스 트리 전체를 종료한다.
        process = self.process
        if os.name == "nt":
            # taskkill /F는 종료를 마친 뒤 돌아오므로 아래 대기는 짧게 둔다(에디터 메인 스레드).
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW, timeout=10,
            )
        else:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            u.log_warning(f"Kimodo process {process.pid} did not exit after cancellation.")

        # 선택한 테이크의 해석과 프롬프트로 되돌린다.
        stage = self.job["stage"]
        self.process = None
        self.job = None
        self.apply_selected()
        self.message = f"Cancelled during {stage}. The previous preview is unchanged."

    @staticmethod
    def delete_staging(path):
        if ASSETS.does_directory_exist(path):
            if not ASSETS.delete_directory(path):
                u.log_warning(f"Could not remove temporary Kimodo assets in {path}")

    def delete_stale_previews(self):
        for path in self.stale_dirs:
            self.delete_staging(path)
        self.stale_dirs.clear()

    def progress(self):
        """(단계 이름, 진행률 0~1 또는 -1, 로그 마지막 줄). 작업이 없으면 ("", -1, "")."""
        if self.process is None or self.job is None:
            return "", -1.0, ""
        stage = self.job["stage"]
        folder = self.job["folder"]
        detail = last_log_line(folder / (stage + ".log"))
        if stage == "Setup":
            info = self.setup_progress()
            total = info.get("total") or 0
            return "Installing", (info.get("step", 0) / total if total else -1.0), detail
        if stage == "Interpretation":
            return "Interpreting request", -1.0, detail
        if stage == "Kimodo":
            label, fraction = kimodo_progress(folder / "Kimodo.log")
            return label, fraction, detail
        if stage == "Conversion":
            return "Converting to glTF", -1.0, detail
        return stage, -1.0, detail

    def save(self, name, config):
        take = self.selected_take()
        if take is None or self.process is not None:
            return
        name = name.strip()
        if not re.fullmatch(r"\w+", name):
            raise ValueError("Use letters, numbers, or underscores in the AnimSequence name.")
        # 패널에서 저장 폴더를 고르지 않았으면 프로젝트 설정의 출력 폴더에 저장한다.
        folder = (config.get("save_folder") or "").strip().rstrip("/") or config["output_folder"]
        if not re.fullmatch(r"(/[\w\-]+)+", folder):
            raise ValueError(f"Invalid save folder: {folder}. Pick a content folder such as /Game/Kimodo.")
        destination = f"{folder}/{name}"
        if ASSETS.does_asset_exist(destination):
            raise ValueError(f"{destination} already exists. Enter another name.")
        saved = ASSETS.duplicate_asset(take["clip"].get_path_name(), destination)
        if not saved:
            raise RuntimeError(f"Could not create {destination}.")
        if not ASSETS.save_loaded_asset(saved, False):
            ASSETS.delete_loaded_asset(saved)
            raise RuntimeError(f"Could not save {destination}.")
        # 테이크는 남겨 두어 다른 테이크와 계속 비교하거나 다시 저장할 수 있게 한다.
        take["saved"] = destination
        self.saved_clip = saved
        if config.get("work_dir"):
            self.save_last_preview_state(config["work_dir"], {
                "saved_clip": destination,
                "prompt": take["prompt"],
                "motion": take["motion"],
                "interpretation": take["interpretation"],
                "english_prompt": take["english"],
                "seed": take["seed"],
            })
        self.message = f"Saved {destination}"


panel = Panel()


def shutdown():
    # 에디터를 닫을 때 진행 중인 해석·생성·변환 프로세스를 함께 종료한다.
    try:
        panel.cancel()
    except Exception:
        u.log_warning(traceback.format_exc())


u.register_python_shutdown_callback(shutdown)


def dispatch(encoded_payload):
    """C++ 패널의 요청(JSON)을 처리하고 상태 한 줄을 Base64로 돌려준다.

    print로 넘기면 0.5초마다 Output Log에 한 줄씩 쌓이므로, C++이 평가 결과(CommandResult)로 받는다."""
    payload = json.loads(b64decode(encoded_payload).decode("utf-8"))
    action = payload.get("action", "poll")
    value = payload.get("value", "")
    was_working = panel.process is not None and panel.job is not None and panel.job.get("stage") != "Setup"
    event = ""
    try:
        panel.delete_stale_previews()
        if action in ("generate", "revise"):
            panel.start(value, payload.get("duration", ""), action == "revise", payload)
        elif action == "poll":
            if not panel.restored and panel.process is None and not panel.takes:
                panel.restored = True
                try:
                    panel.check_last_preview(payload)
                except Exception as restore_error:
                    u.log_warning(f"Could not restore previous Kimodo preview: {restore_error}")
            panel.poll()
        elif action == "restore":
            panel.restore_last_preview(payload)
        elif action == "save":
            panel.save(value, payload)
        elif action == "cancel":
            panel.cancel()
            was_working = False
        elif action == "setup":
            panel.start_setup(payload, value)
        elif action == "select":
            panel.select_take(value)
        elif action == "star":
            panel.toggle_star(value)
        elif action == "discard":
            if panel.process is None:
                panel.remove_take(value)
    except Exception as error:
        u.log_error(traceback.format_exc())
        panel.message = str(error)
        if panel.process is None:
            panel.job = None
            panel.apply_selected()
    # 생성이 이번 요청에서 끝났으면 C++이 에디터 알림을 띄우도록 알려 준다.
    if was_working and panel.process is None:
        event = "failed" if panel.job is None and not panel.message.startswith("Preview ready") else "ready"
    # 설치 상태: installing, ready, missing:<빠진 항목>
    if panel.job is not None and panel.job.get("stage") == "Setup":
        setup = "installing"
    else:
        state, missing = setup_state(payload)
        setup = state if state == "ready" else f"missing:{missing}"

    stage, fraction, detail = panel.progress()
    take = panel.selected_take()
    folder = save_folder_of(payload)
    state = {
        "busy": panel.process is not None,
        "has_preview": take is not None,
        "message": clean_text(panel.message),
        "detail": clean_text(detail),
        "stage": stage,
        "progress": fraction,
        "event": event,
        "path": take["clip"].get_path_name() if take else "",
        "can_revise": bool(panel.previous_motion),
        "interpretation": clean_text(panel.interpretation),
        "english": clean_text(panel.english_prompt.replace("\n", " ")),
        "setup": setup,
        "restorable": panel.restorable is not None and panel.process is None,
        "seed": "" if panel.seed is None else str(panel.seed),
        "selected": panel.selected or "",
        "suggested_name": suggest_name(take, folder) if take else "",
        "takes": [{
            "id": t["id"], "number": t["number"], "prompt": clean_text(t["prompt"] or t["english"]),
            "seed": "" if t["seed"] is None else str(t["seed"]), "duration": round(t["duration"], 2),
            "starred": t["starred"], "saved": t["saved"], "target": t["target"],
        } for t in reversed(panel.takes)],  # 최신 테이크가 위로
    }
    # Base64(UTF-8)로 감싸 EvaluateStatement의 repr 결과에서도 한글이 그대로 전달되게 한다.
    return b64encode(json.dumps(state, ensure_ascii=False).encode("utf-8")).decode("ascii")
