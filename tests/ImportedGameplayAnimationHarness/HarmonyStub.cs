using System.Reflection;

namespace HarmonyLib
{
    [AttributeUsage(AttributeTargets.Class)]
    public sealed class HarmonyPatch : Attribute { }
    [AttributeUsage(AttributeTargets.Method)]
    public sealed class HarmonyTargetMethod : Attribute { }
    [AttributeUsage(AttributeTargets.Method)]
    public sealed class HarmonyPostfix : Attribute { }

    public sealed class HarmonyMethod
    {
        public readonly MethodInfo method;
        public HarmonyMethod(MethodInfo method) => this.method = method;
        public HarmonyMethod(Type type, string name) => method = type.GetMethod(name,
            BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)!;
    }

    public sealed class Harmony
    {
        public sealed class InstalledPatch
        {
            public readonly string owner;
            public readonly MethodBase original;
            public readonly HarmonyMethod? prefix;
            public readonly HarmonyMethod? postfix;
            internal InstalledPatch(string id, MethodBase method, HarmonyMethod? pre, HarmonyMethod? post)
            { owner = id; original = method; prefix = pre; postfix = post; }
        }

        public readonly string Id;
        public static readonly List<InstalledPatch> Patches = new();
        public Harmony(string id) => Id = id;
        public PatchClassProcessor CreateClassProcessor(Type patchType) => new(this, patchType);
        public void Patch(MethodBase original, HarmonyMethod? prefix = null, HarmonyMethod? postfix = null,
            HarmonyMethod? transpiler = null, HarmonyMethod? finalizer = null) =>
            Patches.Add(new InstalledPatch(Id, original, prefix, postfix));
        public void UnpatchAll(string harmonyId) => Patches.RemoveAll(p => p.owner == harmonyId);
    }

    public sealed class PatchClassProcessor
    {
        private readonly Harmony _harmony;
        private readonly Type _patchType;
        internal PatchClassProcessor(Harmony harmony, Type patchType)
        { _harmony = harmony; _patchType = patchType; }

        public List<MethodInfo> Patch()
        {
            MethodInfo? targetFactory = null;
            MethodInfo? postfixFactory = null;
            foreach (var method in _patchType.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic))
            {
                if (method.GetCustomAttribute<HarmonyTargetMethod>() != null) targetFactory = method;
                if (method.GetCustomAttribute<HarmonyPostfix>() != null) postfixFactory = method;
            }
            if (targetFactory == null || postfixFactory == null) throw new MissingMethodException("Harmony patch factory methods are required");
            var original = (MethodBase)targetFactory.Invoke(null, null)!;
            var postfix = (MethodInfo)postfixFactory.Invoke(null, new object[] { original })!;
            _harmony.Patch(original, postfix: new HarmonyMethod(postfix));
            return new List<MethodInfo> { postfix };
        }
    }
}
