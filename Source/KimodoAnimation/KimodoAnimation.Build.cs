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
				"EditorFramework",
				"Slate",
				"SlateCore",
				"ToolMenus",
				"PropertyEditor",
				"ContentBrowser",
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
