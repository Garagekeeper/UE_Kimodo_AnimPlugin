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
# 별표 없는 테이크를 이만큼만 남긴다. 넘으면 별표 없는 가장 오래된 테이크부터 목록에서 뺀다.
# 별표 테이크는 이 개수에 세지 않고 몇 개든 남긴다.
MAX_TAKES = 20
# 메모리에 올려 둘 프리뷰 수. 테이크 결과는 Saved/Kimodo/TakeAssets에 에셋 파일로 남겨 두므로
# 다시 불러오는 데 수십 ms면 된다. 캐시가 없는 예전 테이크만 고를 때 glTF를 다시 가져온다.
MAX_LOADED = MAX_TAKES
AGENT_NAMES = ("codex", "claude", "gemini")
ASSETS = u.get_editor_subsystem(u.EditorAssetSubsystem)
retarget = runpy.run_path(str(SCRIPTS / "retarget.py"))["retarget"]
direct_description = runpy.run_path(str(SCRIPTS / "interpret.py"))["direct_description"]
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
TEXT_ENCODER_DEVICES = ("stream", "stream-exact", "cpu", "cuda", "auto")
# Generate ×N: 한 번에 만들 수 있는 최대 테이크 수(테이크 목록 MAX_TAKES 안에서 비교할 수 있게).
MAX_BATCH = 10
# 상주 Kimodo 워커를 이 시간(초) 동안 쓰지 않으면 내려 VRAM·RAM을 돌려준다.
WORKER_IDLE_SECONDS = 600
# 테이크가 없을 때 상태 줄 안내.
IDLE_MESSAGE = "Choose a target mesh, then enter a motion prompt and duration."
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


def import_and_retarget(glb, staging, target, rig, align_bones, retargeter, output=None, name="KimodoPreview",
                        root_motion=False):
    """glTF 가져오기와 리타게팅. 에셋 작업이라 게임 스레드에서만 돌 수 있으므로 진행 창을 띄워 멈춘 것처럼 보이지 않게 한다.
    원본은 staging/Source에, 결과 클립은 output 폴더(기본 staging)에 name으로 만든다."""
    with u.ScopedSlowTask(2, "Importing the Kimodo motion…") as task:
        task.make_dialog(False)
        task.enter_progress_frame(1, "Importing the Kimodo motion…")
        mesh, motion = import_gltf(glb, staging + "/Source")
        task.enter_progress_frame(1, f"Retargeting to {target.get_name()}…")
        return retarget(ASSETS, mesh, motion, target, rig, output or staging, list(align_bones or []), retargeter,
                        name, root_motion)


# 테이크 루트(/KimodoTakes) 아래 구조: Preview/Take_<id> 클립만 남고, _Import/<id>는 가져오는 동안만 쓰고 지운다.
PREVIEW_SUBFOLDER = "Preview"
IMPORT_SUBFOLDER = "_Import"


def take_staging(config, fallback_folder):
    """새 테이크를 가져올 임시 폴더. C++이 /KimodoTakes 마운트(Saved/Kimodo/TakeAssets)를 알려 주면 그 아래에 둔다."""
    root = (config.get("take_root") or "").rstrip("/")
    if root:
        return f"{root}/{IMPORT_SUBFOLDER}/{uuid4().hex[:12]}"
    return f"{fallback_folder}/_Preview_{uuid4().hex[:12]}"


def delete_asset_path(path):
    """폴더면 폴더째, 에셋 경로면 그 에셋만 지운다."""
    if ASSETS.does_directory_exist(path):
        return ASSETS.delete_directory(path)
    if ASSETS.does_asset_exist(path):
        return ASSETS.delete_asset(path)
    return True


def cleanup_path(clip, staging):
    """테이크를 버릴 때 지울 경로. 테이크 루트에서 가져왔으면 Preview 폴더의 클립 하나, 아니면 임시 폴더."""
    return clip.get_path_name() if f"/{IMPORT_SUBFOLDER}/" in staging else staging


def build_take_clip(glb, staging, target, rig, align_bones, retargeter, take_root, root_motion=False, label=None):
    """glTF 가져오기 + 리타게팅. 테이크 루트 아래라면 결과 클립을 Preview 폴더에 저장하고 가져온 원본은 지운다.
    (클립, 캐시 경로) — 캐시하지 않으면 캐시 경로는 ""."""
    root = (take_root or "").rstrip("/")
    cache = bool(root) and staging.startswith(f"{root}/{IMPORT_SUBFOLDER}/")
    output = f"{root}/{PREVIEW_SUBFOLDER}" if cache else staging
    key = staging.rsplit('/', 1)[1]
    # label = (번호, 프롬프트, 시드). 없으면 예전처럼 Take_<id>.
    name = (preview_name(*label, key) if label else f"Take_{key}") if cache else "KimodoPreview"
    try:
        clip = import_and_retarget(glb, staging, target, rig, align_bones, retargeter, output, name, root_motion)
        if clip.get_editor_property("skeleton") != target.get_editor_property("skeleton"):
            raise RuntimeError(f"Retargeting did not produce an animation for {target.get_name()}.")
    except Exception:
        if cache:
            delete_asset_path(f"{output}/{name}")
        raise
    clip.set_preview_skeletal_mesh(target)
    if not cache:
        return clip, ""
    # 리타게팅이 끝나면 SOMA 원본 메시와 애니메이션은 필요 없다.
    delete_asset_path(staging)
    if not ASSETS.list_assets(f"{root}/{IMPORT_SUBFOLDER}", True, True):
        ASSETS.delete_directory(f"{root}/{IMPORT_SUBFOLDER}")
    if not ASSETS.save_loaded_asset(clip, False):
        u.log_warning(f"Could not cache Kimodo take {clip.get_path_name()}; it will be re-imported next time.")
        return clip, ""
    return clip, clip.get_path_name()


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


class KimodoWorker:
    """메모리에 Kimodo 모델을 띄워 둔 상주 프로세스(kimodo_worker.py).

    처음 생성할 때 한 번 모델을 불러 두고(약 10초), 이후 생성은 프로세스 시작·로딩 없이 바로 한다.
    WORKER_IDLE_SECONDS 동안 쓰지 않거나, 패널을 닫거나, 에디터를 끄면 내린다."""

    def __init__(self, key, python, env, model, log_path):
        self.key = key
        self.log_path = log_path
        self.ready = False
        # 취소했지만 워커가 아직 처리 중일 수 있는 요청의 결과 파일(진행 표시용).
        self.abandoned = None
        self.last_used = time.monotonic()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as log:
            # stdin으로 요청을 보낸다. 에디터가 비정상 종료해도 파이프가 닫히면 워커가 스스로 끝난다.
            self.process = subprocess.Popen(
                [str(python), str(SCRIPTS / "kimodo_worker.py"), "--model", model],
                cwd=log_path.parent, env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", creationflags=NO_WINDOW,
            )

    @property
    def pid(self):
        return self.process.pid

    def alive(self):
        return self.process.poll() is None

    def loaded(self):
        # 한 번 READY를 보면 기억한다(로그가 길어져도 매 poll마다 다시 읽지 않도록).
        if not self.ready:
            self.ready = "WORKER READY" in read_tail(self.log_path, 65536)
        return self.ready

    def submit(self, request):
        self.last_used = time.monotonic()
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def stop(self):
        if not self.alive():
            return
        try:
            self.process.stdin.close()  # 워커가 요청 루프를 빠져나와 정상 종료한다
            self.process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=10)


class WorkerTask:
    """워커에 보낸 생성 요청 하나. Panel.process 자리에 들어가 subprocess.Popen처럼 poll()/pid를 제공한다."""

    def __init__(self, worker, done_path):
        self.worker = worker
        self.done_path = done_path
        self.error = ""

    @property
    def pid(self):
        return self.worker.pid

    def wait(self, timeout=None):
        # Cancel은 워커 프로세스째 끝내므로 그 종료를 기다린다.
        return self.worker.process.wait(timeout=timeout)

    def poll(self):
        if self.done_path.is_file():
            try:
                result = json.loads(self.done_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None  # 아직 쓰는 중
            self.worker.last_used = time.monotonic()
            self.error = result.get("error", "")
            return 0 if result.get("ok") else 1
        if not self.worker.alive():
            self.error = f"the Kimodo worker stopped (see {self.worker.log_path})"
            return self.worker.process.returncode or 1
        return None


def prune_jobs(jobs, keep=()):
    if not jobs.is_dir():
        return

    # 새 작업 폴더를 만들기 전에 최근 작업 로그만 남긴다. 테이크 목록이 참조하는 폴더(원본 glTF)는 남긴다.
    folders = sorted((p for p in jobs.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders[KEPT_JOBS - 1:]:
        if str(folder).lower() not in keep:
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


# 진행 표시 단계 이름. 무엇 때문에 기다리는지(처음 한 번인지, 새 프롬프트라서인지) 알 수 있게 쓴다.
STAGE_STARTING_KIMODO = "Starting Kimodo (first take only)"
STAGE_LOADING_MODEL = "Loading Kimodo model (first take only)"
STAGE_LOADING_ENCODER = "Loading text encoder (new prompt)"
STAGE_ENCODING = "Encoding new prompt (about 30-40 s, cached after)"
STAGE_PREPARING = "Preparing generation"
STAGE_GENERATING = "Generating motion"


def kimodo_progress(path):
    """요청 하나의 Kimodo 로그(Kimodo.log)에서 (세부 단계 이름, 진행률 0~1 또는 -1)을 읽는다.

    로그 순서: (CLI) 'Using device' → 'Loaded model' / (상주 워커) 'Loaded model: … (resident worker)'
    → 새 프롬프트면 'Text encoder: …' → 'Text encoder ready' → 'Encoded prompt', 캐시면 'Text encoder: cached embedding'
    → 확산 샘플링 tqdm(N/100)."""
    try:
        with open(path, "rb") as log:
            text = log.read().decode("utf-8", errors="replace")
    except OSError:
        return STAGE_LOADING_MODEL, -1.0
    steps = TQDM_STEPS.findall(text[-8192:])
    if "Encoded prompt" in text or "cached embedding" in text:
        if steps:
            done, total = (int(v) for v in steps[-1])
            return STAGE_GENERATING, (done / total if total else -1.0)
        return STAGE_GENERATING, 0.0
    if "Text encoder ready" in text:
        return STAGE_ENCODING, -1.0
    if "Text encoder:" in text:
        return STAGE_LOADING_ENCODER, -1.0
    # 모델은 이미 올라와 있고(상주 워커 또는 CLI가 로딩을 마침) 인코더를 부르기 직전이다.
    if "Loaded model" in text:
        return STAGE_PREPARING, -1.0
    return STAGE_LOADING_MODEL, -1.0


def worker_startup_stage(path):
    """상주 워커가 처음 뜰 때의 단계: PyTorch import 중이면 시작, 'Using device' 뒤에는 모델 로딩."""
    return STAGE_LOADING_MODEL if "Using device" in read_tail(path, 65536) else STAGE_STARTING_KIMODO


NAME_STOP_WORDS = {
    "a", "an", "the", "person", "someone", "and", "then", "with", "while", "of", "to", "in", "on", "at",
    "his", "her", "their", "both", "one", "two", "it", "is", "slowly", "quickly",
}


def prompt_core(text):
    """프롬프트의 핵심 단어 3개를 이어 붙인 이름 조각(예: WaveRightHand)."""
    words = [w for w in re.findall(r"[A-Za-z]+", text or "") if w.lower() not in NAME_STOP_WORDS]
    return "".join(w.capitalize() for w in words[:3]) or "Motion"


def preview_name(number, text, seed, key):
    """테이크 프리뷰 클립 이름: T<번호>_<핵심 단어>_s<시드>_<고유 4자리>. 목록·콘텐츠 브라우저에서 바로 구분된다.
    번호는 세션마다 다시 시작할 수 있어 고유 4자리로 겹침을 막는다."""
    return f"T{int(number or 0):02d}_{prompt_core(text)}_s{seed}_{key[:4]}"


def suggest_name(take, folder):
    """AS_Kimodo_<핵심 단어 3개>_s<시드>. 같은 이름이 있으면 _2, _3을 붙인다."""
    core = prompt_core(take.get("english") or take.get("prompt"))
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
        self.message = IDLE_MESSAGE
        self.restored = False
        # 다시 가져와야 하는 직전 미리보기(glTF). 에디터 시작을 막지 않도록 버튼을 눌렀을 때만 복원한다.
        self.restorable = None
        # 생성 결과(테이크) 목록. 새로 생성해도 이전 결과를 지우지 않고 쌓아 두고, 고른 테이크로 수정·저장한다.
        self.takes = []
        self.selected = None
        self.take_counter = 0
        self.work_dir = ""
        self.persisted = ""
        self.use_counter = 0
        self.restore_selected_number = None
        # 메모리에 Kimodo를 띄워 둔 상주 워커(KimodoWorker). 처음 생성할 때 띄운다.
        self.worker = None

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

    def make_take(self, clip, directory, number=None, **info):
        if number is None:
            self.take_counter += 1
            number = self.take_counter
        take = {
            "id": uuid4().hex[:12], "number": number, "clip": clip, "dir": directory,
            "prompt": "", "english": "", "interpretation": "", "motion": "", "seed": None,
            "duration": clip.get_play_length() if clip else 0.0, "starred": False, "saved": "", "target": "",
            "source": None, "cached": "", "used": 0,
        }
        take.update(info)
        return take

    def touch(self, take):
        self.use_counter += 1
        take["used"] = self.use_counter

    def add_take(self, clip, directory, **info):
        take = self.make_take(clip, directory, **info)
        self.takes.append(take)
        self.selected = take["id"]
        self.touch(take)
        self.apply_selected()
        self.prune_takes()
        self.unload_extra()
        return take

    @staticmethod
    def reloadable(take):
        if take.get("cached") and ASSETS.does_asset_exist(take["cached"]):
            return True
        if take["saved"] and ASSETS.does_asset_exist(take["saved"]):
            return True
        source = take.get("source") or {}
        return bool(source.get("glb")) and Path(source["glb"]).is_file()

    def unload_take(self, take, delete=False):
        """메모리에서 내린다. 캐시한 테이크는 파일을 남겨 두고, delete면(목록에서 뺄 때) 캐시도 지운다."""
        if take["dir"] and (delete or not take.get("cached")):
            # 임시 에셋은 C++ 미리보기가 놓은 뒤 다음 요청에서 지운다. 저장한 에셋(dir 없음)은 남긴다.
            self.stale_dirs.append(take["dir"])
            if delete:
                take["cached"] = ""
        take["clip"] = None
        take["dir"] = None

    def unload_extra(self):
        """선택한 테이크를 포함해 최근에 본 MAX_LOADED개만 메모리에 둔다."""
        loaded = sorted((t for t in self.takes if t["clip"] is not None and t["id"] != self.selected
                         and self.reloadable(t)), key=lambda t: t["used"])
        keep = MAX_LOADED - (1 if self.selected_take() and self.selected_take()["clip"] is not None else 0)
        for take in loaded[:max(0, len(loaded) - keep)]:
            self.unload_take(take)

    def load_take(self, take, config):
        """내려 둔 테이크를 다시 불러온다. 캐시한 결과 → 저장한 에셋 → 원본 glTF 리타게팅 순서로 시도한다."""
        if take["clip"] is not None:
            return
        cached = take.get("cached") or ""
        if cached and ASSETS.does_asset_exist(cached):
            clip = ASSETS.load_asset(cached)
            if isinstance(clip, u.AnimSequence):
                take["clip"], take["dir"] = clip, cached
                take["duration"] = clip.get_play_length()
                return
        saved = take["saved"]
        if saved and ASSETS.does_asset_exist(saved):
            clip = ASSETS.load_asset(saved)
            if isinstance(clip, u.AnimSequence):
                take["clip"], take["dir"] = clip, None
                take["duration"] = clip.get_play_length()
                return

        source = take.get("source") or {}
        glb = Path(source.get("glb") or "")
        if not glb.is_file():
            raise FileNotFoundError(f"Take #{take['number']} can no longer be loaded: {glb} was deleted.")
        target, rig, retargeter, _ = resolve_target(source)
        output_folder = source.get("output_folder") or config.get("output_folder") or "/Game/Kimodo"
        staging = take_staging(config, output_folder)
        try:
            clip, take["cached"] = build_take_clip(glb, staging, target, rig, source.get("align_bones"), retargeter,
                                                   config.get("take_root") or "", bool(source.get("root_motion")),
                                                   (take["number"], take.get("english") or take.get("prompt"),
                                                    take.get("seed")))
        except Exception:
            self.delete_staging(staging)
            raise
        # 임시 에셋의 정리 대상: Preview 폴더의 클립 하나, 또는 (테이크 루트가 없을 때) 임시 폴더.
        take["clip"], take["dir"] = clip, cleanup_path(clip, staging)
        take["duration"] = clip.get_play_length()
        take["target"] = target.get_name()

    def prune_takes(self):
        # 별표 테이크는 세지 않는다: 별표 없는 테이크가 MAX_TAKES개를 넘을 때만 그중 가장 오래된 것을 뺀다.
        while sum(1 for t in self.takes if not t["starred"]) > MAX_TAKES:
            victim = next((t for t in self.takes if not t["starred"] and t["id"] != self.selected), None)
            if victim is None:
                return
            self.remove_take(victim["id"])

    def remove_take(self, take_id):
        take = self.find_take(take_id)
        if take is None:
            return
        self.takes.remove(take)
        if take["clip"] is None and take.get("cached"):
            # 메모리에 없는 캐시 테이크도 파일을 지운다.
            take["dir"] = take["cached"]
        self.unload_take(take, delete=True)
        if self.selected == take_id:
            self.selected = None
            # 다음으로 최근 테이크를 고르되, 메모리에 있는 것을 우선한다(다시 가져오지 않도록).
            loaded = [t for t in self.takes if t["clip"] is not None]
            fallback = max(loaded, key=lambda t: t["used"]) if loaded else None
            self.selected = fallback["id"] if fallback else None
            self.apply_selected()
        # 지운 테이크의 안내(Take #6 · …)가 상태 줄에 남지 않도록, 지금 고른 테이크 기준으로 다시 쓴다.
        current = self.selected_take()
        if current is not None:
            self.message = f"Take #{current['number']} · {current['duration']:.2f}s · seed {current['seed']}"
        elif not self.takes:
            self.message = IDLE_MESSAGE
        else:
            self.message = f"Removed take #{take['number']}"

    def select_take(self, take_id, config):
        take = self.find_take(take_id)
        if self.process is not None or take is None:
            return
        self.load_take(take, config)
        self.selected = take_id
        self.touch(take)
        self.apply_selected()
        self.unload_extra()
        self.message = f"Take #{take['number']} · {take['duration']:.2f}s · seed {take['seed']}"

    def toggle_star(self, take_id):
        take = self.find_take(take_id)
        if take is not None:
            take["starred"] = not take["starred"]

    # -- session persistence ----------------------------------------------------
    # 에디터를 다시 열어도 테이크 목록을 되살릴 수 있도록, 테이크마다 원본 glTF와 대상 정보를 남긴다.
    # 복원할 때는 목록만 되살리고, 마지막으로 보던 테이크 하나만 가져온다.
    SESSION_FILE = "session_takes.json"
    LEGACY_FILE = "last_preview.json"

    @staticmethod
    def take_entry(take):
        return {
            "number": take["number"], "prompt": take["prompt"], "english": take["english"],
            "interpretation": take["interpretation"], "motion": take["motion"], "seed": take["seed"],
            "duration": take["duration"], "starred": take["starred"], "saved": take["saved"],
            "target": take["target"], "source": take.get("source"), "cached": take.get("cached", ""),
        }

    def persist_session(self):
        """테이크 목록이 바뀌었을 때만 파일에 쓴다. 아직 복원하지 않은 이전 세션 항목도 함께 남긴다."""
        if not self.work_dir:
            return
        entries = list(self.restorable or []) + [self.take_entry(t) for t in self.takes]
        selected = self.selected_take()
        data = {"selected_number": selected["number"] if selected else None, "takes": entries}
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if text == self.persisted:
            return
        try:
            path = Path(self.work_dir) / self.SESSION_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.persisted = text
        except OSError as error:
            u.log_warning(f"Could not save the Kimodo take list: {error}")

    def referenced_job_folders(self):
        """테이크가 원본 glTF를 참조하는 작업 폴더. 오래된 작업 폴더를 지울 때 남긴다."""
        folders = set()
        for entry in list(self.restorable or []) + [self.take_entry(t) for t in self.takes]:
            glb = (entry.get("source") or {}).get("glb")
            if glb:
                folders.add(str(Path(glb).parent).lower())
        return folders

    def check_last_preview(self, config):
        """첫 poll에서 호출한다. 에디터 시작을 막지 않도록 복원은 버튼을 눌렀을 때만 한다."""
        if config.get("take_root"):
            u.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous([config["take_root"]], True)
        work_dir = config.get("work_dir")
        if not work_dir:
            return
        session = Path(work_dir) / self.SESSION_FILE
        legacy = Path(work_dir) / self.LEGACY_FILE
        entries = []
        try:
            if session.is_file():
                data = json.loads(session.read_text(encoding="utf-8"))
                entries = data.get("takes", [])
                self.restore_selected_number = data.get("selected_number")
            elif legacy.is_file():
                # 이전 버전은 마지막 미리보기 하나만 남겼다.
                data = json.loads(legacy.read_text(encoding="utf-8"))
                entries = [{
                    "number": 1, "prompt": data.get("prompt", ""), "english": data.get("english_prompt", ""),
                    "interpretation": data.get("interpretation", ""), "motion": data.get("motion", ""),
                    "seed": data.get("seed"), "starred": False, "saved": data.get("saved_clip", ""),
                    "source": None if data.get("saved_clip") else {
                        key: data.get(key) for key in
                        ("glb", "target_mesh", "target_rig", "retargeter", "align_bones", "output_folder",
                         "root_motion")
                    },
                }]
        except (OSError, ValueError):
            return

        # 캐시 폴더는 프로젝트 단위(Saved/Kimodo/TakeAssets)라 다른 작업 폴더의 세션도 쓸 수 있다.
        # 이 세션 목록에 없다고 지우면 남의 캐시를 지우게 되므로, 캐시는 테이크를 ✕로 뺄 때만 지운다.
        self.migrate_take_folders(config, entries)
        usable = [e for e in entries if self.entry_restorable(e)]
        # 복원 전에 새로 생성해도 번호가 이전 세션 테이크와 겹치지 않도록 이어서 매긴다.
        self.take_counter = max([self.take_counter] + [int(e.get("number") or 0) for e in entries])
        if usable:
            self.restorable = usable
            count = len(usable)
            self.message = (f"{count} take{'s' if count != 1 else ''} from the previous session can be restored. "
                            "Click Restore last session.")

    @staticmethod
    def migrate_take_folders(config, entries):
        """이전 버전의 캐시 이름을 지금 형식(/KimodoTakes/Preview/T<번호>_<단어>_s<시드>_<id4>)으로 바꾼다.
        - /KimodoTakes/<id>/KimodoPreview (테이크마다 폴더): 옮기고 빈 폴더를 지운다.
        - /KimodoTakes/Preview/Take_<id> (id만 있는 이름): 알아보기 쉬운 이름으로 바꾼다."""
        root = (config.get("take_root") or "").rstrip("/")
        if not root:
            return
        preview = f"{root}/{PREVIEW_SUBFOLDER}"
        for entry in entries:
            cached = entry.get("cached") or ""
            if not cached.startswith(root + "/") or not ASSETS.does_asset_exist(cached):
                continue
            package = cached.split(".", 1)[0]
            if package.startswith(preview + "/"):
                asset = package.rsplit("/", 1)[1]
                if not asset.startswith("Take_"):
                    continue
                key, folder = asset[len("Take_"):], None
            else:
                key, folder = cached[len(root) + 1:].split("/", 1)[0], package.rsplit("/", 1)[0]
            name = preview_name(entry.get("number"), entry.get("english") or entry.get("prompt"), entry.get("seed"), key)
            moved = f"{preview}/{name}"
            if ASSETS.does_asset_exist(moved) or (ASSETS.rename_asset(package, moved) and ASSETS.save_asset(moved, False)):
                entry["cached"] = f"{moved}.{name}"
                if folder:
                    # 테이크 폴더에는 그 클립 하나만 있었다(남은 리디렉터 포함해 폴더째 지운다).
                    ASSETS.delete_directory(folder)
            else:
                u.log_warning(f"Could not rename Kimodo take cache {cached} to {moved}")

    @staticmethod
    def entry_restorable(entry):
        if entry.get("cached") and ASSETS.does_asset_exist(entry["cached"]):
            return True
        if entry.get("saved") and ASSETS.does_asset_exist(entry["saved"]):
            return True
        source = entry.get("source") or {}
        return bool(source.get("glb")) and Path(source["glb"]).is_file()

    def restore_last_preview(self, config):
        """이전 세션 테이크 목록을 되살리고 모두 불러온다. 이후 클릭은 불러온 클립을 바꾸기만 한다."""
        entries = self.restorable
        if self.process is not None or not entries:
            return
        self.restorable = None

        restored = []
        for entry in entries:
            restored.append(self.make_take(
                None, None, entry.get("number"), prompt=entry.get("prompt", ""), english=entry.get("english", ""),
                interpretation=entry.get("interpretation", ""), motion=entry.get("motion", ""),
                seed=entry.get("seed"), duration=entry.get("duration") or 0.0, starred=bool(entry.get("starred")),
                saved=entry.get("saved", ""), target=entry.get("target", ""), source=entry.get("source"),
                cached=entry.get("cached", "")))
        # 이전 세션 테이크를 앞(오래된 쪽)에 두고, 그사이 새로 만든 테이크를 뒤에 둔다.
        current = self.takes
        # 이전 버전에서 복원 전에 만든 테이크는 번호가 겹칠 수 있다(저장된 목록에 이미 겹쳐 있을 수도 있음).
        # 앞(오래된 쪽)의 번호를 유지하고, 겹치는 뒤쪽 테이크를 새 번호로 옮긴다.
        self.takes = restored + current
        self.take_counter = max([self.take_counter] + [t["number"] or 0 for t in self.takes])
        used = set()
        for take in self.takes:
            if not take["number"] or take["number"] in used:
                self.take_counter += 1
                take["number"] = self.take_counter
            used.add(take["number"])
        # 번호를 옮긴 테이크가 목록 중간에 남지 않도록 번호 순으로 정렬한다(목록은 최신 번호가 위로 보인다).
        self.takes.sort(key=lambda t: t["number"])

        self.prune_takes()
        # 클릭할 때 끊기지 않도록 Restore에서 전부 불러온다(별표 없는 MAX_TAKES개 + 별표 테이크 모두).
        # 캐시한 테이크는 파일을 여는 것뿐이고, 캐시가 없는 예전 테이크만 여기서 한 번 리타게팅해 캐시로 만든다.
        failed = 0
        for take in [t for t in restored if t in self.takes]:
            try:
                self.load_take(take, config)
            except Exception as error:
                failed += 1
                u.log_warning(f"Could not restore Kimodo take #{take['number']}: {error}")
                # 불러올 수 없는 테이크는 목록에서 뺀다(나중에 클릭해도 다시 가져올 수 없다).
                self.takes.remove(take)
        restored = [t for t in restored if t in self.takes]
        if not current and restored:
            wanted = next((t for t in restored if t["number"] == self.restore_selected_number), restored[-1])
            self.selected = wanted["id"]
            self.touch(wanted)
            self.apply_selected()
        self.message = f"Restored {len(restored)} take{'s' if len(restored) != 1 else ''} from the previous session"
        if failed:
            self.message += f" ({failed} could not be restored; see the Output Log)"

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

        # Generate ×N: 같은 프롬프트로 N개를 차례로 만든다. 해석은 한 번만 하고, 시드는 테이크마다 다르게 쓴다
        # (시드를 넣었으면 seed, seed+1, …; 비우면 무작위). 수정은 시드를 유지하므로 항상 1개.
        try:
            count = int(config.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = 1 if revision else max(1, min(MAX_BATCH, count))

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
        prune_jobs(jobs, self.referenced_job_folders())
        folder = jobs / job_id
        folder.mkdir(parents=True)
        self.job = {
            "folder": folder, "work_dir": work_dir, "stage": "Interpretation", "target": target, "rig": rig,
            "retargeter": retargeter, "rig_label": rig_label,
            "target_mesh_path": config.get("target_mesh") or "",
            "target_rig_path": config.get("target_rig") or "",
            "retargeter_path": config.get("retargeter") or "",
            "output_folder": config["output_folder"],
            "root_motion": bool(config.get("root_motion")),
            "align_bones": list(config.get("align_bones", [])), "model": config.get("model") or "Kimodo-SOMA-RP-v1.2",
            "staging": take_staging(config, config["output_folder"]), "take_root": config.get("take_root") or "",
            "started": time.monotonic(),
            "duration": duration, "seed": seed, "request": prompt,
            # 수정은 고른 테이크를 기준으로 하므로, 결과 테이크의 원래 요청을 "기준 + 수정"으로 남긴다.
            "base_prompt": (self.selected_take() or {}).get("prompt", "") if revision else "",
            "batch": {"index": 1, "total": count, "fixed_seed": bool(seed_text), "base_seed": seed},
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
        # 설치가 venv를 바꾸므로 그 Python으로 띄운 워커를 먼저 내린다.
        self.stop_worker()

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
        # 상주 워커에 요청한다(kimodo_generate.py CLI와 같은 설정: 샘플 1개, 후처리 끔, 표준 T포즈 BVH).
        worker = self.ensure_worker()
        done = folder / "Kimodo.done.json"
        worker.submit({"prompt": prompt, "duration": duration, "seed": self.job["seed"], "output": str(folder / "SOMA"),
                       "log": str(folder / "Kimodo.log"), "done": str(done)})
        self.process = WorkerTask(worker, done)
        self.message = f"Generating {duration:g}s in Kimodo…"

    # -- resident Kimodo worker ---------------------------------------------------
    def ensure_worker(self):
        """작업 설정(Python, 모델, 인코더, 캐시)에 맞는 워커를 돌려준다. 없거나 설정이 바뀌었으면 새로 띄운다."""
        env = self.job["env"]
        key = (str(self.job["python"]), self.job["model"], env.get("KIMODO_TEXT_ENCODER"), env.get("HF_HUB_CACHE"))
        if self.worker is not None and (self.worker.key != key or not self.worker.alive()):
            self.stop_worker()
        if self.worker is None:
            self.worker = KimodoWorker(key, self.job["python"], env, self.job["model"],
                                       Path(self.job["work_dir"]) / "KimodoWorker.log")
        return self.worker

    def stop_worker(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def stop_idle_worker(self):
        if (self.worker is not None and self.process is None
                and time.monotonic() - self.worker.last_used > WORKER_IDLE_SECONDS):
            self.stop_worker()

    def close(self):
        """패널 탭을 닫을 때: 진행 중인 작업을 멈추고 워커를 내려 메모리를 돌려준다."""
        self.cancel()
        self.stop_worker()

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
            if self.batch_label():
                self.message = f"{self.batch_label()} · {self.message}"
            return

        finished = self.process
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
            # 워커 요청이면 실패 원인(예외 메시지)을 함께 보여 준다.
            reason = getattr(finished, "error", "") or f"exited with code {status}"
            raise RuntimeError(f"{stage} failed: {reason}. See {folder / (stage + '.log')}")
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
            clip, cached = build_take_clip(self.job["folder"] / "SOMA_Motion.glb", staging, target, self.job["rig"],
                                           self.job["align_bones"], self.job["retargeter"], self.job["take_root"],
                                           self.job["root_motion"],
                                           # add_take가 매길 번호(take_counter + 1)를 미리 이름에 쓴다.
                                           (self.take_counter + 1, self.english_prompt or self.job["request"],
                                            self.job["seed"]))
        except Exception:
            self.delete_staging(staging)
            raise

        request = self.job["request"]
        if self.job["base_prompt"]:
            request = f"{self.job['base_prompt']} → {request}"
        self.add_take(clip, cleanup_path(clip, staging), prompt=request, english=self.english_prompt,
                      interpretation=self.interpretation, motion=self.job["motion"],
                      seed=self.job["seed"], target=target.get_name(), cached=cached, source={
                          "glb": str(self.job["folder"] / "SOMA_Motion.glb"),
                          "target_mesh": self.job["target_mesh_path"] or target.get_path_name(),
                          "target_rig": self.job["target_rig_path"],
                          "retargeter": self.job["retargeter_path"],
                          "align_bones": self.job["align_bones"],
                          "output_folder": self.job["output_folder"],
                          "root_motion": self.job["root_motion"],
                      })
        label = f"{target.get_name()} · {self.job['rig_label']}"
        if self.job["root_motion"] and not self.job["retargeter"]:
            label += " · root motion"
        if self.continue_batch():
            return
        batch = self.job.get("batch") or {}
        self.job = None
        # dispatch()가 "Preview ready"로 시작하는지로 성공 알림을 판단하므로 접두어를 유지한다.
        count = f"{batch['total']} takes · " if batch.get("total", 1) > 1 else ""
        self.message = f"Preview ready · {count}{clip.get_play_length():.2f}s · seed {self.seed} · {label}"

    def batch_label(self):
        """Generate ×N 진행 중이면 "Take 2/5", 아니면 빈 문자열."""
        batch = (self.job or {}).get("batch") or {}
        return f"Take {batch['index']}/{batch['total']}" if batch.get("total", 1) > 1 else ""

    def continue_batch(self):
        """Generate ×N의 다음 테이크를 시작한다. 남은 것이 없으면 False.
        해석 결과(interpretation.json)를 새 작업 폴더로 복사해 해석 단계를 건너뛰고 바로 Kimodo를 실행한다."""
        batch = self.job.get("batch") or {}
        if batch.get("index", 1) >= batch.get("total", 1):
            return False
        previous = self.job
        folder = previous["folder"].parent / uuid4().hex
        folder.mkdir(parents=True)
        shutil.copyfile(previous["folder"] / "interpretation.json", folder / "interpretation.json")
        batch = dict(batch, index=batch["index"] + 1)
        seed = ((batch["base_seed"] + batch["index"] - 1) % 2**31 if batch["fixed_seed"]
                else random.randrange(2**31))
        # 작업 dict에도 take_root가 있어 take_staging에 그대로 넘길 수 있다.
        staging = take_staging(previous, previous["output_folder"])
        self.job = dict(previous, folder=folder, seed=seed, staging=staging, batch=batch,
                        stage="Interpretation", started=time.monotonic())
        self.generate()
        return True

    def cancel(self):
        if self.process is None:
            return

        process = self.process
        if isinstance(process, WorkerTask):
            # 상주 워커는 내리지 않는다(다시 불러오는 데 약 10초). 이 요청은 워커가 마저 끝내고(보통 몇 초),
            # 결과는 버린다. 다음 요청은 그 뒤에 처리된다. 패널을 닫을 때는 close()가 워커를 따로 내린다.
            # 다음 요청의 진행 표시가 "취소한 테이크 마무리 중"을 보여 줄 수 있도록 그 결과 파일을 기억한다.
            process.worker.abandoned = process.done_path
        elif os.name == "nt":
            # 해석 단계의 CLI처럼 자식 프로세스가 남지 않도록 프로세스 트리 전체를 종료한다.
            # taskkill /F는 종료를 마친 뒤 돌아오므로 아래 대기는 짧게 둔다(에디터 메인 스레드).
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW, timeout=10,
            )
        else:
            process.kill()
        if not isinstance(process, WorkerTask):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                u.log_warning(f"Kimodo process {process.pid} did not exit after cancellation.")

        # 선택한 테이크의 해석과 프롬프트로 되돌린다.
        stage = self.job["stage"]
        batch = self.batch_label()
        self.process = None
        self.job = None
        self.apply_selected()
        self.message = f"Cancelled during {stage}. The previous preview is unchanged."
        if batch:
            self.message = f"Cancelled at {batch}. Takes already made are kept."

    @staticmethod
    def delete_staging(path):
        if not delete_asset_path(path):
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
            if not (folder / "Kimodo.log").is_file() and self.worker is not None:
                if not self.worker.loaded():
                    # 워커가 아직 뜨는 중(처음 생성할 때만). 워커 로그의 마지막 줄을 보여 준다.
                    return worker_startup_stage(self.worker.log_path), -1.0, last_log_line(self.worker.log_path)
                abandoned = getattr(self.worker, "abandoned", None)
                if abandoned is not None and not abandoned.is_file():
                    # 취소한 직전 요청을 워커가 마저 끝내는 중. 끝나면 이 요청을 바로 시작한다.
                    return "Finishing the cancelled take", -1.0, ""
                # 워커가 요청을 막 받아 로그 파일을 열기 직전(보통 1초 미만).
                return STAGE_PREPARING, -1.0, ""
            label, fraction = kimodo_progress(folder / "Kimodo.log")
            return label, fraction, detail
        if stage == "Conversion":
            return "Converting to glTF", -1.0, detail
        return stage, -1.0, detail

    def save(self, name, config):
        take = self.selected_take()
        if take is None or take["clip"] is None or self.process is not None:
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
        self.message = f"Saved {destination}"


panel = Panel()


def shutdown():
    # 에디터를 닫을 때 진행 중인 해석·생성·변환 프로세스를 함께 종료한다.
    try:
        panel.close()
    except Exception:
        u.log_warning(traceback.format_exc())


u.register_python_shutdown_callback(shutdown)


def close_panel():
    """C++ 패널 탭이 닫힐 때 호출한다. 진행 중인 생성을 멈추고 상주 Kimodo 워커를 내려 메모리를 돌려준다.
    테이크 목록은 남아 있어 패널을 다시 열면 그대로 보인다."""
    try:
        panel.close()
        panel.persist_session()
    except Exception:
        u.log_warning(traceback.format_exc())


def dispatch(encoded_payload):
    """C++ 패널의 요청(JSON)을 처리하고 상태 한 줄을 Base64로 돌려준다.

    print로 넘기면 0.5초마다 Output Log에 한 줄씩 쌓이므로, C++이 평가 결과(CommandResult)로 받는다."""
    payload = json.loads(b64decode(encoded_payload).decode("utf-8"))
    action = payload.get("action", "poll")
    value = payload.get("value", "")
    panel.work_dir = payload.get("work_dir") or panel.work_dir
    was_working = panel.process is not None and panel.job is not None and panel.job.get("stage") != "Setup"
    event = ""
    try:
        panel.delete_stale_previews()
        panel.stop_idle_worker()
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
            panel.select_take(value, payload)
        elif action == "star":
            panel.toggle_star(value)
        elif action == "discard":
            if panel.process is None:
                panel.remove_take(value)
    except Exception as error:
        u.log_error(traceback.format_exc())
        batch = panel.batch_label() if panel.process is None else ""
        panel.message = f"{batch} failed: {error}" if batch else str(error)
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

    panel.persist_session()
    stage, fraction, detail = panel.progress()
    take = panel.selected_take()
    folder = save_folder_of(payload)
    state = {
        "busy": panel.process is not None,
        "has_preview": take is not None and take["clip"] is not None,
        "message": clean_text(panel.message),
        "detail": clean_text(detail),
        "stage": stage,
        "progress": fraction,
        "event": event,
        "path": take["clip"].get_path_name() if take and take["clip"] is not None else "",
        "can_revise": bool(panel.previous_motion),
        "interpretation": clean_text(panel.interpretation),
        "english": clean_text(panel.english_prompt.replace("\n", " ")),
        "setup": setup,
        "restorable": bool(panel.restorable) and panel.process is None,
        "restore_count": len(panel.restorable or []),
        "seed": "" if panel.seed is None else str(panel.seed),
        "selected": panel.selected or "",
        "suggested_name": suggest_name(take, folder) if take else "",
        "takes": [{
            "id": t["id"], "number": t["number"], "prompt": clean_text(t["prompt"] or t["english"]),
            "seed": "" if t["seed"] is None else str(t["seed"]), "duration": round(t["duration"], 2),
            "starred": t["starred"], "saved": t["saved"], "target": t["target"], "loaded": t["clip"] is not None,
        } for t in reversed(panel.takes)],  # 최신 테이크가 위로
    }
    # Base64(UTF-8)로 감싸 EvaluateStatement의 repr 결과에서도 한글이 그대로 전달되게 한다.
    return b64encode(json.dumps(state, ensure_ascii=False).encode("utf-8")).decode("ascii")
