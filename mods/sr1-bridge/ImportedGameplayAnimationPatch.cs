using System;
using System.Reflection;
using System.Reflection.Emit;
using HarmonyLib;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Exact, fail-open Harmony observations for native SR1 gameplay transitions. Every
    /// patch is postfix except the two timer snapshots; no original path is skipped or rewritten.</summary>
    internal static class ImportedGameplayAnimationPatch
    {
        private const string HarmonyId = "slimebridge.sr1.imported-gameplay-animation";
        private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.Public |
            BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        private const int PounceMode = 4;

        private delegate int StalkModeReader(StalkConsumable instance);
        private static Harmony? _harmony;
        private static StalkModeReader? _readStalkMode;
        private static MethodBase? _groundedTarget;
        private static MethodInfo? _groundedFactoryResult;

        internal static bool IsInstalled => _harmony != null;

        internal static bool EnsurePatched()
        {
            if (_harmony != null) return true;
            Harmony? harmony = null;
            try
            {
                // Resolve and validate every target before the first mutation. The local private
                // SR1 assembly proof pins these exact names and signatures for the current build.
                var startChomp = RequireMethod(typeof(Chomper), "StartChomp", typeof(void),
                    typeof(GameObject), typeof(Identifiable.Id), typeof(bool), typeof(bool),
                    typeof(Chomper.OnChompStartDelegate), typeof(Chomper.OnChompCompleteDelegate));
                var biteComplete = RequireMethod(typeof(Chomper), "BiteComplete", typeof(void));
                var forceCancel = RequireMethod(typeof(Chomper), "ForceCancelChomp", typeof(void));
                RequireMethod(typeof(Chomper), "IsChomping", typeof(bool));
                var leapToward = RequireMethod(typeof(StalkConsumable), "LeapToward", typeof(void),
                    typeof(float), typeof(Vector3), typeof(Vector3));
                var randomAction = RequireMethod(typeof(SlimeRandomMove), "Action", typeof(void));
                var moveTowards = RequireMethod(typeof(FindConsumable), "MoveTowards", typeof(void),
                    typeof(Vector3), typeof(bool), typeof(float).MakeByRefType(), typeof(float));
                var grounded = RequireOneArgumentMethod(typeof(SlimeSubbehaviourPlexer),
                    "OnGroundedRaycastResultReceived", typeof(void));
                var groundedParameter = grounded.GetParameters()[0].ParameterType;
                if (!string.Equals(groundedParameter.FullName, "UnityEngine.RaycastHit", StringComparison.Ordinal))
                    throw new MissingMethodException("grounded callback parameter must be UnityEngine.RaycastHit");
                var groundedArgument = grounded.GetParameters()[0];
                if (groundedArgument.IsOut || groundedArgument.ParameterType.IsByRef ||
                    !string.Equals(groundedArgument.Name, "result", StringComparison.Ordinal))
                    throw new MissingMethodException("grounded callback must accept RaycastHit result by value");
                var colliderProperty = groundedParameter.GetProperty("collider",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                var colliderGetter = colliderProperty?.GetGetMethod(true);
                if (colliderGetter == null || colliderGetter.IsStatic ||
                    !string.Equals(colliderGetter.ReturnType.FullName, "UnityEngine.Collider", StringComparison.Ordinal))
                    throw new MissingMethodException("RaycastHit.collider getter signature changed");

                var modeField = RequireField(typeof(StalkConsumable), "mode");
                if (!modeField.FieldType.IsEnum || Enum.GetUnderlyingType(modeField.FieldType) != typeof(int) ||
                    Convert.ToInt32(Enum.Parse(modeField.FieldType, "POUNCE", false)) != PounceMode)
                    throw new MissingFieldException("StalkConsumable.mode must be the verified int-backed Mode.POUNCE value 4");
                var nextJumpField = RequireField(typeof(SlimeRandomMove), "nextJumpTime");
                if (nextJumpField.FieldType != typeof(float))
                    throw new MissingFieldException("SlimeRandomMove.nextJumpTime must be float");

                var moveParameters = moveTowards.GetParameters();
                if (moveParameters[2].Name != "nextJumpAvail" || !moveParameters[2].ParameterType.IsByRef ||
                    moveParameters[2].IsOut || moveParameters[2].ParameterType.GetElementType() != typeof(float))
                    throw new MissingMethodException("FindConsumable.MoveTowards ref float nextJumpAvail signature changed");

                var modeReader = CreateModeReader(modeField);
                var groundedPostfix = CreateGroundedPostfix(groundedParameter, colliderGetter);
                _groundedTarget = grounded;
                _groundedFactoryResult = groundedPostfix;
                harmony = new Harmony(HarmonyId);
                harmony.Patch(startChomp,
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(StartChompPostfix)));
                harmony.Patch(biteComplete,
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(BiteCompletePostfix)));
                harmony.Patch(forceCancel,
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(ForceCancelPostfix)));
                harmony.Patch(leapToward,
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(LeapTowardPostfix)));
                harmony.Patch(randomAction,
                    prefix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(RandomActionPrefix)),
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(RandomActionPostfix)));
                harmony.Patch(moveTowards,
                    prefix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(MoveTowardsPrefix)),
                    postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(MoveTowardsPostfix)));
                // Digestion after a successful chomp (SlimeEat.EatAndProduce is private): fail-open, optional.
                var eatAndProduce = typeof(SlimeEat).GetMethod("EatAndProduce", InstanceFlags);
                if (eatAndProduce != null && !eatAndProduce.IsStatic && eatAndProduce.ReturnType == typeof(void))
                    harmony.Patch(eatAndProduce,
                        postfix: new HarmonyMethod(typeof(ImportedGameplayAnimationPatch), nameof(EatAndProducePostfix)));
                // This SR1 installation uses Harmony 2.0.1, which accepts generated patches
                // only through a static patch factory rather than a DynamicMethod directly.
                harmony.CreateClassProcessor(typeof(GroundedRaycastPatch)).Patch();

                _readStalkMode = modeReader;
                _harmony = harmony;
                UnityEngine.Debug.Log("[SlimeBridge] imported gameplay animation hooks installed");
                return true;
            }
            catch (Exception error)
            {
                try { harmony?.UnpatchAll(HarmonyId); } catch (Exception) { }
                _readStalkMode = null;
                _groundedTarget = null;
                _groundedFactoryResult = null;
                _harmony = null;
                UnityEngine.Debug.LogError("[SlimeBridge] imported gameplay animation hooks unavailable: " + error.GetType().Name);
                return false; // A version mismatch never blocks native gameplay.
            }
        }

        internal static void Unload()
        {
            ImportedGameplayAnimation.RestoreAllForUnload();
            var harmony = _harmony;
            _harmony = null;
            _readStalkMode = null;
            _groundedTarget = null;
            _groundedFactoryResult = null;
            if (harmony == null) return;
            try { harmony.UnpatchAll(HarmonyId); }
            catch (Exception) { /* unloading a visual aid must never interrupt game shutdown */ }
        }

        private static MethodInfo RequireMethod(Type owner, string name, Type returnType, params Type[] parameters)
        {
            var method = owner.GetMethod(name, InstanceFlags, null, parameters, null);
            if (method == null || method.IsStatic || method.ReturnType != returnType || method.ContainsGenericParameters)
                throw new MissingMethodException(owner.FullName, name);
            return method;
        }

        private static MethodInfo RequireOneArgumentMethod(Type owner, string name, Type returnType)
        {
            var method = owner.GetMethod(name, InstanceFlags);
            if (method == null || method.IsStatic || method.ReturnType != returnType ||
                method.ContainsGenericParameters || method.GetParameters().Length != 1)
                throw new MissingMethodException(owner.FullName, name);
            return method;
        }

        private static FieldInfo RequireField(Type owner, string name)
        {
            var field = owner.GetField(name, InstanceFlags);
            if (field == null || field.IsStatic || field.IsLiteral)
                throw new MissingFieldException(owner.FullName, name);
            return field;
        }

        /// <summary>Create one cached, strongly typed enum reader at startup. The hook performs a
        /// direct delegate call: no FieldInfo.GetValue boxing or per-hook reflection/allocation.</summary>
        private static StalkModeReader CreateModeReader(FieldInfo field)
        {
            var getter = new DynamicMethod("SlimeBridge_ReadStalkMode", typeof(int),
                new[] { typeof(StalkConsumable) }, typeof(StalkConsumable), true);
            var code = getter.GetILGenerator();
            code.Emit(OpCodes.Ldarg_0);
            code.Emit(OpCodes.Ldfld, field);
            code.Emit(OpCodes.Conv_I4);
            code.Emit(OpCodes.Ret);
            return (StalkModeReader)getter.CreateDelegate(typeof(StalkModeReader));
        }

        /// <summary>Build one exact-signature postfix against Unity's RaycastHit struct without
        /// adding a runtime assembly reference to the plugin project. It reads result.collider
        /// directly from the value argument and calls the typed observer without boxing.</summary>
        private static MethodInfo CreateGroundedPostfix(Type hitType, MethodInfo colliderGetter)
        {
            if (!typeof(UnityEngine.Object).IsAssignableFrom(colliderGetter.ReturnType))
                throw new MissingMethodException("RaycastHit.collider must return a UnityEngine.Object");
            var unityNullEquality = typeof(UnityEngine.Object).GetMethod("op_Equality", BindingFlags.Public | BindingFlags.Static,
                null, new[] { typeof(UnityEngine.Object), typeof(UnityEngine.Object) }, null) ??
                throw new MissingMethodException("UnityEngine.Object equality operator is missing");
            var postfix = new DynamicMethod("SlimeBridge_GroundedPostfix", typeof(void),
                new[] { typeof(SlimeSubbehaviourPlexer), hitType }, typeof(ImportedGameplayAnimationPatch), true);
            postfix.DefineParameter(1, ParameterAttributes.None, "__instance");
            postfix.DefineParameter(2, ParameterAttributes.None, "result");
            var code = postfix.GetILGenerator();
            var done = code.BeginExceptionBlock();
            code.Emit(OpCodes.Ldarg_0);
            code.Emit(OpCodes.Ldarga_S, (byte)1);
            code.Emit(OpCodes.Call, colliderGetter);
            code.Emit(OpCodes.Ldnull);
            code.Emit(OpCodes.Call, unityNullEquality); // Match native Unity destroyed-object null semantics.
            var observer = typeof(ImportedGameplayAnimationPatch).GetMethod(nameof(GroundedObservationPostfix),
                BindingFlags.Static | BindingFlags.NonPublic)!;
            code.Emit(OpCodes.Call, observer);
            code.Emit(OpCodes.Leave, done);
            code.BeginCatchBlock(typeof(Exception));
            code.Emit(OpCodes.Pop); // Native getter/observer failures never escape the callback.
            code.EndExceptionBlock();
            code.Emit(OpCodes.Ret);
            return postfix;
        }

        [HarmonyPatch]
        private static class GroundedRaycastPatch
        {
            [HarmonyTargetMethod]
            private static MethodBase TargetMethod() => _groundedTarget ??
                throw new MissingMethodException("grounded callback target was not initialized");

            [HarmonyPostfix]
            private static MethodInfo PostfixFactory(MethodBase originalMethod)
            {
                if (!ReferenceEquals(originalMethod, _groundedTarget) || _groundedFactoryResult == null)
                    throw new MissingMethodException("grounded callback factory target changed");
                return _groundedFactoryResult;
            }
        }

        internal static void StartChompPostfix(Chomper __instance)
        {
            try
            {
                if (__instance == null || !__instance.IsChomping()) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnNativeBiteStarted();
            }
            catch (Exception) { }
        }

        internal static void EatAndProducePostfix(SlimeEat __instance)
        {
            try
            {
                if (__instance == null) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnNativeDigestStarted();
            }
            catch (Exception) { }
        }

        internal static void BiteCompletePostfix(Chomper __instance)
        {
            try
            {
                if (__instance == null) return; // Native completion callbacks may destroy the actor.
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnNativeBiteEnded(true);
            }
            catch (Exception) { }
        }

        internal static void ForceCancelPostfix(Chomper __instance)
        {
            try
            {
                if (__instance == null) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnNativeBiteEnded(false);
            }
            catch (Exception) { }
        }

        internal static void LeapTowardPostfix(StalkConsumable __instance)
        {
            try
            {
                if (__instance == null) return;
                var readMode = _readStalkMode;
                if (readMode == null || readMode(__instance) != PounceMode) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnConfirmedJump();
            }
            catch (Exception) { }
        }

        internal static void RandomActionPrefix(SlimeRandomMove __instance, out float __state, float ___nextJumpTime)
        {
            __state = ___nextJumpTime;
        }

        internal static void RandomActionPostfix(SlimeRandomMove __instance, float __state, float ___nextJumpTime)
        {
            try
            {
                if (__instance == null || ___nextJumpTime == __state) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnConfirmedJump();
            }
            catch (Exception) { }
        }

        // Harmony 2.0.1 snapshots a by-value view of a native ref argument before the original:
        // its postfix still sees the old value. Bind the actual ref slot READ-ONLY (C# in) so
        // pre/post reads observe the native timer change; these observers cannot assign to it.
        internal static void MoveTowardsPrefix(FindConsumable __instance, in float nextJumpAvail, out float __state)
        {
            __state = nextJumpAvail;
        }

        internal static void MoveTowardsPostfix(FindConsumable __instance, in float nextJumpAvail, float __state)
        {
            try
            {
                if (__instance == null || nextJumpAvail == __state) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnConfirmedJump();
            }
            catch (Exception) { }
        }

        private static void GroundedObservationPostfix(SlimeSubbehaviourPlexer __instance, bool airborne)
        {
            try
            {
                if (__instance == null) return;
                var driver = __instance.GetComponent<ImportedGameplayAnimation>();
                if (driver != null) driver.OnGroundedResult(__instance, airborne);
            }
            catch (Exception) { }
        }
    }
}
