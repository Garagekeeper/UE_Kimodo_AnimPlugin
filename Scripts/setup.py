"""Kimodo 실행 환경을 설치한다. 에디터 밖의 Python(UE 내장 Python 3.11)으로 실행한다.

사용법: setup.py <venv python 경로> <모델 캐시 폴더> <Kimodo 모델 이름> <진행 상태 파일>
Hugging Face 토큰은 환경변수 HF_TOKEN으로만 받는다. 없으면 `hf auth login`으로 저장된 토큰을 쓴다.
이미 끝난 단계는 건너뛰므로 여러 번 실행해도 된다.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile


# 검증한 Kimodo 커밋과 PyTorch 빌드. 바꾸면 패널 동작을 다시 확인한다.
KIMODO_COMMIT = "e1c219c99b4b94ba48be1fb6a1cbc64ecee4c954"
KIMODO_ARCHIVE = f"https://github.com/nv-tlabs/kimodo/archive/{KIMODO_COMMIT}.zip"
TORCH_REQUIREMENT = "torch==2.11.0"
TORCH_INDEX_CUDA = "https://download.pytorch.org/whl/cu128"
TORCH_INDEX_CPU = "https://download.pytorch.org/whl/cpu"
# Wheels/ 폴더에 동봉한 사전 빌드 휠의 SHA256. 휠을 다시 빌드하면 여기도 갱신한다.
PREBUILT_WHEEL_SHA256 = {
    "kimodo-1.0.0-cp311-cp311-win_amd64.whl": "5929457e2e413c051a310cdd39aab6b74994a633073305f83bcc5c6db76daeee",
}
LLAMA = "meta-llama/Meta-Llama-3-8B-Instruct"
LLAMA_LICENSE_URL = "https://huggingface.co/" + LLAMA
TEXT_ENCODER_REPOS = [
    (LLAMA, ["original/*"]),
    ("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp", []),
    ("McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised", []),
]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 각 단계는 별도 프로세스에서 실행하므로 모델 다운로드는 venv의 huggingface_hub로 처리한다.
DOWNLOAD_SNIPPET = r"""
import json, os, sys
from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
repo, cache, ignore = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
try:
    snapshot_download(repo, cache_dir=cache, ignore_patterns=ignore or None, local_files_only=True)
    print("already downloaded", flush=True)
    sys.exit(0)
except Exception:
    pass
try:
    snapshot_download(repo, cache_dir=cache, ignore_patterns=ignore or None, token=os.environ.get("HF_TOKEN") or None)
except (GatedRepoError, RepositoryNotFoundError) as error:
    print(f"ACCESS_DENIED {type(error).__name__}", flush=True)
    sys.exit(3)
"""


class SetupError(RuntimeError):
    pass


class Progress:
    def __init__(self, path):
        self.path = Path(path)
        self.step = 0
        self.total = 0

    def write(self, message, state="installing"):
        # 패널이 읽는 한 줄 상태. 토큰 같은 비밀 값은 쓰지 않는다.
        payload = {"state": state, "step": self.step, "total": self.total, "message": message}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        print(f"[{self.step}/{self.total}] {message}", flush=True)

    def begin(self, message):
        self.step += 1
        self.write(message)


def run(argv, env=None):
    # 인라인 스크립트(-c)는 첫 줄만 로그에 남긴다.
    print("> " + " ".join(str(a).strip().splitlines()[0] if str(a).strip() else str(a) for a in argv), flush=True)
    result = subprocess.run([str(a) for a in argv], env=env, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return result.returncode


def venv_root(python):
    # venv/Scripts/python.exe 또는 venv/bin/python
    return Path(python).resolve().parent.parent


def can_import(python, module):
    code = f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('{module}') else 1)"
    return run([python, "-c", code]) == 0


def saved_token():
    """`hf auth login`이 저장한 토큰. 없으면 빈 문자열."""
    home = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    token_file = Path(os.environ.get("HF_TOKEN_PATH") or Path(home) / "token")
    try:
        return token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_llama_access(token):
    """큰 다운로드를 시작하기 전에 Llama 3 접근 권한을 확인한다."""
    request = urllib.request.Request(f"https://huggingface.co/{LLAMA}/resolve/main/config.json", method="HEAD")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        # 권한이 있으면 저장소 CDN으로 리디렉션되므로 3xx도 성공으로 본다.
        urllib.request.build_opener(NoRedirect).open(request, timeout=30)
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            return
        if error.code in (401, 403):
            if not token:
                raise SetupError("Enter a Hugging Face token. Llama 3 needs a token from an account that accepted "
                                 f"its license at {LLAMA_LICENSE_URL}") from None
            raise SetupError("This Hugging Face token cannot download Llama 3. Accept the license at "
                             f"{LLAMA_LICENSE_URL} with the token's account and wait for approval.") from None
        raise SetupError(f"Hugging Face returned HTTP {error.code} while checking Llama 3 access.") from None
    except urllib.error.URLError as error:
        raise SetupError(f"Could not reach Hugging Face ({error.reason}). Check the internet connection.") from None


def llama_downloaded(python, cache):
    if not can_import(python, "huggingface_hub"):
        return False
    code = ("import sys; from huggingface_hub import snapshot_download\n"
            f"snapshot_download({LLAMA!r}, cache_dir=sys.argv[1], ignore_patterns=['original/*'], local_files_only=True)")
    return run([python, "-c", code, cache]) == 0


def create_venv(python, progress):
    progress.begin("Creating the Python environment...")
    if Path(python).is_file():
        return
    root = venv_root(python) if Path(python).parent.name.lower() in ("scripts", "bin") else None
    if root is None:
        raise SetupError(f"{python} is not inside a virtual environment folder (.../Scripts/python.exe).")
    root.parent.mkdir(parents=True, exist_ok=True)
    if run([sys.executable, "-m", "venv", root]) or not Path(python).is_file():
        raise SetupError("Could not create the Python virtual environment.")


def torch_index():
    """KIMODO_TORCH_INDEX가 있으면 그 값, 없으면 NVIDIA 드라이버(nvidia-smi)가 있을 때만 CUDA 빌드를 쓴다."""
    override = os.environ.get("KIMODO_TORCH_INDEX", "").strip()
    if override:
        return override
    return TORCH_INDEX_CUDA if shutil.which("nvidia-smi") else TORCH_INDEX_CPU


def install_torch(python, progress):
    index = torch_index()
    cuda = index == TORCH_INDEX_CUDA
    progress.begin("Installing PyTorch (about 3 GB)..." if cuda else "Installing PyTorch (CPU build, no NVIDIA GPU found)...")
    if can_import(python, "torch"):
        return
    if run([python, "-m", "pip", "install", "--disable-pip-version-check", TORCH_REQUIREMENT,
            "--index-url", index]):
        raise SetupError("PyTorch installation failed. See Setup.log.")


def long_path(path):
    """Windows의 260자 경로 제한을 피한다."""
    path = Path(path).resolve()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        return "\\\\?\\" + str(path)
    return str(path)


def extract_source(archive, source):
    # 최상위 폴더(kimodo-<커밋>)를 벗기고 바로 풀며, 설치에 필요 없는 문서는 건너뛴다.
    for info in archive.infolist():
        relative = info.filename.split("/", 1)[1] if "/" in info.filename else ""
        if not relative or relative.startswith("docs/"):
            continue
        target = Path(long_path(source / relative))
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as data, open(target, "wb") as output:
            shutil.copyfileobj(data, output)


def find_vs_install_paths():
    """vswhere.exe로 Visual Studio 설치 경로를 찾는다(드라이브 무관)."""
    paths = []
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.check_output(
                [str(vswhere), "-all", "-prerelease", "-property", "installationPath"],
                text=True,
                stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
                timeout=15,
            )
            for line in out.splitlines():
                line = line.strip()
                if line:
                    paths.append(Path(line))
        except (OSError, subprocess.SubprocessError):
            pass
    for root in (Path(r"C:\Program Files\Microsoft Visual Studio"), Path(r"C:\Program Files (x86)\Microsoft Visual Studio")):
        if root.is_dir():
            paths.extend(sorted(root.glob("*/*"), reverse=True))
    return paths


def find_cmake_dir(python):
    if shutil.which("cmake"):
        return None
    venv_scripts = Path(python).resolve().parent
    candidates = [venv_scripts, Path(r"C:\Program Files\CMake\bin")]
    for vs_root in find_vs_install_paths():
        candidates.append(vs_root / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "CMake" / "CMake" / "bin")
    for folder in candidates:
        if (folder / "cmake.exe").is_file() or (folder / "cmake").is_file():
            return folder
    return None


def find_git_dir():
    if shutil.which("git"):
        return None
    for folder in (Path(r"C:\Program Files\Git\cmd"), Path(r"C:\Program Files (x86)\Git\cmd")):
        if (folder / "git.exe").is_file():
            return folder
    return None


def find_ue_python_dev():
    """UE 내장 Python은 include/libs가 Binaries가 아닌 Source/ThirdParty/Python3/Win64 아래에 있다."""
    base = Path(sys.base_prefix).resolve()
    if (base / "include" / "Python.h").is_file() or (base / "Include" / "Python.h").is_file():
        return None, None
    parts = list(base.parts)
    for i, part in enumerate(parts):
        if part.lower() == "binaries":
            source_base = Path(*parts[:i], "Source", *parts[i + 1:])
            inc = source_base / "include"
            libs = source_base / "libs"
            if (inc / "Python.h").is_file() and libs.is_dir():
                matches = sorted(libs.glob(f"python{sys.version_info.major}{sys.version_info.minor}*.lib"))
                if not matches:
                    matches = sorted(libs.glob("python3*.lib"))
                if matches:
                    return inc, matches[0]
    return None, None


def patch_motion_correction_cmake(source):
    """UE 내장 Python 헤더/라이브러리를 CMake FindPython3가 찾도록 CMakeLists.txt에 힌트를 추가한다."""
    inc, lib = find_ue_python_dev()
    if not inc or not lib:
        return
    cmake_file = source / "MotionCorrection" / "CMakeLists.txt"
    if not cmake_file.is_file():
        return
    text = cmake_file.read_text(encoding="utf-8")
    begin_marker = "# KIMODO_UE_PYTHON_DEV_HINTS_BEGIN"
    end_marker = "# KIMODO_UE_PYTHON_DEV_HINTS_END"
    hint = (
        f"{begin_marker}\n"
        f'set(Python3_INCLUDE_DIR "{inc.as_posix()}" CACHE PATH "")\n'
        f'set(Python3_LIBRARY "{lib.as_posix()}" CACHE FILEPATH "")\n'
        f"{end_marker}\n"
    )
    if begin_marker in text and end_marker in text:
        before = text.split(begin_marker, 1)[0]
        after = text.split(end_marker, 1)[1].lstrip("\r\n")
        cmake_file.write_text(before + hint + after, encoding="utf-8")
        return
    target = "find_package(Python3 COMPONENTS Interpreter Development REQUIRED)"
    if target in text:
        cmake_file.write_text(text.replace(target, hint + target, 1), encoding="utf-8")
        print(f"Configured UE Python dev headers for CMake: {inc} / {lib}", flush=True)


def env_for_kimodo_build(python):
    env = os.environ.copy()
    if not shutil.which("git", path=env.get("PATH")):
        git_dir = find_git_dir()
        if git_dir is not None:
            print(f"Using Git from: {git_dir}", flush=True)
            env["PATH"] = str(git_dir) + os.pathsep + env.get("PATH", "")
        else:
            message = (
                "Git is required to build Kimodo (CMake fetches pybind11 and Eigen via Git). "
                "Install Git for Windows (https://git-scm.com/download/win), restart the editor, "
                "and retry Install Kimodo."
            )
            print("ERROR: " + message, flush=True)
            raise SetupError(message)
    if not shutil.which("cmake", path=env.get("PATH")):
        cmake_dir = find_cmake_dir(python)
        if cmake_dir is None:
            print("CMake was not found on PATH or in Visual Studio. Installing the 'cmake' Python package into the venv...", flush=True)
            if run([python, "-m", "pip", "install", "--disable-pip-version-check", "cmake"]) == 0:
                cmake_dir = find_cmake_dir(python)
        if cmake_dir is not None:
            print(f"Using CMake from: {cmake_dir}", flush=True)
            env["PATH"] = str(cmake_dir) + os.pathsep + env.get("PATH", "")
        else:
            message = (
                "CMake could not be found or installed automatically. "
                "Install CMake (https://cmake.org/download/) or enable 'C++ CMake tools for Windows' "
                "in Visual Studio Installer, then retry Install Kimodo."
            )
            print("ERROR: " + message, flush=True)
            raise SetupError(message)
    return env


def find_prebuilt_wheel():
    """플러그인 Wheels 폴더에 현재 Python/OS와 호환되는 사전 빌드 .whl이 있으면 반환한다."""
    wheels_dir = Path(__file__).resolve().parent.parent / "Wheels"
    if not wheels_dir.is_dir():
        return None
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    plat_tag = "win_amd64" if os.name == "nt" else ""
    for wheel in sorted(wheels_dir.glob("kimodo-*.whl"), reverse=True):
        name = wheel.name.lower()
        if py_tag in name and (not plat_tag or plat_tag in name):
            return wheel
    return None


def wheel_hash_ok(wheel):
    """동봉 휠이 커밋한 파일과 같은지 확인한다. LFS 포인터만 받았거나 바뀐 파일이면 소스 빌드로 넘어간다."""
    expected = PREBUILT_WHEEL_SHA256.get(wheel.name)
    if not expected:
        print(f"No pinned SHA256 for {wheel.name}; skipping the pre-built wheel.", flush=True)
        return False
    digest = hashlib.sha256()
    with open(wheel, "rb") as data:
        for chunk in iter(lambda: data.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        print(f"SHA256 mismatch for {wheel.name} (run `git lfs pull` in the plugin folder); "
              "skipping the pre-built wheel.", flush=True)
        return False
    return True


def install_kimodo(python, progress):
    progress.begin("Installing Kimodo...")
    if can_import(python, "kimodo"):
        return

    prebuilt = find_prebuilt_wheel()
    if prebuilt is not None and wheel_hash_ok(prebuilt):
        print(f"Installing Kimodo from pre-built wheel: {prebuilt}", flush=True)
        if run([python, "-m", "pip", "install", "--disable-pip-version-check", prebuilt]) == 0:
            return
        print("Pre-built wheel installation failed; falling back to building Kimodo from source...", flush=True)

    # git 없이 검증한 커밋의 소스 압축 파일을 받아 설치한다.
    source = venv_root(python).parent / f"kimodo-{KIMODO_COMMIT[:12]}"
    if not (source / "pyproject.toml").is_file():
        print(f"Downloading {KIMODO_ARCHIVE}", flush=True)
        try:
            with urllib.request.urlopen(KIMODO_ARCHIVE, timeout=120) as response:
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
        except urllib.error.URLError as error:
            raise SetupError(f"Could not download Kimodo ({error}).") from None
        shutil.rmtree(long_path(source), ignore_errors=True)
        extract_source(archive, source)
    shutil.rmtree(long_path(source / "build"), ignore_errors=True)
    patch_motion_correction_cmake(source)
    if run([python, "-m", "pip", "install", "--disable-pip-version-check", source], env=env_for_kimodo_build(python)):
        print(
            "Kimodo wheel build failed. Ensure Visual Studio 'Desktop development with C++' "
            "(MSVC compiler), CMake, and Git are installed, or inspect the error above in Setup.log, "
            "then retry Install Kimodo.",
            flush=True,
        )
        raise SetupError(
            "Kimodo installation failed. Ensure Visual Studio C++ build tools, CMake, and Git are installed, "
            "then retry (see Setup.log)."
        )


def download_model(python, cache, repo, ignore, progress, label, token):
    progress.begin(f"Downloading {label}...")
    # 토큰은 모델 다운로드 프로세스에만 넘긴다(pip, CMake, git에는 넘기지 않음).
    env = os.environ.copy()
    if token:
        env["HF_TOKEN"] = token
    code = run([python, "-c", DOWNLOAD_SNIPPET, repo, cache, json.dumps(ignore)], env=env)
    if code == 3:
        raise SetupError(f"Access to {repo} was denied. Accept its license at https://huggingface.co/{repo}.")
    if code:
        raise SetupError(f"Downloading {repo} failed. See Setup.log.")


def main():
    python, cache, model, progress_path = sys.argv[1:5]
    progress = Progress(progress_path)
    progress.total = 5 + len(TEXT_ENCODER_REPOS)
    Path(cache).mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        # 토큰이 필요하면 다운로드 전에 먼저 확인해 몇 GB를 받은 뒤 실패하지 않게 한다.
        progress.begin("Checking Llama 3 access...")
        # 이 프로세스의 환경에서 토큰을 빼 두어 pip·CMake·git 같은 하위 프로세스로 퍼지지 않게 한다.
        token = os.environ.pop("HF_TOKEN", "").strip() or saved_token()
        if not (Path(python).is_file() and llama_downloaded(python, cache)):
            check_llama_access(token)

        create_venv(python, progress)
        install_torch(python, progress)
        install_kimodo(python, progress)
        download_model(python, cache, f"nvidia/{model}", [], progress, f"the {model} model", token)
        for repo, ignore in TEXT_ENCODER_REPOS:
            # Llama 본체(약 15 GB)와 LLM2Vec 어댑터 두 개
            label = "Llama 3 8B (about 15 GB)" if repo == LLAMA else repo.split("/")[1]
            download_model(python, cache, repo, ignore, progress, label, token)
        progress.write(f"Kimodo is installed ({int(time.monotonic() - started)}s).", state="done")
    except SetupError as error:
        progress.write(str(error), state="failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
