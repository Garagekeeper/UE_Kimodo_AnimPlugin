#pragma once

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

class UAnimSequence;
class UDebugSkelMeshComponent;
class ULineBatchComponent;

class FKimodoPreviewSceneOwner
{
protected:
	FAdvancedPreviewScene Scene{FPreviewScene::ConstructionValues().SetCreatePhysicsScene(false).SetTransactional(false)};
};

/** Body keys가 정할 수 있는 부위(Kimodo end-effector: 양손·양발·골반). 패널·Python과 같은 순서. */
enum class EKimodoKeyBone : uint8 { RightHand, LeftHand, RightFoot, LeftFoot, Hips };

/** 미리보기에 표시·드래그할 키프레임. Offset은 cm(캐릭터 기준 X=오른쪽, Y=위, Z=앞). */
struct FKimodoHandTarget
{
	float Time = 0.0f;
	EKimodoKeyBone Bone = EKimodoKeyBone::RightHand;
	FVector Offset = FVector::ZeroVector;
};

/**
 * 미리보기의 엔진 이동 기즈모를 켜는 에디터 모드. 엔진 기즈모는 활성 모드가 "위젯을 그린다"고 할 때만 그려지고
 * 클릭되므로, 모듈 시작 때 등록하고 미리보기 뷰포트에서만 켠다.
 */
void RegisterKimodoKeyEdMode();
void UnregisterKimodoKeyEdMode();

/** 손 키 핸들을 드래그해 오프셋이 바뀌었을 때(키 번호, 새 오프셋 cm). */
DECLARE_DELEGATE_TwoParams(FOnKimodoHandKeyDragged, int32, const FVector&);

class SKimodoPreview : private FKimodoPreviewSceneOwner, public SEditorViewport
{
public:
	SLATE_BEGIN_ARGS(SKimodoPreview) {}
		SLATE_EVENT(FOnKimodoHandKeyDragged, OnHandKeyDragged)
	SLATE_END_ARGS()

	void Construct(const FArguments& Args);
	void SetClip(UAnimSequence* Clip, float StartTime = 0.0f, bool bAutoPlay = true);
	/** 카메라가 따라갈 본. 메시에 없으면 메시 경계 중심을 따라간다. */
	void SetFollowBone(FName Bone) { FollowBone = Bone; }
	/** 재생 중 카메라 추적. 기본은 꺼져 있어 카메라가 사용자가 둔 자리에 머문다. */
	void SetFollowEnabled(bool bEnabled);
	bool IsFollowEnabled() const { return bFollow; }
	/** 카메라 드래그 중에는 추적이 카메라 중심을 덮어쓰지 않도록 한다. */
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

	/** 손 키 목록과 선택한 키(-1이면 없음). 각 키의 목표 위치에 표시를 그리고, 선택한 키는 드래그할 수 있다. */
	void SetHandKeys(const TArray<FKimodoHandTarget>& InKeys, int32 InSelected);
	/** 엔진 이동 기즈모용: 고른 키의 목표 위치와 캐릭터 축(X 앞, Y 오른쪽, Z 위). 고른 키가 없으면 false. */
	bool GetKeyGizmo(FVector& OutLocation, FMatrix& OutAxes) const;
	/** 기즈모 드래그(월드 이동량)만큼 고른 키를 옮기고 패널에 알린다. */
	void MoveSelectedKey(const FVector& WorldDelta);
	void SetDraggingKey(bool bDragging);

protected:
	TSharedRef<FEditorViewportClient> MakeEditorViewportClient() override;
	void Tick(const FGeometry& Geometry, double CurrentTime, float DeltaTime) override;

private:
	void Seek(float Seconds);
	void UpdateCamera();
	/** 재생 중 카메라 중심을 FollowBone(없으면 메시 경계 중심)의 수평 위치로 옮긴다. */
	void FollowTarget();
	/** 루트 모션 클립이면 Time까지의 루트 이동을 메시 위치에 적용한다(애니메이션 에디터의 Loop and Reset과 같음). */
	void ApplyRootMotion();
	/** 루트 모션 경로를 바닥에 선으로 그린다. 루트 모션이 없으면 지운다. */
	void DrawRootMotionPath();
	/** Time 시점의 손 위치와 캐릭터 기준 방향(오른쪽·앞, 수평)을 읽는다. 카메라는 건드리지 않는다. */
	bool SampleHand(float SampleTime, EKimodoKeyBone Bone, FVector& OutHand, FVector& OutRight, FVector& OutForward);
	/** 키마다 기준 손 위치·방향을 다시 계산하고 표시를 그린다(클립·키가 바뀔 때). */
	void RefreshKeyMarkers();
	void DrawKeyMarkers();
	FVector KeyTarget(int32 Index) const;

	struct FKeyBasis
	{
		FVector Hand = FVector::ZeroVector;
		FVector Right = FVector::RightVector;
		FVector Forward = FVector::ForwardVector;
	};
	TArray<FKimodoHandTarget> HandKeys;
	TArray<FKeyBasis> KeyBases;
	int32 SelectedKey = INDEX_NONE;
	bool bDraggingKey = false;
	FOnKimodoHandKeyDragged OnHandKeyDragged;
	ULineBatchComponent* KeyMarkers = nullptr;

	UDebugSkelMeshComponent* Mesh = nullptr;
	ULineBatchComponent* RootPath = nullptr;
	const UAnimSequence* CurrentClip = nullptr;
	bool bRootMotion = false;
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
