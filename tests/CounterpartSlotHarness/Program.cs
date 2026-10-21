using System.Reflection;

var selector = Assembly.GetExecutingAssembly().GetType("SlimeBridge.Shared.SaveSlotSelection");
if (selector == null) throw new Exception("Missing generic native free-slot selector");
var method = selector.GetMethod("SelectFree", BindingFlags.Public | BindingFlags.Static)
    ?? throw new Exception("Missing SelectFree API");
var selected = (int)method.Invoke(null, new object[] {
    6, new[] { "save_0", "save_1", "save_2" },
    new[] { "save", "save", "save" }, new[] { 0, 0, 0 }, new[] { false, false, false }
})!;
if (selected != 1) throw new Exception("Must select the least unused zero-based slot, not a revision count");
Console.WriteLine("PASS: multiple native revisions occupy one slot; first free slot is 1");

void Refuses(string name, int count, string[] rawNames, string[] gameNames, int[] indices, bool[] invalid)
{
    try { method.Invoke(null, new object[] { count, rawNames, gameNames, indices, invalid }); }
    catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException)
    { Console.WriteLine("PASS: " + name); return; }
    throw new Exception("Selector did not refuse " + name);
}
Refuses("invalid summary", 6, new[] { "save_0" }, new[] { "save" }, new[] { 0 }, new[] { true });
Refuses("out of range summary", 6, new[] { "save_0" }, new[] { "save" }, new[] { 6 }, new[] { false });
Refuses("negative summary slot", 6, new[] { "save_0" }, new[] { "save" }, new[] { -1 }, new[] { false });
Refuses("conflicting games in one slot", 6, new[] { "a_0", "b_0" }, new[] { "a", "b" }, new[] { 0, 0 }, new[] { false, false });
Refuses("one game in multiple slots", 6, new[] { "a_0", "a_1" }, new[] { "a", "a" }, new[] { 0, 1 }, new[] { false, false });
Refuses("duplicate raw save", 6, new[] { "a_0", "a_0" }, new[] { "a", "a" }, new[] { 0, 0 }, new[] { false, false });
Refuses("missing native name", 6, new[] { " " }, new[] { "a" }, new[] { 0 }, new[] { false });
Refuses("incomplete traversal", 6, new[] { "a_0" }, Array.Empty<string>(), new[] { 0 }, new[] { false });
Refuses("unknown native slot range", 0, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<int>(), Array.Empty<bool>());
Refuses("full native slots", 2, new[] { "a_0", "b_0" }, new[] { "a", "b" }, new[] { 0, 1 }, new[] { false, false });
Console.WriteLine("PASS: native slot selection checks; no game was created");
