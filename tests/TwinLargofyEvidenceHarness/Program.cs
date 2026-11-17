using SlimeBridge.SR1;
internal static class Program
{
    private static int Main()
    {
        var checks = 0;
        try
        {
            for (var mask = 0; mask < 16; mask++)
            foreach (var count in new[] { -1, 0, 1, 2 })
            {
                var actual = TwinLargofyEvidence.Accept((mask & 1) != 0, (mask & 2) != 0, (mask & 4) != 0, (mask & 8) != 0, count);
                var expected = mask == 15 && count == 1;
                checks++;
                if (actual != expected) throw new InvalidOperationException("Acceptance mismatch mask=" + mask + " count=" + count);
            }
            Console.WriteLine("TWIN_EVIDENCE_CHECKS=" + checks + " FAILURES=0; pure production-linked evidence policy, no Unity/native.");
            return 0;
        }
        catch (Exception error)
        {
            Console.WriteLine("CAUGHT " + error.GetType().Name + ": " + error.Message);
            Console.WriteLine("TWIN_EVIDENCE_CHECKS=" + checks + " FAILURES=1");
            return 1;
        }
    }
}
