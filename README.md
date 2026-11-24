# KimodoAnimation (Unreal Engine 5.8 Editor Plugin)

텍스트 프롬프트로 로컬 Kimodo 모션을 생성하고, UE 에디터 안에서 바로 프리뷰·리타게팅·`AnimSequence` 저장까지 할 수 있는 에디터 전용 플러그인입니다.

버전 **1.0.0** · 변경 내역은 [CHANGELOG.md](CHANGELOG.md)

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

- **좌우 배치**: 왼쪽은 3D 프리뷰(좌클릭 드래그 회전, 휠 줌, `Reset Camera`, 재생 중 카메라 추적 `Follow`), 오른쪽은 `Target → Prompt → Takes → Save` 섹션. Generate/Cancel과 진행 막대는 아래에 항상 보임
- **테이크(Take)**: 생성할 때마다 결과가 목록에 쌓임(최대 20개, 별표 표시한 테이크는 유지). 클릭해 프리뷰를 바꿔 비교하고, 고른 테이크로 수정(Revise)하거나 저장
- **테이크 캐시와 세션 복원**: 테이크 프리뷰는 `Saved/Kimodo/TakeAssets`(`/KimodoTakes/Preview/T<번호>_<핵심 단어>_s<시드>_<id>`)에 에셋으로 남아, 클릭하거나 복원할 때 다시 가져오거나 리타게팅하지 않음. 에디터를 다시 열면 **`Restore last session`**으로 이전 테이크를 모두 되살림. 캐시는 테이크를 ✕로 지울 때만 삭제
- **루트 모션**: `Advanced > Root motion`을 켜면 pelvis의 수평 이동으로 root 이동을 만들고 시퀀스의 `Enable Root Motion`도 켬(이동하는 동작용). 끄면 root가 원점에 고정(제자리 루프용). 프리뷰는 루트 모션 클립을 실제로 이동시키고 바닥에 경로를 그림. Retargeter를 직접 지정하면 그 설정을 따름
- **Advanced**: 시드(`Use selected`/`Clear`), 루트 모션, 텍스트 인코더 방식
- **단축키와 알림**: `Ctrl+Enter`로 생성, 수정 칸에서 `Enter`로 수정, 빈 입력란에서 `Tab`으로 예시 문장 입력. 생성이 끝나면 에디터 알림
- **자동 이름·기억**: 저장 이름은 프롬프트와 시드로 제안(`AS_Kimodo_WaveRightHand_s11`, 겹치면 `_2`). 마지막으로 고른 대상 에셋과 저장 폴더는 다음에 패널을 열 때 그대로 유지

## 생성 장치와 메모리

- Llama 3 8B 텍스트 인코더는 기본적으로 **Low memory (streaming)** 방식으로 돕니다. 32개 층을 디스크에서 하나씩 읽어 CPU로 계산하므로 **RAM 1~2 GB**로 충분합니다. 프롬프트 인코딩은 약 15초 걸리지만 16 GB 로딩(약 25초)이 없어, 측정한 PC(NVMe)에서 생성 1회가 약 50초에서 약 36초로 줄었습니다.
- 기본 Low memory는 빠르게 하려고 어댑터를 계산 중에 더해서, 원래 방식과 비교하면 관절 위치가 평균 약 0.5 cm(최대 약 2 cm) 다릅니다. 원래 CPU 방식과 **똑같은 결과**가 필요하면 **Low memory (exact)**를 고릅니다(메모리는 같고 인코딩이 조금 더 걸림, 측정 시 생성 1회 약 40초).
- 같은 프롬프트의 임베딩은 `<model-cache>/kimodo-text-embeddings`에 캐시되어, 시드·길이만 바꿔 다시 생성하면 인코더를 아예 돌리지 않습니다.
- RAM이 16 GB 이상 남으면 `Project Settings > Plugins > Kimodo Animation (Local) > Text Encoder Device`를 `CPU`로, VRAM 16 GB 이상이면 `CUDA`나 `Auto`로 바꿔 모델 전체를 올릴 수 있습니다. 매 생성마다 16 GB를 불러오므로 보통은 Low memory가 더 빠릅니다.
- 설치 시 `nvidia-smi`가 없으면 CPU용 PyTorch를 받습니다. 직접 고르려면 환경변수 `KIMODO_TORCH_INDEX`에 PyTorch 인덱스 URL을 지정합니다.
- 동봉 휠은 `setup.py`에 고정한 SHA256과 다르면 쓰지 않고 소스 빌드로 넘어갑니다 (LFS 파일을 받지 않았을 때 등).

## 라이선스

- 플러그인 코드(`Source/`, `Scripts/`): [MIT](LICENSE) © 2026 Garagekeeper
- 동봉한 `kimodo` 휠: Apache-2.0 ([ThirdParty/kimodo/LICENSE](ThirdParty/kimodo/LICENSE))
- 설치 시 내려받는 Llama 3, LLM2Vec, Kimodo 모델은 각 배포처의 라이선스를 따르며, 사용자가 직접 승인하고 받습니다. 자세한 내용은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 보세요.
