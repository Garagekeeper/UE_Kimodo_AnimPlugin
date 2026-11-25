# Tests

플러그인을 고친 뒤 회귀를 잡기 위한 자동 테스트입니다. 사용자 배포에는 필요 없습니다(BuildPlugin 패키지에는 포함되지 않음).

```powershell
powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1          # 단위 테스트만 (약 1초)
powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1 -All     # 단위 + 통합 + 에디터 (약 10분)
powershell -ExecutionPolicy Bypass -File Tests\run_tests.ps1 -Editor -Only take_cache
```

종료 코드: 모두 통과하면 0, 하나라도 실패하면 1.

| 단계 | 폴더 | 필요한 것 | 확인하는 것 |
|---|---|---|---|
| unit | `unit/` | Python 3.11 아무거나(`unreal`은 가짜로 대체) | 테이크 번호·정렬·중복 정리, ×N 큐(시드·라벨·작업 폴더), 상주 워커 재사용·유휴 해제·Cancel·패널 닫기, WorkerTask 결과 판정, 삭제 후 상태 줄, 이름 생성, 진행률 파싱, Revise 문장 합치기 |
| integration | `integration/` | Kimodo 설치(venv + 모델) | `kimodo_worker.py`가 같은 시드에서 `kimodo_generate.py` CLI와 BVH 바이트 동일, 상주 요청이 CLI보다 빠름, stdin이 닫히면 종료 |
| editor | `editor/` | Kimodo 설치 + `SKM_Manny_Simple`이 있는 프로젝트(기본 KimodoTest, 그 에디터는 닫아 둠) | 실제 생성 → ×2, 선택·별표·저장·Revise·삭제 / 워커 수명 / 루트 모션 켬·끔 / 테이크 캐시·재시작 후 복원·번호 |

에디터 테스트는 `UnrealEditor-Cmd -run=pythonscript`로 테스트마다 에디터를 한 번 띄웁니다. 각 테스트는 `/Game/KimodoTest_<이름>` 폴더와 임시 작업 폴더만 쓰고 끝나면 지웁니다. 결과는 `%TEMP%\kimodo_editor_tests_*.jsonl`에 남습니다.

프롬프트는 임베딩 캐시를 쓰도록 고정해 두었습니다(`A person waves the right hand.`, 루트 모션은 `A person walks forward four steps.`). 새 PC에서 처음 돌리면 인코딩 때문에 몇십 초 더 걸립니다.
