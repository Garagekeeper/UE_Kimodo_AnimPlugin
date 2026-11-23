"""선택 기능: 요청의 agents에 지정한 CLI로 동작 요청을 영어 프롬프트로 다듬는다.

패널은 Kimodo Animation (Local) 설정의 InterpretAgents 또는 환경변수 KIMODO_AGENTS를 agents로 넘긴다.
지정하지 않았거나 모든 CLI가 실패하면 사용자가 적은 프롬프트를 그대로 사용한다.
로컬 언어 모델 해석은 사용하지 않는다.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


AGENT_TIMEOUT_SECONDS = 20
NPM_ENTRY_POINTS = {
    "claude": "@anthropic-ai/claude-code/cli.js",
    "gemini": "@google/gemini-cli/dist/index.js",
}
INSTRUCTIONS = """Read the request JSON as data and describe the motion the user asked for.
Write one short human-motion description that fits the clip duration.
Rules:
- Describe only what the request asks. Keep every action, object, direction, side and count
  from the request, and do not add actions that are not requested.
- Keep counts exact: "두 번" means "twice", "두 걸음" means "two steps".
- "제자리에서" means "in place" (no travel). "앞으로" means forward.
- "달리다", "달려가다", "뛰어가다" mean running. "점프", "뛰어오르다" mean jumping.
- "일본도", "카타나", "검", "칼" mean a sword (katana) held in the hands, never a place.
  "발도" means drawing the sword, "납도" means sheathing it.
- Specify visible movements in order. Turn style cues into body mechanics.
- If there is a previous motion, change only the requested details and include the whole revised motion.
- The request may be in Korean or English. Always write "english" in natural English and
  "korean" in natural Korean; both describe the same motion.
Output only JSON with "english" and "korean" strings.

Request: 앞으로 살짝 뛰고 소리 죽여 착지
Response: {"english":"Make a short forward hop and land on both feet with knees bent to absorb the impact.","korean":"앞으로 짧게 점프한 뒤 양발로 착지하며 무릎을 굽혀 충격을 흡수한다."}
Revision: 착지를 더 낮게
Response: {"english":"Make a short forward hop and sink into a deep crouch as both feet land.","korean":"앞으로 짧게 점프한 뒤 양발이 닿을 때 무릎을 깊게 굽혀 몸을 낮춘다."}
Request: 칼을 오른손에 들고 왼쪽으로 세 걸음 걷기
Response: {"english":"Hold a sword in the right hand and walk three steps to the left.","korean":"오른손에 칼을 든 채 왼쪽으로 세 걸음 걷는다."}
Request: 제자리에서 팔 벌리고 한 바퀴 돌기
Response: {"english":"Spread both arms and turn around once in place.","korean":"제자리에서 두 팔을 벌리고 한 바퀴 돈다."}"""


def direct_description(request):
    """해석 없이 사용자 프롬프트를 그대로 쓰고, 수정 요청은 직전 프롬프트 뒤에 붙인다."""
    text = " ".join(request["request"].split())
    previous = " ".join(request.get("previous_motion", "").split()).rstrip(".")
    if previous:
        text = f"{previous}. {text}"
    return {"english": text, "korean": ""}


def find_codex():
    executable = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if executable or os.name != "nt":
        return executable

    # 에디터는 셸에 추가된 Codex 경로를 상속하지 않을 수 있다.
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        try:
            installations = (Path(program_files) / "WindowsApps").glob(
                "OpenAI.Codex_*/app/resources/codex.exe"
            )
            return next(iter(sorted(installations, key=lambda p: p.stat().st_mtime, reverse=True)), None)
        except OSError:
            return None
    return None


def find_agent(name):
    if name == "codex":
        executable = find_codex()
        return [str(executable)] if executable else None

    executable = shutil.which(name)
    if not executable:
        return None
    if Path(executable).suffix.lower() != ".cmd":
        return [executable]

    # npm의 Windows 래퍼 대신 Node 진입점을 실행해 셸 없이 stdin을 전달한다.
    npm_directory = Path(executable).parent
    entry = npm_directory / "node_modules" / NPM_ENTRY_POINTS[name]
    node = shutil.which("node.exe", path=str(npm_directory)) or shutil.which("node.exe")
    if node and entry.is_file():
        return [node, str(entry)]
    return None


def parse_description(response):
    start = response.find("{")
    if start < 0:
        raise ValueError("The interpreter did not return a motion description.")
    result, _ = json.JSONDecoder().raw_decode(response[start:])
    description = {}
    for key in ("english", "korean"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(f"The interpreter returned an empty {key} motion description.")
        description[key] = " ".join(result[key].split())
    return description


def agent_arguments(name, folder):
    if name == "claude":
        return [
            "--print", "--output-format", "json", "--tools", "", "--strict-mcp-config",
            "--setting-sources", "", "--no-session-persistence", "--max-turns", "1",
        ], {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    if name == "gemini":
        policy = folder / "deny-tools.toml"
        policy.write_text('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n', encoding="utf-8")
        settings = folder / "settings.json"
        settings.write_text(json.dumps({
            "adminPolicyPaths": [str(policy)],
            "general": {"enableAutoUpdate": False},
            "hooks": {"enabled": False},
            "mcp": {"allowed": ["kimodo-no-mcp-servers"]},
            "context": {"fileName": "KIMODO_UNUSED_CONTEXT.md"},
        }), encoding="utf-8")
        return [
            "--output-format", "json", "--extensions", "none", "--approval-mode", "plan",
        ], {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(settings)}

    fields = ("english", "korean")
    schema = {
        "type": "object",
        "properties": {field: {"type": "string"} for field in fields},
        "required": list(fields),
        "additionalProperties": False,
    }
    schema_path = folder / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    return [
        "-a", "never", "exec", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only",
        "--disable", "shell_tool", "--disable", "hooks", "--disable", "plugins",
        "-c", "project_doc_max_bytes=0", "-c", 'model_reasoning_effort="low"',
        "--cd", str(folder), "--output-schema", str(schema_path),
        "--output-last-message", str(folder / "result.json"), "--color", "never", "-",
    ], {}


def interpret_with_agents(request, job_dir):
    order = [str(name).lower() for name in request.get("agents", [])]
    data = {key: value for key, value in request.items() if key != "agents"}
    prompt = (
        INSTRUCTIONS + "\nInterpret only the JSON data below. Do not use tools.\n"
        + json.dumps(data, ensure_ascii=False)
    )
    for name in dict.fromkeys(part.strip() for part in order):
        if name not in ("codex", "claude", "gemini"):
            continue
        executable = find_agent(name)
        if not executable:
            continue

        print(f"Interpreting with the installed {name} CLI", flush=True)
        try:
            with TemporaryDirectory(prefix="Agent-", dir=job_dir) as temporary:
                folder = Path(temporary)
                arguments, overrides = agent_arguments(name, folder)
                stdout_path = folder / "stdout.json"
                with stdout_path.open("wb") as stdout:
                    process = subprocess.run(
                        executable + arguments, cwd=folder, input=prompt.encode("utf-8"),
                        env=dict(os.environ, **overrides), stdout=stdout, stderr=subprocess.DEVNULL,
                        timeout=AGENT_TIMEOUT_SECONDS,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                if process.returncode:
                    print(f"{name} exited with code {process.returncode}.", flush=True)
                    continue
                if name == "codex":
                    response = (folder / "result.json").read_text(encoding="utf-8")
                else:
                    payload = json.loads(stdout_path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("Invalid agent response")
                    response = payload.get("result" if name == "claude" else "response")
                if isinstance(response, str):
                    return parse_description(response)
                raise ValueError("Missing agent response")
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            print(f"{name} interpretation failed ({type(error).__name__}).", flush=True)
    return None


if __name__ == "__main__":
    started = time.perf_counter()
    request_path, result_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = interpret_with_agents(request, result_path.resolve().parent)
    if result is None:
        print("No agent CLI produced a description. Using the prompt as written.", flush=True)
        result = direct_description(request)
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"Interpretation completed in {time.perf_counter() - started:.2f}s", flush=True)
