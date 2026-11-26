#include "SKimodoPreview.h"

#include "Animation/AnimSequence.h"
#include "Animation/AnimSequenceHelpers.h"
#include "Components/LineBatchComponent.h"
#include "Animation/DebugSkelMeshComponent.h"
#include "AnimPreviewInstance.h"
#include "EditorViewportClient.h"
#include "Engine/World.h"
#include "InputCoreTypes.h"
#include "Widgets/SCompoundWidget.h"

#define LOCTEXT_NAMESPACE "KimodoPreview"

namespace
{
class SKimodoOrbitInput : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SKimodoOrbitInput) {}
		SLATE_ARGUMENT(TWeakPtr<SKimodoPreview>, Preview)
	SLATE_END_ARGS()

	void Construct(const FArguments& Args)
	{
		Preview = Args._Preview;
		SetToolTipText(LOCTEXT("CameraControls", "Drag with the left mouse button to orbit. Scroll to zoom.\nDrag the yellow hand key handle to move the selected hand key."));
	}

	FReply OnMouseButtonDown(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (Event.GetEffectingButton() != EKeys::LeftMouseButton) return FReply::Unhandled();

		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin())
		{
			// 선택한 손 키 핸들 위를 누르면 그 키를 옮기고, 아니면 카메라를 돌린다.
			const FVector2D Local = Geometry.AbsoluteToLocal(Event.GetScreenSpacePosition());
			if (!Viewport->TryBeginKeyDrag(Local, Geometry.GetLocalSize()))
			{
				Viewport->SyncFromViewportCamera();
				Viewport->SetOrbiting(true);
			}
		}
		return FReply::Handled().CaptureMouse(SharedThis(this)).PreventThrottling();
	}

	FReply OnMouseButtonUp(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!HasMouseCapture() || Event.GetEffectingButton() != EKeys::LeftMouseButton) return FReply::Unhandled();

		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin())
		{
			Viewport->SetOrbiting(false);
			Viewport->EndKeyDrag();
		}
		return FReply::Handled().ReleaseMouseCapture();
	}

	void OnMouseCaptureLost(const FCaptureLostEvent& CaptureLostEvent) override
	{
		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin())
		{
			Viewport->SetOrbiting(false);
			Viewport->EndKeyDrag();
		}
	}

	FReply OnMouseMove(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!HasMouseCapture()) return FReply::Unhandled();

		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin())
		{
			if (Viewport->IsDraggingKey())
			{
				// 커서 이동량은 화면 픽셀이므로 위젯 로컬 단위(DPI 배율 반영)로 바꾼다.
				Viewport->DragKey(Event.GetCursorDelta() / FMath::Max(Geometry.Scale, UE_SMALL_NUMBER), Geometry.GetLocalSize());
			}
			else
			{
				Viewport->Orbit(Event.GetCursorDelta());
			}
		}
		return FReply::Handled();
	}

	FReply OnMouseWheel(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin())
		{
			Viewport->SyncFromViewportCamera();
			Viewport->Zoom(Event.GetWheelDelta());
		}
		return FReply::Handled();
	}

private:
	TWeakPtr<SKimodoPreview> Preview;
};
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
	AddOverlayWidget(SNew(SKimodoOrbitInput).Preview(SharedThis(this)));
	UpdateCamera();
}

TSharedRef<FEditorViewportClient> SKimodoPreview::MakeEditorViewportClient()
{
	const TSharedRef<FEditorViewportClient> PreviewClient = MakeShared<FEditorViewportClient>(nullptr, &Scene, SharedThis(this));
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

	// 우클릭/WASD 등으로 이동한 카메라 상태가 좌클릭 회전·휠 줌 시 초기화되지 않도록 동기화한다.
	const FVector Forward = PreviewClient->GetViewRotation().Vector();
	CameraTarget = PreviewClient->GetViewLocation() + Forward * CameraDistance;
	OrbitRotation = (-Forward).Rotation();
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
	OrbitRotation.Yaw = FRotator::NormalizeAxis(OrbitRotation.Yaw + Delta.X * 0.35);
	OrbitRotation.Pitch = FMath::Clamp(OrbitRotation.Pitch - Delta.Y * 0.35, -75.0, 75.0);
	UpdateCamera();
}

void SKimodoPreview::Zoom(float WheelDelta)
{
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

namespace
{
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

bool SKimodoPreview::SampleHand(float SampleTime, EKimodoKeyBone Bone, FVector& OutHand, FVector& OutRight, FVector& OutForward)
{
	if (!Mesh || !Mesh->PreviewInstance || !Mesh->GetSkeletalMeshAsset() || Length <= 0.0f) return false;
	FName Hand;
	switch (Bone)
	{
	case EKimodoKeyBone::LeftHand: Hand = FindBone(Mesh, { TEXT("hand_l"), TEXT("LeftHand"), TEXT("Hand_L"), TEXT("mixamorig:LeftHand") }); break;
	case EKimodoKeyBone::RightFoot: Hand = FindBone(Mesh, { TEXT("foot_r"), TEXT("RightFoot"), TEXT("Foot_R"), TEXT("mixamorig:RightFoot") }); break;
	case EKimodoKeyBone::LeftFoot: Hand = FindBone(Mesh, { TEXT("foot_l"), TEXT("LeftFoot"), TEXT("Foot_L"), TEXT("mixamorig:LeftFoot") }); break;
	case EKimodoKeyBone::Hips: Hand = FindBone(Mesh, { TEXT("pelvis"), TEXT("Hips"), TEXT("Pelvis"), TEXT("mixamorig:Hips") }); break;
	default: Hand = FindBone(Mesh, { TEXT("hand_r"), TEXT("RightHand"), TEXT("Hand_R"), TEXT("mixamorig:RightHand") }); break;
	}
	if (Hand.IsNone()) return false;

	const float Saved = Time;
	Time = FMath::Clamp(SampleTime, 0.0f, Length);
	Mesh->PreviewInstance->SetPosition(Time, false);
	ApplyRootMotion();
	Mesh->TickAnimation(UE_SMALL_NUMBER, true);
	Mesh->RefreshBoneTransforms();
	OutHand = Mesh->GetBoneLocation(Hand);

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
			const float Size = bSelected ? 6.0f : 3.0f;
			const FLinearColor Color = bSelected ? FLinearColor(1.0f, 0.8f, 0.1f) : FLinearColor(0.2f, 0.8f, 1.0f);
			const float Thickness = bSelected ? 2.5f : 1.5f;
			// 앞쪽에 그려 몸에 가려지지 않게 한다.
			KeyMarkers->DrawLine(Target - FVector(Size, 0, 0), Target + FVector(Size, 0, 0), Color, SDPG_Foreground, Thickness, 0.0f);
			KeyMarkers->DrawLine(Target - FVector(0, Size, 0), Target + FVector(0, Size, 0), Color, SDPG_Foreground, Thickness, 0.0f);
			KeyMarkers->DrawLine(Target - FVector(0, 0, Size), Target + FVector(0, 0, Size), Color, SDPG_Foreground, Thickness, 0.0f);
			if (bSelected)
			{
				// 기준 손 위치(작은 흰 십자)에서 목표까지 선
				const FVector& Hand = KeyBases[Index].Hand;
				KeyMarkers->DrawLine(Hand, Target, FLinearColor(1, 1, 1, 0.8f), SDPG_Foreground, 1.0f, 0.0f);
				KeyMarkers->DrawLine(Hand - FVector(0, 0, 2), Hand + FVector(0, 0, 2), FLinearColor::White, SDPG_Foreground, 1.0f, 0.0f);
				KeyMarkers->DrawLine(Hand - FVector(2, 0, 0), Hand + FVector(2, 0, 0), FLinearColor::White, SDPG_Foreground, 1.0f, 0.0f);
			}
		}
	}
	if (const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient()) PreviewClient->Invalidate();
}

FVector2D SKimodoPreview::ProjectToLocal(const FVector& World, const FVector2D& LocalSize, bool& bInFront) const
{
	const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient();
	bInFront = false;
	if (!PreviewClient || LocalSize.X <= 0.0) return FVector2D::ZeroVector;
	const FRotationMatrix Axes(PreviewClient->GetViewRotation());
	const FVector Forward = Axes.GetScaledAxis(EAxis::X), Right = Axes.GetScaledAxis(EAxis::Y), Up = Axes.GetScaledAxis(EAxis::Z);
	const FVector Direction = World - PreviewClient->GetViewLocation();
	const double Depth = FVector::DotProduct(Direction, Forward);
	bInFront = Depth > 1.0;
	if (!bInFront) return FVector2D::ZeroVector;
	// 에디터 뷰포트 FOV는 가로 기준이다.
	const double Scale = (LocalSize.X * 0.5) / FMath::Tan(FMath::DegreesToRadians(PreviewClient->ViewFOV * 0.5));
	return FVector2D(LocalSize.X * 0.5 + FVector::DotProduct(Direction, Right) / Depth * Scale,
		LocalSize.Y * 0.5 - FVector::DotProduct(Direction, Up) / Depth * Scale);
}

bool SKimodoPreview::TryBeginKeyDrag(const FVector2D& LocalPosition, const FVector2D& LocalSize)
{
	if (!HandKeys.IsValidIndex(SelectedKey) || !KeyBases.IsValidIndex(SelectedKey) || Length <= 0.0f) return false;
	bool bInFront = false;
	const FVector2D Handle = ProjectToLocal(KeyTarget(SelectedKey), LocalSize, bInFront);
	bDraggingKey = bInFront && FVector2D::Distance(Handle, LocalPosition) <= 16.0;
	if (bDraggingKey) bPlaying = false;
	return bDraggingKey;
}

void SKimodoPreview::DragKey(const FVector2D& LocalDelta, const FVector2D& LocalSize)
{
	const TSharedPtr<FEditorViewportClient> PreviewClient = GetViewportClient();
	if (!bDraggingKey || !PreviewClient || !HandKeys.IsValidIndex(SelectedKey) || LocalSize.X <= 0.0) return;

	// 카메라와 마주 보는 평면에서 커서를 따라 움직인다(핸들 깊이에서 1픽셀 = 이만큼의 cm).
	const FRotationMatrix Axes(PreviewClient->GetViewRotation());
	const FVector Forward = Axes.GetScaledAxis(EAxis::X), Right = Axes.GetScaledAxis(EAxis::Y), Up = Axes.GetScaledAxis(EAxis::Z);
	const FVector Target = KeyTarget(SelectedKey);
	const double Depth = FMath::Max(FVector::DotProduct(Target - PreviewClient->GetViewLocation(), Forward), 1.0);
	const double PerPixel = Depth * FMath::Tan(FMath::DegreesToRadians(PreviewClient->ViewFOV * 0.5)) / (LocalSize.X * 0.5);
	const FVector Moved = Target + (Right * LocalDelta.X - Up * LocalDelta.Y) * PerPixel;

	const FKeyBasis& Basis = KeyBases[SelectedKey];
	const FVector Relative = Moved - Basis.Hand;
	FVector& Offset = HandKeys[SelectedKey].Offset;
	Offset.X = FMath::Clamp(FVector::DotProduct(Relative, Basis.Right), -100.0, 100.0);
	Offset.Y = FMath::Clamp(Relative.Z, -100.0, 100.0);
	Offset.Z = FMath::Clamp(FVector::DotProduct(Relative, Basis.Forward), -100.0, 100.0);
	DrawKeyMarkers();
	OnHandKeyDragged.ExecuteIfBound(SelectedKey, Offset);
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
	// 추적을 끄면 재생 중에도 카메라를 건드리지 않아 좌클릭 회전이 그대로 유지된다.
	if (!bFollow || bOrbiting || FollowBone.IsNone() || Length <= 0.0f || !Mesh->GetSkeletalMeshAsset()) return;

	const FVector Location = Mesh->GetBoneIndex(FollowBone) != INDEX_NONE
		? Mesh->GetBoneLocation(FollowBone)
		: Mesh->Bounds.Origin;

	// 높이는 그대로 두어 걷기·점프할 때 카메라가 위아래로 흔들리지 않게 한다.
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
	if (!bPlaying) return;

	Seek(FMath::Fmod(Time + DeltaTime, Length));
}

#undef LOCTEXT_NAMESPACE
