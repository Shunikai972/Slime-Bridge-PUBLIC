using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;
using Vacuumable = Il2CppMonomiPark.SlimeRancher.Vacuumable;
using VacuumItem = Il2CppMonomiPark.SlimeRancher.Player.PlayerItems.VacuumItem;

namespace SlimeBridge.SR2
{
    /// <summary>Explicit LAB-only observer. Never changes materials, MPBs, actors, inventory or inputs.</summary>
    internal static class Sr2LabVacuumTrace
    {
        private const int Limit = 512;
        private static readonly string[] Colors = { "_BaseColor", "_Color", "_TopColor", "_MiddleColor", "_BottomColor", "_EmissiveColor" };
        private static readonly string[] Textures = { "_BaseColorMap", "_MainTex", "_EmissionMap", "_EmissiveColorMap", "_FaceAtlas" };
        private static readonly List<Pending> Deferred = new List<Pending>();
        private static HarmonyLib.Harmony? _patch;
        private static string? _file, _root;
        private static int _rows, _thread;
        private sealed class Pending { internal Vacuumable Actor = null!; internal int Frame, Stage; }

        internal static void Arm(bool exactLabExecutable)
        {
            var output = Environment.GetEnvironmentVariable("SLIMEBRIDGE_VAC_TRACE_OUT");
            if (string.IsNullOrEmpty(output)) return;
            try
            {
                if (!exactLabExecutable || _patch != null) throw new InvalidOperationException("not an exact LAB executable or already armed");
                var repo = LabSpecPathGuard.FindRepoRoot();
                if (repo == null) throw new InvalidOperationException("no verified repo root");
                _root = Path.Combine(repo, LabSpecPathGuard.OutRelativeRoot);
                if (!LabSpecPathGuard.IsAllowedDirectory(_root, output, out var reason) || !Directory.Exists(output))
                    throw new InvalidOperationException("trace output refused: " + reason);
                _file = Path.Combine(output, "vacuum-trace.jsonl");
                Sr2LabTraceOutput.Create(_root, _file);
                _thread = System.Threading.Thread.CurrentThread.ManagedThreadId;
                _rows = 0;
                _patch = new HarmonyLib.Harmony("slimebridge.lab.vacuum-trace");
                _patch.Patch(AccessTools.Method(typeof(Vacuumable), "UpdateMaterialsHeldState", Type.EmptyTypes),
                    prefix: new HarmonyMethod(typeof(Sr2LabVacuumTrace), nameof(WriterBefore)),
                    postfix: new HarmonyMethod(typeof(Sr2LabVacuumTrace), nameof(WriterAfter)));
                _patch.Patch(AccessTools.Method(typeof(VacuumItem), "ExpelHeld", new[] { typeof(float) }),
                    prefix: new HarmonyMethod(typeof(Sr2LabVacuumTrace), nameof(ExpelBefore)),
                    postfix: new HarmonyMethod(typeof(Sr2LabVacuumTrace), nameof(ExpelAfter)));
                Emit(new { phase = "armed", observer_only = true, native_writer = "Vacuumable.UpdateMaterialsHeldState()", expel = "VacuumItem.ExpelHeld(float)" });
                Debug.Log("[SlimeBridge] LAB vacuum trace armed (read-only, 512 rows)");
            }
            catch (Exception ex) { Stop(); Debug.Log("[SlimeBridge] LAB vacuum trace refused: " + ex.GetType().Name + " " + ex.Message); }
        }

        private static bool Eligible(Vacuumable actor)
        {
            if (_file == null || _rows >= Limit || System.Threading.Thread.CurrentThread.ManagedThreadId != _thread || actor == null) return false;
            var ident = actor.GetComponentInParent<Il2Cpp.Identifiable>();
            if (ident == null || ident.identType == null) return false;
            var visual = ident.transform.Find(Sr2ImportedVisualMarker.RootName);
            var marker = visual == null ? null : visual.GetComponent<Sr2ImportedVisualMarker>();
            if (marker != null) return marker.Initialized && !marker.Disposed && marker.CanonicalId == "largo.cotton.quantum";
            var name = ident.identType.name ?? "";
            return name.IndexOf("Cotton", StringComparison.OrdinalIgnoreCase) >= 0 && name.IndexOf("Pink", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static float[] ColorValue(Color c) => new[] { c.r, c.g, c.b, c.a };
        private static object ObjectId(UnityEngine.Object? value) => value == null ? new { id = 0, name = "" } : new { id = value.GetInstanceID(), name = value.name };
        private static object Block(MaterialPropertyBlock block)
        {
            var colors = new Dictionary<string, float[]>();
            foreach (var key in Colors) colors[key] = ColorValue(block.GetColor(key));
            return new { empty = block.isEmpty, held_raw = block.GetFloat("_HeldInVac"), raw_color_samples = colors,
                note = "Raw zero is not proof of an override; inspect empty/material/wide/indexed state together." };
        }

        private static void Snapshot(Vacuumable actor, string phase)
        {
            try
            {
                if (!Eligible(actor)) return;
                var ident = actor.GetComponentInParent<Il2Cpp.Identifiable>();
                var renderers = new List<object>();
                foreach (var renderer in ident.GetComponentsInChildren<Renderer>(true))
                {
                    if (renderer == null || renderers.Count >= 48) continue;
                    var wide = new MaterialPropertyBlock();
                    renderer.GetPropertyBlock(wide);
                    var slots = new List<object>();
                    var materials = renderer.sharedMaterials; // never renderer.material(s), which would instantiate
                    for (var slot = 0; slot < materials.Length && slot < 12; slot++)
                    {
                        var material = materials[slot];
                        var indexed = new MaterialPropertyBlock(); renderer.GetPropertyBlock(indexed, slot);
                        var colors = new Dictionary<string, float[]>();
                        var textures = new Dictionary<string, object>();
                        if (material != null)
                        {
                            foreach (var key in Colors) if (material.HasProperty(key)) colors[key] = ColorValue(material.GetColor(key));
                            foreach (var key in Textures) if (material.HasProperty(key)) textures[key] = ObjectId(material.GetTexture(key));
                        }
                        slots.Add(new { slot, material = ObjectId(material), shader = material?.shader?.name ?? "", material_colors = colors,
                            material_textures = textures, indexed = Block(indexed) });
                    }
                    renderers.Add(new { renderer = ObjectId(renderer), active = renderer.gameObject.activeInHierarchy, enabled = renderer.enabled,
                        shadow = renderer.shadowCastingMode.ToString(), imported = renderer.GetComponentInParent<Sr2ImportedVisualMarker>() != null,
                        wide = Block(wide), slots });
                }
                Emit(new { phase, frame = Time.frameCount, actor = ObjectId(ident.gameObject), definition = ObjectId(ident.identType),
                    held = actor._held, launched = actor._launched, renderers });
            }
            catch (Exception ex) { Fault(phase, ex); }
        }

        private static void WriterBefore(Vacuumable __instance) => Snapshot(__instance, "writer.before");
        private static void WriterAfter(Vacuumable __instance)
        {
            Snapshot(__instance, "writer.after");
            try
            {
                if (Eligible(__instance) && Deferred.Count < 32)
                    Deferred.Add(new Pending { Actor = __instance, Frame = Time.frameCount });
            }
            catch (Exception ex) { Fault("writer.defer", ex); }
        }
        private static void ExpelBefore(VacuumItem __instance, out Vacuumable? __state)
        {
            __state = null;
            if (_file == null || _rows >= Limit || System.Threading.Thread.CurrentThread.ManagedThreadId != _thread) return;
            try
            {
                var held = __instance._held;
                __state = held == null ? null : held.GetComponent<Vacuumable>();
                if (__state != null) Snapshot(__state, "native.expel_held.before");
            }
            catch (Exception ex) { Fault("expel.before", ex); }
        }
        private static void ExpelAfter(Vacuumable? __state) { if (__state != null) Snapshot(__state, "native.expel_held.after"); }

        internal static void LateTick()
        {
            if (_file == null || System.Threading.Thread.CurrentThread.ManagedThreadId != _thread) return;
            for (var i = Deferred.Count - 1; i >= 0; i--)
            {
                var pending = Deferred[i];
                if (pending.Actor == null) { Deferred.RemoveAt(i); continue; }
                if (pending.Stage == 0)
                {
                    Snapshot(pending.Actor, "writer.late_tick"); pending.Stage = 1; pending.Frame = Time.frameCount + 1;
                }
                else if (Time.frameCount >= pending.Frame)
                {
                    Snapshot(pending.Actor, "writer.next_frame"); Deferred.RemoveAt(i);
                }
            }
        }
        private static void Fault(string phase, Exception ex)
        {
            try { Emit(new { phase = "observer.error", operation = phase, type = ex.GetType().Name }); }
            catch (Exception) { } // a diagnostic must never change native callback success
        }
        private static void Emit(object row)
        {
            if (_file == null || _root == null || _rows >= Limit) return;
            if (!LabSpecPathGuard.IsAllowedFile(_root, _file, out var reason)) throw new IOException("unsafe trace target: " + reason);
            var bytes = System.Text.Encoding.UTF8.GetBytes(JsonSerializer.Serialize(row) + Environment.NewLine);
            if (!Sr2LabTraceOutput.Append(_root, _file, bytes, 16 * 1024 * 1024)) { _rows = Limit; return; }
            _rows++;
        }
        internal static void Stop()
        {
            try { _patch?.UnpatchSelf(); _patch = null; }
            catch (Exception ex) { Debug.Log("[SlimeBridge] LAB vacuum trace unpatch failed: " + ex.GetType().Name); }
            finally { _file = null; _root = null; Deferred.Clear(); }
        }
    }
}
