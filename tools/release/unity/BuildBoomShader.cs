using System;
using System.IO;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;

public static class BuildBoomShader
{
    public static void Run()
    {
        const string asset = "Assets/ImportedBoom.shader";
        var shader = AssetDatabase.LoadAssetAtPath<Shader>(asset);
        if (shader == null || ShaderUtil.ShaderHasError(shader)) throw new Exception("Imported Boom shader import failed");
        var output = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Output"));
        Directory.CreateDirectory(output);
        var manifest = BuildPipeline.BuildAssetBundles(output,
            new[] { new AssetBundleBuild { assetBundleName = "slimebridge-boom", assetNames = new[] { asset } } },
            BuildAssetBundleOptions.ChunkBasedCompression | BuildAssetBundleOptions.ForceRebuildAssetBundle,
            BuildTarget.StandaloneWindows64);
        if (manifest == null || ShaderUtil.ShaderHasError(shader)) throw new Exception("Imported Boom shader bundle failed");
        foreach (var message in ShaderUtil.GetShaderMessages(shader))
            if (message.severity == ShaderCompilerMessageSeverity.Error) throw new Exception(message.message);
        File.WriteAllText(Path.Combine(output, "BUILD-RECEIPT.json"),
            JsonUtility.ToJson(new Receipt { unity = Application.unityVersion, shader = shader.name,
                target = "StandaloneWindows64", shaderErrors = false }, true));
        Debug.Log("BOOM_SHADER_BUILD_SUCCESS " + shader.name);
    }

    [Serializable] private class Receipt { public string unity, shader, target; public bool shaderErrors; }
}
