// SR2 1.x (Unity 6000.0) ships UnityEngine.CoreModule with a public System.Runtime.CompilerServices.NullableAttribute
// that has none of the constructors the C# compiler needs, so it can no longer synthesize nullable metadata on its own
// (CS0656 on every `string?`). Declaring the attributes in this assembly makes the compiler use these instead.
namespace System.Runtime.CompilerServices
{
    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Event | AttributeTargets.Field | AttributeTargets.GenericParameter |
                    AttributeTargets.Parameter | AttributeTargets.Property | AttributeTargets.ReturnValue, Inherited = false)]
    internal sealed class NullableAttribute : Attribute
    {
        public readonly byte[] NullableFlags;
        public NullableAttribute(byte flag) { NullableFlags = new[] { flag }; }
        public NullableAttribute(byte[] flags) { NullableFlags = flags; }
    }

    [AttributeUsage(AttributeTargets.Class | AttributeTargets.Delegate | AttributeTargets.Interface | AttributeTargets.Method |
                    AttributeTargets.Struct, Inherited = false)]
    internal sealed class NullableContextAttribute : Attribute
    {
        public readonly byte Flag;
        public NullableContextAttribute(byte flag) { Flag = flag; }
    }

    [AttributeUsage(AttributeTargets.Module, Inherited = false)]
    internal sealed class NullablePublicOnlyAttribute : Attribute
    {
        public readonly bool IncludesInternals;
        public NullablePublicOnlyAttribute(bool includesInternals) { IncludesInternals = includesInternals; }
    }
}
