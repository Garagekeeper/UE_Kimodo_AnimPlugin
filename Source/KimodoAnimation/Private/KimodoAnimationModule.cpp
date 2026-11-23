#include "KimodoSettings.h"
#include "SKimodoPreview.h"

#include "Animation/AnimSequence.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "Framework/Docking/TabManager.h"
#include "HAL/PlatformMisc.h"
#include "InputCoreTypes.h"
#include "Interfaces/IPluginManager.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Base64.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "PropertyCustomizationHelpers.h"
#include "Retargeter/IKRetargeter.h"
#include "Rig/IKRigDefinition.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "ToolMenus.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectIterator.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SHyperlink.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Input/SSlider.h"
#include "Widgets/Images/SThrobber.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSplitter.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "KimodoAnimation"

namespace
{
const FName KimodoTabName(TEXT("KimodoAnimation"));
const TCHAR* KimodoPluginName = TEXT("KimodoAnimation");

/** 환경변수 → 설정값 순서로 경로를 고르고, 상대 경로는 프로젝트 폴더 기준으로 바꾼다. */
FString ResolveLocalPath(const TCHAR* EnvName, const TCHAR* LegacyEnvName, const FString& SettingPath, const FString& DefaultPath = FString())
{
	FString Path = FPlatformMisc::GetEnvironmentVariable(EnvName);
	if (Path.IsEmpty()) Path = FPlatformMisc::GetEnvironmentVariable(LegacyEnvName);
	if (Path.IsEmpty()) Path = SettingPath;
	if (Path.IsEmpty()) Path = DefaultPath;
	if (Path.IsEmpty()) return Path;

	if (FPaths::IsRelative(Path))
	{
		Path = FPaths::ProjectDir() / Path;
	}
	return FPaths::ConvertRelativePathToFull(Path);
}

TArray<TSharedPtr<FJsonValue>> ToJsonArray(const TArray<FString>& Values)
{
	TArray<TSharedPtr<FJsonValue>> Result;
	for (const FString& Value : Values)
	{
		Result.Add(MakeShared<FJsonValueString>(Value));
	}
	return Result;
}

/** 설정이 비어 있을 때 쓰는 컴퓨터별 설치 폴더. 여러 프로젝트가 같은 설치본을 공유한다. */
FString GetManagedInstallRoot()
{
	return FPaths::ConvertRelativePathToFull(FPaths::Combine(FPlatformProcess::UserSettingsDir(), TEXT("KimodoAnimation")));
}

FString GetManagedPython()
{
#if PLATFORM_WINDOWS
	return GetManagedInstallRoot() / TEXT("venv/Scripts/python.exe");
#else
	return GetManagedInstallRoot() / TEXT("venv/bin/python");
#endif
}

/** venv를 만들 때 쓰는 UE 내장 Python. 별도 Python 설치가 필요 없다. */
FString GetBootstrapPython()
{
#if PLATFORM_WINDOWS
	const TCHAR* Relative = TEXT("Binaries/ThirdParty/Python3/Win64/python.exe");
#elif PLATFORM_MAC
	const TCHAR* Relative = TEXT("Binaries/ThirdParty/Python3/Mac/bin/python3");
#else
	const TCHAR* Relative = TEXT("Binaries/ThirdParty/Python3/Linux/bin/python3");
#endif
	return FPaths::ConvertRelativePathToFull(FPaths::EngineDir() / Relative);
}

FString GetOutputFolder()
{
	FString Folder = GetDefault<UKimodoSettings>()->OutputFolder.Path;
	Folder.RemoveFromEnd(TEXT("/"));
	if (Folder.IsEmpty()) Folder = TEXT("/Game/Kimodo");
	return Folder;
}

FString ToAssetPath(const FAssetData& Asset)
{
	return Asset.IsValid() ? Asset.GetSoftObjectPath().ToString() : FString();
}

class SKimodoPanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SKimodoPanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& Args)
	{
		const UKimodoSettings* Settings = GetDefault<UKimodoSettings>();
		TargetMeshPath = Settings->TargetMesh.ToSoftObjectPath().ToString();
		TargetRigPath = Settings->TargetIKRig.ToSoftObjectPath().ToString();
		RetargeterPath = Settings->Retargeter.ToSoftObjectPath().ToString();

		ChildSlot
		[
			SNew(SSplitter)
			.Orientation(Orient_Vertical)
			// 상단(크기 조절 가능): 3D 프리뷰와 재생·타임라인·카메라 리셋 바
			+ SSplitter::Slot()
			.Value(0.45f)
			.MinSize(160.0f)
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().FillHeight(1.0f).Padding(8, 8, 8, 0)
				[
					SAssignNew(Preview, SKimodoPreview)
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(8, 6, 8, 8)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.IsEnabled_Lambda([this] { return Preview->GetLength() > 0.0f; })
						.Text_Lambda([this] { return Preview->IsPlaying() ? LOCTEXT("Pause", "Pause") : LOCTEXT("Play", "Play"); })
						.OnClicked_Lambda([this] {
							Preview->TogglePlay();
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot().FillWidth(1).VAlign(VAlign_Center).Padding(10, 0)
					[
						SNew(SSlider)
						.IsEnabled_Lambda([this] { return Preview->GetLength() > 0.0f; })
						.PreventThrottling(true)
						.Value_Lambda([this] {
							const float Length = Preview->GetLength();
							return Length > 0.0f ? Preview->GetTime() / Length : 0.0f;
						})
						.OnValueChanged_Lambda([this](float Fraction) { Preview->Scrub(Fraction * Preview->GetLength()); })
						.ToolTipText(LOCTEXT("Timeline", "Drag to pause and inspect a pose."))
					]
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 10, 0)
					[
						SNew(STextBlock).Text_Lambda([this] {
							return FText::FromString(FString::Printf(TEXT("%.2f / %.2f s"), Preview->GetTime(), Preview->GetLength()));
						})
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.Text(LOCTEXT("ResetCamera", "Reset Camera"))
						.OnClicked_Lambda([this] {
							Preview->ResetCamera();
							return FReply::Handled();
						})
					]
				]
			]
			// 하단 스크롤 영역: 설치·대상 메시·프롬프트·생성·저장 컨트롤
			+ SSplitter::Slot()
			.Value(0.55f)
			.MinSize(160.0f)
			[
				SNew(SScrollBox)
				+ SScrollBox::Slot().Padding(16, 8, 16, 16)
				[
					SNew(SVerticalBox)
				// 이 컴퓨터에 Kimodo가 없으면 설치 안내와 버튼을 보여 준다.
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
				[
					SNew(SVerticalBox)
					.Visibility_Lambda([this] {
						if (bNeedsInitialState || IsReady()) return EVisibility::Collapsed;
						return EVisibility::Visible;
					})
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
					[
						SNew(STextBlock)
						.AutoWrapText(true)
						.Text_Lambda([this] {
							if (SetupState == TEXT("installing"))
							{
								return LOCTEXT("SetupRunning", "Installing Kimodo. You can keep using the editor; progress is shown below.");
							}
							return FText::Format(LOCTEXT("SetupMissing",
								"Kimodo is not installed on this computer (missing {0}).\n"
								"Install downloads PyTorch, Kimodo and the models (about 20 GB) to {1}.\n"
								"Llama 3 needs a Hugging Face token from an account that accepted its license."),
								FText::FromString(SetupMissing), FText::FromString(GetInstallLocationText()));
						})
					]
					+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 6)
					[
						SNew(SHyperlink)
						.Text(LOCTEXT("LlamaLicense", "Open the Llama 3 license page"))
						.OnNavigate_Lambda([] {
							FPlatformProcess::LaunchURL(TEXT("https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct"), nullptr, nullptr);
						})
					]
					+ SVerticalBox::Slot().AutoHeight()
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().FillWidth(1)
						[
							SAssignNew(Token, SEditableTextBox)
							.IsPassword(true)
							.HintText(LOCTEXT("TokenHint", "Hugging Face token (leave empty to use a saved `hf auth login` token)"))
							.Visibility_Lambda([this] { return bBusy ? EVisibility::Collapsed : EVisibility::Visible; })
						]
						+ SHorizontalBox::Slot().AutoWidth().Padding(8, 0, 0, 0)
						[
							SNew(SButton)
							.Text(LOCTEXT("Install", "Install Kimodo"))
							.Visibility_Lambda([this] { return bBusy ? EVisibility::Collapsed : EVisibility::Visible; })
							.OnClicked_Lambda([this] {
								// 토큰은 설치 프로세스에만 넘기고 입력란에 남기지 않는다.
								const FString TokenValue = Token->GetText().ToString();
								Token->SetText(FText::GetEmpty());
								return RunAction(TEXT("setup"), TokenValue);
							})
						]
						+ SHorizontalBox::Slot().AutoWidth()
						[
							SNew(SButton)
							.Text(LOCTEXT("CancelSetup", "Cancel install"))
							.Visibility_Lambda([this] { return bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
							.OnClicked_Lambda([this] { return RunAction(TEXT("cancel")); })
						]
					]
				]
				// 생성마다 대상 캐릭터를 고를 수 있다. 기본값은 프로젝트 설정에서 읽는다.
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
				[
					SNew(SVerticalBox)
					.IsEnabled_Lambda([this] { return !bBusy; })
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
					[
						SNew(STextBlock).Text(LOCTEXT("TargetMesh", "Target skeletal mesh"))
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
					[
						SNew(SObjectPropertyEntryBox)
						.AllowedClass(USkeletalMesh::StaticClass())
						.ObjectPath_Lambda([this] { return TargetMeshPath; })
						.OnObjectChanged_Lambda([this](const FAssetData& Asset)
						{
							// 다른 메시의 IK Rig와 Retargeter가 남지 않도록 함께 비운다.
							const FString NewPath = ToAssetPath(Asset);
							if (NewPath == TargetMeshPath) return;

							TargetMeshPath = NewPath;
							TargetRigPath.Reset();
							RetargeterPath.Reset();
						})
						.DisplayThumbnail(false)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("TargetRig", "Target IK Rig (optional)"))
						.ToolTipText(LOCTEXT("TargetRigTip", "Leave empty to use an IK Rig for the same skeleton, or generate one automatically."))
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
					[
						SNew(SObjectPropertyEntryBox)
						.AllowedClass(UIKRigDefinition::StaticClass())
						.ObjectPath_Lambda([this] { return TargetRigPath; })
						.OnObjectChanged_Lambda([this](const FAssetData& Asset) { TargetRigPath = ToAssetPath(Asset); })
						.DisplayThumbnail(false)
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("Retargeter", "Retargeter (optional)"))
						.ToolTipText(LOCTEXT("RetargeterTip", "A SOMA-to-target IK Retargeter. When set, it replaces automatic chain mapping."))
					]
					+ SVerticalBox::Slot().AutoHeight()
					[
						SNew(SObjectPropertyEntryBox)
						.AllowedClass(UIKRetargeter::StaticClass())
						.ObjectPath_Lambda([this] { return RetargeterPath; })
						.OnObjectChanged_Lambda([this](const FAssetData& Asset) { RetargeterPath = ToAssetPath(Asset); })
						.DisplayThumbnail(false)
					]
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(STextBlock).Text(LOCTEXT("Prompt", "동작 프롬프트 (영어)"))
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SAssignNew(Prompt, SMultiLineEditableTextBox)
					.AutoWrapText(true)
					.HintText(LOCTEXT("PromptHint", "Make a short forward hop and land softly with knees bent"))
					.IsEnabled_Lambda([this] { return !bBusy; })
					.OnKeyDownHandler_Lambda([this](const FGeometry&, const FKeyEvent& Event)
					{
						if (Event.GetKey() == EKeys::Tab && Prompt->GetText().IsEmptyOrWhitespace())
						{
							Prompt->SetText(LOCTEXT("PromptHint", "Make a short forward hop and land softly with knees bent"));
							return FReply::Handled();
						}
						return FReply::Unhandled();
					})
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(STextBlock).Text(LOCTEXT("Duration", "Duration (seconds)"))
				]
				+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 8)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SAssignNew(Duration, SEditableTextBox)
						.MinDesiredWidth(92)
						.Text(LOCTEXT("DefaultDuration", "2.0"))
						.IsEnabled_Lambda([this] { return !bBusy; })
					]
					// 시드: 비우면 새 생성은 랜덤, 수정은 직전 시드. 값을 넣으면 항상 그 시드로 생성한다.
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(16, 0, 6, 0)
					[
						SNew(STextBlock).Text(LOCTEXT("Seed", "Seed"))
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SAssignNew(Seed, SEditableTextBox)
						.MinDesiredWidth(110)
						.HintText(LOCTEXT("SeedHint", "random"))
						.ToolTipText(LOCTEXT("SeedTip", "Leave empty for a random seed (revisions keep the previous one). Enter a number to reproduce a result."))
						.IsEnabled_Lambda([this] { return !bBusy; })
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6, 0, 0, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("UseLastSeed", "Use last"))
						.ToolTipText(LOCTEXT("UseLastSeedTip", "Fill in the seed of the current preview."))
						.IsEnabled_Lambda([this] { return !bBusy && !LastSeed.IsEmpty(); })
						.OnClicked_Lambda([this] {
							Seed->SetText(FText::FromString(LastSeed));
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("ClearSeed", "Clear"))
						.IsEnabled_Lambda([this] { return !bBusy && !Seed->GetText().IsEmptyOrWhitespace(); })
						.OnClicked_Lambda([this] {
							Seed->SetText(FText::GetEmpty());
							return FReply::Handled();
						})
					]
				]
				+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 16)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.Text(LOCTEXT("Generate", "새 미리보기 생성"))
						.IsEnabled_Lambda([this] {
							if (bNeedsInitialState || bBusy) return false;
							if (!IsReady()) return false;
							if (TargetMeshPath.IsEmpty()) return false;
							return !Prompt->GetText().ToString().TrimStartAndEnd().IsEmpty();
						})
						.OnClicked_Lambda([this] {
							return RunAction(TEXT("generate"), Prompt->GetText().ToString(), Duration->GetText().ToString());
						})
					]
					// 해석·생성·변환 중에만 보이며 진행 중인 작업을 종료한다.
					+ SHorizontalBox::Slot().AutoWidth().Padding(8, 0, 0, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("Cancel", "중단"))
						.ToolTipText(LOCTEXT("CancelTip", "진행 중인 생성을 중단하고 직전 미리보기를 유지합니다."))
						.Visibility_Lambda([this] { return bBusy && IsReady() ? EVisibility::Visible : EVisibility::Collapsed; })
						.OnClicked_Lambda([this] { return RunAction(TEXT("cancel")); })
					]
					// 에디터 시작을 막지 않도록 직전 미리보기(glTF 다시 가져오기)는 눌렀을 때만 복원한다.
					+ SHorizontalBox::Slot().AutoWidth().Padding(8, 0, 0, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("Restore", "Restore last preview"))
						.ToolTipText(LOCTEXT("RestoreTip", "Re-import and retarget the preview from the previous session."))
						.Visibility_Lambda([this] { return bCanRestore && !bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
						.OnClicked_Lambda([this] { return RunAction(TEXT("restore")); })
					]
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SNew(STextBlock)
					.AutoWrapText(true)
					.Text_Lambda([this] { return Interpretation; })
					.Visibility_Lambda([this] {
						return Interpretation.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible;
					})
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
				[
					SNew(SHorizontalBox)
					.Visibility_Lambda([this] {
						return bCanRevise ? EVisibility::Visible : EVisibility::Collapsed;
					})
					+ SHorizontalBox::Slot().FillWidth(1)
					[
						SAssignNew(Revision, SEditableTextBox)
						.HintText(LOCTEXT("RevisionHint", "Land in a deeper crouch"))
						.IsEnabled_Lambda([this] { return !bBusy; })
						.OnKeyDownHandler_Lambda([this](const FGeometry&, const FKeyEvent& Event)
						{
							if (Event.GetKey() == EKeys::Tab && Revision->GetText().IsEmptyOrWhitespace())
							{
								Revision->SetText(LOCTEXT("RevisionHint", "Land in a deeper crouch"));
								return FReply::Handled();
							}
							return FReply::Unhandled();
						})
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(8, 0, 0, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("Revise", "수정해 다시 생성"))
						.IsEnabled_Lambda([this] {
							if (bBusy || !bCanRevise) return false;
							if (!IsReady()) return false;
							if (TargetMeshPath.IsEmpty()) return false;
							return !Revision->GetText().IsEmptyOrWhitespace();
						})
						.OnClicked_Lambda([this] {
							return RunAction(TEXT("revise"), Revision->GetText().ToString(), Duration->GetText().ToString());
						})
					]
				]
				// 진행 상태: 작업 중에는 스피너와 함께 단계·경과 시간·로그 마지막 줄을 보여 준다.
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Top).Padding(0, 2, 8, 0)
					[
						SNew(SCircularThrobber)
						.Radius(8.0f)
						.Visibility_Lambda([this] { return bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
					]
					+ SHorizontalBox::Slot().FillWidth(1)
					[
						SNew(STextBlock)
						.AutoWrapText(true)
						.Text_Lambda([this] { return Status; })
					]
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(STextBlock).Text(LOCTEXT("Name", "AnimSequence name"))
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SAssignNew(AssetName, SEditableTextBox)
					.Text(FText::FromString(Settings->DefaultAssetName))
				]
				+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left)
				[
					SNew(SButton)
					.Text(LOCTEXT("Save", "Save AnimSequence"))
					.IsEnabled_Lambda([this] { return !bBusy && bHasPreview && !AssetName->GetText().IsEmptyOrWhitespace(); })
					.OnClicked_Lambda([this] { return RunAction(TEXT("save"), AssetName->GetText().ToString()); })
				]
				]
			]
		];

		Preview->SetFollowBone(Settings->PreviewFollowBone);
		Status = LOCTEXT("WaitingForPython", "Waiting for Unreal Python…");
		RegisterActiveTimer(0.5f, FWidgetActiveTimerDelegate::CreateSP(this, &SKimodoPanel::Poll));
	}

private:
	bool IsReady() const { return SetupState == TEXT("ready"); }

	/** 설치 안내에 보여 줄 Python 폴더. 환경변수나 설정이 있으면 그 경로를 쓴다. */
	FString GetInstallLocationText() const
	{
		const UKimodoUserSettings* UserSettings = GetDefault<UKimodoUserSettings>();
		const FString Python = ResolveLocalPath(TEXT("KIMODO_PYTHON"), TEXT("ECLIPSE_KIMODO_PYTHON"), UserSettings->PythonExecutable.FilePath, GetManagedPython());
		return FPaths::GetPath(FPaths::GetPath(Python));
	}

	/** Python 패널에 넘길 요청. 설정과 패널 선택값을 함께 담는다. */
	FString MakePayload(const TCHAR* Action, const FString& Value, const FString& DurationValue) const
	{
		const UKimodoSettings* Settings = GetDefault<UKimodoSettings>();
		const UKimodoUserSettings* UserSettings = GetDefault<UKimodoUserSettings>();

		TArray<FString> AlignBones;
		for (const FName& Bone : Settings->AutoAlignBones)
		{
			AlignBones.Add(Bone.ToString());
		}

		// 해석 CLI 목록은 설정이 비어 있을 때만 환경변수에서 읽는다.
		TArray<FString> Agents = UserSettings->InterpretAgents;
		if (Agents.IsEmpty())
		{
			FString AgentText = FPlatformMisc::GetEnvironmentVariable(TEXT("KIMODO_AGENTS"));
			if (AgentText.IsEmpty()) AgentText = FPlatformMisc::GetEnvironmentVariable(TEXT("ECLIPSE_KIMODO_AGENTS"));
			AgentText.ParseIntoArray(Agents, TEXT(","), true);
		}

		const FString DefaultWorkDir = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("Kimodo"));

		const TSharedRef<FJsonObject> Payload = MakeShared<FJsonObject>();
		Payload->SetStringField(TEXT("action"), Action);
		Payload->SetStringField(TEXT("value"), Value);
		Payload->SetStringField(TEXT("duration"), DurationValue);
		Payload->SetStringField(TEXT("seed"), Seed.IsValid() ? Seed->GetText().ToString().TrimStartAndEnd() : FString());
		Payload->SetStringField(TEXT("target_mesh"), TargetMeshPath);
		Payload->SetStringField(TEXT("target_rig"), TargetRigPath);
		Payload->SetStringField(TEXT("retargeter"), RetargeterPath);
		Payload->SetStringField(TEXT("output_folder"), GetOutputFolder());
		Payload->SetStringField(TEXT("model"), Settings->ModelName);
		Payload->SetArrayField(TEXT("align_bones"), ToJsonArray(AlignBones));
		Payload->SetStringField(TEXT("python"), ResolveLocalPath(TEXT("KIMODO_PYTHON"), TEXT("ECLIPSE_KIMODO_PYTHON"), UserSettings->PythonExecutable.FilePath, GetManagedPython()));
		Payload->SetStringField(TEXT("model_cache"), ResolveLocalPath(TEXT("KIMODO_MODEL_CACHE"), TEXT("ECLIPSE_KIMODO_MODEL_CACHE"), UserSettings->ModelCacheDirectory.Path, GetManagedInstallRoot() / TEXT("model-cache")));
		Payload->SetStringField(TEXT("bootstrap_python"), GetBootstrapPython());
		Payload->SetStringField(TEXT("work_dir"), ResolveLocalPath(TEXT("KIMODO_WORK_DIR"), TEXT("ECLIPSE_KIMODO_WORK_DIR"), UserSettings->WorkingDirectory.Path, DefaultWorkDir));
		Payload->SetArrayField(TEXT("agents"), ToJsonArray(Agents));
		Payload->SetStringField(TEXT("text_encoder_device"),
			UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::CUDA ? TEXT("cuda")
			: UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::Auto ? TEXT("auto")
			: TEXT("cpu"));

		FString Json;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
		FJsonSerializer::Serialize(Payload, Writer);
		return Json;
	}

	FReply RunAction(const TCHAR* Action, const FString& Value = FString(), const FString& DurationValue = FString(), bool bAllowAutoPlay = true)
	{
		IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
		if (!Python || !Python->IsPythonInitialized())
		{
			Status = LOCTEXT("PythonUnavailable", "Unreal Python is unavailable.");
			return FReply::Handled();
		}

		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(KimodoPluginName);
		if (!Plugin)
		{
			Status = LOCTEXT("PluginMissing", "The KimodoAnimation plugin folder could not be found.");
			return FReply::Handled();
		}

		// 설치 위치와 관계없이 플러그인 폴더의 스크립트를 실행한다.
		// 요청은 JSON으로 묶어 Base64로 넘겨 한글이나 따옴표도 그대로 전달한다.
		const FString Script = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("Scripts/panel.py"));
		const FString EncodedScript = FBase64::Encode(Script);
		const FString EncodedPayload = FBase64::Encode(MakePayload(Action, Value, DurationValue));

		// 1) 패널 스크립트를 한 번만 불러 둔다.
		FPythonCommandEx Load;
		Load.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
		Load.FileExecutionScope = EPythonFileExecutionScope::Public;
		Load.Flags = EPythonCommandFlags::Unattended;
		Load.Command = FString::Printf(
			TEXT("import base64, runpy\n")
			TEXT("if 'kimodo_animation_panel' not in globals():\n")
			TEXT("    kimodo_animation_panel = runpy.run_path(base64.b64decode('%s').decode('utf-8'))\n"),
			*EncodedScript);

		// 2) 요청을 처리하고 상태 한 줄(Base64)을 평가 결과로 받는다. print를 쓰지 않아 Output Log에 쌓이지 않는다.
		FPythonCommandEx Command;
		Command.ExecutionMode = EPythonCommandExecutionMode::EvaluateStatement;
		Command.FileExecutionScope = EPythonFileExecutionScope::Public;
		Command.Flags = EPythonCommandFlags::Unattended;
		Command.Command = FString::Printf(TEXT("kimodo_animation_panel['dispatch']('%s')"), *EncodedPayload);

		const bool bSaving = FCString::Strcmp(Action, TEXT("save")) == 0;
		const float PreviousTime = Preview->GetTime();
		const bool bWasPlaying = Preview->IsPlaying();
		if (bSaving)
		{
			// 저장 후 Python이 임시 에셋을 정리하기 전에 컴포넌트 참조를 해제한다.
			Preview->SetClip(nullptr);
		}
		FString Line;
		if (Python->ExecPythonCommandEx(Load) && Python->ExecPythonCommandEx(Command) && DecodeStatusLine(Command.CommandResult, Line))
		{
			{
				TArray<FString> Fields;
				Line.ParseIntoArray(Fields, TEXT("|"), false);
				if (Fields.Num() != 10)
				{
					bBusy = false;
					Status = LOCTEXT("PythonMismatch", "The Kimodo panel script and plugin do not match. Restart the editor.");
					return FReply::Handled();
				}

				bBusy = Fields[0] == TEXT("1");
				bHasPreview = Fields[1] == TEXT("1");
				bCanRevise = Fields[4] == TEXT("1");
				bCanRestore = Fields[8] == TEXT("1");
				LastSeed = Fields[9].TrimStartAndEnd();

				// 설치 상태: installing, ready, missing:<빠진 항목>
				const FString SetupField = Fields[7].TrimStartAndEnd();
				SetupState = SetupField;
				SetupMissing.Reset();
				FString StateName;
				FString MissingItem;
				if (SetupField.Split(TEXT(":"), &StateName, &MissingItem))
				{
					SetupState = StateName;
					SetupMissing = MissingItem;
				}

				// 한국어 해석과 Kimodo에 실제로 전달한 영어 프롬프트를 함께 보여 준다.
				const FString Description = Fields[5].TrimStartAndEnd();
				const FString EnglishPrompt = Fields[6].TrimStartAndEnd();
				Interpretation = FText::GetEmpty();
				if (!Description.IsEmpty() && !EnglishPrompt.IsEmpty())
				{
					Interpretation = FText::Format(LOCTEXT("InterpretationWithPrompt", "동작 해석 · {0}\nKimodo 프롬프트 · {1}"),
						FText::FromString(Description), FText::FromString(EnglishPrompt));
				}
				else if (!Description.IsEmpty())
				{
					Interpretation = FText::Format(LOCTEXT("Interpretation", "동작 해석 · {0}"), FText::FromString(Description));
				}
				else if (!EnglishPrompt.IsEmpty())
				{
					// 해석 없이 직접 입력한 프롬프트(수정 요청은 이어 붙인 결과)를 보여 준다.
					Interpretation = FText::Format(LOCTEXT("PromptOnly", "Kimodo 프롬프트 · {0}"), FText::FromString(EnglishPrompt));
				}
				const FString ClipPath = bBusy ? FString() : Fields[3].TrimStartAndEnd();
				if (bSaving || ClipPath != PreviewPath)
				{
					PreviewPath = ClipPath;
					UAnimSequence* Clip = nullptr;
					if (!ClipPath.IsEmpty())
					{
						const FSoftObjectPath ObjectPath(ClipPath);
						Clip = Cast<UAnimSequence>(ObjectPath.ResolveObject());
						if (!Clip) Clip = Cast<UAnimSequence>(ObjectPath.TryLoad());
					}
					const bool bAutoPlay = bSaving ? bWasPlaying : bAllowAutoPlay;
					Preview->SetClip(Clip, bSaving ? PreviousTime : 0.0f, bAutoPlay);
				}
				if (!bBusy && bHasPreview)
				{
					// 패널에서 저장하기 전에는 Save All에 임시 에셋을 포함하지 않는다.
					const FString PreviewPrefix = GetOutputFolder() / TEXT("_Preview_");
					for (TObjectIterator<UPackage> Package; Package; ++Package)
					{
						if (Package->IsDirty() && Package->GetName().StartsWith(PreviewPrefix))
						{
							Package->SetDirtyFlag(false);
						}
					}
				}
				Status = FText::FromString(Fields[2]);
				return FReply::Handled();
			}
		}

		bBusy = false;
		Status = LOCTEXT("PythonError", "Kimodo could not run. See the Unreal Output Log.");
		return FReply::Handled();
	}

	/** EvaluateStatement 결과는 Python repr('...')이므로 따옴표를 벗기고 Base64(UTF-8)를 푼다. */
	static bool DecodeStatusLine(const FString& Result, FString& OutLine)
	{
		FString Encoded = Result.TrimStartAndEnd();
		if (Encoded.Len() < 2 || !(Encoded.StartsWith(TEXT("'")) && Encoded.EndsWith(TEXT("'")))) return false;
		Encoded = Encoded.Mid(1, Encoded.Len() - 2);

		TArray<uint8> Bytes;
		if (!FBase64::Decode(Encoded, Bytes)) return false;
		const FUTF8ToTCHAR Converted(reinterpret_cast<const ANSICHAR*>(Bytes.GetData()), Bytes.Num());
		OutLine = FString::ConstructFromPtrSize(Converted.Get(), Converted.Length());
		return true;
	}

	EActiveTimerReturnType Poll(double CurrentTime, float DeltaTime)
	{
		// 에디터가 복원한 탭은 Python 초기화보다 먼저 열릴 수 있다.
		if (bNeedsInitialState)
		{
			const IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
			if (!Python || !Python->IsPythonInitialized()) return EActiveTimerReturnType::Continue;

			bNeedsInitialState = false;
			RunAction(TEXT("poll"), FString(), FString(), false);
		}
		else if (bBusy)
		{
			RunAction(TEXT("poll"));
		}
		return EActiveTimerReturnType::Continue;
	}

	TSharedPtr<SMultiLineEditableTextBox> Prompt;
	TSharedPtr<SEditableTextBox> Duration;
	TSharedPtr<SEditableTextBox> Seed;
	TSharedPtr<SEditableTextBox> Revision;
	TSharedPtr<SEditableTextBox> AssetName;
	TSharedPtr<SKimodoPreview> Preview;
	TSharedPtr<SEditableTextBox> Token;
	FString TargetMeshPath;
	FString TargetRigPath;
	FString RetargeterPath;
	FString PreviewPath;
	FString LastSeed;
	FString SetupState;
	FString SetupMissing;
	FText Status;
	FText Interpretation;
	bool bNeedsInitialState = true;
	bool bBusy = false;
	bool bHasPreview = false;
	bool bCanRevise = false;
	bool bCanRestore = false;
};
}

class FKimodoAnimationModule : public IModuleInterface
{
public:
	void StartupModule() override
	{
		FGlobalTabmanager::Get()->RegisterNomadTabSpawner(KimodoTabName,
			FOnSpawnTab::CreateRaw(this, &FKimodoAnimationModule::SpawnKimodoTab))
			.SetDisplayName(LOCTEXT("Tab", "Kimodo Animation"))
			.SetMenuType(ETabSpawnerMenuType::Hidden);
		UToolMenus::RegisterStartupCallback(
			FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FKimodoAnimationModule::RegisterMenus));
	}

	void ShutdownModule() override
	{
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(KimodoTabName);
	}

private:
	TSharedRef<SDockTab> SpawnKimodoTab(const FSpawnTabArgs&)
	{
		return SNew(SDockTab).TabRole(ETabRole::NomadTab)
		[
			SNew(SKimodoPanel)
		];
	}

	void RegisterMenus()
	{
		FToolMenuOwnerScoped Owner(this);
		UToolMenu* Menu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
		Menu->FindOrAddSection(TEXT("KimodoAnimation")).AddMenuEntry(
			KimodoTabName, LOCTEXT("Menu", "Kimodo Animation"),
			LOCTEXT("MenuTip", "Generate and preview an animation for a skeletal mesh from a motion prompt."),
			FSlateIcon(), FUIAction(FExecuteAction::CreateLambda([] {
				FGlobalTabmanager::Get()->TryInvokeTab(KimodoTabName);
			})));
	}
};

IMPLEMENT_MODULE(FKimodoAnimationModule, KimodoAnimation)

#undef LOCTEXT_NAMESPACE
