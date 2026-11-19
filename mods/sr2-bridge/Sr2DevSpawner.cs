using System;
using System.Collections.Generic;
using System.Linq;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    /// <summary>
    /// Developer mode only (SLIMEBRIDGE_DEV=1, set on the launcher's own environment, never in a release): spawn the imported
    /// species and Largos in front of the player. F7 / F9 pick the previous / next one, F8 spawns it. Largos come first.
    /// </summary>
    internal sealed class Sr2DevSpawner
    {
        private readonly Sr2SpeciesRegistrar _registrar;
        private List<string>? _list;
        private int _index;

        private Sr2DevSpawner(Sr2SpeciesRegistrar registrar) { _registrar = registrar; }

        internal static Sr2DevSpawner? TryCreate(Sr2SpeciesRegistrar? registrar) =>
            registrar != null && Environment.GetEnvironmentVariable("SLIMEBRIDGE_DEV") == "1" ? new Sr2DevSpawner(registrar) : null;

        internal void Tick()
        {
            if (!_registrar.Done) return;
            var keyboard = UnityEngine.InputSystem.Keyboard.current;
            if (keyboard == null) return;
            var previous = keyboard.f7Key.wasPressedThisFrame;
            var next = keyboard.f9Key.wasPressedThisFrame;
            var spawn = keyboard.f8Key.wasPressedThisFrame;
            if (!previous && !next && !spawn) return;
            _list ??= _registrar.Canonicals.OrderBy(c => c.StartsWith("largo.", StringComparison.Ordinal) ? 0 : 1)
                .ThenBy(c => c, StringComparer.Ordinal).ToList();
            if (_list.Count == 0) { TravelNotice.Post("SlimeBridge dev: no imported species registered"); return; }
            if (previous) _index = (_index + _list.Count - 1) % _list.Count;
            if (next) _index = (_index + 1) % _list.Count;
            var canonical = _list[_index];
            if (!spawn) { TravelNotice.Post($"SlimeBridge dev: {canonical} ({_index + 1}/{_list.Count}) - F8 to spawn"); return; }
            var result = Spawn(canonical);
            TravelNotice.Post("SlimeBridge dev: " + (result.Length == 0 ? "spawned " + canonical : canonical + ": " + result));
            MelonLoader.MelonLogger.Msg("dev spawn " + canonical + (result.Length == 0 ? " ok" : ": " + result));
        }

        private string Spawn(string canonical)
        {
            if (!CounterpartCreator.TryReadWorld(out var world) || world?.Scene?.Player == null) return "no world loaded";
            try { _registrar.EnsureRuntimeMembership(); _registrar.EnsureAppearances(); } catch (Exception) { /* best effort */ }
            if (!_registrar.TryResolvePrefab(canonical, out var prefab) || prefab == null) return "no registered prefab";
            var player = world.Scene.Player!.transform;
            var distance = 3.0f;
            try
            {
                if (UnityEngine.Physics.Raycast(player.position + UnityEngine.Vector3.up * 1.5f, player.forward, out var hit, 3.5f,
                        ~0, UnityEngine.QueryTriggerInteraction.Ignore))
                    distance = UnityEngine.Mathf.Max(1.4f, hit.distance - 0.9f);
            }
            catch (Exception) { /* keep the default distance */ }
            var position = player.position + player.forward * distance + UnityEngine.Vector3.up;
            UnityEngine.GameObject? instance = null;
            try
            {
                instance = UnityEngine.Object.Instantiate(prefab, position, UnityEngine.Quaternion.LookRotation(-player.forward));
                instance.SetActive(true);
                foreach (var applicator in instance.GetComponentsInChildren<Il2Cpp.SlimeAppearanceApplicator>(true))
                {
                    try { applicator.Initialize(true); applicator.ApplyAppearance(); }
                    catch (Exception) { /* the imported visual below still applies */ }
                }
                Sr2ImportedSpawner.TryApply(instance, out var problem);
                return problem ?? "";
            }
            catch (Exception ex)
            {
                if (instance != null) UnityEngine.Object.Destroy(instance);
                return "spawn failed: " + ex.GetType().Name;
            }
        }
    }
}
