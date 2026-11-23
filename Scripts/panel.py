from base64 import b64decode
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
AGENT_NAMES = ("codex", "claude", "gemini")
ASSETS = u.get_editor_subsystem(u.EditorAssetSubsystem)
retarget = runpy.run_path(str(SCRIPTS / "retarget.py"))["retarget"]
direct_description = runpy.run_path(str(SCRIPTS / "interpret.py"))["direct_description"]
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")


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


def kimodo_installed(python):
    if python.parent.name.lower() not in ("scripts", "bin"):
        # venv가 아닌 Python을 직접 지정하면 설치 여부를 확인하지 않는다.
        return True
    root = python.parent.parent
    packages = list(root.glob("Lib/site-packages/kimodo")) + list(root.glob("lib/python*/site-packages/kimodo"))
    return bool(packages)


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

    def restore_last_preview(self, config):
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
                self.saved_clip = clip
                self.previous_motion = data.get("motion", "")
                self.interpretation = data.get("interpretation", "")
                self.english_prompt = data.get("english_prompt", "")
                self.seed = data.get("seed")
                self.message = f"Restored saved clip · {saved_path}"
                return

        glb = Path(data.get("glb") or "")
        if not glb.is_file():
            return
        target, rig, retargeter, rig_label = resolve_target(data)
        output_folder = data.get("output_folder") or config.get("output_folder") or "/Game/Kimodo"
        staging = f"{output_folder}/_Preview_Restored_{uuid4().hex[:8]}"
        try:
            mesh, motion = import_gltf(glb, staging + "/Source")
            clip = retarget(ASSETS, mesh, motion, target, rig, staging,
                            list(data.get("align_bones") or []), retargeter)
            if clip.get_editor_property("skeleton") != target.get_editor_property("skeleton"):
                self.delete_staging(staging)
                return
            clip.set_preview_skeletal_mesh(target)
        except Exception:
            self.delete_staging(staging)
            raise

        self.preview = clip
        self.saved_clip = None
        self.preview_dir = staging
        self.previous_motion = data.get("motion", "")
        self.interpretation = data.get("interpretation", "")
        self.english_prompt = data.get("english_prompt", "")
        self.seed = data.get("seed")
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

        # 새 미리보기는 다른 변형을 뽑고, 수정은 직전 시드를 유지해 요청한 차이만 반영한다.
        seed = random.randrange(2**31)
        if revision and self.seed is not None:
            seed = self.seed

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
            "duration": duration, "seed": seed,
        }
        env = dict(
            os.environ, HF_HUB_CACHE=str(cache), HF_HUB_OFFLINE="1",
            TEXT_ENCODER_MODE="local", TEXT_ENCODER_DEVICE="cpu", PYTHONUNBUFFERED="1",
        )
        self.job.update(python=python, env=env)
        self.job["previous_interpretation"] = self.interpretation
        self.job["previous_english_prompt"] = self.english_prompt
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
            "previous_interpretation": self.interpretation, "previous_english_prompt": self.english_prompt,
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
            str(self.job["python"]), "-m", "kimodo.scripts.generate", "--model", self.job["model"],
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
            self.message = f"{stage} · {self.job['duration']:g}s clip · {elapsed}s elapsed…"
            return

        self.process = None
        if stage == "Setup":
            progress = self.setup_progress()
            self.job = None
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
        source_path = staging + "/Source"
        try:
            mesh, motion = import_gltf(self.job["folder"] / "SOMA_Motion.glb", source_path)
            clip = retarget(ASSETS, mesh, motion, target, self.job["rig"], staging,
                            self.job["align_bones"], self.job["retargeter"])
            if clip.get_editor_property("skeleton") != target.get_editor_property("skeleton"):
                raise RuntimeError(f"Retargeting did not produce an animation for {target.get_name()}.")
            clip.set_preview_skeletal_mesh(target)
        except Exception:
            self.delete_staging(staging)
            raise

        self.discard_preview()
        self.preview = clip
        self.saved_clip = None
        self.preview_dir = staging
        self.previous_motion = self.job["motion"]
        self.seed = self.job["seed"]
        self.save_last_preview_state(self.job["work_dir"], {
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
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            u.log_warning(f"Kimodo process {process.pid} did not exit after cancellation.")

        # 직전 성공 미리보기의 해석과 프롬프트로 되돌린다.
        stage = self.job["stage"]
        self.process = None
        self.interpretation = self.job["previous_interpretation"]
        self.english_prompt = self.job["previous_english_prompt"]
        self.job = None
        self.message = f"Cancelled during {stage}. The previous preview is unchanged."

    @staticmethod
    def delete_staging(path):
        if ASSETS.does_directory_exist(path):
            if not ASSETS.delete_directory(path):
                u.log_warning(f"Could not remove temporary Kimodo assets in {path}")

    def discard_preview(self):
        if self.preview is None:
            return

        # C++ 미리보기가 새 클립으로 바뀐 뒤에 지우도록 다음 요청까지 미룬다.
        self.preview = None
        self.stale_dirs.append(self.preview_dir)
        self.preview_dir = None

    def delete_stale_previews(self):
        for path in self.stale_dirs:
            self.delete_staging(path)
        self.stale_dirs.clear()

    def save(self, name, config):
        if self.preview is None or self.process is not None:
            return
        name = name.strip()
        if not re.fullmatch(r"\w+", name):
            raise ValueError("Use letters, numbers, or underscores in the AnimSequence name.")
        destination = f"{config['output_folder']}/{name}"
        if ASSETS.does_asset_exist(destination):
            raise ValueError(f"{destination} already exists. Enter another name.")
        saved = ASSETS.duplicate_asset(self.preview.get_path_name(), destination)
        if not saved:
            raise RuntimeError(f"Could not create {destination}.")
        if not ASSETS.save_loaded_asset(saved, False):
            ASSETS.delete_loaded_asset(saved)
            raise RuntimeError(f"Could not save {destination}.")
        self.discard_preview()
        self.saved_clip = saved
        if config.get("work_dir"):
            self.save_last_preview_state(config["work_dir"], {
                "saved_clip": destination,
                "motion": self.previous_motion,
                "interpretation": self.interpretation,
                "english_prompt": self.english_prompt,
                "seed": self.seed,
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
    """C++ 패널의 요청(JSON)을 처리하고 상태 한 줄을 출력한다."""
    payload = json.loads(b64decode(encoded_payload).decode("utf-8"))
    action = payload.get("action", "poll")
    value = payload.get("value", "")
    try:
        panel.delete_stale_previews()
        if action in ("generate", "revise"):
            panel.start(value, payload.get("duration", ""), action == "revise", payload)
        elif action == "poll":
            if not panel.restored and panel.process is None and panel.preview is None and panel.saved_clip is None:
                panel.restored = True
                try:
                    panel.restore_last_preview(payload)
                except Exception as restore_error:
                    u.log_warning(f"Could not restore previous Kimodo preview: {restore_error}")
            panel.poll()
        elif action == "save":
            panel.save(value, payload)
        elif action == "cancel":
            panel.cancel()
        elif action == "setup":
            panel.start_setup(payload, value)
    except Exception as error:
        u.log_error(traceback.format_exc())
        panel.message = str(error)
        if panel.process is None:
            if panel.job is not None:
                panel.interpretation = panel.job["previous_interpretation"]
                panel.english_prompt = panel.job["previous_english_prompt"]
            panel.job = None
    # 설치 상태: installing, ready, missing:<빠진 항목>
    if panel.job is not None and panel.job.get("stage") == "Setup":
        setup = "installing"
    else:
        state, missing = setup_state(payload)
        setup = state if state == "ready" else f"missing:{missing}"
    message = panel.message.replace("|", "/").replace("\n", " ")
    clip = panel.preview or panel.saved_clip
    path = clip.get_path_name() if clip else ""
    interpretation = panel.interpretation.replace("|", "/")
    english = panel.english_prompt.replace("|", "/").replace("\n", " ")
    print(f"KIMODO_PANEL|{int(panel.process is not None)}|{int(panel.preview is not None)}|"
          f"{message}|{path}|{int(bool(panel.previous_motion))}|{interpretation}|{english}|{setup.replace('|', '/')}")
