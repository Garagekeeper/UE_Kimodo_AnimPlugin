#pragma once

#include "Engine/DeveloperSettings.h"
#include "Engine/EngineTypes.h"

#include "KimodoSettings.generated.h"

class UIKRetargeter;
class UIKRigDefinition;
class USkeletalMesh;

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
};
