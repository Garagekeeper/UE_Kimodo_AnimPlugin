#pragma once

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

class UAnimSequence;
class UDebugSkelMeshComponent;

class FKimodoPreviewSceneOwner
{
protected:
	FAdvancedPreviewScene Scene{FPreviewScene::ConstructionValues().SetCreatePhysicsScene(false).SetTransactional(false)};
};

class SKimodoPreview : private FKimodoPreviewSceneOwner, public SEditorViewport
{
public:
	SLATE_BEGIN_ARGS(SKimodoPreview) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& Args);
	void SetClip(UAnimSequence* Clip, float StartTime = 0.0f, bool bAutoPlay = true);
	/** 카메라가 따라갈 본. 메시에 없으면 메시 경계 중심을 따라간다. */
	void SetFollowBone(FName Bone) { FollowBone = Bone; }
	void Scrub(float Seconds);
	void TogglePlay();
	void SyncFromViewportCamera();
	void ResetCamera();
	void Orbit(const FVector2D& Delta);
	void Zoom(float WheelDelta);
	float GetTime() const { return Time; }
	float GetLength() const { return Length; }
	bool IsPlaying() const { return bPlaying; }

protected:
	TSharedRef<FEditorViewportClient> MakeEditorViewportClient() override;
	void Tick(const FGeometry& Geometry, double CurrentTime, float DeltaTime) override;

private:
	void Seek(float Seconds);
	void UpdateCamera();
	UDebugSkelMeshComponent* Mesh = nullptr;
	FName FollowBone;
	FVector CameraTarget = FVector(0, 0, 92);
	FRotator OrbitRotation = FRotator(11, 48, 0);
	float CameraDistance = 410.0f;
	float Time = 0.0f;
	float Length = 0.0f;
	bool bPlaying = false;
};
