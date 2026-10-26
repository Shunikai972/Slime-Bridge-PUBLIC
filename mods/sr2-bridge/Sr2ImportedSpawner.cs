using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using SlimeBridge.Shared;
using UnityEngine;
namespace SlimeBridge.SR2
{
    /// <summary>Own native shader clones, never generic Shader.Find or shared-asset writes.</summary>
    internal static class Sr2ImportedSpawner
    {
        private sealed class Binding
        {
            internal NativeMeshCache Cache = null!;
            internal ImportedSpeciesEntry Entry = null!;
            internal Il2Cpp.SlimeDefinition? Template;
            internal string LastProblem = "";
        }
        private static NativeMeshCache? _cache;
        // SLIMEBRIDGE_IMPORT_CACHE_ROOT: <root>/<canonical id>/ per imported species or Largo (the launcher's Lab/AssetCache/sr1-to-sr2).
        private static string? _cacheRoot;
        private static string? _repoRoot;
        private static readonly Dictionary<string, NativeMeshCache?> RootCaches = new Dictionary<string, NativeMeshCache?>(StringComparer.Ordinal);
        private static string _cacheProblem = "cache not requested";
        private static readonly Dictionary<IntPtr, Binding> Bindings = new Dictionary<IntPtr, Binding>();
        internal static void Configure(bool labExecutable)
        {
            _cache = null; Bindings.Clear(); _cacheRoot = null; _repoRoot = null; RootCaches.Clear();
            _cacheProblem = "cache not requested";
            var rootPath = labExecutable ? Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_CACHE_ROOT") : null;
            if (string.IsNullOrEmpty(rootPath) && PlayerContent.TryGet("SR2", out var player)) rootPath = player.CacheRoot;
            if (!string.IsNullOrEmpty(rootPath))
            {
                try
                {
                    _repoRoot = LabSpecPathGuard.FindRepoRoot();
                    if (_repoRoot == null) throw new FormatException("Bridge LAB root missing");
                    if (!Path.IsPathRooted(rootPath) || !LabSpecPathGuard.IsAllowedDirectory(
                        Path.Combine(_repoRoot, LabSpecPathGuard.AssetCacheRelativeRoot), rootPath, out var rootReason))
                        throw new FormatException("cache root outside allowed LAB directory");
                    _cacheRoot = rootPath;
                    Sr2ImportedAppearancePatch.Install();
                    _cacheProblem = "";
                }
                catch (Exception ex)
                {
                    _cacheRoot = null;
                    _cacheProblem = "cache root refused: " + ex.GetType().Name + ": " + ex.Message;
                    Debug.Log("[SlimeBridge] " + _cacheProblem);
                }
            }
            var path = Environment.GetEnvironmentVariable("SLIMEBRIDGE_IMPORT_CACHE_DIR");
            if (string.IsNullOrEmpty(path)) return;
            if (!labExecutable) { _cacheProblem = "cache refused: not authorized LAB executable"; return; }
            try
            {
                var repo = LabSpecPathGuard.FindRepoRoot();
                if (repo == null) throw new FormatException("Bridge LAB root missing");
                _cache = NativeMeshCache.Load(repo, path);
                Sr2ImportedAppearancePatch.Install();
                _cacheProblem = "";
            }
            catch (Exception ex)
            {
                _cache = null;
                _cacheProblem = "cache refused: " + ex.GetType().Name + ": " + ex.Message;
                Debug.Log("[SlimeBridge] " + _cacheProblem);
            }
        }
        internal static string Register(Il2Cpp.SlimeDefinition definition, ImportedSpeciesEntry entry, GameObject prefab,
            Il2Cpp.SlimeDefinition? template)
        {
            var rootProblem = "";
            var cache = _cache != null && _cache.CanonicalId == entry.canonical_id ? _cache : CacheFromRoot(entry.canonical_id, out rootProblem);
            if (cache == null)
            {
                if (_cacheRoot != null) return rootProblem + "; placeholder retained";
                if (_cache == null) return _cacheProblem;
                return "cache canonical_id differs; placeholder retained";
            }
            Bindings[definition.Pointer] = new Binding {Cache = cache, Entry = entry, Template = template};
            // Only cloned prefab fields, never the native template assets. Visual setup failures
            // must not roll back the separately proven imported identity/registration path.
            try
            {
                foreach (var applicator in prefab.GetComponentsInChildren<Il2Cpp.SlimeAppearanceApplicator>(true))
                    applicator.SlimeDefinition = definition;
            }
            catch (Exception ex)
            {
                Bindings.Remove(definition.Pointer);
                return "visual binding refused; native identity retained: " + ex.GetType().Name;
            }
            // Keep the identity prefab wholly native: source clones own per-instance meshes.
            // Preparing a hidden renderer on the prefab would serialize disabled native renderers
            // into every actor and defeat fail-closed fallback after a later reconstruction failure.
            return "validated cache bound; per-actor application deferred to appearance lifecycle";
        }
        private static NativeMeshCache? CacheFromRoot(string canonical, out string problem)
        {
            problem = "";
            if (_cacheRoot == null || _repoRoot == null) return null;
            if (RootCaches.TryGetValue(canonical, out var held)) { if (held == null) problem = "no valid cache for " + canonical; return held; }
            NativeMeshCache? loaded = null;
            try
            {
                var folder = Path.Combine(_cacheRoot, canonical);
                if (!Directory.Exists(folder)) problem = "no extracted cache for " + canonical + " (launcher Extract builds it from Slime Rancher)";
                else loaded = NativeMeshCache.Load(_repoRoot, folder, LabSpecPathGuard.AssetCacheRelativeRoot);
                if (loaded != null && loaded.CanonicalId != canonical) { problem = "cache canonical_id differs for " + canonical; loaded = null; }
            }
            catch (Exception ex)
            {
                problem = "cache refused for " + canonical + ": " + ex.GetType().Name + ": " + ex.Message;
                Debug.Log("[SlimeBridge] " + problem);
            }
            RootCaches[canonical] = loaded;
            return loaded;
        }
        internal static void Unregister(Il2Cpp.SlimeDefinition definition) { Bindings.Remove(definition.Pointer); }
        internal static bool IsImported(GameObject actor)
        {
            var ident = actor.GetComponentInParent<Il2Cpp.Identifiable>(true) ?? actor.GetComponentInChildren<Il2Cpp.Identifiable>(true);
            return ident != null && ident.identType != null && Bindings.ContainsKey(ident.identType.Pointer);
        }
        internal static bool HasAppliedVisual(GameObject obj)
        {
            var owner = FindMarker(obj);
            return owner != null && owner.Initialized && !owner.Disposed && owner.gameObject.activeSelf;
        }
        private static Sr2ImportedVisualMarker? FindMarker(GameObject obj)
        {
            var actor = Actor(obj, out _);
            var root = actor == null ? null : actor.transform.Find(Sr2ImportedVisualMarker.RootName);
            return root == null ? null : root.gameObject.GetComponent<Sr2ImportedVisualMarker>();
        }

        // ---- per-frame fast paths -------------------------------------------------------------------------------------------------
        // The Harmony hooks on the bounds helper's Update run every frame for EVERY slime of the game. Walking the hierarchy for each
        // call (GetComponentInParent + Find + GetComponent across the IL2CPP boundary) is expensive, so the answer is remembered per
        // component wrapper: once an actor's identity is known to be native it never changes, and an imported actor's marker is kept.
        // ConditionalWeakTable ties each entry to the wrapper's lifetime, so a reused native pointer can never inherit a stale answer.
        private static readonly object Native = new object();
        private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<Component, object> KnownNative =
            new System.Runtime.CompilerServices.ConditionalWeakTable<Component, object>();
        private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<Component, Sr2ImportedVisualMarker> KnownMarker =
            new System.Runtime.CompilerServices.ConditionalWeakTable<Component, Sr2ImportedVisualMarker>();

        /// <summary>IsImported for a component of the actor (cheap after the first call about a native actor).</summary>
        internal static bool IsImportedFast(Component component)
        {
            if (component == null || Bindings.Count == 0 || KnownNative.TryGetValue(component, out _)) return false;
            var go = component.gameObject;
            var ident = go.GetComponentInParent<Il2Cpp.Identifiable>(true) ?? go.GetComponentInChildren<Il2Cpp.Identifiable>(true);
            if (ident == null || ident.identType == null) return false;              // identity not set yet: ask again next time
            if (Bindings.ContainsKey(ident.identType.Pointer)) return true;
            KnownNative.AddOrUpdate(component, Native);
            return false;
        }

        /// <summary>HasAppliedVisual for a component of the actor: native actors cost one table lookup, imported ones reuse their marker.</summary>
        internal static bool HasAppliedVisualFast(Component component)
        {
            if (!IsImportedFast(component)) return false;
            if (KnownMarker.TryGetValue(component, out var cached) && cached != null && cached.Initialized && !cached.Disposed && cached.gameObject.activeSelf)
                return true;
            var marker = FindMarker(component.gameObject);
            if (marker == null) return false;
            KnownMarker.AddOrUpdate(component, marker);
            return marker.Initialized && !marker.Disposed && marker.gameObject.activeSelf;
        }
        private static GameObject? Actor(GameObject obj, out Binding? binding)
        {
            binding = null;
            var ident = obj.GetComponentInParent<Il2Cpp.Identifiable>(true) ?? obj.GetComponentInChildren<Il2Cpp.Identifiable>(true);
            if (ident == null || ident.identType == null || !Bindings.TryGetValue(ident.identType.Pointer, out binding)) return null;
            return ident.gameObject;
        }
        internal static bool TryApply(GameObject obj, out string problem)
        {
            problem = "";
            Sr2ImportedVisualMarker? built = null;
            Binding? selected = null;
            try
            {
                var actor = Actor(obj, out var binding);
                if (actor == null || binding == null) { problem = _cacheProblem.Length == 0 ? "species has no matching cache" : _cacheProblem; return false; }
                selected = binding;
                var oldRoot = actor.transform.Find(Sr2ImportedVisualMarker.RootName);
                var existing = oldRoot == null ? null : oldRoot.gameObject.GetComponent<Sr2ImportedVisualMarker>();
                if (oldRoot != null && existing == null) throw new FormatException("reserved visual root name collision");
                if (existing != null && existing.Initialized && !existing.Disposed && existing.ModelSha256 == binding.Cache.ModelSha256)
                {
                    DisableNative(actor, existing);
                    if (!existing.gameObject.activeSelf) existing.gameObject.SetActive(true);
                    return true;
                }
                var native = NativeMaterials(actor);
                // A raw Instantiate/spawn may not run the game's appearance pipeline, so native
                // family materials can be absent from the actor. The template definition's
                // appearance structures always carry the genuine SR2 family materials; add them
                // as deterministic templates (deduplicated by pointer).
                var seenNative = new HashSet<IntPtr>();
                foreach (var material in native) seenNative.Add(material.Pointer);
                string templateProblem = "";
                foreach (var material in TemplateMaterials(binding, out templateProblem))
                    if (material != null && seenNative.Add(material.Pointer)) native.Add(material);
                if (templateProblem.Length > 0)
                    Debug.Log("[SlimeBridge] template material walk failed: " + templateProblem);
                var textures = new Dictionary<string, Texture2D>(StringComparer.Ordinal);
                built = Sr2ImportedModelLoader.Build(actor, binding.Cache,
                    (part, owner) => BindMaterials(part, binding, owner, native, textures));
                ApplyPaletteBlocks(built);
                // No native visibility mutation until all source meshes/materials/clips are built.
                if (oldRoot != null)
                {
                    if (existing != null) existing.ReleaseOwned();
                    oldRoot.gameObject.SetActive(false);
                    oldRoot.name = "SlimeBridgeRetiredVisual";
                    UnityEngine.Object.Destroy(oldRoot.gameObject);
                }
                DisableNative(actor, built);
                built.Initialized = true;
                built.gameObject.SetActive(true);
                if (built.DefaultClip.Length > 0)
                {
                    var animation = built.GetComponent<Animation>();
                    if (animation == null || !animation.Play(built.DefaultClip, PlayMode.StopAll))
                        throw new InvalidOperationException("native legacy playback refused selected clip");
                }
                return true;
            }
            catch (Exception ex)
            {
                if (built != null)
                {
                    built.ReleaseOwned(); built.gameObject.SetActive(false); UnityEngine.Object.Destroy(built.gameObject);
                }
                problem = "visual refused; placeholder retained: " + ex.GetType().Name + ": " + ex.Message;
                if (selected == null || selected.LastProblem != problem) Debug.Log("[SlimeBridge] " + problem);
                if (selected != null) selected.LastProblem = problem;
                return false;
            }
        }
        private static List<Material> NativeMaterials(GameObject actor)
        {
            var result = new List<Material>(); var seen = new HashSet<IntPtr>();
            foreach (var renderer in actor.GetComponentsInChildren<Renderer>(true))
            {
                if (InImportedRoot(renderer.transform)) continue;
                foreach (var material in renderer.sharedMaterials)
                    if (material != null && seen.Add(material.Pointer)) result.Add(material);
            }
            if (result.Count == 0) throw new FormatException("no native actor materials");
            return result;
        }
        /// <summary>Family templates from the template definition's appearance structures and face expressions.</summary>
        private static List<Material> TemplateMaterials(Binding binding, out string problem)
        {
            var result = new List<Material>();
            problem = "";
            var template = binding.Template;
            if (template == null) return result;
            try
            {
                var appearances = template.AppearancesDefault;
                if (appearances == null || appearances.Length == 0) return result;
                foreach (var appearance in appearances)
                {
                    if (appearance == null) continue;
                    var structures = appearance.Structures;
                    if (structures != null)
                        foreach (var structure in structures)
                        {
                            if (structure == null) continue;
                            var defaults = structure.DefaultMaterials;
                            if (defaults != null)
                                foreach (var material in defaults)
                                    if (material != null) result.Add(material);
                            var overrides = structure.ElementMaterials;
                            if (overrides != null)
                                foreach (var entry in overrides)
                                {
                                    var entryMaterials = entry.Materials;
                                    if (entryMaterials == null) continue;
                                    foreach (var material in entryMaterials)
                                        if (material != null) result.Add(material);
                                }
                            var prefabs = structure.Element?.Prefabs;
                            if (prefabs != null)
                                foreach (var prefab in prefabs)
                                {
                                    if (prefab == null) continue;
                                    foreach (var renderer in prefab.gameObject.GetComponentsInChildren<UnityEngine.Renderer>(true))
                                        foreach (var material in renderer.sharedMaterials)
                                            if (material != null) result.Add(material);
                                }
                        }
                    // Eye/mouth family materials live on the appearance's SlimeFace expressions.
                    var expressions = appearance._face?.ExpressionFaces;
                    if (expressions != null)
                        foreach (var row in expressions)
                        {
                            if (row.Eyes != null) result.Add(row.Eyes);
                            if (row.Mouth != null) result.Add(row.Mouth);
                        }
                }
            }
            catch (Exception ex)
            {
                problem = ex.GetType().Name + ": " + ex.Message;
            }
            return result;
        }
        private static bool InImportedRoot(Transform value)
        {
            for (var n = value; n != null; n = n.parent)
                if (n.name == Sr2ImportedVisualMarker.RootName || n.name == "SlimeBridgeRetiredVisual") return true;
            return false;
        }
        private static bool PaletteMaterial(Material value) => value.HasProperty("_TopColor") && value.HasProperty("_MiddleColor") && value.HasProperty("_BottomColor");
        private static Material NativeFor(NativeMeshPart part, NativeMeshMaterial source, List<Material> native)
        {
            if (source.Shader.Length > 0)
                foreach (var candidate in native) if (candidate.shader != null && candidate.shader.name == source.Shader) return candidate;
            string family = NativeMeshVisualPolicy.Family(source, part.Role);
            foreach (var candidate in native)
            {
                if (family == "body" && PaletteMaterial(candidate)) return candidate;
                if (family == "eyes" && !PaletteMaterial(candidate) && candidate.HasProperty("_EyeRed")) return candidate;
                if (family == "mouth" && !PaletteMaterial(candidate) && candidate.HasProperty("_MouthMid")) return candidate;
                if (family == "face" && !PaletteMaterial(candidate) && candidate.HasProperty("_FaceAtlas")) return candidate;
            }
            throw new FormatException("no compatible native " + family + " material for " + source.Name
                + " (candidates: " + DescribeMaterials(native) + ")");
        }
        private static string DescribeMaterials(List<Material> values)
        {
            var parts = new List<string>();
            foreach (var value in values)
            {
                if (value == null || parts.Count >= 8) continue;
                try
                {
                    parts.Add((value.name ?? "") + "/" + (value.shader != null ? value.shader.name : "") +
                              (PaletteMaterial(value) ? "[palette]" : ""));
                }
                catch (Exception) { }
            }
            return string.Join(", ", parts);
        }
        private static Material[] BindMaterials(NativeMeshPart part, Binding binding, Sr2ImportedVisualMarker owner,
            List<Material> native, Dictionary<string, Texture2D> textures)
        {
            var result = new Material[part.Materials.Length];
            for (var slot = 0; slot < result.Length; slot++)
            {
                var source = part.Materials[slot];
                var clone = owner.Own(new Material(NativeFor(part, source, native)));
                clone.name = source.Name + "__SR2Imported";
                foreach (var property in source.Colors)
                    if (clone.HasProperty(property.Key)) clone.SetColor(property.Key, Sr2ImportedModelLoader.Color4(property.Value));
                foreach (var property in source.Floats)
                    if (clone.HasProperty(property.Key)) clone.SetFloat(property.Key, property.Value);
                // SR1 scalar gloss -> SR2 RGB gloss, only when the native shader supports it.
                foreach (var key in new [] {"_Gloss", "_GlossPower"})
                    if (source.Floats.TryGetValue(key, out var gloss) && clone.HasProperty(key + "RGB")) clone.SetColor(key + "RGB", new Color(gloss,gloss,gloss,1));
                var palette = binding.Entry.appearance;
                if (palette != null)
                {
                    PaletteFallback(clone, source, "_TopColor", palette.top);
                    PaletteFallback(clone, source, "_MiddleColor", palette.middle);
                    PaletteFallback(clone, source, "_BottomColor", palette.bottom);
                }
                foreach (var texture in source.Textures)
                {
                    string property = texture.Property;
                    if (!clone.HasProperty(property))
                    {
                        // Only portable albedo/face-atlas aliases; DATA masks are never baked or guessed.
                        if (property == "_MainTex" && clone.HasProperty("_FaceAtlas")) property = "_FaceAtlas";
                        else if (property == "_FaceAtlas" && clone.HasProperty("_MainTex")) property = "_MainTex";
                        else if (NativeMeshVisualPolicy.IsRequiredTexture(property)) throw new FormatException("native shader does not support required source texture " + property);
                        else
                        {
                            owner.SkippedTextureProperties.Add(property);
                            continue; // Authored shader-only effects are recorded, not fabricated.
                        }
                    }
                    var key = texture.File + (texture.Linear ? "|linear" : "|srgb");
                    if (!textures.TryGetValue(key, out var loaded))
                    {
                        loaded = owner.Own(new Texture2D(2,2,TextureFormat.RGBA32,false,texture.Linear));
                        loaded.name = texture.File;
                        if (!ImageConversion.LoadImage(loaded, new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<byte>(binding.Cache.Textures[texture.File]), false))
                            throw new FormatException("source texture decode failed: " + texture.File);
                        loaded.filterMode = FilterMode.Bilinear; loaded.wrapMode = TextureWrapMode.Clamp;
                        textures.Add(key, loaded);
                    }
                    clone.SetTexture(property, loaded);
                    clone.SetTextureScale(property, new Vector2(texture.Scale[0],texture.Scale[1]));
                    clone.SetTextureOffset(property, new Vector2(texture.Offset[0],texture.Offset[1]));
                }
                if (source.RenderQueue.HasValue) clone.renderQueue = source.RenderQueue.Value;
                result[slot] = clone;
            }
            return result;
        }
        private static void PaletteFallback(Material material, NativeMeshMaterial source, string key, float[] value)
        {
            if (!source.Colors.ContainsKey(key) && value.Length == 4 && material.HasProperty(key)) material.SetColor(key, Sr2ImportedModelLoader.Color4(value));
        }
        private static void ApplyPaletteBlocks(Sr2ImportedVisualMarker owner)
        {
            for (var i = 0; i < owner.Renderers.Length; i++)
            {
                var renderer = owner.Renderers[i]; var materials = renderer.sharedMaterials;
                for (var slot = 0; slot < materials.Length; slot++)
                {
                    var material = materials[slot];
                    if (!PaletteMaterial(material)) continue;
                    var block = new MaterialPropertyBlock();
                    foreach (var key in new [] {"_TopColor","_MiddleColor","_BottomColor"}) block.SetColor(key, material.GetColor(key));
                    renderer.SetPropertyBlock(block,slot);
                    var observed = new MaterialPropertyBlock(); renderer.GetPropertyBlock(observed,slot);
                    foreach (var key in new [] {"_TopColor","_MiddleColor","_BottomColor"})
                    {
                        var a = observed.GetColor(key); var b = material.GetColor(key);
                        if (Math.Abs(a.r-b.r)>0.0001f || Math.Abs(a.g-b.g)>0.0001f || Math.Abs(a.b-b.b)>0.0001f || Math.Abs(a.a-b.a)>0.0001f)
                            throw new InvalidOperationException("native palette block readback failed");
                    }
                    owner.PaletteVerified++;
                }
            }
        }
        private static void DisableNative(GameObject actor, Sr2ImportedVisualMarker owner)
        {
            foreach (var renderer in actor.GetComponentsInChildren<Renderer>(true))
                if (!InImportedRoot(renderer.transform)) owner.Hide(renderer);
            foreach (var lod in actor.GetComponentsInChildren<LODGroup>(true))
                if (!InImportedRoot(lod.transform)) owner.Hide(lod);
        }
        private static string QuoteBounded(string text) => RegistrySnapshotFormat.Quote(text.Length > 128 ? text.Substring(0,128) : text);
        internal static string Proof(GameObject obj)
        {
            try
            {
                var actor = Actor(obj, out var binding);
                var root = actor == null ? null : actor.transform.Find(Sr2ImportedVisualMarker.RootName);
                var owner = root == null ? null : root.gameObject.GetComponent<Sr2ImportedVisualMarker>();
                if (owner == null || !owner.Initialized || owner.Disposed) return "{\"applied\":false,\"reason\":" + RegistrySnapshotFormat.Quote(_cacheProblem.Length == 0 ? "no verified imported root" : _cacheProblem) + "}";
                var sb = new StringBuilder("{\"applied\":true,\"model_sha256\":");
                sb.Append(RegistrySnapshotFormat.Quote(owner.ModelSha256)).Append(",\"palette_verified_slots\":").Append(owner.PaletteVerified);
                sb.Append(",\"active_in_hierarchy\":").Append(root!.gameObject.activeInHierarchy ? "true" : "false");
                sb.Append(",\"ignored_effect_parts\":").Append(owner.IgnoredEffectParts);
                sb.Append(",\"nonportable_texture_properties\":[");
                var skipped = 0;
                foreach (var key in owner.SkippedTextureProperties)
                {
                    if (skipped++ >= 16) break;
                    if (skipped > 1) sb.Append(',');
                    sb.Append(QuoteBounded(key));
                }
                sb.Append(']');
                var animation = root!.gameObject.GetComponent<Animation>();
                sb.Append(",\"animation_component\":").Append(RegistrySnapshotFormat.Quote(animation == null ? "" : "UnityEngine.Animation"));
                sb.Append(",\"default_clip\":").Append(QuoteBounded(owner.DefaultClip));
                sb.Append(",\"animation_playing\":").Append(animation != null && animation.isPlaying ? "true" : "false");
                sb.Append(",\"renderers\":[");
                var count = Math.Min(owner.Renderers.Length,16);
                for (var i=0; i<count; i++)
                {
                    if (i>0) sb.Append(',');
                    var renderer=owner.Renderers[i]; var skin=renderer.TryCast<SkinnedMeshRenderer>();
                    var mesh=skin==null ? renderer.gameObject.GetComponent<MeshFilter>()?.sharedMesh : skin.sharedMesh;
                    sb.Append("{\"component_type\":").Append(RegistrySnapshotFormat.Quote(skin==null ? "UnityEngine.MeshRenderer" : "UnityEngine.SkinnedMeshRenderer"));
                    sb.Append(",\"enabled\":").Append(renderer.enabled ? "true" : "false");
                    sb.Append(",\"vertices\":").Append(mesh==null ? 0 : mesh.vertexCount);
                    sb.Append(",\"bone_weights\":").Append(mesh==null ? 0 : mesh.boneWeights.Length);
                    var boundBones = 0;
                    if (skin != null) foreach (var bone in skin.bones) if (bone != null) boundBones++;
                    sb.Append(",\"bone_slots\":").Append(skin==null ? 0 : skin.bones.Length);
                    sb.Append(",\"bound_bones\":").Append(boundBones);
                    sb.Append(",\"root_bone\":").Append(QuoteBounded(skin?.rootBone?.name ?? ""));
                    sb.Append(",\"materials\":[");
                    var slots=renderer.sharedMaterials;
                    for(var j=0;j<Math.Min(slots.Length,8);j++)
                    {
                        if(j>0) sb.Append(',');
                        sb.Append(QuoteBounded(slots[j]?.shader?.name ?? ""));
                    }
                    sb.Append("]}");
                }
                sb.Append("],\"renderers_truncated\":").Append(owner.Renderers.Length>16 ? "true" : "false").Append('}');
                return sb.ToString();
            }
            catch (Exception ex) { return "{\"applied\":false,\"reason\":" + RegistrySnapshotFormat.Quote("proof failed: " + ex.GetType().Name) + "}"; }
        }
    }
}
