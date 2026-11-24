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
	/** 재생 중 카메라 추적. 기본은 꺼져 있어 카메라가 사용자가 둔 자리에 머문다. */
	void SetFollowEnabled(bool bEnabled);
	bool IsFollowEnabled() const { return bFollow; }
	/** 좌클릭 드래그 중에는 추적이 카메라 중심을 덮어쓰지 않도록 한다. */
	void SetOrbiting(bool bInOrbiting) { bOrbiting = bInOrbiting; }
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
	/** 재생 중 카메라 중심을 FollowBone(없으면 메시 경계 중심)의 수평 위치로 옮긴다. */
	void FollowTarget();
	UDebugSkelMeshComponent* Mesh = nullptr;
	FName FollowBone;
	FVector CameraTarget = FVector(0, 0, 92);
	FRotator OrbitRotation = FRotator(11, 48, 0);
	float CameraDistance = 410.0f;
	float Time = 0.0f;
	float Length = 0.0f;
	bool bPlaying = false;
	bool bFollow = false;
	bool bOrbiting = false;
};
