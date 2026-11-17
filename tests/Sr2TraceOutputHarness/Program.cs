using SlimeBridge.SR2;
using SlimeBridge.Shared;
using System.Text;
internal static class Program
{
    private static int checks, failures;
    private static void Check(bool value,string message) { checks++;if(!value){failures++;Console.WriteLine("FAIL "+message);} }
    private static bool Refused(Action action) { try { action();return false; } catch(IOException) { return true; } }
    public static int Main(string[] args)
    {
        try
        {
            if(args.Length!=1)throw new ArgumentException("One new caller-owned scratch directory required");
            var root=Path.GetFullPath(args[0]);if(Directory.Exists(root))throw new IOException("scratch already exists");
            var output=Path.Combine(root,"run");Directory.CreateDirectory(output);
            var file=Path.Combine(output,"trace.jsonl");
            Check(!LabSpecPathGuard.IsAllowedFile(root,file,out _),"existing-file validator correctly refuses an absent file");
            // Replay the original Arm conjunction against the actual guard, not a guessed API.
            Check(!(LabSpecPathGuard.IsAllowedFile(root,file,out _)&&!File.Exists(file)),"old absent-target gate cannot arm");
            Sr2LabTraceOutput.Create(root,file);
            Check(File.Exists(file)&&new FileInfo(file).Length==0,"prospective target exclusively created");
            Check(LabSpecPathGuard.IsAllowedFile(root,file,out _),"new trace accepted by existing-file validator");
            var row=Encoding.UTF8.GetBytes("é\n");
            Check(Sr2LabTraceOutput.Append(root,file,row,3),"exact UTF8 byte bound permits one row");
            Check(new FileInfo(file).Length==3,"UTF8 row uses actual bytes, not character count");
            Check(!Sr2LabTraceOutput.Append(root,file,row,3)&&new FileInfo(file).Length==3,"strict cap refuses overshooting row without writing");
            Check(Refused(()=>Sr2LabTraceOutput.Create(root,file)),"existing evidence refused");
            Check(File.ReadAllBytes(file).SequenceEqual(row),"existing evidence not truncated");
            var outside=Path.Combine(Path.GetDirectoryName(root)!,"outside-trace.jsonl");
            Check(Refused(()=>Sr2LabTraceOutput.Create(root,outside))&&!File.Exists(outside),"outside target refused without creation");
            Check(Refused(()=>Sr2LabTraceOutput.Create(root,Path.Combine(root,"missing","trace.jsonl"))),"missing parent refused");
            Check(Refused(()=>Sr2LabTraceOutput.Create(root,root)),"root itself refused");
            File.Delete(file);
            Check(Refused(()=>Sr2LabTraceOutput.Append(root,file,row,3))&&!File.Exists(file),"append never recreates removed evidence");
            var link=Path.Combine(root,"linked");
            if(OperatingSystem.IsWindows())
            {
                var start=new System.Diagnostics.ProcessStartInfo("cmd.exe") {UseShellExecute=false,CreateNoWindow=true};
                foreach(var arg in new[]{"/d","/c","mklink","/J",link,output})start.ArgumentList.Add(arg);
                using var create=System.Diagnostics.Process.Start(start)!;create.WaitForExit();
                if(create.ExitCode!=0)throw new IOException("fixture junction creation failed");
            }
            else Directory.CreateSymbolicLink(link,output);
            Check(Refused(()=>Sr2LabTraceOutput.Create(root,Path.Combine(link,"new.jsonl")))&&!File.Exists(Path.Combine(output,"new.jsonl")),"directory reparse refused before create");
            // Leave evidence in the one supplied scratch directory; no recursive delete or user profile access.
        }
        catch(Exception ex){failures++;Console.WriteLine("CAUGHT "+ex.GetType().Name+": "+ex.Message);}
        Console.WriteLine($"TRACE_OUTPUT_CHECKS={checks} FAILURES={failures}; real file guard and production output helper, no game/Unity");
        return failures==0?0:1;
    }
}
namespace SlimeBridge.Shared { public static class PlayerContent { public static string? Home=>null; } }
