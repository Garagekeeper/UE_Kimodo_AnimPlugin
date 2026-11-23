#include "SKimodoPreview.h"

#include "Animation/AnimSequence.h"
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
		SetToolTipText(LOCTEXT("CameraControls", "Drag with the left mouse button to orbit. Scroll to zoom."));
	}

	FReply OnMouseButtonDown(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (Event.GetEffectingButton() != EKeys::LeftMouseButton) return FReply::Unhandled();

		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin()) Viewport->SyncFromViewportCamera();
		return FReply::Handled().CaptureMouse(SharedThis(this)).PreventThrottling();
	}

	FReply OnMouseButtonUp(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!HasMouseCapture() || Event.GetEffectingButton() != EKeys::LeftMouseButton) return FReply::Unhandled();

		return FReply::Handled().ReleaseMouseCapture();
	}

	FReply OnMouseMove(const FGeometry& Geometry, const FPointerEvent& Event) override
	{
		if (!HasMouseCapture()) return FReply::Unhandled();

		if (const TSharedPtr<SKimodoPreview> Viewport = Preview.Pin()) Viewport->Orbit(Event.GetCursorDelta());
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
	Mesh = NewObject<UDebugSkelMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Mesh->SuspendClothingSimulation();
	Scene.AddComponent(Mesh, FTransform::Identity);
	Mesh->SetUpdateAnimationInEditor(true);
	Mesh->SetComponentTickEnabled(false);
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
	Seek(StartTime);
	bPlaying = bAutoPlay && Length > 0.0f;
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

void SKimodoPreview::FollowTarget()
{
	if (FollowBone.IsNone() || Length <= 0.0f || !Mesh->GetSkeletalMeshAsset()) return;

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
