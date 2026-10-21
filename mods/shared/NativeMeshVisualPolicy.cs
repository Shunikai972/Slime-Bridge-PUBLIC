using System;
namespace SlimeBridge.Shared
{
    /// <summary>Authored shader metadata only: no guessed colors, meshes or Animator state.</summary>
    public static class NativeMeshVisualPolicy
    {
        public static bool InScope(string role) { return role != "source-effect"; }
        public static string Family(NativeMeshMaterial source, string role)
        {
            if (source.FaceRole == "eyes" || source.Colors.ContainsKey("_EyeRed") || source.Shader.IndexOf("/Face/Eyes", StringComparison.OrdinalIgnoreCase) >= 0) return "eyes";
            if (source.FaceRole == "mouth" || source.Colors.ContainsKey("_MouthMid") || source.Shader.IndexOf("/Face/Mouth", StringComparison.OrdinalIgnoreCase) >= 0) return "mouth";
            return role == "face" ? "face" : "body";
        }
        public static bool IsRequiredTexture(string property)
        {
            return property == "_FaceAtlas" || property == "_MainTex" || property == "_BaseMap" || property == "_Diffuse" || property == "_ColorMask" || property == "_StripeTexture";
        }
    }
}
