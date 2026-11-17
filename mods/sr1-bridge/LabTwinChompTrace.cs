using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    // Scoped to the one explicit LAB Twin feeding probe. Observation only: no arguments, results,
    // metadata, joints, clocks, exceptions or animation parameters are changed by these hooks.
    internal sealed class LabTwinChompTrace
    {
        private const string HarmonyId = "slimebridge.sr1.lab-twin-chomp-trace";
        private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
        private static LabTwinChompTrace? _active;
        private readonly Chomper _chomper;
        private readonly SlimeEat _eater;
        private readonly GameObject _food;
        private readonly FieldInfo _metadata;
        private readonly List<string> _rows = new List<string>();
        private Harmony? _harmony;
        private int _dropped;

        private LabTwinChompTrace(SlimeEat eater, GameObject food)
        {
            _eater = eater; _food = food; _chomper = eater.GetComponent<Chomper>();
            if (_chomper == null) throw new InvalidOperationException("Twin has no native Chomper");
            _metadata = typeof(Chomper).GetField("metadata", Fields) ?? throw new MissingFieldException("Chomper.metadata");
        }

        internal static LabTwinChompTrace Start(SlimeEat eater, GameObject food)
        {
            if (_active != null) throw new InvalidOperationException("Twin trace already active");
            var trace = new LabTwinChompTrace(eater, food);
            var harmony = new Harmony(HarmonyId);
            try
            {
                var targets = new[]
                {
                    Require(typeof(Chomper), "StartChomp", typeof(GameObject), typeof(Identifiable.Id), typeof(bool), typeof(bool),
                        typeof(Chomper.OnChompStartDelegate), typeof(Chomper.OnChompCompleteDelegate)),
                    Require(typeof(Chomper), "BiteComplete"), Require(typeof(Chomper), "ForceCancelChomp"),
                    Require(typeof(SlimeEat), "FinishChomp", typeof(GameObject), typeof(Identifiable.Id), typeof(bool), typeof(bool))
                };
                trace._harmony = harmony;
                _active = trace;
                foreach (var target in targets)
                    harmony.Patch(target,
                        prefix: new HarmonyMethod(typeof(LabTwinChompTrace), nameof(Before)),
                        postfix: new HarmonyMethod(typeof(LabTwinChompTrace), nameof(After)),
                        finalizer: new HarmonyMethod(typeof(LabTwinChompTrace), nameof(OnException)));
                trace.Record("armed", null);
                return trace;
            }
            catch
            {
                _active = null;
                harmony.UnpatchAll(HarmonyId);
                throw;
            }
        }

        private static MethodInfo Require(Type owner, string name, params Type[] args)
        {
            var method = owner.GetMethod(name, Fields | BindingFlags.DeclaredOnly, null, args, null);
            if (method == null || method.IsStatic || method.ReturnType != typeof(void))
                throw new MissingMethodException(owner.FullName, name);
            return method;
        }

        private static void Before(object __instance, MethodBase __originalMethod) => Observe(__instance, __originalMethod.Name + ".before", null);
        private static void After(object __instance, MethodBase __originalMethod) => Observe(__instance, __originalMethod.Name + ".after", null);
        // A void finalizer records, but never consumes/replaces, the original native exception.
        private static void OnException(object __instance, MethodBase __originalMethod, Exception? __exception)
        {
            if (__exception != null) Observe(__instance, __originalMethod.Name + ".exception", __exception.GetType().Name);
        }

        private static void Observe(object instance, string phase, string? error)
        {
            try
            {
                var trace = _active;
                if (trace == null || (!ReferenceEquals(instance, trace._chomper) && !ReferenceEquals(instance, trace._eater))) return;
                trace.Record(phase, error);
            }
            catch (Exception) { } // Diagnostics never escape into native callbacks.
        }

        private void Record(string phase, string? error)
        {
            if (_rows.Count >= 64) { _dropped++; return; }
            var data = _metadata.GetValue(_chomper);
            object? Read(string key) => data?.GetType().GetField(key, Fields)?.GetValue(data);
            var food = Read("gameObject") as GameObject;
            var joint = Read("joint") as FixedJoint;
            var callback = Read("onComplete") as Delegate;
            _rows.Add("{\"phase\":" + RegistrySnapshotFormat.Quote(phase) +
                ",\"time\":" + Time.realtimeSinceStartup.ToString("R", CultureInfo.InvariantCulture) +
                ",\"metadata_present\":" + B(data != null) + ",\"food_is_probe\":" + B(food != null && food == _food) +
                ",\"food_alive\":" + B(food != null) + ",\"food_id\":" + RegistrySnapshotFormat.Quote(Read("id")?.ToString() ?? "") +
                ",\"quick\":" + B(Read("isQuickChomp") is bool quick && quick) +
                ",\"joint_present\":" + B(joint != null) + ",\"joint_connected\":" + B(joint != null && joint.connectedBody != null) +
                ",\"callback\":" + RegistrySnapshotFormat.Quote(callback?.Method.Name ?? "") +
                ",\"error\":" + RegistrySnapshotFormat.Quote(error ?? "") + "}");
        }

        internal string ToJson() => "{\"rows\":[" + string.Join(",", _rows) + "],\"dropped\":" + _dropped.ToString(CultureInfo.InvariantCulture) + "}";
        internal void Stop()
        {
            if (ReferenceEquals(_active, this)) _active = null;
            var harmony = _harmony; _harmony = null;
            harmony?.UnpatchAll(HarmonyId);
        }
        private static string B(bool value) => value ? "true" : "false";
    }
}
