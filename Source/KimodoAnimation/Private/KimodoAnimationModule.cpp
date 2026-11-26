#include "KimodoSettings.h"
#include "SKimodoPreview.h"

#include "Animation/AnimSequence.h"
#include "ContentBrowserModule.h"
#include "IContentBrowserSingleton.h"
#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "Framework/Docking/TabManager.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "Framework/Notifications/NotificationManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "InputCoreTypes.h"
#include "Interfaces/IPluginManager.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Base64.h"
#include "Misc/Paths.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "PropertyCustomizationHelpers.h"
#include "Retargeter/IKRetargeter.h"
#include "Rig/IKRigDefinition.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Styling/AppStyle.h"
#include "ThumbnailRendering/ThumbnailManager.h"
#include "ToolMenus.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectIterator.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SComboButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SHyperlink.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Input/SSegmentedControl.h"
#include "Widgets/Input/SSpinBox.h"
#include "Widgets/SLeafWidget.h"
#include "Framework/Application/SlateApplication.h"
#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Input/SSlider.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Images/SThrobber.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Layout/SSplitter.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "Widgets/Notifications/SProgressBar.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/SListView.h"
#include "Widgets/Views/STableRow.h"

#define LOCTEXT_NAMESPACE "KimodoAnimation"

namespace
{
const FName KimodoTabName(TEXT("KimodoAnimation"));
/** 테이크 결과 에셋의 마운트 경로. 실제 파일은 Saved/Kimodo/TakeAssets에 있다. */
const TCHAR* TakeRootPath = TEXT("/KimodoTakes/");

FString GetTakeRootDir()
{
	return FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("Kimodo/TakeAssets/"));
}
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

/** 패널의 텍스트 인코더 선택지. 프로젝트 설정의 Text Encoder Device와 같은 값이다. */
struct FEncoderOption
{
	EKimodoTextEncoderDevice Value;
	FText Label;
	FText Tip;
};

const TArray<FEncoderOption>& GetEncoderOptions()
{
	static const TArray<FEncoderOption> Options = {
		{EKimodoTextEncoderDevice::LowMemory, LOCTEXT("EncoderLowMemory", "Low memory (fast)"),
			LOCTEXT("EncoderLowMemoryTip", "Streams Llama 3 one layer at a time on the CPU. About 1-2 GB RAM. Joint positions differ from the original by about 0.5 cm on average.")},
		{EKimodoTextEncoderDevice::LowMemoryExact, LOCTEXT("EncoderLowMemoryExact", "Low memory (exact)"),
			LOCTEXT("EncoderLowMemoryExactTip", "Same memory as Low memory, and results identical to the original CPU encoder. Encoding takes a little longer.")},
		{EKimodoTextEncoderDevice::CPU, LOCTEXT("EncoderCPU", "Full model on CPU"),
			LOCTEXT("EncoderCPUTip", "Loads the whole Llama 3 8B into RAM (about 16 GB) for every generation.")},
		{EKimodoTextEncoderDevice::CUDA, LOCTEXT("EncoderCUDA", "Full model on GPU"),
			LOCTEXT("EncoderCUDATip", "Needs an NVIDIA GPU with 16 GB+ VRAM. Fails with out-of-memory otherwise.")},
		{EKimodoTextEncoderDevice::Auto, LOCTEXT("EncoderAuto", "Full model, GPU if available"),
			LOCTEXT("EncoderAutoTip", "Uses the GPU when CUDA is available (VRAM size is not checked), else the CPU.")},
	};
	return Options;
}

const FEncoderOption& GetEncoderOption(EKimodoTextEncoderDevice Value)
{
	for (const FEncoderOption& Option : GetEncoderOptions())
	{
		if (Option.Value == Value) return Option;
	}
	return GetEncoderOptions()[0];
}

/** 손 키프레임 한 개: 시각(초), 왼손 여부, 캐릭터 기준 오프셋(cm: 오른쪽, 위, 앞). */
struct FHandKey
{
	float Time = 0.0f;
	EKimodoKeyBone Bone = EKimodoKeyBone::RightHand;
	float Right = 0.0f;
	float Up = 0.0f;
	float Forward = 0.0f;

	bool operator==(const FHandKey& Other) const
	{
		return Time == Other.Time && Bone == Other.Bone && Right == Other.Right && Up == Other.Up && Forward == Other.Forward;
	}
};

static const TCHAR* const KeyBoneNames[] = { TEXT("right_hand"), TEXT("left_hand"), TEXT("right_foot"), TEXT("left_foot"), TEXT("hips") };

/** Body keys → JSON(패널 형식: time 초, bone 이름, offset [오른쪽, 위, 앞] cm). */
TArray<TSharedPtr<FJsonValue>> HandKeysToJson(const TArray<FHandKey>& InKeys)
{
	TArray<TSharedPtr<FJsonValue>> Keys;
	for (const FHandKey& Key : InKeys)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetNumberField(TEXT("time"), Key.Time);
		Item->SetStringField(TEXT("bone"), KeyBoneNames[FMath::Clamp(static_cast<int32>(Key.Bone), 0, 4)]);
		TArray<TSharedPtr<FJsonValue>> Offset;
		Offset.Add(MakeShared<FJsonValueNumber>(Key.Right));
		Offset.Add(MakeShared<FJsonValueNumber>(Key.Up));
		Offset.Add(MakeShared<FJsonValueNumber>(Key.Forward));
		Item->SetArrayField(TEXT("offset"), Offset);
		Keys.Add(MakeShared<FJsonValueObject>(Item));
	}
	return Keys;
}

/** JSON(패널 형식) → Body keys. 모르는 부위는 건너뛴다. */
TArray<FHandKey> HandKeysFromJson(const TArray<TSharedPtr<FJsonValue>>& Items)
{
	TArray<FHandKey> Keys;
	for (const TSharedPtr<FJsonValue>& Value : Items)
	{
		const TSharedPtr<FJsonObject> Object = Value.IsValid() ? Value->AsObject() : nullptr;
		if (!Object.IsValid()) continue;
		const FString BoneName = Object->GetStringField(TEXT("bone"));
		int32 Bone = INDEX_NONE;
		for (int32 Index = 0; Index < 5; ++Index) if (BoneName == KeyBoneNames[Index]) Bone = Index;
		const TArray<TSharedPtr<FJsonValue>>* Offset = nullptr;
		if (Bone == INDEX_NONE || !Object->TryGetArrayField(TEXT("offset"), Offset) || Offset->Num() < 3) continue;
		FHandKey Key;
		Key.Time = static_cast<float>(Object->GetNumberField(TEXT("time")));
		Key.Bone = static_cast<EKimodoKeyBone>(Bone);
		Key.Right = static_cast<float>((*Offset)[0]->AsNumber());
		Key.Up = static_cast<float>((*Offset)[1]->AsNumber());
		Key.Forward = static_cast<float>((*Offset)[2]->AsNumber());
		Keys.Add(Key);
	}
	return Keys;
}

/**
 * Body keys 타임라인: 부위별 트랙(RH/LH/RF/LF/Hip)에 키를 ◆로 보여 준다(시퀀서처럼).
 * ◆ 클릭 = 선택(재생 위치 이동), ◆ 좌우 드래그 = 시각 변경, 트랙 더블클릭 = 그 자리에 키 추가,
 * 빈 곳 클릭·드래그 = 재생 위치 이동, Delete = 선택한 키 삭제, K = 재생 위치에 키 추가.
 */
class SKimodoKeyTimeline : public SLeafWidget
{
public:
	SLATE_BEGIN_ARGS(SKimodoKeyTimeline) {}
	SLATE_END_ARGS()

	void Construct(const FArguments&) {}

	TFunction<const TArray<FHandKey>&()> GetKeys;
	TFunction<int32()> GetSelected;
	TFunction<float()> GetLength;
	TFunction<float()> GetTime;
	TFunction<void(int32)> OnSelect;
	TFunction<void(int32, float)> OnMoveKey;
	TFunction<void(EKimodoKeyBone, float)> OnAddKey;
	/** K로 추가할 때(고른 키가 없으면) 쓸 부위: 키 바에서 고른 부위. */
	TFunction<EKimodoKeyBone()> GetNewKeyBone;
	TFunction<void(int32)> OnRemoveKey;
	TFunction<void(float)> OnScrub;

	static constexpr float LabelWidth = 34.0f;
	static constexpr float MinRowHeight = 15.0f;
	static constexpr int32 Rows = 5;

	FVector2D ComputeDesiredSize(float) const override { return FVector2D(200.0f, Rows * MinRowHeight + 4.0f); }
	bool SupportsKeyboardFocus() const override { return true; }

	int32 OnPaint(const FPaintArgs& Args, const FGeometry& Geometry, const FSlateRect& Culling, FSlateWindowElementList& Out,
		int32 Layer, const FWidgetStyle& Style, bool bParentEnabled) const override
	{
		const FSlateBrush* White = FAppStyle::GetBrush("WhiteBrush");
		const FVector2D Size = Geometry.GetLocalSize();
		const float Length = GetLength ? GetLength() : 0.0f;
		const FSlateFontInfo Font = FCoreStyle::GetDefaultFontStyle("Regular", 8);
		const float RowHeight = RowHeightFor(Size.Y);
		static const TCHAR* Labels[] = { TEXT("RH"), TEXT("LH"), TEXT("RF"), TEXT("LF"), TEXT("Hip") };
		for (int32 Row = 0; Row < Rows; ++Row)
		{
			const float Y = 2.0f + Row * RowHeight;
			FSlateDrawElement::MakeBox(Out, Layer, Geometry.ToPaintGeometry(FVector2f(Size.X - LabelWidth, RowHeight - 1.0f), FSlateLayoutTransform(FVector2f(LabelWidth, Y))),
				White, ESlateDrawEffect::None, FLinearColor(1, 1, 1, Row % 2 ? 0.03f : 0.06f));
			FSlateDrawElement::MakeText(Out, Layer + 1, Geometry.ToPaintGeometry(FVector2f(LabelWidth, RowHeight), FSlateLayoutTransform(FVector2f(2.0f, Y + 1.0f))),
				FString(Labels[Row]), Font, ESlateDrawEffect::None, FLinearColor(0.6f, 0.6f, 0.6f));
		}
		if (Length <= 0.0f) return Layer + 2;

		// 0.5초 눈금
		for (float T = 0.0f; T <= Length + 1e-3f; T += 0.5f)
		{
			const float X = TimeToX(T, Size.X, Length);
			TArray<FVector2f> Tick = { FVector2f(X, 0.0f), FVector2f(X, Size.Y) };
			FSlateDrawElement::MakeLines(Out, Layer + 1, Geometry.ToPaintGeometry(), Tick, ESlateDrawEffect::None,
				FLinearColor(1, 1, 1, FMath::IsNearlyZero(FMath::Fmod(T, 1.0f), 1e-3f) ? 0.15f : 0.06f));
		}
		// 키 ◆: 고른 키는 노랑·크게, 나머지는 하늘색
		const TArray<FHandKey>& Keys = GetKeys();
		const int32 Selected = GetSelected ? GetSelected() : INDEX_NONE;
		for (int32 Index = 0; Index < Keys.Num(); ++Index)
		{
			const bool bSelected = Index == Selected;
			const float Diamond = bSelected ? 10.0f : 8.0f;
			const FVector2f Center(TimeToX(Keys[Index].Time, Size.X, Length), RowCenter(static_cast<int32>(Keys[Index].Bone), RowHeight));
			FSlateDrawElement::MakeRotatedBox(Out, Layer + 2,
				Geometry.ToPaintGeometry(FVector2f(Diamond, Diamond), FSlateLayoutTransform(Center - FVector2f(Diamond * 0.5f))),
				White, ESlateDrawEffect::None, UE_PI / 4.0f, TOptional<FVector2f>(), FSlateDrawElement::RelativeToElement,
				bSelected ? FLinearColor(1.0f, 0.8f, 0.1f) : FLinearColor(0.2f, 0.75f, 1.0f));
		}
		// 재생 위치
		const float PlayX = TimeToX(GetTime ? GetTime() : 0.0f, Size.X, Length);
		TArray<FVector2f> Playhead = { FVector2f(PlayX, 0.0f), FVector2f(PlayX, Size.Y) };
		FSlateDrawElement::MakeLines(Out, Layer + 3, Geometry.ToPaintGeometry(), Playhead, ESlateDrawEffect::None, FLinearColor(1.0f, 0.3f, 0.2f), true, 1.5f);
		return Layer + 4;
	}

	FReply OnMouseButtonDown(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!GetLength || GetLength() <= 0.0f) return FReply::Unhandled();
		if (Event.GetEffectingButton() == EKeys::RightMouseButton) return FReply::Handled();  // 메뉴는 버튼을 뗄 때
		if (Event.GetEffectingButton() != EKeys::LeftMouseButton) return FReply::Unhandled();
		const FVector2D Local = Geometry.AbsoluteToLocal(Event.GetScreenSpacePosition());
		DraggingKey = HitKey(Local, Geometry.GetLocalSize());
		if (DraggingKey != INDEX_NONE)
		{
			if (OnSelect) OnSelect(DraggingKey);
		}
		else if (Local.X >= LabelWidth && OnScrub)
		{
			bScrubbing = true;
			OnScrub(XToTime(Local.X, Geometry.GetLocalSize().X));
		}
		return FReply::Handled().CaptureMouse(SharedThis(this)).SetUserFocus(SharedThis(this), EFocusCause::Mouse);
	}

	FReply OnMouseMove(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!HasMouseCapture()) return FReply::Unhandled();
		const FVector2D Local = Geometry.AbsoluteToLocal(Event.GetScreenSpacePosition());
		const float Time = XToTime(Local.X, Geometry.GetLocalSize().X);
		if (DraggingKey != INDEX_NONE && OnMoveKey) OnMoveKey(DraggingKey, Time);
		else if (bScrubbing && OnScrub) OnScrub(Time);
		return FReply::Handled();
	}

	FReply OnMouseButtonUp(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (Event.GetEffectingButton() == EKeys::RightMouseButton && GetLength && GetLength() > 0.0f)
		{
			OpenContextMenu(Geometry, Event);
			return FReply::Handled();
		}
		DraggingKey = INDEX_NONE;
		bScrubbing = false;
		return HasMouseCapture() ? FReply::Handled().ReleaseMouseCapture() : FReply::Unhandled();
	}

	/** 우클릭 메뉴: 그 자리(시각)에 부위별 키 추가, ◆ 위면 그 키 삭제. 클릭한 트랙의 부위가 맨 위. */
	void OpenContextMenu(const FGeometry& Geometry, const FPointerEvent& Event)
	{
		const FVector2D Local = Geometry.AbsoluteToLocal(Event.GetScreenSpacePosition());
		const float Time = XToTime(FMath::Max(static_cast<float>(Local.X), LabelWidth), Geometry.GetLocalSize().X);
		const EKimodoKeyBone RowBone = static_cast<EKimodoKeyBone>(RowAt(Local.Y, Geometry.GetLocalSize().Y));
		const int32 Hit = HitKey(Local, Geometry.GetLocalSize());
		static const TCHAR* Names[] = { TEXT("right hand"), TEXT("left hand"), TEXT("right foot"), TEXT("left foot"), TEXT("hips") };

		FMenuBuilder Menu(true, nullptr);
		Menu.BeginSection(NAME_None, FText::Format(NSLOCTEXT("KimodoTimeline", "AddAt", "Add key at {0}s"), FText::AsNumber(Time)));
		TArray<EKimodoKeyBone> Order = { RowBone };
		for (int32 Bone = 0; Bone < Rows; ++Bone) if (static_cast<EKimodoKeyBone>(Bone) != RowBone) Order.Add(static_cast<EKimodoKeyBone>(Bone));
		for (const EKimodoKeyBone Bone : Order)
		{
			Menu.AddMenuEntry(FText::Format(NSLOCTEXT("KimodoTimeline", "AddPart", "Add {0} key"), FText::FromString(Names[static_cast<int32>(Bone)])),
				FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([this, Bone, Time] { if (OnAddKey) OnAddKey(Bone, Time); })));
		}
		Menu.EndSection();
		if (Hit != INDEX_NONE)
		{
			Menu.AddMenuSeparator();
			Menu.AddMenuEntry(NSLOCTEXT("KimodoTimeline", "DeleteKey", "Delete this key"), FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([this, Hit] { if (OnRemoveKey) OnRemoveKey(Hit); })));
		}
		FSlateApplication::Get().PushMenu(SharedThis(this), FWidgetPath(), Menu.MakeWidget(), Event.GetScreenSpacePosition(),
			FPopupTransitionEffect::ContextMenu);
	}

	FReply OnMouseButtonDoubleClick(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		const FVector2D Local = Geometry.AbsoluteToLocal(Event.GetScreenSpacePosition());
		const int32 Row = RowAt(Local.Y, Geometry.GetLocalSize().Y);
		if (Local.X >= LabelWidth && OnAddKey && HitKey(Local, Geometry.GetLocalSize()) == INDEX_NONE)
		{
			OnAddKey(static_cast<EKimodoKeyBone>(Row), XToTime(Local.X, Geometry.GetLocalSize().X));
		}
		return FReply::Handled();
	}

	FReply OnKeyDown(const FGeometry& Geometry, const FKeyEvent& Event) override
	{
		const int32 Selected = GetSelected ? GetSelected() : INDEX_NONE;
		if ((Event.GetKey() == EKeys::Delete || Event.GetKey() == EKeys::BackSpace) && Selected != INDEX_NONE && OnRemoveKey)
		{
			OnRemoveKey(Selected);
			return FReply::Handled();
		}
		if (Event.GetKey() == EKeys::K && OnAddKey && GetTime)
		{
			const TArray<FHandKey>& Keys = GetKeys();
			OnAddKey(Keys.IsValidIndex(Selected) ? Keys[Selected].Bone : (GetNewKeyBone ? GetNewKeyBone() : EKimodoKeyBone::RightHand), GetTime());
			return FReply::Handled();
		}
		return FReply::Unhandled();
	}

private:
	float TimeToX(float Time, float Width, float Length) const
	{
		return LabelWidth + (Width - LabelWidth - 6.0f) * FMath::Clamp(Time / Length, 0.0f, 1.0f);
	}
	float XToTime(float X, float Width) const
	{
		const float Length = GetLength();
		// 0.1초 단위로 맞춘다(키 시각 칸과 같은 단위).
		const float Raw = (X - LabelWidth) / FMath::Max(Width - LabelWidth - 6.0f, 1.0f) * Length;
		return FMath::Clamp(FMath::RoundToFloat(Raw * 10.0f) / 10.0f, 0.0f, Length);
	}
	/** 아래 영역을 키우면 트랙도 같이 커진다. */
	/** 영역이 작아도 5줄이 모두 보이게 줄인다(트랙이 잘려 RH만 보이지 않도록). */
	static float RowHeightFor(float Height) { return FMath::Max(8.0f, (Height - 4.0f) / Rows); }
	static float RowCenter(int32 Row, float RowHeight) { return 2.0f + Row * RowHeight + RowHeight * 0.5f - 0.5f; }
	static int32 RowAt(float Y, float Height) { return FMath::Clamp(FMath::FloorToInt((Y - 2.0f) / RowHeightFor(Height)), 0, Rows - 1); }
	int32 HitKey(const FVector2D& Local, const FVector2D& Size) const
	{
		const float Length = GetLength ? GetLength() : 0.0f;
		if (Length <= 0.0f || !GetKeys) return INDEX_NONE;
		const TArray<FHandKey>& Keys = GetKeys();
		int32 Best = INDEX_NONE;
		float BestDistance = 7.0f;
		for (int32 Index = 0; Index < Keys.Num(); ++Index)
		{
			const FVector2D Center(TimeToX(Keys[Index].Time, Size.X, Length), RowCenter(static_cast<int32>(Keys[Index].Bone), RowHeightFor(Size.Y)));
			const float Distance = FVector2D::Distance(Center, Local);
			if (Distance <= BestDistance)
			{
				BestDistance = Distance;
				Best = Index;
			}
		}
		return Best;
	}

	int32 DraggingKey = INDEX_NONE;
	bool bScrubbing = false;
};

/** 패널의 테이크 목록 한 줄. Python 상태(JSON)의 takes 항목을 그대로 옮긴다. */
struct FKimodoTake
{
	FString Id;
	int32 Number = 0;
	FString Prompt;
	FString Seed;
	double Duration = 0.0;
	bool bStarred = false;
	FString Saved;
	FString Target;
	/** 프리뷰가 메모리에 올라와 있는지. 아니면 고를 때 원본 glTF를 다시 가져온다. */
	bool bLoaded = false;
	/** 이 테이크의 Body keys(편집한 키, 없으면 이 테이크를 만들 때 쓴 키). */
	TArray<FHandKey> Keys;
};

/** 같은 경로의 에셋이 실제로 있는지. 지난번 고른 에셋이 지워졌으면 프로젝트 기본값으로 돌아간다. */
bool AssetExists(const FString& Path)
{
	if (Path.IsEmpty()) return false;
	const FSoftObjectPath ObjectPath(Path);
	return ObjectPath.IsValid() && FPackageName::DoesPackageExist(ObjectPath.GetLongPackageName());
}

FString PreferLast(const FString& Last, const FString& Default)
{
	return AssetExists(Last) ? Last : Default;
}

class SKimodoPanel : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SKimodoPanel) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& Args)
	{
		const UKimodoSettings* Settings = GetDefault<UKimodoSettings>();
		const UKimodoUserSettings* UserSettings = GetDefault<UKimodoUserSettings>();
		// 지난번 패널에서 고른 에셋을 먼저 쓰고, 없으면 프로젝트 설정의 기본값을 쓴다.
		TargetMeshPath = PreferLast(UserSettings->LastTargetMesh, Settings->TargetMesh.ToSoftObjectPath().ToString());
		TargetRigPath = UserSettings->LastTargetMesh == TargetMeshPath
			? PreferLast(UserSettings->LastTargetIKRig, FString())
			: Settings->TargetIKRig.ToSoftObjectPath().ToString();
		RetargeterPath = UserSettings->LastTargetMesh == TargetMeshPath
			? PreferLast(UserSettings->LastRetargeter, FString())
			: Settings->Retargeter.ToSoftObjectPath().ToString();

		ChildSlot
		[
			SNew(SSplitter)
			.Orientation(Orient_Horizontal)
			// 왼쪽(폭 조절 가능): 3D 프리뷰와 재생 바. 오른쪽을 스크롤해도 프리뷰는 움직이지 않는다.
			+ SSplitter::Slot()
			.Value(0.5f)
			.MinSize(280.0f)
			[
				MakePreviewArea()
			]
			// 오른쪽: 단계별 구역(스크롤) + 항상 보이는 실행 바
			+ SSplitter::Slot()
			.Value(0.5f)
			.MinSize(360.0f)
			[
				SNew(SVerticalBox)
				+ SVerticalBox::Slot().FillHeight(1.0f)
				[
					SNew(SScrollBox)
					+ SScrollBox::Slot().Padding(16, 8, 16, 12)
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							MakeInstallArea()
						]
						+ SVerticalBox::Slot().AutoHeight()
						[
							MakeSection(LOCTEXT("SectionTarget", "Target"), MakeTargetArea())
						]
						// 프롬프트 입력은 아래 실행 바(Generate 바로 위)에 있다. 위에는 대상·결과(테이크)·저장만 둔다.
						+ SVerticalBox::Slot().AutoHeight()
						[
							MakeSection(LOCTEXT("SectionTakes", "Takes"), MakeTakesArea(),
								TAttribute<EVisibility>::CreateLambda([this] { return Takes.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible; }))
						]
						+ SVerticalBox::Slot().AutoHeight()
						[
							MakeSection(LOCTEXT("SectionSave", "Save"), MakeSaveArea(),
								TAttribute<EVisibility>::CreateLambda([this] { return bHasPreview ? EVisibility::Visible : EVisibility::Collapsed; }))
						]
					]
				]
				+ SVerticalBox::Slot().AutoHeight()
				[
					MakeActionBar()
				]
			]
		];

		Preview->SetFollowBone(Settings->PreviewFollowBone);
		Status = LOCTEXT("WaitingForPython", "Waiting for Unreal Python…");
		RegisterActiveTimer(0.5f, FWidgetActiveTimerDelegate::CreateSP(this, &SKimodoPanel::Poll));
		BindKeyTimeline();
		RebuildHandKeyRows();
	}

	/** Ctrl+Enter는 패널 어디에 포커스가 있어도 생성한다(프롬프트 칸 안에서도). */
	virtual FReply OnPreviewKeyDown(const FGeometry& Geometry, const FKeyEvent& Event) override
	{
		if (Event.GetKey() == EKeys::Enter && Event.IsControlDown())
		{
			if (CanGenerate()) RunAction(TEXT("generate"), Prompt->GetText().ToString(), Duration->GetText().ToString());
			return FReply::Handled();
		}
		return FReply::Unhandled();
	}

private:
	// ---------------------------------------------------------------- layout

	/** 번호와 제목이 있는 구역. 구역 사이에 얇은 구분선을 둔다. */
	TSharedRef<SWidget> MakeSection(const FText& Title, const TSharedRef<SWidget>& Content,
		TAttribute<EVisibility> Visibility = EVisibility::Visible)
	{
		return SNew(SVerticalBox)
			.Visibility(Visibility)
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 4, 0, 4)
			[
				SNew(STextBlock)
				.Text(Title)
				.Font(FCoreStyle::GetDefaultFontStyle("Bold", 11))
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				SNew(SSeparator).Thickness(1.0f)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
			[
				Content
			];
	}

	TSharedRef<SWidget> MakePreviewArea()
	{
		// 위: 3D 미리보기, 아래: 재생 바·키 추가 바·키 타임라인. 경계를 끌어 아래 영역 높이를 바꾼다.
		return SNew(SSplitter)
			.Orientation(Orient_Vertical)
			+ SSplitter::Slot().Value(0.72f)
			[
				SNew(SBox).Padding(FMargin(8, 8, 4, 0))
				[
				SAssignNew(Preview, SKimodoPreview)
				// 미리보기에서 손 키 핸들을 끌면 그 키의 오프셋(cm)을 바로 바꾼다(칸은 값을 직접 읽어 따라온다).
				.OnHandKeyDragged_Lambda([this](int32 Index, const FVector& Offset) {
					if (!HandKeys.IsValidIndex(Index)) return;
					HandKeys[Index].Right = FMath::RoundToFloat(Offset.X);
					HandKeys[Index].Up = FMath::RoundToFloat(Offset.Y);
					HandKeys[Index].Forward = FMath::RoundToFloat(Offset.Z);
				})
				]
			]
			// 재생 바 + 키 바 + 트랙 5줄이 다 보이는 높이가 최소
			+ SSplitter::Slot().Value(0.28f).MinSize(130.0f)
			[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(8, 6, 4, 4)
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
				// 재생 중 카메라 추적. 기본은 꺼 두어 카메라가 사용자가 둔 자리에 머문다.
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 10, 0)
				[
					SNew(SCheckBox)
					.ToolTipText(LOCTEXT("FollowTip", "Keep the camera centered on the character while it plays."))
					.IsChecked_Lambda([this] { return Preview->IsFollowEnabled() ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
					.OnCheckStateChanged_Lambda([this](ECheckBoxState State) { Preview->SetFollowEnabled(State == ECheckBoxState::Checked); })
					[
						SNew(STextBlock).Text(LOCTEXT("Follow", "Follow"))
					]
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
			// 키 추가 바: 부위 고르기 + Add Key(재생 위치에)
			+ SVerticalBox::Slot().AutoHeight().Padding(8, 0, 4, 4)
			[
				SNew(SHorizontalBox)
				.Visibility_Lambda([this] { return Preview.IsValid() && Preview->GetLength() > 0.0f && bCanRevise ? EVisibility::Visible : EVisibility::Collapsed; })
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
				[
					SNew(STextBlock).Text(LOCTEXT("KeysLabel", "Body keys")).ColorAndOpacity(FSlateColor::UseSubduedForeground())
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
				[
					SNew(SSegmentedControl<EKimodoKeyBone>)
					.ToolTipText(LOCTEXT("NewKeyPartTip", "Part for Add Key"))
					.Value_Lambda([this] { return NewKeyBone; })
					.OnValueChanged_Lambda([this](EKimodoKeyBone Bone) { NewKeyBone = Bone; })
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::RightHand).Text(LOCTEXT("NewRH", "RH"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::LeftHand).Text(LOCTEXT("NewLH", "LH"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::RightFoot).Text(LOCTEXT("NewRF", "RF"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::LeftFoot).Text(LOCTEXT("NewLF", "LF"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::Hips).Text(LOCTEXT("NewHip", "Hip"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(SButton)
					.Text(LOCTEXT("AddKeyButton", "+ Add Key"))
					.ToolTipText(LOCTEXT("AddKeyButtonTip", "Add a key for the chosen part at the playhead (K). Right-click the timeline for more."))
					.OnClicked_Lambda([this] {
						if (KeyTimeline.IsValid() && KeyTimeline->OnAddKey) KeyTimeline->OnAddKey(NewKeyBone, FMath::RoundToFloat(Preview->GetTime() * 10.0f) / 10.0f);
						return FReply::Handled();
					})
				]
			]
			// Body keys 타임라인(Body keys를 쓸 수 있을 때만). 아래 영역을 키우면 트랙도 커진다.
			+ SVerticalBox::Slot().FillHeight(1.0f).Padding(8, 0, 4, 8)
			[
				SNew(SBox)
				.Visibility_Lambda([this] { return Preview.IsValid() && Preview->GetLength() > 0.0f && bCanRevise ? EVisibility::Visible : EVisibility::Collapsed; })
				.ToolTipText(LOCTEXT("TimelineTip", "Body keys: click a diamond to select it, drag it to change its time, double-click a track to add a key.\n"
					"Right-click for the add/delete menu. Click an empty spot to move the playhead. Delete removes the selected key, K adds one at the playhead."))
				[
					SAssignNew(KeyTimeline, SKimodoKeyTimeline)
				]
			]
			];
	}

	/** 타임라인이 패널의 키 목록을 읽고 고치도록 연결한다. */
	void BindKeyTimeline()
	{
		if (!KeyTimeline.IsValid()) return;
		KeyTimeline->GetKeys = [this]() -> const TArray<FHandKey>& { return HandKeys; };
		KeyTimeline->GetSelected = [this] { return SelectedHandKey; };
		KeyTimeline->GetLength = [this] { return Preview.IsValid() ? Preview->GetLength() : 0.0f; };
		KeyTimeline->GetTime = [this] { return Preview.IsValid() ? Preview->GetTime() : 0.0f; };
		KeyTimeline->OnSelect = [this](int32 Index) { SelectHandKey(Index); RebuildHandKeyRows(); };
		KeyTimeline->OnMoveKey = [this](int32 Index, float Time) {
			if (!HandKeys.IsValidIndex(Index)) return;
			// 같은 부위의 다른 키와 같은 시각으로는 옮기지 않는다(◆가 겹치고 Kimodo도 하나만 쓴다).
			if (FindHandKey(HandKeys[Index].Bone, Time, Index) != INDEX_NONE) return;
			HandKeys[Index].Time = Time;
			if (Preview.IsValid()) Preview->Scrub(Time);
			SyncHandKeysToPreview();
		};
		KeyTimeline->OnAddKey = [this](EKimodoKeyBone Bone, float Time) {
			// 그 부위·시각에 이미 키가 있으면 새로 겹쳐 만들지 않고 그 키를 고른다.
			const int32 Existing = FindHandKey(Bone, Time);
			if (Existing != INDEX_NONE)
			{
				SelectHandKey(Existing);
				RebuildHandKeyRows();
				return;
			}
			HandKeys.Add({ Time, Bone, 0.0f, 0.0f, 0.0f });
			SelectedHandKey = HandKeys.Num() - 1;
			NewKeyBone = Bone;
			if (Preview.IsValid()) Preview->Scrub(Time);
			RebuildHandKeyRows();
		};
		KeyTimeline->GetNewKeyBone = [this] { return NewKeyBone; };
		KeyTimeline->OnRemoveKey = [this](int32 Index) {
			if (!HandKeys.IsValidIndex(Index)) return;
			HandKeys.RemoveAt(Index);
			SelectedHandKey = INDEX_NONE;
			RebuildHandKeyRows();
		};
		KeyTimeline->OnScrub = [this](float Time) { if (Preview.IsValid()) Preview->Scrub(Time); };
	}

	/** 이 컴퓨터에 Kimodo가 없으면 설치 안내와 버튼을 보여 준다. */
	TSharedRef<SWidget> MakeInstallArea()
	{
		return SNew(SVerticalBox)
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
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 16)
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
			];
	}

	/** 1. 대상: 세 에셋을 썸네일과 함께 한 줄에 둔다. 고른 값은 다음에 패널을 열 때도 유지한다. */
	TSharedRef<SWidget> MakeTargetArea()
	{
		return SNew(SHorizontalBox)
			.IsEnabled_Lambda([this] { return !bBusy; })
			+ SHorizontalBox::Slot().FillWidth(1).Padding(0, 0, 8, 0)
			[
				MakeAssetPicker(LOCTEXT("TargetMesh", "Target mesh"),
					LOCTEXT("TargetMeshTip", "Skeletal mesh the generated motion is retargeted to."),
					USkeletalMesh::StaticClass(), &TargetMeshPath, true)
			]
			+ SHorizontalBox::Slot().FillWidth(1).Padding(0, 0, 8, 0)
			[
				MakeAssetPicker(LOCTEXT("TargetRig", "IK Rig (optional)"),
					LOCTEXT("TargetRigTip", "Leave empty to use an IK Rig for the same skeleton, or generate one automatically."),
					UIKRigDefinition::StaticClass(), &TargetRigPath, false)
			]
			+ SHorizontalBox::Slot().FillWidth(1)
			[
				MakeAssetPicker(LOCTEXT("Retargeter", "Retargeter (optional)"),
					LOCTEXT("RetargeterTip", "A SOMA-to-target IK Retargeter. When set, it replaces automatic chain mapping."),
					UIKRetargeter::StaticClass(), &RetargeterPath, false)
			];
	}

	/** 프롬프트 입력 블록(아래 실행 바 맨 위, Generate 바로 위). 채팅 입력창처럼 항상 보인다.
	 *  1줄: 프롬프트 / 2줄: 단축키 안내 · Duration / 3줄: Advanced(시드·루트 모션·인코더, 접힘 상태 기억). */
	TSharedRef<SWidget> MakePromptArea()
	{
		return SNew(SVerticalBox)
			.IsEnabled_Lambda([this] { return !bBusy; })
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
			[
				// 긴 프롬프트도 실행 바가 너무 커지지 않게 높이를 제한하고, 넘치면 입력칸 안에서 스크롤한다.
				SNew(SBox)
				.MaxDesiredHeight(84.0f)
				[
					SAssignNew(Prompt, SMultiLineEditableTextBox)
					.AutoWrapText(true)
					.HintText(LOCTEXT("PromptHint", "Make a short forward hop and land softly with knees bent"))
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
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 2)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().FillWidth(1).VAlign(VAlign_Center)
				[
					SNew(STextBlock)
					.Text(LOCTEXT("PromptKeys", "English. Tab fills the example, Ctrl+Enter generates."))
					.ColorAndOpacity(FSlateColor::UseSubduedForeground())
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(8, 0, 6, 0)
				[
					SNew(STextBlock).Text(LOCTEXT("Duration", "Duration"))
				]
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SAssignNew(Duration, SEditableTextBox)
					.MinDesiredWidth(56)
					.Text(LOCTEXT("DefaultDuration", "2.0"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(4, 0, 0, 0)
				[
					SNew(STextBlock).Text(LOCTEXT("Seconds", "s"))
				]
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SExpandableArea)
				.AreaTitle(LOCTEXT("Advanced", "Advanced"))
				.InitiallyCollapsed(!GetDefault<UKimodoUserSettings>()->bAdvancedExpanded)
				// 접는 애니메이션 동안 하위 항목(시드·루트 모션)이 잘리지 않고 비쳐 보여 어색하므로 바로 접고 편다.
				.AllowAnimatedTransition(false)
				.OnAreaExpansionChanged_Lambda([](bool bExpanded) {
					UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
					UserSettings->bAdvancedExpanded = bExpanded;
					UserSettings->SaveConfig();
				})
				.Padding(FMargin(0, 6, 0, 0))
				.BodyContent()
				[
					SNew(SVerticalBox)
					// 시드: 비우면 새 생성은 랜덤, 수정은 고른 테이크의 시드. 값을 넣으면 항상 그 시드로 생성한다.
					+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 6)
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
						[
							SNew(SBox).MinDesiredWidth(90)[SNew(STextBlock).Text(LOCTEXT("Seed", "Seed"))]
						]
						+ SHorizontalBox::Slot().AutoWidth()
						[
							SAssignNew(Seed, SEditableTextBox)
							.MinDesiredWidth(110)
							.HintText(LOCTEXT("SeedHint", "random"))
							.ToolTipText(LOCTEXT("SeedTip", "Leave empty for a random seed (revisions keep the selected take's seed). Enter a number to reproduce a result."))
						]
						+ SHorizontalBox::Slot().AutoWidth().Padding(6, 0, 0, 0)
						[
							SNew(SButton)
							.Text(LOCTEXT("UseLastSeed", "Use selected"))
							.ToolTipText(LOCTEXT("UseLastSeedTip", "Fill in the seed of the selected take."))
							.IsEnabled_Lambda([this] { return !LastSeed.IsEmpty(); })
							.OnClicked_Lambda([this] {
								Seed->SetText(FText::FromString(LastSeed));
								return FReply::Handled();
							})
						]
						+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
						[
							SNew(SButton)
							.Text(LOCTEXT("ClearSeed", "Clear"))
							.IsEnabled_Lambda([this] { return !Seed->GetText().IsEmptyOrWhitespace(); })
							.OnClicked_Lambda([this] {
								Seed->SetText(FText::GetEmpty());
								return FReply::Handled();
							})
						]
					]
					// 확산 스텝: 적을수록 빠르고 거칠다. 같은 시드라도 스텝이 다르면 다른 동작이 나온다.
					+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 6)
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
						[
							SNew(SBox).MinDesiredWidth(90)[SNew(STextBlock).Text(LOCTEXT("Steps", "Steps"))]
						]
						+ SHorizontalBox::Slot().AutoWidth()
						[
							SNew(SBox).WidthOverride(110)
							[
								SNew(SSpinBox<int32>)
								.MinValue(1).MaxValue(1000).MinSliderValue(1).MaxSliderValue(200).Delta(1)
								.ToolTipText(LOCTEXT("StepsTip", "Diffusion steps (Kimodo default 100). Fewer steps are faster but rougher.\nThe same seed gives a different motion with a different step count, so keep both to reproduce a take."))
								.Value_Lambda([] { return GetDiffusionSteps(); })
								.OnValueCommitted_Lambda([](int32 Value, ETextCommit::Type) {
									UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
									UserSettings->DiffusionSteps = FMath::Clamp(Value, 1, 1000);
									UserSettings->SaveConfig();
								})
							]
						]
						+ SHorizontalBox::Slot().AutoWidth().Padding(6, 0, 0, 0)
						[
							SNew(SButton)
							.Text(LOCTEXT("DefaultSteps", "Default"))
							.ToolTipText(LOCTEXT("DefaultStepsTip", "Back to 100 steps."))
							.IsEnabled_Lambda([] { return GetDiffusionSteps() != 100; })
							.OnClicked_Lambda([] {
								UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
								UserSettings->DiffusionSteps = 100;
								UserSettings->SaveConfig();
								return FReply::Handled();
							})
						]
					]
					// 루트 모션: Kimodo는 이동을 pelvis에만 넣으므로, 켜면 pelvis 이동으로 root 이동을 만든다.
					+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left).Padding(0, 0, 0, 6)
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
						[
							SNew(SBox).MinDesiredWidth(90)[SNew(STextBlock).Text(LOCTEXT("RootMotion", "Root motion"))]
						]
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
						[
							SNew(SCheckBox)
							.ToolTipText(LOCTEXT("RootMotionTip", "On: the root bone follows the pelvis on the ground and the clip has Enable Root Motion set (for moves that travel).\nOff: the root stays at the origin and the travel stays in the pelvis (for in-place loops).\nApplies to new takes. A custom Retargeter keeps its own root motion settings."))
							.IsChecked_Lambda([] {
								return GetDefault<UKimodoUserSettings>()->bRootMotion ? ECheckBoxState::Checked : ECheckBoxState::Unchecked;
							})
							.OnCheckStateChanged_Lambda([](ECheckBoxState State) {
								UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
								UserSettings->bRootMotion = State == ECheckBoxState::Checked;
								UserSettings->SaveConfig();
							})
						]
					]
					// 텍스트 인코더 방식. 프로젝트 설정(Kimodo Animation (Local))과 같은 값을 바로 바꾼다.
					+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Left)
					[
						SNew(SHorizontalBox)
						+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
						[
							SNew(SBox).MinDesiredWidth(90)[SNew(STextBlock).Text(LOCTEXT("TextEncoder", "Text encoder"))]
						]
						+ SHorizontalBox::Slot().AutoWidth()
						[
							SNew(SComboButton)
							.ToolTipText_Lambda([] { return GetEncoderOption(GetDefault<UKimodoUserSettings>()->TextEncoderDevice).Tip; })
							.OnGetMenuContent_Lambda([this] { return MakeEncoderMenu(); })
							.ButtonContent()
							[
								SNew(STextBlock).Text_Lambda([] {
									return GetEncoderOption(GetDefault<UKimodoUserSettings>()->TextEncoderDevice).Label;
								})
							]
						]
					]
				]
			];
	}

	/** 3. 테이크: 생성할 때마다 한 줄씩 쌓인다. 골라서 비교하고, 고른 테이크로 수정·저장한다. */
	TSharedRef<SWidget> MakeTakesArea()
	{
		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				SNew(SBox)
				.MaxDesiredHeight(190.0f)
				[
					SAssignNew(TakesView, SListView<TSharedPtr<FKimodoTake>>)
					.ListItemsSource(&Takes)
					.SelectionMode(ESelectionMode::Single)
					.OnGenerateRow(this, &SKimodoPanel::MakeTakeRow)
					.OnSelectionChanged_Lambda([this](TSharedPtr<FKimodoTake> Item, ESelectInfo::Type Info)
					{
						// 코드에서 맞춘 선택(Direct)은 Python으로 다시 보내지 않는다.
						if (Info == ESelectInfo::Direct || !Item.IsValid() || Item->Id == SelectedTakeId) return;
						if (bBusy)
						{
							SyncTakeSelection();
							return;
						}
						RunAction(TEXT("select"), Item->Id);
					})				]
			]
			// 고른 테이크의 해석과 실제 Kimodo 프롬프트. 드래그로 선택해 Ctrl+C로 복사할 수 있다.
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				SNew(SExpandableArea)
				.AreaTitle(LOCTEXT("InputPrompt", "Input prompt"))
				.InitiallyCollapsed(true)
				.Padding(FMargin(0, 4, 0, 0))
				.Visibility_Lambda([this] { return Interpretation.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible; })
				.BodyContent()
				[
					SNew(SMultiLineEditableTextBox)
					.IsReadOnly(true)
					.AutoWrapText(true)
					.AlwaysShowScrollbars(false)
					.ToolTipText(LOCTEXT("InterpretationCopyTip", "Drag to select, Ctrl+C to copy."))
					.Text_Lambda([this] { return Interpretation; })
				]
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SHorizontalBox)
				.Visibility_Lambda([this] { return bCanRevise ? EVisibility::Visible : EVisibility::Collapsed; })
				.IsEnabled_Lambda([this] { return !bBusy; })
				+ SHorizontalBox::Slot().FillWidth(1)
				[
					SAssignNew(Revision, SEditableTextBox)
					// 어느 테이크를 기준으로 고치는지(번호·시드) 안내문에 보여 준다. 고른 테이크가 없으면 입력을 막는다.
					.IsEnabled_Lambda([this] { return GetSelectedTake().IsValid(); })
					.HintText_Lambda([this] {
						const TSharedPtr<FKimodoTake> Take = GetSelectedTake();
						return Take.IsValid()
							? FText::Format(LOCTEXT("RevisionHintTake", "Revise #{0} (seed {1}), e.g. Land in a deeper crouch"),
								FText::AsNumber(Take->Number), FText::FromString(Take->Seed))
							: LOCTEXT("RevisionHintNone", "Select a take above to revise it");
					})
					.OnKeyDownHandler_Lambda([this](const FGeometry&, const FKeyEvent& Event)
					{
						if (Event.GetKey() == EKeys::Tab && Revision->GetText().IsEmptyOrWhitespace())
						{
							Revision->SetText(LOCTEXT("RevisionExample", "Land in a deeper crouch"));
							return FReply::Handled();
						}
						if (Event.GetKey() == EKeys::Enter && CanRevise())
						{
							RunAction(TEXT("revise"), Revision->GetText().ToString(), Duration->GetText().ToString());
							return FReply::Handled();
						}
						return FReply::Unhandled();
					})
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(8, 0, 0, 0)
				[
					SNew(SButton)
					.Text_Lambda([this] {
						const TSharedPtr<FKimodoTake> Take = GetSelectedTake();
						return Take.IsValid()
							? FText::Format(LOCTEXT("ReviseTake", "Revise #{0}"), FText::AsNumber(Take->Number))
							: LOCTEXT("Revise", "Revise");
					})
					.ToolTipText(LOCTEXT("ReviseTip", "Regenerate from the selected take with this change (Enter). The result is added as a new take."))
					.IsEnabled_Lambda([this] { return CanRevise(); })
					.OnClicked_Lambda([this] {
						return RunAction(TEXT("revise"), Revision->GetText().ToString(), Duration->GetText().ToString());
					})
				]
			]
			// 손 키프레임: 고른 테이크를 기준으로 손 위치를 정해 다시 생성(Kimodo 손 제약).
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 8, 0, 0)
			[
				MakeHandKeysArea()
			];
	}

	/** Hand keys: 시각·손·오프셋(cm, 캐릭터 기준 오른쪽/위/앞) 목록과 생성 버튼. 기본은 접혀 있다. */
	TSharedRef<SWidget> MakeHandKeysArea()
	{
		return SNew(SExpandableArea)
			.AreaTitle(LOCTEXT("HandKeys", "Body keys (hands, feet, hips)"))
			.InitiallyCollapsed(true)
			.AllowAnimatedTransition(false)
			.Visibility_Lambda([this] { return bCanRevise ? EVisibility::Visible : EVisibility::Collapsed; })
			.Padding(FMargin(0, 6, 0, 0))
			.BodyContent()
			[
				SNew(SVerticalBox)
				.IsEnabled_Lambda([this] { return !bBusy; })
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(STextBlock)
					.AutoWrapText(true)
					.ColorAndOpacity(FSlateColor::UseSubduedForeground())
					.Text(LOCTEXT("HandKeysHelp", "Move a hand, foot or the hips at given times, relative to the selected take's pose (cm: right / up / forward from the character). "
						"Kimodo can pin only these five parts; moving the hips moves the whole body (e.g. lower it to crouch). "
						"Pick a key on the timeline under the preview (diamonds); in the preview, drag the move gizmo (red forward, green right, blue up) to move it. "
						"Each take keeps its own keys. Generate with keys keeps the take's prompt, seed, length and steps and adds a new take. Fewer than 20 keys per part work best."))
				]
				+ SVerticalBox::Slot().AutoHeight()
				[
					SAssignNew(HandKeyRows, SVerticalBox)
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 6, 0, 0)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 6, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("AddHandKey", "+ Key at playhead"))
						.ToolTipText(LOCTEXT("AddHandKeyTip", "Add a key at the preview's current time (pause the preview where you want it), for the part chosen under the preview (RH, LH, RF, LF, Hip)."))
						.OnClicked_Lambda([this] {
							// 프리뷰 아래 키 바에서 고른 부위로 추가한다(타임라인 추가와 같은 경로).
							if (KeyTimeline.IsValid() && KeyTimeline->OnAddKey)
							{
								KeyTimeline->OnAddKey(NewKeyBone, FMath::RoundToFloat(Preview->GetTime() * 10.0f) / 10.0f);
							}
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 6, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("WavePreset", "Big wave preset"))
						.ToolTipText(LOCTEXT("WavePresetTip", "Fill keys for a big, slow hello wave: the right hand swings 30 cm left and right every 0.6 s.\nWorks best on a take where the right hand is already raised."))
						.OnClicked_Lambda([this] {
							HandKeys.Reset();
							const TSharedPtr<FKimodoTake> Take = GetSelectedTake();
							const float Length = Take.IsValid() ? FMath::Max(Take->Duration, 1.5f) : 4.0f;
							int32 Index = 0;
							for (float Time = 0.9f; Time < Length - 0.35f && Index < 19; Time += 0.6f, ++Index)
							{
								HandKeys.Add({ FMath::RoundToFloat(Time * 10.0f) / 10.0f, EKimodoKeyBone::RightHand, Index % 2 == 0 ? 30.0f : -30.0f, 5.0f, 0.0f });
							}
							SelectedHandKey = INDEX_NONE;
							RebuildHandKeyRows();
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.Text(LOCTEXT("ClearHandKeys", "Clear"))
						.IsEnabled_Lambda([this] { return HandKeys.Num() > 0; })
						.OnClicked_Lambda([this] {
							HandKeys.Reset();
							SelectedHandKey = INDEX_NONE;
							RebuildHandKeyRows();
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot().FillWidth(1)
					[
						SNullWidget::NullWidget
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.Text_Lambda([this] {
							const TSharedPtr<FKimodoTake> Take = GetSelectedTake();
							return Take.IsValid()
								? FText::Format(LOCTEXT("GenerateKeyed", "Generate with keys (#{0})"), FText::AsNumber(Take->Number))
								: LOCTEXT("GenerateKeyedNone", "Generate with keys");
						})
						.ToolTipText(LOCTEXT("GenerateKeyedTip", "Regenerate the selected take with these body keys. The result is added as a new take."))
						.IsEnabled_Lambda([this] {
							return !bBusy && IsReady() && bCanRevise && GetSelectedTake().IsValid() && HandKeys.Num() > 0 && !TargetMeshPath.IsEmpty();
						})
						.OnClicked_Lambda([this] { return RunAction(TEXT("keyed")); })
					]
				]
			];
	}

	/** 키 목록과 선택을 미리보기에 넘겨 목표 위치 표시·드래그 핸들을 갱신한다. */
	void SyncHandKeysToPreview()
	{
		if (!Preview.IsValid()) return;
		TArray<FKimodoHandTarget> Targets;
		for (const FHandKey& Key : HandKeys)
		{
			Targets.Add({ Key.Time, Key.Bone, FVector(Key.Right, Key.Up, Key.Forward) });
		}
		Preview->SetHandKeys(Targets, SelectedHandKey);
	}

	/** 키를 골라 미리보기를 그 시각으로 옮긴다. 노란 핸들을 끌어 위치를 정한다. */
	void SelectHandKey(int32 Index)
	{
		SelectedHandKey = HandKeys.IsValidIndex(Index) ? Index : INDEX_NONE;
		if (HandKeys.IsValidIndex(SelectedHandKey))
		{
			NewKeyBone = HandKeys[SelectedHandKey].Bone;  // Add Key가 고른 키의 부위를 따른다
			if (Preview.IsValid()) Preview->Scrub(HandKeys[SelectedHandKey].Time);
		}
		SyncHandKeysToPreview();
	}

	/** 같은 부위에서 Time(0.05초 이내)에 있는 키. Ignore 번 키는 건너뛴다. 없으면 INDEX_NONE. */
	int32 FindHandKey(EKimodoKeyBone Bone, float Time, int32 Ignore = INDEX_NONE) const
	{
		for (int32 Index = 0; Index < HandKeys.Num(); ++Index)
		{
			if (Index != Ignore && HandKeys[Index].Bone == Bone && FMath::Abs(HandKeys[Index].Time - Time) < 0.05f) return Index;
		}
		return INDEX_NONE;
	}

	/** 손 키프레임 한 줄씩 다시 그린다(추가·삭제·프리셋 때). 값은 칸에서 바로 HandKeys에 반영한다. */
	void RebuildHandKeyRows()
	{
		if (!HandKeyRows.IsValid()) return;
		if (!HandKeys.IsValidIndex(SelectedHandKey)) SelectedHandKey = INDEX_NONE;
		SyncHandKeysToPreview();
		HandKeyRows->ClearChildren();
		if (HandKeys.IsEmpty())
		{
			HandKeyRows->AddSlot().AutoHeight()
			[
				SNew(STextBlock).Text(LOCTEXT("NoHandKeys", "No keys yet. Double-click a track on the timeline under the preview, or use the buttons below."))
				.AutoWrapText(true).ColorAndOpacity(FSlateColor::UseSubduedForeground())
			];
			return;
		}
		auto Number = [this](int32 Index, float FHandKey::* Field, float Min, float Max, const FText& Tip) -> TSharedRef<SWidget>
		{
			return SNew(SBox).WidthOverride(72)
			[
				SNew(SSpinBox<float>)
				.MinValue(Min).MaxValue(Max).Delta(Field == &FHandKey::Time ? 0.1f : 1.0f)
				.ToolTipText(Tip)
				.Value_Lambda([this, Index, Field] { return HandKeys.IsValidIndex(Index) ? HandKeys[Index].*Field : 0.0f; })
				.OnValueChanged_Lambda([this, Index, Field](float Value) {
					if (!HandKeys.IsValidIndex(Index)) return;
					HandKeys[Index].*Field = Value;
					// 고른 키의 시각을 바꾸면 미리보기도 그 시각으로 따라간다.
					if (Field == &FHandKey::Time && Index == SelectedHandKey && Preview.IsValid()) Preview->Scrub(Value);
					SyncHandKeysToPreview();
				})
			];
		};
		// 모든 키를 부위·시각 순으로 한 줄씩. 고른 키(타임라인의 노란 ◆)는 줄 배경을 밝힌다.
		TArray<int32> Order;
		for (int32 Index = 0; Index < HandKeys.Num(); ++Index) Order.Add(Index);
		Order.StableSort([this](int32 A, int32 B) {
			if (HandKeys[A].Bone != HandKeys[B].Bone) return HandKeys[A].Bone < HandKeys[B].Bone;
			return HandKeys[A].Time < HandKeys[B].Time;
		});
		for (const int32 Index : Order)
		{
			HandKeyRows->AddSlot().AutoHeight().Padding(0, 0, 0, 2)
			[
				SNew(SBorder)
				.BorderImage(FAppStyle::GetBrush("WhiteBrush"))
				.BorderBackgroundColor_Lambda([this, Index] {
					return SelectedHandKey == Index ? FLinearColor(1.0f, 0.8f, 0.1f, 0.12f) : FLinearColor::Transparent;
				})
				.Padding(FMargin(2, 1))
				[
				SNew(SHorizontalBox)
				// 선택(●): 미리보기를 이 키의 시각으로 옮기고, 노란 핸들을 끌어 손 위치를 정한다.
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
				[
					SNew(SCheckBox)
					.Style(FAppStyle::Get(), "RadioButton")
					.ToolTipText(LOCTEXT("SelectKeyTip", "Select this key: the preview jumps to its time and shows a yellow handle you can drag."))
					.IsChecked_Lambda([this, Index] { return SelectedHandKey == Index ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
					.OnCheckStateChanged_Lambda([this, Index](ECheckBoxState) { SelectHandKey(Index); })
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
				[
					Number(Index, &FHandKey::Time, 0.0f, 60.0f, LOCTEXT("KeyTimeTip", "Time in seconds"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
				[
					SNew(STextBlock).Text(LOCTEXT("KeySeconds", "s"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
				[
					// 부위: 오른손·왼손·오른발·왼발·골반(Kimodo가 위치를 받는 부위)
					SNew(SSegmentedControl<EKimodoKeyBone>)
					.Value_Lambda([this, Index] { return HandKeys.IsValidIndex(Index) ? HandKeys[Index].Bone : EKimodoKeyBone::RightHand; })
					.OnValueChanged_Lambda([this, Index](EKimodoKeyBone Bone) {
						if (!HandKeys.IsValidIndex(Index) || FindHandKey(Bone, HandKeys[Index].Time, Index) != INDEX_NONE) return;
						HandKeys[Index].Bone = Bone;
						RebuildHandKeyRows();  // 부위·시각 순서가 바뀐다
					})
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::RightHand).Text(LOCTEXT("PartRightHand", "RH")).ToolTip(LOCTEXT("PartRightHandTip", "Right hand"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::LeftHand).Text(LOCTEXT("PartLeftHand", "LH")).ToolTip(LOCTEXT("PartLeftHandTip", "Left hand"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::RightFoot).Text(LOCTEXT("PartRightFoot", "RF")).ToolTip(LOCTEXT("PartRightFootTip", "Right foot"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::LeftFoot).Text(LOCTEXT("PartLeftFoot", "LF")).ToolTip(LOCTEXT("PartLeftFootTip", "Left foot"))
					+ SSegmentedControl<EKimodoKeyBone>::Slot(EKimodoKeyBone::Hips).Text(LOCTEXT("PartHips", "Hip")).ToolTip(LOCTEXT("PartHipsTip", "Hips (pelvis): moves the whole body, e.g. lower it to crouch"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
				[
					Number(Index, &FHandKey::Right, -100.0f, 100.0f, LOCTEXT("KeyRightTip", "cm to the character's right (negative = left)"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
				[
					Number(Index, &FHandKey::Up, -100.0f, 100.0f, LOCTEXT("KeyUpTip", "cm up (negative = down)"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
				[
					Number(Index, &FHandKey::Forward, -100.0f, 100.0f, LOCTEXT("KeyForwardTip", "cm forward from the character (negative = back)"))
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
				[
					SNew(SButton)
					.ButtonStyle(FAppStyle::Get(), "SimpleButton")
					.ToolTipText(LOCTEXT("RemoveHandKey", "Remove this key"))
					.OnClicked_Lambda([this, Index] {
						if (HandKeys.IsValidIndex(Index)) HandKeys.RemoveAt(Index);
						if (SelectedHandKey == Index) SelectedHandKey = INDEX_NONE;
						else if (SelectedHandKey > Index) --SelectedHandKey;
						RebuildHandKeyRows();
						return FReply::Handled();
					})
					[
						SNew(SImage).Image(FAppStyle::GetBrush("Icons.X")).ColorAndOpacity(FSlateColor::UseSubduedForeground())
					]
				]
				]
			];
		}
	}

	TSharedRef<ITableRow> MakeTakeRow(TSharedPtr<FKimodoTake> Item, const TSharedRef<STableViewBase>& Owner)
	{
		const FString Id = Item->Id;
		return SNew(STableRow<TSharedPtr<FKimodoTake>>, Owner)
			.Padding(FMargin(2, 3))
			[
				SNew(SHorizontalBox)
				// 별표: 표시한 테이크는 테이크가 많아져도 자동으로 지워지지 않는다.
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 4, 0)
				[
					SNew(SButton)
					.ButtonStyle(FAppStyle::Get(), "SimpleButton")
					.ContentPadding(FMargin(2))
					.ToolTipText(LOCTEXT("StarTip", "Star to keep this take in the list. Starred takes are kept and do not count toward the limit; beyond 20 unstarred takes, the oldest unstarred ones are removed."))
					.OnClicked_Lambda([this, Id] { return RunAction(TEXT("star"), Id); })
					[
						SNew(SImage)
						.Image(FAppStyle::GetBrush(Item->bStarred ? "Icons.Star" : "Icons.Star.Outline"))
						.ColorAndOpacity(Item->bStarred ? FSlateColor(FLinearColor(1.0f, 0.75f, 0.1f)) : FSlateColor::UseSubduedForeground())
					]
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
				[
					SNew(STextBlock)
					.Text(FText::FromString(FString::Printf(TEXT("#%d"), Item->Number)))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
				]
				+ SHorizontalBox::Slot().FillWidth(1).VAlign(VAlign_Center)
				[
					SNew(STextBlock)
					.Text(FText::FromString(Item->Prompt))
					// 메모리에 없는 테이크는 흐리게 보여 주고, 고르면 다시 가져온다는 것을 툴팁으로 알린다.
					.ColorAndOpacity(Item->bLoaded ? FSlateColor::UseForeground() : FSlateColor::UseSubduedForeground())
					.ToolTipText(Item->bLoaded
						? FText::FromString(Item->Prompt)
						: FText::Format(LOCTEXT("TakeNotLoaded", "{0}\n\nNot in memory. Selecting it re-imports the preview (a few seconds)."), FText::FromString(Item->Prompt)))
					.OverflowPolicy(ETextOverflowPolicy::Ellipsis)
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(8, 0, 0, 0)
				[
					SNew(STextBlock)
					.Text(FText::FromString(FString::Printf(TEXT("seed %s · %.2fs"), *Item->Seed, Item->Duration)))
					.ColorAndOpacity(FSlateColor::UseSubduedForeground())
				]
				// 저장한 테이크는 체크 표시와 저장 경로 툴팁
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(8, 0, 0, 0)
				[
					SNew(SImage)
					.Image(FAppStyle::GetBrush("Icons.Check"))
					.ColorAndOpacity(FSlateColor(FLinearColor(0.3f, 0.8f, 0.4f)))
					.ToolTipText(FText::Format(LOCTEXT("SavedTip", "Saved as {0}"), FText::FromString(Item->Saved)))
					.Visibility(Item->Saved.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible)
				]
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(4, 0, 0, 0)
				[
					SNew(SButton)
					.ButtonStyle(FAppStyle::Get(), "SimpleButton")
					.ContentPadding(FMargin(2))
					.ToolTipText(LOCTEXT("DiscardTip", "Remove this take (its temporary preview assets are deleted; saved assets stay)."))
					.IsEnabled_Lambda([this] { return !bBusy; })
					.OnClicked_Lambda([this, Id] { return RunAction(TEXT("discard"), Id); })
					[
						SNew(SImage)
						.Image(FAppStyle::GetBrush("Icons.X"))
						.ColorAndOpacity(FSlateColor::UseSubduedForeground())
					]
				]
			];
	}

	/** 4. 저장: 이름은 고른 테이크에 맞춰 자동으로 제안하고, 폴더는 마지막으로 저장한 곳을 기억한다. */
	TSharedRef<SWidget> MakeSaveArea()
	{
		const UKimodoUserSettings* UserSettings = GetDefault<UKimodoUserSettings>();
		return SNew(SVerticalBox)
			.IsEnabled_Lambda([this] { return !bBusy; })
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
				[
					SNew(SBox).MinDesiredWidth(60)[SNew(STextBlock).Text(LOCTEXT("Name", "Name"))]
				]
				+ SHorizontalBox::Slot().FillWidth(1)
				[
					SAssignNew(AssetName, SEditableTextBox)
					.ToolTipText(LOCTEXT("NameTip", "Suggested from the selected take's prompt and seed. Edit it to use your own name."))
				]
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 6, 0)
				[
					SNew(SBox).MinDesiredWidth(60)[SNew(STextBlock).Text(LOCTEXT("SaveFolder", "Folder"))]
				]
				+ SHorizontalBox::Slot().FillWidth(1)
				[
					SAssignNew(SaveFolder, SEditableTextBox)
					.Text(FText::FromString(UserSettings->LastSaveFolder))
					.HintText_Lambda([] {
						return FText::Format(LOCTEXT("SaveFolderHint", "Default: {0}"), FText::FromString(GetOutputFolder()));
					})
					.ToolTipText(LOCTEXT("SaveFolderTip", "Content folder for the saved AnimSequence, such as /Game/Animations. Leave empty to use the project default. The last folder you saved to is remembered."))
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(6, 0, 0, 0)
				[
					SAssignNew(FolderPicker, SComboButton)
					.ToolTipText(LOCTEXT("BrowseFolderTip", "Pick a folder from the Content Browser."))
					.OnGetMenuContent_Lambda([this] { return MakeFolderPicker(); })
					.ButtonContent()
					[
						SNew(STextBlock).Text(LOCTEXT("BrowseFolder", "Browse..."))
					]
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(4, 0, 0, 0)
				[
					SNew(SButton)
					.Text(LOCTEXT("DefaultFolder", "Default"))
					.ToolTipText(LOCTEXT("DefaultFolderTip", "Clear the folder to save to the project default."))
					.IsEnabled_Lambda([this] { return !SaveFolder->GetText().IsEmptyOrWhitespace(); })
					.OnClicked_Lambda([this] {
						SaveFolder->SetText(FText::GetEmpty());
						return FReply::Handled();
					})
				]
			]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Right)
			[
				SNew(SButton)
				.Text(LOCTEXT("Save", "Save selected take"))
				.IsEnabled_Lambda([this] { return bHasPreview && !AssetName->GetText().IsEmptyOrWhitespace(); })
				.OnClicked_Lambda([this] { return RunAction(TEXT("save"), AssetName->GetText().ToString()); })
			];
	}

	/** 항상 보이는 실행 바: 상태, 진행 막대, Restore / Cancel / Generate. */
	TSharedRef<SWidget> MakeActionBar()
	{
		return SNew(SBorder)
			.BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder"))
			.Padding(FMargin(16, 8))
			[
				SNew(SVerticalBox)
				// 프롬프트 입력 블록: Generate 바로 위. Kimodo를 설치하기 전에는 숨겨 설치 안내만 보이게 한다.
				+ SVerticalBox::Slot().AutoHeight()
				[
					SNew(SVerticalBox)
					.Visibility_Lambda([this] { return !bNeedsInitialState && IsReady() ? EVisibility::Visible : EVisibility::Collapsed; })
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("SectionPrompt", "Prompt"))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 11))
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
					[
						MakePromptArea()
					]
					+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
					[
						SNew(SSeparator).Thickness(1.0f)
					]
				]
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 6)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Top).Padding(0, 1, 8, 0)
					[
						SNew(SCircularThrobber)
						.Radius(7.0f)
						.Visibility_Lambda([this] { return bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
					]
					+ SHorizontalBox::Slot().FillWidth(1)
					[
						SNew(STextBlock)
						.AutoWrapText(true)
						.Text_Lambda([this] {
							if (bBusy && !StageText.IsEmpty())
							{
								return Progress >= 0.0f
									? FText::Format(LOCTEXT("StageProgress", "{0} {1}%  ·  {2}"), FText::FromString(StageText),
										FText::AsNumber(FMath::RoundToInt(Progress * 100.0f)), Status)
									: FText::Format(LOCTEXT("StageOnly", "{0}  ·  {1}"), FText::FromString(StageText), Status);
							}
							return Status;
						})
						.ToolTipText_Lambda([this] { return FText::FromString(Detail); })
					]
				]
				// 단계 진행률을 모르면(로딩·해석·변환) 왕복하는 막대로 보여 준다.
				+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 8)
				[
					SNew(SProgressBar)
					.Visibility_Lambda([this] { return bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
					.Percent_Lambda([this] { return Progress >= 0.0f ? TOptional<float>(Progress) : TOptional<float>(); })
					.ToolTipText_Lambda([this] { return FText::FromString(Detail); })
				]
				+ SVerticalBox::Slot().AutoHeight()
				[
					SNew(SHorizontalBox)
					// 에디터 시작을 막지 않도록 직전 미리보기(glTF 다시 가져오기)는 눌렀을 때만 복원한다.
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.Text_Lambda([this] { return FText::Format(LOCTEXT("Restore", "Restore last session ({0})"), FText::AsNumber(RestoreCount)); })
						.ToolTipText(LOCTEXT("RestoreTip", "Bring back the take list from the previous session. Only the take you were viewing is re-imported; the others load when you select them."))
						.Visibility_Lambda([this] { return bCanRestore && !bBusy ? EVisibility::Visible : EVisibility::Collapsed; })
						.OnClicked_Lambda([this] { return RunAction(TEXT("restore")); })
					]
					+ SHorizontalBox::Slot().FillWidth(1)
					[
						SNullWidget::NullWidget
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(0, 0, 8, 0)
					[
						SNew(SButton)
						.Text(LOCTEXT("Cancel", "Cancel"))
						.ToolTipText(LOCTEXT("CancelTip", "Stop the running generation and keep the takes you have."))
						.Visibility_Lambda([this] { return bBusy && IsReady() ? EVisibility::Visible : EVisibility::Collapsed; })
						.OnClicked_Lambda([this] { return RunAction(TEXT("cancel")); })
					]
					// Generate ×N: 같은 프롬프트로 여러 테이크를 차례로 만든다(시드만 다름). 누를 버튼 바로 옆에 둔다.
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0, 0, 8, 0)
					[
						SNew(SSegmentedControl<int32>)
						.ToolTipText(LOCTEXT("BatchTip", "How many takes Generate makes from this prompt, one after another with different seeds.\nWith a seed entered, they use seed, seed+1, … Revise always makes one take."))
						.IsEnabled_Lambda([this] { return !bBusy; })
						// 설치 전에는 프롬프트 입력처럼 숨긴다(생성할 수 없으므로).
						.Visibility_Lambda([this] { return !bNeedsInitialState && IsReady() ? EVisibility::Visible : EVisibility::Collapsed; })
						.Value_Lambda([] { return GetBatchCount(); })
						.OnValueChanged_Lambda([](int32 Value) {
							UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
							UserSettings->BatchCount = Value;
							UserSettings->SaveConfig();
						})
						+ SSegmentedControl<int32>::Slot(1).Text(LOCTEXT("Batch1", "×1"))
						+ SSegmentedControl<int32>::Slot(3).Text(LOCTEXT("Batch3", "×3"))
						+ SSegmentedControl<int32>::Slot(5).Text(LOCTEXT("Batch5", "×5"))
						+ SSegmentedControl<int32>::Slot(10).Text(LOCTEXT("Batch10", "×10"))
					]
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SButton)
						.ButtonStyle(FAppStyle::Get(), "PrimaryButton")
						.ContentPadding(FMargin(16, 4))
						.ToolTipText_Lambda([this] { return GenerateBlockedReason(); })
						.IsEnabled_Lambda([this] { return CanGenerate(); })
						.OnClicked_Lambda([this] {
							return RunAction(TEXT("generate"), Prompt->GetText().ToString(), Duration->GetText().ToString());
						})
						[
							SNew(STextBlock)
							.Text_Lambda([] {
								const int32 Count = GetBatchCount();
								return Count > 1
									? FText::Format(LOCTEXT("GenerateBatch", "Generate ×{0}  (Ctrl+Enter)"), FText::AsNumber(Count))
									: LOCTEXT("Generate", "Generate  (Ctrl+Enter)");
							})
							.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
						]
					]
				]
			];
	}

	// ---------------------------------------------------------------- helpers

	bool IsReady() const { return SetupState == TEXT("ready"); }

	/** Generate ×N의 N(1~10). Python(MAX_BATCH)도 같은 범위로 자른다. */
	static int32 GetBatchCount()
	{
		return FMath::Clamp(GetDefault<UKimodoUserSettings>()->BatchCount, 1, 10);
	}

	/** 확산 스텝(1~1000, 기본 100). Python(panel.py)도 같은 범위로 자른다. */
	static int32 GetDiffusionSteps()
	{
		return FMath::Clamp(GetDefault<UKimodoUserSettings>()->DiffusionSteps, 1, 1000);
	}

	bool CanGenerate() const
	{
		return GenerateBlockedReason().IsEmpty();
	}

	/** Generate가 꺼져 있는 이유. 버튼 툴팁으로 보여 준다(비어 있으면 생성 가능). */
	FText GenerateBlockedReason() const
	{
		if (bNeedsInitialState) return LOCTEXT("BlockedStarting", "Waiting for Unreal Python…");
		if (bBusy) return LOCTEXT("BlockedBusy", "A generation is already running.");
		if (!IsReady()) return LOCTEXT("BlockedSetup", "Install Kimodo first.");
		if (TargetMeshPath.IsEmpty()) return LOCTEXT("BlockedMesh", "Choose a target mesh under Target.");
		if (!Prompt.IsValid() || Prompt->GetText().ToString().TrimStartAndEnd().IsEmpty()) return LOCTEXT("BlockedPrompt", "Enter a motion prompt under Prompt.");
		return FText::GetEmpty();
	}

	bool CanRevise() const
	{
		return !bBusy && bCanRevise && IsReady() && !TargetMeshPath.IsEmpty() && Revision.IsValid() && !Revision->GetText().IsEmptyOrWhitespace();
	}

	/** 라벨 + 썸네일이 있는 에셋 선택 칸. bClearsDependents면 바뀔 때 IK Rig·Retargeter를 비운다. */
	TSharedRef<SWidget> MakeAssetPicker(const FText& Label, const FText& Tip, const UClass* Class, FString* Path, bool bClearsDependents)
	{
		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(0, 0, 0, 4)
			[
				SNew(STextBlock).Text(Label).ToolTipText(Tip)
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(SObjectPropertyEntryBox)
				.AllowedClass(Class)
				.ObjectPath_Lambda([Path] { return *Path; })
				.OnObjectChanged_Lambda([this, Path, bClearsDependents](const FAssetData& Asset)
				{
					// 다른 메시의 IK Rig와 Retargeter가 남지 않도록 함께 비운다.
					const FString NewPath = ToAssetPath(Asset);
					if (NewPath == *Path) return;

					*Path = NewPath;
					if (bClearsDependents)
					{
						TargetRigPath.Reset();
						RetargeterPath.Reset();
					}
					RememberTargets();
				})
				.ThumbnailPool(UThumbnailManager::Get().GetSharedThumbnailPool())
				.DisplayThumbnail(true)
				.ThumbnailSizeOverride(FIntPoint(48, 48))
				.DisplayCompactSize(true)
			];
	}

	void RememberTargets() const
	{
		UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
		UserSettings->LastTargetMesh = TargetMeshPath;
		UserSettings->LastTargetIKRig = TargetRigPath;
		UserSettings->LastRetargeter = RetargeterPath;
		UserSettings->SaveConfig();
	}

	/** 텍스트 인코더 방식 메뉴. 고르면 사용자별 설정(EditorPerProjectUserSettings)에 바로 저장한다. */
	TSharedRef<SWidget> MakeEncoderMenu()
	{
		FMenuBuilder Menu(true, nullptr);
		for (const FEncoderOption& Option : GetEncoderOptions())
		{
			const EKimodoTextEncoderDevice Value = Option.Value;
			Menu.AddMenuEntry(Option.Label, Option.Tip, FSlateIcon(),
				FUIAction(
					FExecuteAction::CreateLambda([Value] {
						UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
						UserSettings->TextEncoderDevice = Value;
						UserSettings->SaveConfig();
					}),
					FCanExecuteAction(),
					FIsActionChecked::CreateLambda([Value] {
						return GetDefault<UKimodoUserSettings>()->TextEncoderDevice == Value;
					})),
				NAME_None, EUserInterfaceActionType::RadioButton);
		}
		return Menu.MakeWidget();
	}

	/** Content Browser 폴더 트리. 고르면 저장 폴더 칸에 넣고 닫는다. */
	TSharedRef<SWidget> MakeFolderPicker()
	{
		const FString Current = SaveFolder->GetText().ToString().TrimStartAndEnd();

		FPathPickerConfig Config;
		Config.DefaultPath = Current.IsEmpty() ? GetOutputFolder() : Current;
		Config.bAddDefaultPath = true;
		Config.bAllowClassesFolder = false;
		Config.bAllowReadOnlyFolders = false;
		Config.OnPathSelected = FOnPathSelected::CreateLambda([this](const FString& Path)
		{
			SaveFolder->SetText(FText::FromString(Path));
			FolderPicker->SetIsOpen(false);
		});

		FContentBrowserModule& ContentBrowser = FModuleManager::LoadModuleChecked<FContentBrowserModule>(TEXT("ContentBrowser"));
		return SNew(SBox)
			.WidthOverride(320.0f)
			.HeightOverride(400.0f)
			[
				ContentBrowser.Get().CreatePathPicker(Config)
			];
	}

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
		Payload->SetStringField(TEXT("take_root"), FString(TakeRootPath).LeftChop(1));
		Payload->SetStringField(TEXT("take_root_dir"), GetTakeRootDir());
		Payload->SetStringField(TEXT("save_folder"), SaveFolder.IsValid() ? SaveFolder->GetText().ToString().TrimStartAndEnd() : FString());
		Payload->SetStringField(TEXT("model"), Settings->ModelName);
		Payload->SetArrayField(TEXT("align_bones"), ToJsonArray(AlignBones));
		Payload->SetBoolField(TEXT("root_motion"), UserSettings->bRootMotion);
		Payload->SetNumberField(TEXT("count"), GetBatchCount());
		Payload->SetNumberField(TEXT("steps"), GetDiffusionSteps());
		// 손 키프레임(cm). Python(parse_hand_keys)이 m로 바꾸고 검사한다.
		Payload->SetArrayField(TEXT("hand_keys"), HandKeysToJson(HandKeys));
		// 편집 중인 키가 바뀌었으면 그 테이크에 남기도록 같이 보낸다(테이크마다 키를 따로 기억).
		if (!KeysTakeId.IsEmpty() && HandKeys != SentHandKeys)
		{
			const TSharedRef<FJsonObject> TakeKeys = MakeShared<FJsonObject>();
			TakeKeys->SetStringField(TEXT("id"), KeysTakeId);
			TakeKeys->SetArrayField(TEXT("keys"), HandKeysToJson(HandKeys));
			Payload->SetObjectField(TEXT("take_keys"), TakeKeys);
			SentHandKeys = HandKeys;
		}
		Payload->SetStringField(TEXT("python"), ResolveLocalPath(TEXT("KIMODO_PYTHON"), TEXT("ECLIPSE_KIMODO_PYTHON"), UserSettings->PythonExecutable.FilePath, GetManagedPython()));
		Payload->SetStringField(TEXT("model_cache"), ResolveLocalPath(TEXT("KIMODO_MODEL_CACHE"), TEXT("ECLIPSE_KIMODO_MODEL_CACHE"), UserSettings->ModelCacheDirectory.Path, GetManagedInstallRoot() / TEXT("model-cache")));
		Payload->SetStringField(TEXT("bootstrap_python"), GetBootstrapPython());
		Payload->SetStringField(TEXT("work_dir"), ResolveLocalPath(TEXT("KIMODO_WORK_DIR"), TEXT("ECLIPSE_KIMODO_WORK_DIR"), UserSettings->WorkingDirectory.Path, DefaultWorkDir));
		Payload->SetArrayField(TEXT("agents"), ToJsonArray(Agents));
		Payload->SetStringField(TEXT("text_encoder_device"),
			UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::CUDA ? TEXT("cuda")
			: UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::Auto ? TEXT("auto")
			: UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::CPU ? TEXT("cpu")
			: UserSettings->TextEncoderDevice == EKimodoTextEncoderDevice::LowMemoryExact ? TEXT("stream-exact")
			: TEXT("stream"));

		FString Json;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
		FJsonSerializer::Serialize(Payload, Writer);
		return Json;
	}

	// ---------------------------------------------------------------- python bridge

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

		// 2) 요청을 처리하고 상태(JSON, Base64)를 평가 결과로 받는다. print를 쓰지 않아 Output Log에 쌓이지 않는다.
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
			// 저장 중 Python이 임시 에셋을 정리할 수 있으므로 컴포넌트 참조를 잠시 해제한다.
			Preview->SetClip(nullptr);
		}

		// 리타게팅(IKRetargetBatchOperation)은 끝날 때 Content Browser를 새 에셋으로 옮긴다(SyncBrowserToAssets).
		// Content Browser가 있는 레벨 에디터 탭과 같은 탭 묶음에 패널을 두면 그 탭이 앞으로 와서 패널이 가려지므로,
		// 호출 전에 앞에 있던 패널 탭이 뒤로 밀렸으면 다시 앞으로 가져온다.
		const TSharedPtr<SDockTab> OwnTab = FGlobalTabmanager::Get()->FindExistingLiveTab(FTabId(KimodoTabName));
		const bool bWasForeground = OwnTab.IsValid() && OwnTab->IsForeground();

		FString Line;
		TSharedPtr<FJsonObject> State;
		const bool bRan = Python->ExecPythonCommandEx(Load) && Python->ExecPythonCommandEx(Command) && DecodeStatusLine(Command.CommandResult, Line);
		if (bWasForeground && OwnTab.IsValid() && !OwnTab->IsForeground())
		{
			OwnTab->ActivateInParent(ETabActivationCause::SetDirectly);
		}
		if (!bRan)
		{
			bBusy = false;
			Status = LOCTEXT("PythonError", "Kimodo could not run. See the Unreal Output Log.");
			return FReply::Handled();
		}
		if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Line), State) || !State.IsValid())
		{
			bBusy = false;
			Status = LOCTEXT("PythonMismatch", "The Kimodo panel script and plugin do not match. Restart the editor.");
			return FReply::Handled();
		}

		ApplyState(*State, bSaving, PreviousTime, bWasPlaying, bAllowAutoPlay);
		return FReply::Handled();
	}

	void ApplyState(const FJsonObject& State, bool bSaving, float PreviousTime, bool bWasPlaying, bool bAllowAutoPlay)
	{
		bBusy = State.GetBoolField(TEXT("busy"));
		bHasPreview = State.GetBoolField(TEXT("has_preview"));
		bCanRevise = State.GetBoolField(TEXT("can_revise"));
		bCanRestore = State.GetBoolField(TEXT("restorable"));
		RestoreCount = static_cast<int32>(State.GetNumberField(TEXT("restore_count")));
		LastSeed = State.GetStringField(TEXT("seed"));
		StageText = State.GetStringField(TEXT("stage"));
		Progress = static_cast<float>(State.GetNumberField(TEXT("progress")));
		Detail = State.GetStringField(TEXT("detail"));
		Status = FText::FromString(State.GetStringField(TEXT("message")));

		// 설치 상태: installing, ready, missing:<빠진 항목>
		const FString SetupField = State.GetStringField(TEXT("setup"));
		SetupState = SetupField;
		SetupMissing.Reset();
		FString StateName;
		FString MissingItem;
		if (SetupField.Split(TEXT(":"), &StateName, &MissingItem))
		{
			SetupState = StateName;
			SetupMissing = MissingItem;
		}

		// 고른 테이크의 해석과 Kimodo에 실제로 전달한 영어 프롬프트
		const FString Description = State.GetStringField(TEXT("interpretation")).TrimStartAndEnd();
		const FString EnglishPrompt = State.GetStringField(TEXT("english")).TrimStartAndEnd();
		Interpretation = FText::GetEmpty();
		if (!Description.IsEmpty() && !EnglishPrompt.IsEmpty())
		{
			Interpretation = FText::Format(LOCTEXT("InterpretationWithPrompt", "Interpretation · {0}\nKimodo prompt · {1}"),
				FText::FromString(Description), FText::FromString(EnglishPrompt));
		}
		else if (!Description.IsEmpty())
		{
			Interpretation = FText::Format(LOCTEXT("Interpretation", "Interpretation · {0}"), FText::FromString(Description));
		}
		else if (!EnglishPrompt.IsEmpty())
		{
			Interpretation = FText::Format(LOCTEXT("PromptOnly", "Kimodo prompt · {0}"), FText::FromString(EnglishPrompt));
		}

		UpdateTakes(State);

		// 미리보기: 고른 테이크가 바뀌었거나 저장 직후면 클립을 다시 연결한다.
		const FString ClipPath = State.GetStringField(TEXT("path"));
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

		// 저장 이름 제안: 사용자가 직접 고친 이름은 덮어쓰지 않는다.
		const FString Suggested = State.GetStringField(TEXT("suggested_name"));
		if (AssetName.IsValid() && !Suggested.IsEmpty() && Suggested != LastAutoName)
		{
			const FString Current = AssetName->GetText().ToString().TrimStartAndEnd();
			if (Current.IsEmpty() || Current == LastAutoName || Current == GetDefault<UKimodoSettings>()->DefaultAssetName)
			{
				AssetName->SetText(FText::FromString(Suggested));
			}
			LastAutoName = Suggested;
		}

		// 저장에 성공하면 그 폴더를 기억한다.
		if (bSaving && Status.ToString().StartsWith(TEXT("Saved ")))
		{
			UKimodoUserSettings* UserSettings = GetMutableDefault<UKimodoUserSettings>();
			UserSettings->LastSaveFolder = SaveFolder->GetText().ToString().TrimStartAndEnd();
			UserSettings->SaveConfig();
		}

		// 생성이 끝나면 다른 창을 보고 있어도 알 수 있게 에디터 알림을 띄운다.
		const FString Event = State.GetStringField(TEXT("event"));
		if (!Event.IsEmpty())
		{
			Notify(Event == TEXT("ready"));
		}

		if (!bBusy && bHasPreview)
		{
			// 패널에서 저장하기 전에는 Save All에 임시 에셋을 포함하지 않는다.
			const FString PreviewPrefix = GetOutputFolder() / TEXT("_Preview_");
			for (TObjectIterator<UPackage> Package; Package; ++Package)
			{
				if (Package->IsDirty() && (Package->GetName().StartsWith(PreviewPrefix) || Package->GetName().StartsWith(TakeRootPath)))
				{
					Package->SetDirtyFlag(false);
				}
			}
		}
	}

	/** Python의 takes 목록이 바뀌었을 때만 리스트를 다시 만든다. */
	void UpdateTakes(const FJsonObject& State)
	{
		SelectedTakeId = State.GetStringField(TEXT("selected"));

		TArray<TSharedPtr<FKimodoTake>> NewTakes;
		FString Signature;
		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (State.TryGetArrayField(TEXT("takes"), Items))
		{
			for (const TSharedPtr<FJsonValue>& Value : *Items)
			{
				const TSharedPtr<FJsonObject> Object = Value->AsObject();
				if (!Object.IsValid()) continue;
				TSharedPtr<FKimodoTake> Take = MakeShared<FKimodoTake>();
				Take->Id = Object->GetStringField(TEXT("id"));
				Take->Number = static_cast<int32>(Object->GetNumberField(TEXT("number")));
				Take->Prompt = Object->GetStringField(TEXT("prompt"));
				Take->Seed = Object->GetStringField(TEXT("seed"));
				Take->Duration = Object->GetNumberField(TEXT("duration"));
				Take->bStarred = Object->GetBoolField(TEXT("starred"));
				Take->Saved = Object->GetStringField(TEXT("saved"));
				Take->Target = Object->GetStringField(TEXT("target"));
				Take->bLoaded = Object->GetBoolField(TEXT("loaded"));
				const TArray<TSharedPtr<FJsonValue>>* KeyItems = nullptr;
				if (Object->TryGetArrayField(TEXT("keys"), KeyItems)) Take->Keys = HandKeysFromJson(*KeyItems);
				Signature += FString::Printf(TEXT("%s:%d:%d:%s;"), *Take->Id, Take->bStarred ? 1 : 0, Take->bLoaded ? 1 : 0, *Take->Saved);
				NewTakes.Add(Take);
			}
		}

		if (Signature != TakesSignature)
		{
			TakesSignature = Signature;
			Takes = MoveTemp(NewTakes);
			if (TakesView.IsValid()) TakesView->RequestListRefresh();
		}
		else
		{
			// 목록은 그대로여도 키는 최신으로(다음 테이크 전환 때 쓴다).
			for (int32 Index = 0; Index < Takes.Num() && Index < NewTakes.Num(); ++Index) Takes[Index]->Keys = MoveTemp(NewTakes[Index]->Keys);
		}
		SyncTakeSelection();

		// 다른 테이크로 바뀌면 그 테이크의 Body keys를 불러온다. 이전 테이크의 키는 이번 요청에 실려 이미 저장됐다.
		if (SelectedTakeId != KeysTakeId)
		{
			const TSharedPtr<FKimodoTake> Take = GetSelectedTake();
			KeysTakeId = Take.IsValid() ? SelectedTakeId : FString();
			if (Take.IsValid() || !HandKeys.IsEmpty())
			{
				HandKeys = Take.IsValid() ? Take->Keys : TArray<FHandKey>();
				SentHandKeys = HandKeys;
				SelectedHandKey = INDEX_NONE;
				RebuildHandKeyRows();
			}
		}
	}

	/** Python이 알려 준 선택 테이크(없으면 null). Revise 버튼·안내문이 기준 테이크를 보여 줄 때 쓴다. */
	TSharedPtr<FKimodoTake> GetSelectedTake() const
	{
		const TSharedPtr<FKimodoTake>* Found = Takes.FindByPredicate([this](const TSharedPtr<FKimodoTake>& Take) { return Take->Id == SelectedTakeId; });
		return Found ? *Found : TSharedPtr<FKimodoTake>();
	}

	void SyncTakeSelection()
	{
		if (!TakesView.IsValid()) return;
		const TSharedPtr<FKimodoTake> Found = GetSelectedTake();
		if (Found.IsValid())
		{
			if (!TakesView->IsItemSelected(Found)) TakesView->SetSelection(Found, ESelectInfo::Direct);
		}
		else
		{
			TakesView->ClearSelection();
		}
	}

	void Notify(bool bSucceeded) const
	{
		FNotificationInfo Info(bSucceeded ? LOCTEXT("NotifyReady", "Kimodo take ready") : LOCTEXT("NotifyFailed", "Kimodo generation failed"));
		Info.SubText = Status;
		Info.ExpireDuration = bSucceeded ? 5.0f : 8.0f;
		Info.bUseSuccessFailIcons = true;
		// 기본값(메인 에디터 창)에 붙이면 패널이 떠 있는 창 대신 메인 창이 앞으로 올라온다.
		// 패널이 있는 창에 붙여 알림만 띄우고 창 순서는 그대로 둔다.
		Info.ForWindow = FSlateApplication::Get().FindWidgetWindow(AsShared());
		if (const TSharedPtr<SNotificationItem> Item = FSlateNotificationManager::Get().AddNotification(Info))
		{
			Item->SetCompletionState(bSucceeded ? SNotificationItem::CS_Success : SNotificationItem::CS_Fail);
		}
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
	/** 손 키프레임 목록과 그 줄들을 담는 상자(Hand keys). */
	TArray<FHandKey> HandKeys;
	/** HandKeys가 어느 테이크의 키인지, 그 테이크에 마지막으로 보낸 키. 테이크를 바꾸면 그 테이크의 키를 불러온다. */
	FString KeysTakeId;
	mutable TArray<FHandKey> SentHandKeys;
	TSharedPtr<SVerticalBox> HandKeyRows;
	/** 미리보기에서 드래그할 수 있는 키(●). INDEX_NONE이면 선택 없음. */
	int32 SelectedHandKey = INDEX_NONE;
	TSharedPtr<SKimodoKeyTimeline> KeyTimeline;
	/** + Add Key로 추가할 부위 */
	EKimodoKeyBone NewKeyBone = EKimodoKeyBone::RightHand;
	TSharedPtr<SEditableTextBox> AssetName;
	TSharedPtr<SEditableTextBox> SaveFolder;
	TSharedPtr<SComboButton> FolderPicker;
	TSharedPtr<SKimodoPreview> Preview;
	TSharedPtr<SEditableTextBox> Token;
	TSharedPtr<SListView<TSharedPtr<FKimodoTake>>> TakesView;
	TArray<TSharedPtr<FKimodoTake>> Takes;
	FString TakesSignature;
	FString SelectedTakeId;
	FString TargetMeshPath;
	FString TargetRigPath;
	FString RetargeterPath;
	FString PreviewPath;
	FString LastSeed;
	FString LastAutoName;
	FString SetupState;
	FString SetupMissing;
	FString StageText;
	FString Detail;
	FText Status;
	FText Interpretation;
	float Progress = -1.0f;
	int32 RestoreCount = 0;
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
		// 테이크 결과를 Content가 아닌 Saved/Kimodo/TakeAssets에 에셋 파일로 둔다.
		// Content Browser와 소스 관리에 섞이지 않고, 테이크를 다시 고르거나 복원할 때 가져오기 없이 바로 연다.
		IFileManager::Get().MakeDirectory(*GetTakeRootDir(), true);
		FPackageName::RegisterMountPoint(TakeRootPath, GetTakeRootDir());

		FGlobalTabmanager::Get()->RegisterNomadTabSpawner(KimodoTabName,
			FOnSpawnTab::CreateRaw(this, &FKimodoAnimationModule::SpawnKimodoTab))
			.SetDisplayName(LOCTEXT("Tab", "Kimodo Animation"))
			.SetMenuType(ETabSpawnerMenuType::Hidden);
		UToolMenus::RegisterStartupCallback(
			FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FKimodoAnimationModule::RegisterMenus));
		RegisterKimodoKeyEdMode();
	}

	void ShutdownModule() override
	{
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);
		UnregisterKimodoKeyEdMode();
		FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(KimodoTabName);
		// 에디터 종료 중에는 해제하지 않는다. 이때는 Content Browser 데이터 서브시스템이 먼저 정리되어
		// 마운트 해제 알림(OnContentPathDismounted)을 받은 쪽이 null을 참조해 크래시한다. 프로세스가 끝나므로 해제할 필요도 없다.
		if (!IsEngineExitRequested())
		{
			FPackageName::UnRegisterMountPoint(TakeRootPath, GetTakeRootDir());
		}
	}

private:
	TSharedRef<SDockTab> SpawnKimodoTab(const FSpawnTabArgs&)
	{
		return SNew(SDockTab).TabRole(ETabRole::NomadTab)
			.OnTabClosed_Static(&FKimodoAnimationModule::OnKimodoTabClosed)
		[
			SNew(SKimodoPanel)
		];
	}

	/** 패널 탭을 닫으면 진행 중인 생성을 멈추고 메모리에 띄워 둔 Kimodo 워커를 내린다(panel.py close_panel). */
	static void OnKimodoTabClosed(TSharedRef<SDockTab>)
	{
		IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
		if (!Python || !Python->IsPythonAvailable() || IsEngineExitRequested())
		{
			return;  // 에디터 종료 때는 panel.py의 종료 콜백이 정리한다.
		}
		FPythonCommandEx Command;
		Command.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
		Command.FileExecutionScope = EPythonFileExecutionScope::Public;
		Command.Flags = EPythonCommandFlags::Unattended;
		// 패널 스크립트를 한 번도 불러오지 않았으면(생성 전) 할 일이 없다.
		Command.Command = TEXT("if 'kimodo_animation_panel' in globals():\n    kimodo_animation_panel['close_panel']()\n");
		Python->ExecPythonCommandEx(Command);
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
