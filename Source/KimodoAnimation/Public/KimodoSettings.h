#pragma once

#include "Engine/DeveloperSettings.h"
#include "Engine/EngineTypes.h"

#include "KimodoSettings.generated.h"

class UIKRetargeter;
class UIKRigDefinition;
class USkeletalMesh;

/** Llama 3 8B 텍스트 인코더를 돌리는 방식. 어느 방식이든 같은 프롬프트는 디스크 캐시를 쓴다. */
UENUM()
enum class EKimodoTextEncoderDevice : uint8
{
	/** 층을 하나씩 디스크에서 읽어 CPU로 계산한다. RAM 1~2 GB, 프롬프트당 약 15초(로딩 없음). 일반 PC 권장. */
	LowMemory UMETA(DisplayName = "Low memory (streaming)"),
	/** Low memory와 같은 메모리로, 원래 CPU 방식과 비트 단위로 같은 결과를 낸다. 인코딩이 조금 더 걸린다(측정 시 생성 1회 약 40초). */
	LowMemoryExact UMETA(DisplayName = "Low memory (exact)"),
	/** 모델 전체를 RAM에 올린다(약 16 GB). 로딩이 길다. */
	CPU,
	/** VRAM 16 GB 이상인 NVIDIA GPU. 부족하면 생성이 메모리 부족으로 실패한다. */
	CUDA,
	/** CUDA를 쓸 수 있으면 GPU, 아니면 CPU (VRAM 크기는 확인하지 않음). */
	Auto
};

/** 프로젝트 전체가 공유하는 Kimodo 기본값 (DefaultEditor.ini). */
UCLASS(Config = Editor, DefaultConfig, meta = (DisplayName = "Kimodo Animation"))
class KIMODOANIMATION_API UKimodoSettings : public UDeveloperSettings
{
	GENERATED_BODY()

public:
	UKimodoSettings();

	virtual FName GetCategoryName() const override { return TEXT("Plugins"); }

	/** 패널을 열 때 선택되는 기본 대상 메시. 패널에서 생성마다 바꿀 수 있다. */
	UPROPERTY(Config, EditAnywhere, Category = "Target")
	TSoftObjectPtr<USkeletalMesh> TargetMesh;

	/** 대상 IK Rig. 비우면 같은 스켈레톤의 IK Rig를 찾고, 없으면 자동 생성한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Target")
	TSoftObjectPtr<UIKRigDefinition> TargetIKRig;

	/** SOMA 소스 → 대상 Retargeter. 지정하면 자동 체인 매핑 대신 이 에셋을 그대로 사용한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Target", AdvancedDisplay)
	TSoftObjectPtr<UIKRetargeter> Retargeter;

	/** A/T 포즈 차이를 맞출 대상 본. 대상에 없는 본은 무시하고, 모두 없으면 팔 체인 시작 본을 사용한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Retargeting")
	TArray<FName> AutoAlignBones;

	/** 미리보기 카메라가 따라갈 본. 메시에 없으면 메시 경계 중심을 따라간다. */
	UPROPERTY(Config, EditAnywhere, Category = "Preview")
	FName PreviewFollowBone;

	/** 저장 폴더. 미리보기 임시 에셋도 이 폴더의 _Preview_ 하위 폴더에 만든다. */
	UPROPERTY(Config, EditAnywhere, Category = "Output", meta = (ContentDir))
	FDirectoryPath OutputFolder;

	UPROPERTY(Config, EditAnywhere, Category = "Output")
	FString DefaultAssetName;

	/** kimodo.scripts.generate의 --model 값. */
	UPROPERTY(Config, EditAnywhere, Category = "Generation")
	FString ModelName;
};

/** 컴퓨터마다 다른 Kimodo 설치 경로 (EditorPerProjectUserSettings.ini). 상대 경로는 프로젝트 폴더 기준이다. */
UCLASS(Config = EditorPerProjectUserSettings, meta = (DisplayName = "Kimodo Animation (Local)"))
class KIMODOANIMATION_API UKimodoUserSettings : public UDeveloperSettings
{
	GENERATED_BODY()

public:
	virtual FName GetCategoryName() const override { return TEXT("Plugins"); }

	/** Kimodo가 설치된 venv의 Python. 비우면 %LOCALAPPDATA%/KimodoAnimation/venv를 쓰며, 패널의 Install Kimodo가 그 위치에 설치한다. 환경변수 KIMODO_PYTHON이 있으면 그 값을 우선한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Installation", meta = (FilePathFilter = "exe"))
	FFilePath PythonExecutable;

	/** Hugging Face 모델 캐시. 비우면 %LOCALAPPDATA%/KimodoAnimation/model-cache를 쓴다. 환경변수 KIMODO_MODEL_CACHE가 있으면 그 값을 우선한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Installation")
	FDirectoryPath ModelCacheDirectory;

	/** 작업 로그와 중간 파일 폴더. 비우면 Saved/Kimodo를 사용한다. */
	UPROPERTY(Config, EditAnywhere, Category = "Installation")
	FDirectoryPath WorkingDirectory;

	/** 프롬프트를 다듬을 CLI 순서(codex, claude, gemini). 비우면 환경변수 KIMODO_AGENTS를 읽고, 그것도 없으면 프롬프트를 그대로 쓴다. */
	UPROPERTY(Config, EditAnywhere, Category = "Interpretation")
	TArray<FString> InterpretAgents;

	/** Llama 3 8B 텍스트 인코더 방식. 기본 Low memory(RAM 1~2 GB). CPU는 RAM 16 GB 이상, CUDA는 VRAM 16 GB 이상일 때만 고른다. */
	UPROPERTY(Config, EditAnywhere, Category = "Generation")
	EKimodoTextEncoderDevice TextEncoderDevice = EKimodoTextEncoderDevice::LowMemory;

	/** 켜면 pelvis의 수평 이동으로 root 본 이동(루트 모션)을 만들고 시퀀스의 Enable Root Motion도 켠다.
	 *  끄면 root는 원점에 고정되고 이동은 pelvis에 남는다(제자리 루프용). Retargeter를 직접 지정하면 그 설정을 따른다. */
	UPROPERTY(Config, EditAnywhere, Category = "Generation")
	bool bRootMotion = false;

	/** 확산(디노이징) 스텝 수. Kimodo 기본 100. 적을수록 빠르고 거칠며, 같은 시드라도 스텝이 다르면 다른 동작이 나온다. */
	UPROPERTY(Config, EditAnywhere, Category = "Generation", meta = (ClampMin = "1", ClampMax = "1000", UIMin = "1", UIMax = "200"))
	int32 DiffusionSteps = 100;

	/** 패널에서 마지막으로 고른 값. 다음에 패널을 열 때 프로젝트 기본값보다 먼저 쓴다(설정 화면에는 표시하지 않음). */
	UPROPERTY(Config)
	FString LastTargetMesh;

	UPROPERTY(Config)
	FString LastTargetIKRig;

	UPROPERTY(Config)
	FString LastRetargeter;

	/** Generate ×N: 한 번에 만들 테이크 수(1~10). 패널에서 마지막으로 고른 값. */
	UPROPERTY(Config)
	int32 BatchCount = 1;

	/** 패널의 Advanced(시드·루트 모션·인코더)를 펼쳐 두었는지. 다음에 패널을 열 때 그대로 연다. */
	UPROPERTY(Config)
	bool bAdvancedExpanded = false;

	/** 마지막으로 저장에 성공한 폴더. 비어 있으면 프로젝트 설정의 출력 폴더를 쓴다. */
	UPROPERTY(Config)
	FString LastSaveFolder;
};
