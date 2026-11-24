# Kimodo Animation (Unreal Engine 5.8 Editor Plugin)

텍스트 프롬프트 한 줄로 동작을 만들어, 내 프로젝트의 스켈레탈 메시에 맞는 `AnimSequence`로 저장하는 **에디터 전용** 플러그인입니다.
NVIDIA의 모션 생성 모델 [Kimodo](https://github.com/nv-tlabs/kimodo)를 **내 PC에서 로컬로** 실행하며, 생성·리타게팅·미리보기·저장을 UE 에디터 패널 하나에서 처리합니다.

버전 **1.0.0** · 변경 내역은 [CHANGELOG.md](CHANGELOG.md) · 라이선스 [MIT](LICENSE)

```
"Walk forward four steps"
   │  (선택) CLI 에이전트가 영어 동작 설명으로 다듬기
   ▼
Kimodo (로컬 Python)  ──▶  SOMA 스켈레톤 모션 (BVH → glTF)
   ▼
UE: glTF 가져오기 → IK Retargeter로 대상 메시에 리타게팅 → 패널에서 미리보기
   ▼
Save → /Game/... 의 AnimSequence
```

---

## 목차

1. [요구 사항](#요구-사항)
2. [설치](#설치)
3. [Kimodo 설치 (패널에서 한 번)](#kimodo-설치-패널에서-한-번)
4. [빠른 시작](#빠른-시작)
5. [패널 사용법](#패널-사용법)
6. [루트 모션](#루트-모션)
7. [테이크와 저장 위치](#테이크와-저장-위치)
8. [텍스트 인코더와 메모리](#텍스트-인코더와-메모리)
9. [설정 항목](#설정-항목)
10. [파일과 폴더](#파일과-폴더)
11. [문제 해결](#문제-해결)
12. [제한 사항](#제한-사항)
13. [제거](#제거)
14. [라이선스](#라이선스)

---

## 요구 사항

| 항목 | 내용 |
|---|---|
| 엔진 | **Unreal Engine 5.8**, Windows 64-bit. C++ 프로젝트(플러그인 C++ 모듈을 빌드하므로) |
| 빌드 도구 | Visual Studio(“C++를 사용한 게임 개발” 워크로드)와 Windows SDK |
| GPU | **NVIDIA GPU 권장**(CUDA 12.8 PyTorch). 없으면 CPU용 PyTorch로 설치되며 생성이 느려집니다 |
| RAM | 기본 설정(Low memory)에서 생성 중 약 **3 GB** 추가 사용. 16 GB PC에서도 동작 |
| 디스크 | 약 **20 GB**(PyTorch 약 3 GB + Llama 3 8B 약 16 GB + Kimodo 모델). 기본 위치 `%LOCALAPPDATA%\KimodoAnimation\` |
| 계정 | **Hugging Face 계정**, [`meta-llama/Meta-Llama-3-8B-Instruct`](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) 라이선스 승인, **읽기(Read) 토큰** |
| 인터넷 | 설치할 때만 필요. 설치 후 생성은 오프라인으로 동작 |

> UE에 내장된 Python 3.11을 쓰므로 별도 Python 설치는 필요 없습니다.
> Windows / Python 3.11용 `kimodo` 사전 빌드 휠(`Wheels/`)이 동봉되어 있어 CMake·Git·MSVC로 Kimodo를 컴파일하지 않습니다.

---

## 설치

### 방법 A. Git Submodule (권장, `main` 추적)

프로젝트 저장소 루트에서:

```powershell
git submodule add -b main https://github.com/Garagekeeper/UE_Kimodo_AnimPlugin.git Plugins/KimodoAnimation
git lfs pull
```

- 휠 파일은 **Git LFS**로 관리됩니다. `git lfs pull`을 빠뜨리면 휠 대신 작은 포인터 파일만 받아져, 설치가 소스 빌드로 넘어갑니다([문제 해결](#문제-해결) 참고).
- 특정 버전에 고정하려면 서브모듈 안에서 태그를 체크아웃합니다: `git -C Plugins/KimodoAnimation checkout v1.0.0`

업데이트:

```powershell
git submodule update --remote Plugins/KimodoAnimation
```

다른 PC에서 처음 클론할 때:

```powershell
git clone --recurse-submodules <프로젝트 저장소 URL>
git lfs pull
```

### 방법 B. 폴더 복사

이 저장소(또는 릴리스 압축 파일)를 `<프로젝트>/Plugins/KimodoAnimation/`에 넣습니다. `Wheels/*.whl`이 실제 파일(약 28 MB)인지 확인하세요.

### 빌드와 활성화

1. 프로젝트(`.uproject`)를 열면 모듈 빌드 창이 뜹니다. **Yes/Build**를 누릅니다.
   (또는 Visual Studio에서 `<프로젝트>Editor` 타깃을 빌드)
2. `PythonScriptPlugin`, `IKRig`, `Interchange`, `KimodoAnimation`은 자동으로 켜집니다.
3. 메뉴 **`Tools > Kimodo Animation`**으로 패널을 엽니다.

---

## Kimodo 설치 (패널에서 한 번)

패널을 처음 열면 **Install Kimodo** 영역이 보입니다. Kimodo가 설치되어 있으면 이 영역은 숨겨집니다.

1. Hugging Face에서 [Llama 3 8B Instruct 라이선스를 승인](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct)합니다(승인까지 시간이 걸릴 수 있음).
2. [Hugging Face 설정 > Access Tokens](https://huggingface.co/settings/tokens)에서 **Read** 토큰을 만듭니다.
3. 패널의 토큰 칸에 붙여 넣고 **Install Kimodo**를 누릅니다.

설치 단계(진행 막대와 로그 마지막 줄이 패널에 표시됨):

| 단계 | 내용 |
|---|---|
| Checking Llama 3 access | 토큰으로 Llama 3에 접근할 수 있는지 먼저 확인 |
| Creating the Python environment | `%LOCALAPPDATA%\KimodoAnimation\venv` 생성 |
| Installing PyTorch | NVIDIA GPU가 있으면 CUDA 12.8 빌드(약 3 GB), 없으면 CPU 빌드 |
| Installing Kimodo | 동봉 휠 설치(SHA256 확인). 휠을 쓸 수 없으면 GitHub 소스에서 빌드 |
| Downloading … | Llama 3 8B, LLM2Vec 어댑터, Kimodo 모델을 `model-cache`로 다운로드 |

- 토큰은 설치 과정에서만 쓰며 로그에 남기지 않습니다.
- 중간에 실패해도 다시 누르면 이미 받은 것은 건너뜁니다. 상세 로그는 `Saved/Kimodo/PanelJobs/<작업 id>/Setup.log`에 있습니다.
- 설치 위치를 바꾸려면 [설정 항목](#설정-항목)의 `Python Executable`, `Model Cache Directory`를 지정합니다.

---

## 빠른 시작

1. **Target**에서 대상 스켈레탈 메시를 고릅니다(예: `SKM_Manny_Simple`). IK Rig와 Retargeter는 비워 두면 자동입니다.
2. **Prompt**에 영어로 동작을 적습니다. 예: `Wave the right hand`, `Walk forward four steps`
3. **Duration**(초)을 정하고 **Generate**(`Ctrl+Enter`)를 누릅니다.
4. 1분 안팎이면 왼쪽 미리보기에서 재생됩니다. 마음에 들 때까지 다시 생성하면 **Takes** 목록에 쌓입니다.
5. 고른 테이크를 **Save**하면 `/Game/Kimodo`(또는 지정한 폴더)에 `AnimSequence`가 생깁니다.

> 생성 시간 예시(RTX 5060 8 GB, 기본 설정): 처음 보는 프롬프트 약 40초, 같은 프롬프트로 시드·길이만 바꾸면 약 17초.

---

## 패널 사용법

패널은 **왼쪽 미리보기**와 **오른쪽 설정 영역**으로 나뉩니다. 가운데 경계를 끌어 폭을 바꿀 수 있습니다.
Generate / Cancel 버튼, 진행 단계와 막대는 오른쪽 아래에 항상 보입니다.

### 미리보기 (왼쪽)

- **좌클릭 드래그**: 회전 · **휠**: 줌 · **우클릭 + WASD**: 이동(UE 뷰포트와 같음)
- **Play / Pause**, 타임라인 슬라이더로 원하는 포즈에서 멈추기, 시간 표시
- **Follow**: 재생 중 카메라가 캐릭터(`PreviewFollowBone`, 기본 `pelvis`)를 수평으로 따라감. 기본은 꺼져 있음
- **Reset Camera**: 처음 카메라 위치로
- 루트 모션 클립은 실제로 이동하며, 바닥에 루트 경로(노란 선, 시작 초록 + / 끝 빨강 +)가 그려집니다

### Target

- **Skeletal Mesh**(필수), **IK Rig**(선택), **Retargeter**(선택)를 썸네일과 함께 한 줄에 고릅니다.
- IK Rig를 비우면 같은 스켈레톤의 IK Rig를 찾고, 없으면 UE 자동 캐릭터화로 만듭니다.
- Retargeter를 지정하면 자동 체인 매핑 대신 그 에셋의 설정(루트 모션 포함)을 그대로 씁니다.
- 마지막으로 고른 값은 다음에 패널을 열 때 유지됩니다.

### Prompt

- **동작 설명**(영어). 빈 칸에서 `Tab`을 누르면 예시 문장이 들어갑니다.
  - 한국어로 쓰려면 [프롬프트 해석](#설정-항목)(InterpretAgents)을 설정해야 합니다. 설정이 없으면 영어만 받습니다.
- **Duration**: 초 단위(최소 0.1초).
- **Advanced**(접힘):
  - **Seed**: 비우면 무작위. 숫자를 넣으면 같은 결과를 재현. `Use selected`는 고른 테이크의 시드를, `Clear`는 비우기
  - **Root motion**: [루트 모션](#루트-모션) 참고
  - **Text encoder**: [텍스트 인코더와 메모리](#텍스트-인코더와-메모리) 참고

### Takes

- 생성할 때마다 한 줄씩 쌓입니다(최대 **20개**). 각 줄: ☆ 별표, 번호, 프롬프트, 시드·길이, 저장 여부 ✓, ✕ 삭제
- **클릭**: 미리보기 전환(바로 바뀜, 다시 리타게팅하지 않음)
- **☆ 별표**: 20개를 넘어 오래된 테이크가 정리될 때도 남김
- **✕**: 목록과 캐시 파일에서 삭제
- **Input prompt**(접힘): 고른 테이크를 만들 때 Kimodo에 실제로 보낸 프롬프트(해석을 썼다면 해석 결과)를 보여 줍니다. 드래그해 `Ctrl+C`로 복사할 수 있습니다.
- **Revise**(수정 칸에서 `Enter`): 고른 테이크를 기준으로 “조금 더 높이 점프” 같은 수정 요청을 덧붙여 다시 생성. 시드는 유지됩니다.

### Save

- **Name**: 프롬프트와 시드로 자동 제안(`AS_Kimodo_WaveRightHand_s11`, 이미 있으면 `_2`). 직접 고치면 덮어쓰지 않습니다.
- **Folder**: `Browse`로 콘텐츠 폴더 선택, `Default`는 프로젝트 설정의 출력 폴더. 마지막으로 저장한 폴더를 기억합니다.
- **Save selected take**: 고른 테이크를 `AnimSequence`로 복사해 저장합니다. 테이크 목록은 그대로 남습니다.

### 단축키와 알림

| 키 | 동작 |
|---|---|
| `Ctrl+Enter` | Generate |
| `Enter` (수정 칸) | Revise |
| `Tab` (빈 입력 칸) | 예시 문장 입력 |

생성이 끝나거나 실패하면 에디터 오른쪽 아래에 알림이 뜹니다(다른 창을 보고 있어도 됨).

---

## 루트 모션

Kimodo는 이동(앞으로 걷기, 점프해서 나아가기 등)을 모두 **pelvis**에 넣고 root 본은 움직이지 않습니다.
그래서 기본 상태에서는 root가 원점에 고정되고 pelvis만 이동합니다.

| `Advanced > Root motion` | 결과 | 용도 |
|---|---|---|
| **끔**(기본) | root 고정, 이동은 pelvis에 남음 | 제자리 루프(걷기 사이클 등), 블렌드 스페이스 |
| **켬** | pelvis의 수평 이동으로 root 이동을 만들고 바닥 높이에 붙임. 시퀀스의 `Enable Root Motion`도 켬 | 캐릭터를 실제로 이동시키는 동작(몽타주, 루트 모션 기반 이동) |

- 켜고 만든 테이크는 상태 줄에 `· root motion`이 붙고, 미리보기에서 캐릭터가 이동하며 경로가 그려집니다.
- 설정은 **새로 만드는 테이크**에 적용됩니다. 이미 만든 테이크는 바뀌지 않습니다.
- Retargeter를 직접 지정하면 그 에셋의 Root Motion 설정을 따릅니다.

---

## 테이크와 저장 위치

- 테이크 미리보기 클립은 **`Saved/Kimodo/TakeAssets`**에 실제 에셋 파일로 남습니다. 에디터에서는 **`/KimodoTakes/Preview`**로 보입니다.
  - 이름: `T<번호>_<핵심 단어>_s<시드>_<id 4자리>`, 예: `T03_WalkForwardFour_s7_a1b2`
  - `Content` 폴더나 소스 관리에 섞이지 않습니다.
- 테이크 목록은 `Saved/Kimodo/session_takes.json`에 저장됩니다.
- 에디터를 다시 열면 **Restore last session (N)**으로 이전 테이크를 **모두** 되살립니다. 캐시 파일을 여는 것뿐이라 테이크 수십 개도 1초 안에 끝납니다.
- 캐시 파일은 테이크를 **✕로 지울 때만** 삭제됩니다. 별표 없는 오래된 테이크는 20개를 넘으면 목록과 함께 정리됩니다.
- 계속 쓸 애니메이션은 반드시 **Save**로 `/Game/...`에 저장하세요. 테이크 캐시는 작업용입니다.
- 이전 버전에서 만든 테이크 캐시(`/KimodoTakes/<id>/KimodoPreview`, `Take_<id>`)는 패널을 처음 열 때 자동으로 새 이름으로 옮겨집니다.

---

## 텍스트 인코더와 메모리

Kimodo는 프롬프트를 **Llama 3 8B**(LLM2Vec) 텍스트 인코더로 4096차원 벡터로 바꿉니다. 모델 전체가 약 16 GB라, 그대로 올리면 일반 PC에서는 메모리가 부족합니다.
이 플러그인은 기본으로 **층을 하나씩 디스크에서 읽어 계산하는 방식**을 씁니다.

| Text encoder | 최대 RAM | 생성 1회 | 결과 |
|---|---|---|---|
| **Low memory (streaming)** (기본) | 약 2.6 GB | 약 42초 | 원래 방식 대비 관절 위치 평균 약 0.5 cm(최대 약 2 cm) 차이 |
| **Low memory (exact)** | 약 2.8 GB | 약 40초 | 원래 CPU 방식과 비트 단위로 동일 |
| CPU | 약 15.6 GB | 약 56초 | 기준 |
| CUDA | VRAM 16 GB 이상 필요 | GPU에 따라 다름 | VRAM이 부족하면 매우 느려지거나 실패 |
| Auto | CUDA를 쓸 수 있으면 GPU, 아니면 CPU | | VRAM 크기는 확인하지 않음 |

측정: RTX 5060 8 GB, RAM 31 GB, NVMe SSD, 2초 클립 생성 전체 시간(프로세스 최대 메모리).

- **임베딩 캐시**: 같은 프롬프트의 임베딩은 `<model-cache>/kimodo-text-embeddings`에 저장됩니다. 시드·길이만 바꿔 다시 생성하면 인코더를 아예 돌리지 않습니다(약 17초).
- 패널의 `Advanced > Text encoder`나 `Project Settings > Plugins > Kimodo Animation (Local) > Text Encoder Device`에서 바꿉니다.
- 설치 때 `nvidia-smi`가 없으면 CPU용 PyTorch를 받습니다. 직접 고르려면 환경변수 `KIMODO_TORCH_INDEX`에 PyTorch 인덱스 URL을 지정합니다.

---

## 설정 항목

### Project Settings > Plugins > Kimodo Animation (프로젝트 공용, `DefaultEditor.ini`)

| 항목 | 설명 |
|---|---|
| Target Mesh | 패널을 처음 열 때 고를 대상 메시 |
| Target IK Rig | 대상 IK Rig. 비우면 자동으로 찾거나 생성 |
| Retargeter (고급) | 지정하면 자동 체인 매핑 대신 사용 |
| Auto Align Bones | A/T 포즈 차이를 맞출 대상 본(기본 팔 본). 대상에 없는 본은 무시 |
| Preview Follow Bone | 미리보기 Follow가 따라갈 본(기본 `pelvis`) |
| Output Folder | 기본 저장 폴더(기본 `/Game/Kimodo`) |
| Default Asset Name | 기본 저장 이름 |
| Model Name | Kimodo 모델 이름(`Kimodo-SOMA-RP-v1.2`) |

### Project Settings > Plugins > Kimodo Animation (Local) (PC별, `EditorPerProjectUserSettings.ini`)

| 항목 | 설명 | 환경변수(우선) |
|---|---|---|
| Python Executable | Kimodo venv의 `python.exe`. 비우면 `%LOCALAPPDATA%\KimodoAnimation\venv` | `KIMODO_PYTHON` |
| Model Cache Directory | Hugging Face 모델 캐시. 비우면 `%LOCALAPPDATA%\KimodoAnimation\model-cache` | `KIMODO_MODEL_CACHE` |
| Working Directory | 작업 로그·중간 파일. 비우면 `Saved/Kimodo` | `KIMODO_WORK_DIR` |
| Interpret Agents | 프롬프트를 다듬을 CLI 순서(`codex`, `claude`, `gemini`). 비우면 프롬프트를 그대로 사용 | `KIMODO_AGENTS` |
| Text Encoder Device | 위 표 참고 | |
| Root Motion | 패널의 `Advanced > Root motion`과 같은 값 | |

**프롬프트 해석(선택)**: `Interpret Agents`에 설치된 CLI(예: `claude`)를 지정하면, 한국어 등 자유로운 요청을 Kimodo가 잘 이해하는 짧은 영어 동작 설명으로 바꾼 뒤 생성합니다. 해석 결과는 Takes의 Input prompt에 표시됩니다. 지정한 CLI가 모두 실패하면 원래 프롬프트를 그대로 씁니다(이때는 영어여야 함).

---

## 파일과 폴더

| 위치 | 내용 |
|---|---|
| `%LOCALAPPDATA%\KimodoAnimation\venv` | Kimodo용 Python 환경(PyTorch, kimodo) |
| `%LOCALAPPDATA%\KimodoAnimation\model-cache` | Llama 3, LLM2Vec, Kimodo 모델과 프롬프트 임베딩 캐시 |
| `<프로젝트>/Saved/Kimodo/PanelJobs/<id>/` | 생성 작업별 로그(`Interpretation.log`, `Kimodo.log`, `Setup.log`)와 결과 `SOMA_Motion.glb`. 최근 20개(테이크가 참조하는 작업 포함)만 유지 |
| `<프로젝트>/Saved/Kimodo/TakeAssets/` | 테이크 미리보기 캐시(`/KimodoTakes/Preview`) |
| `<프로젝트>/Saved/Kimodo/session_takes.json` | 테이크 목록 |
| 저장 폴더(`/Game/Kimodo` 등) | Save로 만든 `AnimSequence` |

---

## 문제 해결

| 증상 | 해결 |
|---|---|
| `This Hugging Face token cannot download Llama 3` | Llama 3 라이선스 승인이 끝났는지, 토큰이 같은 계정의 **Read** 토큰인지 확인 |
| `Access to <repo> was denied` | 해당 Hugging Face 페이지에서 라이선스를 승인 |
| 설치가 “building from source”로 넘어가고 실패 | `git lfs pull`로 휠을 받았는지 확인(`Wheels/*.whl`이 약 28 MB여야 함). 소스 빌드에는 Visual Studio C++, CMake, Git이 필요 |
| `Write the Kimodo prompt in English.` | 영어로 쓰거나 `Interpret Agents`를 설정 |
| `Kimodo is not installed on this computer` | 패널에서 Install Kimodo 실행. 경로를 바꿨다면 Local 설정의 Python/모델 캐시 경로 확인 |
| `Unreal could not characterize <mesh>` | 표준 휴머노이드가 아닌 메시. Target IK Rig(리타게팅 체인 포함)를 직접 지정 |
| `No retarget chain of <rig> matched the SOMA skeleton` | IK Rig 체인 이름을 표준(Spine, Head, LeftArm, LeftLeg…)으로 바꾸거나 Retargeter를 지정 |
| 팔이 몸을 뚫거나 벌어짐 | `Auto Align Bones`에 대상의 상완·전완 본 이름을 지정 |
| 생성이 매우 느리거나 메모리 부족 | Text encoder를 `Low memory`로. CUDA는 VRAM 16 GB 이상에서만 |
| 실패 원인을 더 보고 싶음 | 알림/상태 줄에 나온 로그 경로(`Saved/Kimodo/PanelJobs/<id>/*.log`) 확인 |
| 루트 모션 클립이 게임에서 제자리 | 캐릭터의 Anim Blueprint `Root Motion Mode`가 `Root Motion from Everything`인지(몽타주면 Montages Only도 가능) 확인 |

---

## 제한 사항

- **Windows 64-bit, UE 5.8** 전용 에디터 플러그인입니다. 런타임(패키징된 게임)에는 포함되지 않습니다.
- 생성은 **사람 형태(휴머노이드) 동작**만 다룹니다. 손가락은 대략적인 수준입니다.
- 한 번에 한 작업만 실행됩니다(생성 중에는 다른 테이크 선택·저장 불가, Cancel 가능).
- 프롬프트는 짧고 구체적일수록 잘 따릅니다(예: “Jump forward twice” > 긴 문장).

---

## 제거

1. 프로젝트에서 `Plugins/KimodoAnimation`을 지우거나 플러그인을 끕니다.
2. 디스크를 비우려면 `%LOCALAPPDATA%\KimodoAnimation\`(약 20 GB)과 `<프로젝트>/Saved/Kimodo/`를 삭제합니다.
3. Hugging Face 토큰은 필요 없으면 Hugging Face 설정에서 폐기합니다.

---

## 라이선스

- 플러그인 코드(`Source/`, `Scripts/`): [MIT](LICENSE) © 2026 Garagekeeper
- 동봉한 `kimodo` 휠: Apache-2.0 ([ThirdParty/kimodo/LICENSE](ThirdParty/kimodo/LICENSE))
- 설치 때 내려받는 Llama 3, LLM2Vec, Kimodo 모델은 각 배포처의 라이선스를 따르며, 사용자가 직접 승인하고 받습니다. 생성한 애니메이션의 사용도 그 조건을 따릅니다. 자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 보세요.
