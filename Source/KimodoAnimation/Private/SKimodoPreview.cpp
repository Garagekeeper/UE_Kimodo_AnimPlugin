#include "SKimodoPreview.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimSequenceHelpers.h"
#include "Components/LineBatchComponent.h"
#include "Animation/DebugSkelMeshComponent.h"
#include "AnimPreviewInstance.h"
#include "EditorViewportClient.h"
#include "Engine/World.h"
#include "InputCoreTypes.h"
#include "UnrealWidget.h"

#include "EdMode.h"
#include "EditorModeManager.h"
#include "EditorModeRegistry.h"

#define LOCTEXT_NAMESPACE "KimodoPreview"

namespace
{
const FEditorModeID KimodoKeyEdModeId = TEXT("KimodoKeyEdMode");

/**
 * Body keys 편집 모드. 고른 키가 있으면 엔진(레거시) 이동 기즈모를 그 목표 위치에 캐릭터 축으로 그리게 한다.
 * 드래그 이동량은 뷰포트 클라이언트의 InputWidgetDelta가 받는다.
 */
class FKimodoKeyEdMode : public FEdMode
{
public:
	TWeakPtr<SKimodoPreview> Preview;

	bool IsCompatibleWith(FEditorModeID OtherModeID) const override { return true; }
	bool UsesTransformWidget() const override { return true; }
	bool UsesTransformWidget(UE::Widget::EWidgetMode CheckMode) const override { return CheckMode == UE::Widget::WM_Translate; }
	bool AllowWidgetMove() override { return true; }
	bool ShouldDrawWidget() const override
	{
		FVector Location;
		FMatrix Axes;
		const TSharedPtr<SKimodoPreview> Pinned = Preview.Pin();
		return Pinned && Pinned->GetKeyGizmo(Location, Axes);
	}
	EAxisList::Type GetWidgetAxisToDraw(UE::Widget::EWidgetMode InWidgetMode) const override
	{
		return InWidgetMode == UE::Widget::WM_Translate ? EAxisList::All : EAxisList::None;
	}
	FVector GetWidgetLocation() const override
	{
		FVector Location = FVector::ZeroVector;
		FMatrix Axes;
		if (const TSharedPtr<SKimodoPreview> Pinned = Preview.Pin()) Pinned->GetKeyGizmo(Location, Axes);
		return Location;
	}
	bool GetCustomDrawingCoordinateSystem(FMatrix& InMatrix, void* InData) override
	{
		FVector Location;
		const TSharedPtr<SKimodoPreview> Pinned = Preview.Pin();
		return Pinned && Pinned->GetKeyGizmo(Location, InMatrix);
	}
	bool GetCustomInputCoordinateSystem(FMatrix& InMatrix, void* InData) override
	{
		return GetCustomDrawingCoordinateSystem(InMatrix, InData);
	}
};
}

void RegisterKimodoKeyEdMode()
{
	FEditorModeRegistry::Get().RegisterMode<FKimodoKeyEdMode>(KimodoKeyEdModeId,
		LOCTEXT("KeyEdMode", "Kimodo Body Keys"), FSlateIcon(), false);
}

void UnregisterKimodoKeyEdMode()
{
	FEditorModeRegistry::Get().UnregisterMode(KimodoKeyEdModeId);
}

namespace
{
/**
 * 미리보기 뷰포트 클라이언트. 카메라는 엔진 방식(좌클릭 드래그 = 궤도 회전, 휠 = 줌, 우클릭+WASD = 이동)이고,
 * Body keys에서 키를 고르면 그 목표 위치에 엔진 이동 기즈모(빨강 X 앞, 초록 Y 오른쪽, 파랑 Z 위 — 캐릭터 기준)를 띄운다.
 */
class FKimodoPreviewClient : public FEditorViewportClient
{
public:
	FKimodoPreviewClient(FPreviewScene* InScene, const TSharedRef<SKimodoPreview>& InOwner)
		: FEditorViewportClient(nullptr, InScene, StaticCastSharedRef<SEditorViewport>(InOwner))
		, Owner(InOwner)
	{
		// 새 TRS 기즈모(Interactive Tools)는 레벨 선택을 따라가므로, 이 뷰포트는 레거시 위젯으로 고정하고
		// 키 편집 모드를 켜서 엔진 이동 기즈모가 그려지고 클릭되게 한다.
		ModeTools->SetSupportsViewportITF(false);
		ModeTools->SetWidgetMode(UE::Widget::WM_Translate);
		ModeTools->ActivateMode(KimodoKeyEdModeId);
		if (FKimodoKeyEdMode* KeyMode = ModeTools->GetActiveModeTyped<FKimodoKeyEdMode>(KimodoKeyEdModeId))
		{
			KeyMode->Preview = InOwner;
		}
		EngineShowFlags.SetModeWidgets(true);
	}

	/** 좌클릭 드래그·휠은 궤도 카메라. 우클릭(가운데 버튼)을 누르고 있으면 엔진 비행 모드(WASD·QE)로 넘긴다. */
	bool ShouldOrbitCamera() const override
	{
		if (Viewport && (Viewport->KeyState(EKeys::RightMouseButton) || Viewport->KeyState(EKeys::MiddleMouseButton)))
		{
			return FEditorViewportClient::ShouldOrbitCamera();
		}
		return true;
	}

	UE::Widget::EWidgetMode GetWidgetMode() const override
	{
		FVector Location;
		FMatrix Axes;
		const TSharedPtr<SKimodoPreview> Preview = Owner.Pin();
		return Preview && Preview->GetKeyGizmo(Location, Axes) ? UE::Widget::WM_Translate : UE::Widget::WM_None;
	}

	bool CanSetWidgetMode(UE::Widget::EWidgetMode NewMode) const override { return NewMode == UE::Widget::WM_Translate; }
	bool CanCycleWidgetMode() const override { return false; }

	FVector GetWidgetLocation() const override
	{
		FVector Location = FVector::ZeroVector;
		FMatrix Axes;
		if (const TSharedPtr<SKimodoPreview> Preview = Owner.Pin()) Preview->GetKeyGizmo(Location, Axes);
		return Location;
	}

	FMatrix GetWidgetCoordSystem() const override
	{
		FVector Location;
		FMatrix Axes = FMatrix::Identity;
		if (const TSharedPtr<SKimodoPreview> Preview = Owner.Pin()) Preview->GetKeyGizmo(Location, Axes);
		return Axes;
	}

	ECoordSystem GetWidgetCoordSystemSpace() const override { return COORD_Local; }

	bool InputWidgetDelta(FViewport* InViewport, EAxisList::Type CurrentAxis, FVector& Drag, FRotator& Rot, FVector& Scale) override
	{
		const TSharedPtr<SKimodoPreview> Preview = Owner.Pin();
		if (!Preview || CurrentAxis == EAxisList::None || Drag.IsNearlyZero()) return false;
		Preview->MoveSelectedKey(Drag);
		return true;
	}

	void TrackingStarted(const FInputEventState& InInputState, bool bIsDraggingWidget, bool bNudge) override
	{
		bFlying = !bIsDraggingWidget && (InInputState.IsRightMouseButtonPressed() || InInputState.IsMiddleMouseButtonPressed());
		FlyOrbitDistance = FMath::Max((GetLookAtLocation() - GetViewLocation()).Size(), 50.0);
		if (const TSharedPtr<SKimodoPreview> Preview = Owner.Pin())
		{
			if (bIsDraggingWidget) Preview->SetDraggingKey(true);
			else Preview->SetOrbiting(true);
		}
	}

	void TrackingStopped() override
	{
		// 비행 뒤에는 궤도 중심을 카메라 앞(비행 전과 같은 거리)으로 옮겨, 다음 좌클릭 궤도가 옛 중심으로 튀지 않게 한다.
		if (bFlying)
		{
			SetLookAtLocation(GetViewLocation() + GetViewRotation().Vector() * FlyOrbitDistance);
			bFlying = false;
		}
		if (const TSharedPtr<SKimodoPreview> Preview = Owner.Pin())
		{
			Preview->SetDraggingKey(false);
			Preview->SetOrbiting(false);
			Preview->SyncFromViewportCamera();
		}
	}

private:
	TWeakPtr<SKimodoPreview> Owner;
	bool bFlying = false;
	double FlyOrbitDistance = 400.0;
};

/** 메시에서 첫 번째로 있는 본 이름(UE 마네킹과 SOMA/믹사모식 이름 모두 시도). */
FName FindBone(const UDebugSkelMeshComponent* Mesh, std::initializer_list<const TCHAR*> Candidates)
{
	for (const TCHAR* Candidate : Candidates)
	{
		if (Mesh->GetBoneIndex(Candidate) != INDEX_NONE) return FName(Candidate);
	}
	return NAME_None;
}
}

void SKimodoPreview::Construct(const FArguments& Args)
{
	OnHandKeyDragged = Args._OnHandKeyDragged;
	KeyMarkers = NewObject<ULineBatchComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Scene.AddComponent(KeyMarkers, FTransform::Identity);
	Mesh = NewObject<UDebugSkelMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Mesh->SuspendClothingSimulation();
	Scene.AddComponent(Mesh, FTransform::Identity);
	Mesh->SetUpdateAnimationInEditor(true);
	Mesh->SetComponentTickEnabled(false);
	RootPath = NewObject<ULineBatchComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Scene.AddComponent(RootPath, FTransform::Identity);
	Scene.SetFloorVisibility(true, true);
	SEditorViewport::Construct(SEditorViewport::FArguments());
	SetToolTipText(LOCTEXT("CameraControls", "Left-drag: orbit. Wheel: zoom. Right-drag + WASD: fly.\n"
		"With a body key selected, drag the move gizmo (red forward, green right, blue up) to place it."));
	UpdateCamera();
}

TSharedRef<FEditorViewportClient> SKimodoPreview::MakeEditorViewportClient()
{
	const TSharedRef<FKimodoPreviewClient> PreviewClient = MakeShared<FKimodoPreviewClient>(&Scene, SharedThis(this));
	PreviewClient->SetViewModes(VMI_Lit, VMI_Lit);
	PreviewClient->SetRealtime(true);

	// 동작 확인용 프리뷰이므로 블러·잔상·무거운 후처리를 끈다. (TemporalAA만 꺼서 가벼운 FXAA 유지)
	FEngineShowFlags& ShowFlags = PreviewClient->EngineShowFlags;
	ShowFlags.SetMotionBlur(false);
	ShowFlags.SetTemporalAA(false);
	ShowFlags.SetBloom(false);
	ShowFlags.SetDepthOfField(false);
	ShowFlags.SetEyeAdaptation(false);
	ShowFlags.SetAmbientOcclusion(false);
	ShowFlags.SetScreenSpaceReflections(false);
	ShowFlags.SetLumenGlobalIllumination(false);
	ShowFlags.SetLumenReflections(false);
	ShowFlags.SetPostProcessMaterial(false);
	return PreviewClient;
}

void SKimodoPreview::SyncFromViewportCamera()
{
	const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient();
	if (!PreviewClient) return;

	// 엔진 카메라 조작(궤도·줌·이동) 뒤의 상태를 추적·리셋 계산에 쓸 값으로 옮긴다.
	CameraTarget = PreviewClient->GetLookAtLocation();
	const FVector Offset = PreviewClient->GetViewLocation() - CameraTarget;
	CameraDistance = FMath::Max(Offset.Size(), 10.0);
	OrbitRotation = Offset.Rotation();
}

void SKimodoPreview::ResetCamera()
{
	CameraTarget = FVector(0, 0, 92);
	OrbitRotation = FRotator(11, 48, 0);
	CameraDistance = 410.0f;
	UpdateCamera();
}

void SKimodoPreview::Orbit(const FVector2D& Delta)
{
	SyncFromViewportCamera();
	OrbitRotation.Yaw = FRotator::NormalizeAxis(OrbitRotation.Yaw + Delta.X * 0.35);
	OrbitRotation.Pitch = FMath::Clamp(OrbitRotation.Pitch - Delta.Y * 0.35, -75.0, 75.0);
	UpdateCamera();
}

void SKimodoPreview::Zoom(float WheelDelta)
{
	SyncFromViewportCamera();
	CameraDistance = FMath::Clamp(CameraDistance * FMath::Pow(0.85f, WheelDelta), 110.0f, 1200.0f);
	UpdateCamera();
}

void SKimodoPreview::UpdateCamera()
{
	const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient();
	if (!PreviewClient) return;

	const FVector Offset = OrbitRotation.Vector() * CameraDistance;
	PreviewClient->SetViewLocation(CameraTarget + Offset);
	PreviewClient->SetViewRotation((-Offset).Rotation());
	// 엔진 궤도 회전의 중심
	PreviewClient->SetLookAtLocation(CameraTarget);
	Invalidate();
}

void SKimodoPreview::SetClip(UAnimSequence* Clip, float StartTime, bool bAutoPlay)
{
	bPlaying = false;
	Length = Clip ? Clip->GetPlayLength() : 0.0f;
	Mesh->EnablePreview(false, nullptr);
	Mesh->SetSkeletalMesh(Clip ? Clip->GetPreviewMesh() : nullptr);
	if (Clip) Mesh->EnablePreview(true, Clip);
	if (Mesh->PreviewInstance) Mesh->PreviewInstance->SetPlaying(false);
	// 미리보기 인스턴스는 루트 모션을 포즈에서 빼낸다. 그대로 두면 제자리에서 걷으므로 빼낸 이동을 메시 위치로 돌려준다.
	CurrentClip = Clip;
	bRootMotion = Clip && Clip->HasRootMotion();
	Mesh->SetRelativeTransform(FTransform::Identity);
	DrawRootMotionPath();
	Seek(StartTime);
	RefreshKeyMarkers();
	bPlaying = bAutoPlay && Length > 0.0f;
}

bool SKimodoPreview::SampleHand(float SampleTime, EKimodoKeyBone Bone, FVector& OutHand, FVector& OutRight, FVector& OutForward)
{
	if (!Mesh || !Mesh->PreviewInstance || !Mesh->GetSkeletalMeshAsset() || Length <= 0.0f) return false;
	FName Part;
	switch (Bone)
	{
	case EKimodoKeyBone::LeftHand: Part = FindBone(Mesh, { TEXT("hand_l"), TEXT("LeftHand"), TEXT("Hand_L"), TEXT("mixamorig:LeftHand") }); break;
	case EKimodoKeyBone::RightFoot: Part = FindBone(Mesh, { TEXT("foot_r"), TEXT("RightFoot"), TEXT("Foot_R"), TEXT("mixamorig:RightFoot") }); break;
	case EKimodoKeyBone::LeftFoot: Part = FindBone(Mesh, { TEXT("foot_l"), TEXT("LeftFoot"), TEXT("Foot_L"), TEXT("mixamorig:LeftFoot") }); break;
	case EKimodoKeyBone::Hips: Part = FindBone(Mesh, { TEXT("pelvis"), TEXT("Hips"), TEXT("Pelvis"), TEXT("mixamorig:Hips") }); break;
	default: Part = FindBone(Mesh, { TEXT("hand_r"), TEXT("RightHand"), TEXT("Hand_R"), TEXT("mixamorig:RightHand") }); break;
	}
	if (Part.IsNone()) return false;

	const float Saved = Time;
	Time = FMath::Clamp(SampleTime, 0.0f, Length);
	Mesh->PreviewInstance->SetPosition(Time, false);
	ApplyRootMotion();
	Mesh->TickAnimation(UE_SMALL_NUMBER, true);
	Mesh->RefreshBoneTransforms();
	OutHand = Mesh->GetBoneLocation(Part);

	// 캐릭터의 오른쪽 = 왼어깨 → 오른어깨(수평). 앞 = 오른쪽 × 위(UE: Y × Z = X).
	const FName RightShoulder = FindBone(Mesh, { TEXT("upperarm_r"), TEXT("RightArm"), TEXT("RightShoulder"), TEXT("clavicle_r") });
	const FName LeftShoulder = FindBone(Mesh, { TEXT("upperarm_l"), TEXT("LeftArm"), TEXT("LeftShoulder"), TEXT("clavicle_l") });
	FVector Right = !RightShoulder.IsNone() && !LeftShoulder.IsNone()
		? Mesh->GetBoneLocation(RightShoulder) - Mesh->GetBoneLocation(LeftShoulder)
		: Mesh->GetRightVector();
	Right.Z = 0.0;
	OutRight = Right.GetSafeNormal(UE_SMALL_NUMBER, FVector::RightVector);
	OutForward = FVector::CrossProduct(OutRight, FVector::UpVector);
	Time = Saved;
	return true;
}

void SKimodoPreview::SetHandKeys(const TArray<FKimodoHandTarget>& InKeys, int32 InSelected)
{
	HandKeys = InKeys;
	SelectedKey = HandKeys.IsValidIndex(InSelected) ? InSelected : INDEX_NONE;
	if (!HandKeys.IsValidIndex(SelectedKey)) bDraggingKey = false;
	RefreshKeyMarkers();
}

void SKimodoPreview::RefreshKeyMarkers()
{
	KeyBases.SetNum(HandKeys.Num());
	bool bSampled = false;
	for (int32 Index = 0; Index < HandKeys.Num(); ++Index)
	{
		FKeyBasis& Basis = KeyBases[Index];
		bSampled |= SampleHand(HandKeys[Index].Time, HandKeys[Index].Bone, Basis.Hand, Basis.Right, Basis.Forward);
	}
	if (bSampled && Mesh->PreviewInstance)
	{
		// 표본을 읽느라 바꾼 포즈를 지금 시각으로 되돌린다(카메라는 그대로).
		Mesh->PreviewInstance->SetPosition(Time, false);
		ApplyRootMotion();
		Mesh->TickAnimation(UE_SMALL_NUMBER, true);
		Mesh->RefreshBoneTransforms();
		Mesh->MarkRenderDynamicDataDirty();
	}
	DrawKeyMarkers();
}

FVector SKimodoPreview::KeyTarget(int32 Index) const
{
	const FKeyBasis& Basis = KeyBases[Index];
	const FVector& Offset = HandKeys[Index].Offset;
	return Basis.Hand + Basis.Right * Offset.X + FVector::UpVector * Offset.Y + Basis.Forward * Offset.Z;
}

void SKimodoPreview::DrawKeyMarkers()
{
	KeyMarkers->Flush();
	if (Length > 0.0f)
	{
		for (int32 Index = 0; Index < HandKeys.Num() && Index < KeyBases.Num(); ++Index)
		{
			const bool bSelected = Index == SelectedKey;
			const FVector Target = KeyTarget(Index);
			if (bSelected)
			{
				// 고른 키: 기준 위치(작은 흰 십자)에서 목표까지 선. 목표에는 엔진 이동 기즈모가 그려진다.
				const FVector& Base = KeyBases[Index].Hand;
				KeyMarkers->DrawLine(Base, Target, FLinearColor(1, 1, 1, 0.8f), SDPG_Foreground, 1.0f, 0.0f);
				KeyMarkers->DrawLine(Base - FVector(0, 0, 2), Base + FVector(0, 0, 2), FLinearColor::White, SDPG_Foreground, 1.0f, 0.0f);
				KeyMarkers->DrawLine(Base - FVector(2, 0, 0), Base + FVector(2, 0, 0), FLinearColor::White, SDPG_Foreground, 1.0f, 0.0f);
				continue;
			}
			// 다른 키: 하늘색 십자(몸에 가려지지 않게 앞쪽에 그린다)
			const float Size = 3.0f;
			const FLinearColor Color(0.2f, 0.8f, 1.0f);
			KeyMarkers->DrawLine(Target - FVector(Size, 0, 0), Target + FVector(Size, 0, 0), Color, SDPG_Foreground, 1.5f, 0.0f);
			KeyMarkers->DrawLine(Target - FVector(0, Size, 0), Target + FVector(0, Size, 0), Color, SDPG_Foreground, 1.5f, 0.0f);
			KeyMarkers->DrawLine(Target - FVector(0, 0, Size), Target + FVector(0, 0, Size), Color, SDPG_Foreground, 1.5f, 0.0f);
		}
	}
	if (const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient()) PreviewClient->Invalidate();
}

bool SKimodoPreview::GetKeyGizmo(FVector& OutLocation, FMatrix& OutAxes) const
{
	if (!HandKeys.IsValidIndex(SelectedKey) || !KeyBases.IsValidIndex(SelectedKey) || Length <= 0.0f) return false;
	const FKeyBasis& Basis = KeyBases[SelectedKey];
	OutLocation = KeyTarget(SelectedKey);
	// 기즈모 축: X = 캐릭터 앞, Y = 오른쪽, Z = 위
	OutAxes = FMatrix(Basis.Forward, Basis.Right, FVector::UpVector, FVector::ZeroVector);
	return true;
}

void SKimodoPreview::MoveSelectedKey(const FVector& WorldDelta)
{
	if (!HandKeys.IsValidIndex(SelectedKey) || !KeyBases.IsValidIndex(SelectedKey)) return;
	bPlaying = false;
	const FKeyBasis& Basis = KeyBases[SelectedKey];
	const FVector Relative = KeyTarget(SelectedKey) + WorldDelta - Basis.Hand;
	FVector& Offset = HandKeys[SelectedKey].Offset;
	Offset.X = FMath::Clamp(FVector::DotProduct(Relative, Basis.Right), -100.0, 100.0);
	Offset.Y = FMath::Clamp(Relative.Z, -100.0, 100.0);
	Offset.Z = FMath::Clamp(FVector::DotProduct(Relative, Basis.Forward), -100.0, 100.0);
	DrawKeyMarkers();
	OnHandKeyDragged.ExecuteIfBound(SelectedKey, Offset);
}

void SKimodoPreview::SetDraggingKey(bool bDragging)
{
	bDraggingKey = bDragging && HandKeys.IsValidIndex(SelectedKey);
	if (bDraggingKey) bPlaying = false;
}

void SKimodoPreview::Scrub(float Seconds)
{
	bPlaying = false;
	Seek(Seconds);
}

void SKimodoPreview::Seek(float Seconds)
{
	Time = FMath::Clamp(Seconds, 0.0f, Length);
	if (Length > 0.0f && Mesh->PreviewInstance)
	{
		// 정지 상태에서도 지정 시점의 본 트랜스폼을 즉시 갱신한다.
		Mesh->PreviewInstance->SetPosition(Time, false);
		ApplyRootMotion();
		Mesh->TickAnimation(UE_SMALL_NUMBER, true);
		Mesh->RefreshBoneTransforms();
		Mesh->MarkRenderDynamicDataDirty();
	}
	Scene.GetWorld()->SendAllEndOfFrameUpdates();
	FollowTarget();
	if (const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient())
	{
		PreviewClient->Invalidate();
	}
}

void SKimodoPreview::ApplyRootMotion()
{
	if (!bRootMotion || !CurrentClip) return;

	// 시작 루트 위치 + 0초~Time 사이의 루트 이동. 시간으로 바로 계산하므로 스크럽해도 위치가 맞고, 반복하면 처음 자리로 돌아온다.
	const FTransform Start = UE::Anim::ExtractRootTransformFromAnimationAsset(CurrentClip, 0.0f);
	const FTransform Delta = UE::Anim::ExtractRootMotionFromAnimationAsset(CurrentClip, nullptr, 0.0f, Time);
	Mesh->SetRelativeTransform(Delta * Start);
}

void SKimodoPreview::DrawRootMotionPath()
{
	RootPath->Flush();
	if (!bRootMotion || !CurrentClip || Length <= 0.0f) return;

	const FTransform Start = UE::Anim::ExtractRootTransformFromAnimationAsset(CurrentClip, 0.0f);
	const int32 Samples = FMath::Clamp(FMath::CeilToInt(Length * 30.0f), 2, 600);
	const FVector Lift(0, 0, 0.5);  // 바닥과 겹쳐 깜빡이지 않게 살짝 띄운다.
	const FLinearColor PathColor(1.0f, 0.75f, 0.1f);
	FVector Previous = Start.GetLocation() + Lift;
	for (int32 Index = 1; Index <= Samples; ++Index)
	{
		const float SampleTime = Length * Index / Samples;
		const FTransform Delta = UE::Anim::ExtractRootMotionFromAnimationAsset(CurrentClip, nullptr, 0.0f, SampleTime);
		const FVector Point = (Delta * Start).GetLocation() + Lift;
		RootPath->DrawLine(Previous, Point, PathColor, SDPG_World, 1.5f, 0.0f);
		Previous = Point;
	}
	// 시작(초록)·끝(빨강) 표시
	const FVector First = Start.GetLocation() + Lift;
	RootPath->DrawLine(First - FVector(6, 0, 0), First + FVector(6, 0, 0), FLinearColor::Green, SDPG_World, 1.5f, 0.0f);
	RootPath->DrawLine(First - FVector(0, 6, 0), First + FVector(0, 6, 0), FLinearColor::Green, SDPG_World, 1.5f, 0.0f);
	RootPath->DrawLine(Previous - FVector(6, 0, 0), Previous + FVector(6, 0, 0), FLinearColor::Red, SDPG_World, 1.5f, 0.0f);
	RootPath->DrawLine(Previous - FVector(0, 6, 0), Previous + FVector(0, 6, 0), FLinearColor::Red, SDPG_World, 1.5f, 0.0f);
}

void SKimodoPreview::SetFollowEnabled(bool bEnabled)
{
	bFollow = bEnabled;
	if (bFollow) FollowTarget();
}

void SKimodoPreview::FollowTarget()
{
	// 추적을 끄면 재생 중에도 카메라를 건드리지 않는다. 카메라를 끄는 중에도 덮어쓰지 않는다.
	if (!bFollow || bOrbiting || FollowBone.IsNone() || Length <= 0.0f || !Mesh->GetSkeletalMeshAsset()) return;

	const FVector Location = Mesh->GetBoneIndex(FollowBone) != INDEX_NONE
		? Mesh->GetBoneLocation(FollowBone)
		: Mesh->Bounds.Origin;

	// 사용자가 돌리거나 줌한 카메라 각도·거리는 유지하고 중심만 옮긴다. 높이는 그대로 두어 위아래로 흔들리지 않게 한다.
	SyncFromViewportCamera();
	CameraTarget.X = Location.X;
	CameraTarget.Y = Location.Y;
	UpdateCamera();
}

void SKimodoPreview::TogglePlay()
{
	if (Length <= 0.0f) return;
	if (!bPlaying && Time >= Length) Seek(0.0f);
	bPlaying = !bPlaying;
}

void SKimodoPreview::Tick(const FGeometry& Geometry, double CurrentTime, float DeltaTime)
{
	SEditorViewport::Tick(Geometry, CurrentTime, DeltaTime);
	if (!bPlaying || bDraggingKey) return;

	Seek(FMath::Fmod(Time + DeltaTime, Length));
}

#undef LOCTEXT_NAMESPACE
