# KimodoAnimation (Unreal Engine 5.8 Editor Plugin)

텍스트 프롬프트로 로컬 Kimodo 모션을 생성하고, UE 에디터 안에서 바로 프리뷰·리타게팅·`AnimSequence` 저장까지 할 수 있는 에디터 전용 플러그인입니다.

## 요구 사항

- **Unreal Engine 5.8** (Windows 64-bit, C++ 프로젝트)
- **Visual Studio** UE C++ 개발 구성요소 및 Windows SDK (플러그인 C++ 모듈 빌드용)
- **NVIDIA GPU** 권장 (CUDA 12.8 PyTorch 사용)
- **Hugging Face 계정** 및 [`meta-llama/Meta-Llama-3-8B-Instruct`](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) 라이선스 승인 + 읽기(Read) 토큰
- 디스크 여유 공간 약 **20 GB** (모델 캐시 저장용, 기본 경로 `%LOCALAPPDATA%\KimodoAnimation\`)

> **참고**: Windows UE 5.8(Python 3.11)용 사전 빌드 휠(`Wheels/kimodo-1.0.0-cp311-cp311-win_amd64.whl`)이 Git LFS로 동봉되어 있어, 다른 Windows PC에서 CMake·Git·MSVC 컴파일 없이 바로 `kimodo`가 설치됩니다.

## 프로젝트에 연결 (Git Submodule, `main` 브랜치 추적)

```powershell
git submodule add -b main https://github.com/Garagekeeper/UE_Kimodo_AnimPlugin.git Plugins/KimodoAnimation
git lfs pull
```

플러그인 최신 버전으로 업데이트할 때:

```powershell
git submodule update --remote Plugins/KimodoAnimation
```

다른 컴퓨터에서 프로젝트를 처음 클론할 때:

```powershell
git clone --recurse-submodules <프로젝트 저장소 URL>
git lfs pull
```

## 첫 실행

1. UE 5.8로 프로젝트를 열고 C++ 모듈 빌드 창이 뜨면 **Build**를 선택합니다.
   - `PythonScriptPlugin`, `IKRig`, `Interchange`, `KimodoAnimation`은 기본 활성화됩니다.
2. `Edit > Project Settings > Plugins > Kimodo Animation`에서 대상 스켈레탈 메시(`TargetMesh`), IK Rig(선택), 출력 폴더(기본 `/Game/Kimodo`)를 확인합니다.
3. `Tools > Kimodo Animation` 패널을 열고 Hugging Face 읽기 토큰을 입력한 뒤 **`Install Kimodo`**를 클릭합니다.
   - 설치가 완료되어 상태가 `ready`가 되면 `Install Kimodo` 영역은 자동으로 숨겨집니다.

## 주요 기능

- **상단 크기 조절 가능한 3D 프리뷰 + 하단 스크롤 컨트롤**: 좌클릭 드래그 회전, 휠 줌, `Reset Camera`, 블러 없는 선명한 프리뷰
- **세션 간 마지막 프리뷰 복원**: 저장한 클립은 에디터를 다시 열면 바로 복원됩니다. 저장하지 않은 프리뷰는 glTF를 다시 가져와야 해서 에디터 시작을 막지 않도록 **`Restore last preview`** 버튼을 눌렀을 때만 복원합니다 (`Saved/Kimodo/last_preview.json`)
- **카메라 추적**: 재생 중 프리뷰 카메라가 `PreviewFollowBone`(기본 `pelvis`, 없으면 메시 중심)을 수평으로 따라갑니다. 비우면 고정 카메라
- **힌트 자동 완성**: 비어 있는 프롬프트/수정 입력란에서 `Tab` 키를 누르면 예시 힌트 문장이 그대로 입력됨

## 생성 장치와 메모리

- Llama 3 8B 텍스트 인코더는 기본적으로 **CPU**에서 돌아갑니다 (시스템 RAM 약 32 GB 권장, 느림).
- VRAM 16 GB 이상인 NVIDIA GPU라면 `Project Settings > Plugins > Kimodo Animation (Local) > Text Encoder Device`를 `CUDA`나 `Auto`로 바꾸면 빨라집니다. VRAM이 부족하면 생성이 메모리 부족으로 실패합니다.
- 설치 시 `nvidia-smi`가 없으면 CPU용 PyTorch를 받습니다. 직접 고르려면 환경변수 `KIMODO_TORCH_INDEX`에 PyTorch 인덱스 URL을 지정합니다.
- 동봉 휠은 `setup.py`에 고정한 SHA256과 다르면 쓰지 않고 소스 빌드로 넘어갑니다 (LFS 파일을 받지 않았을 때 등).
