using UnrealBuildTool;

public class KimodoAnimation : ModuleRules
{
	public KimodoAnimation(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine",
				"DeveloperSettings",
			}
		);

		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"Projects",
				"UnrealEd",
				"Slate",
				"SlateCore",
				"ToolMenus",
				"PropertyEditor",
				"PythonScriptPlugin",
				"AdvancedPreviewScene",
				"AnimGraph",
				"InputCore",
				"Json",
				"IKRig",
			}
		);
	}
}
