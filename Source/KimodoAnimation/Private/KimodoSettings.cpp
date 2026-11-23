#include "KimodoSettings.h"

UKimodoSettings::UKimodoSettings()
{
	// UE5 마네킹 계열 스켈레톤의 기본값. 다른 스켈레톤은 프로젝트 설정에서 바꾼다.
	AutoAlignBones = {TEXT("upperarm_l"), TEXT("lowerarm_l"), TEXT("upperarm_r"), TEXT("lowerarm_r")};
	PreviewFollowBone = TEXT("pelvis");
	OutputFolder.Path = TEXT("/Game/Kimodo");
	DefaultAssetName = TEXT("AS_Kimodo_Motion");
	ModelName = TEXT("Kimodo-SOMA-RP-v1.2");
}
