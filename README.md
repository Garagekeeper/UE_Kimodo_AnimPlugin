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
- **세션 간 마지막 프리뷰 자동 복원**: 에디터를 재시작해도 직전 프리뷰(또는 저장된 클립)가 일시정지 상태로 자동 복원됨 (`Saved/Kimodo/last_preview.json`)
- **힌트 자동 완성**: 비어 있는 프롬프트/수정 입력란에서 `Tab` 키를 누르면 예시 힌트 문장이 그대로 입력됨
