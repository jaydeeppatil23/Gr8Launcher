// Built with the Windows .NET Framework compiler; no Python or downloaded SDK.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

namespace Gr8Updater
{
    public sealed class Identity
    {
        public int pid { get; set; }
        public string started { get; set; }
    }
    public sealed class Handoff
    {
        public int schema { get; set; }
        public string operation_id { get; set; }
        public string nonce { get; set; }
        public string app_dir { get; set; }
        public string mode { get; set; }
        public string scope { get; set; }
        public string old_version { get; set; }
        public string new_version { get; set; }
        public string package { get; set; }
        public string package_hash { get; set; }
        public string staging { get; set; }
        public string state_path { get; set; }
        public Identity launcher { get; set; }
    }
    public sealed class Entry
    {
        public long size { get; set; }
        public string sha256 { get; set; }
    }
    public sealed class Inventory
    {
        public int schema { get; set; }
        public string version { get; set; }
        public Dictionary<string, Entry> files { get; set; }
    }
    public sealed class FileAction
    {
        public string path { get; set; }
        public Entry old_file { get; set; }
        public Entry new_file { get; set; }
        public bool backed_up { get; set; }
        public bool installed { get; set; }
    }
    public sealed class Journal
    {
        public int schema { get; set; }
        public string operation_id { get; set; }
        public string phase { get; set; }
        public List<FileAction> actions { get; set; }
    }
    public sealed class Ack
    {
        public string operation_id { get; set; }
        public string nonce { get; set; }
        public string version { get; set; }
        public Identity process { get; set; }
    }

    internal static class Data
    {
        static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
        public static T Read<T>(string path)
        {
            if (new FileInfo(path).Length > 16 * 1024 * 1024) throw new IOException("The update record is too large.");
            return Serializer.Deserialize<T>(File.ReadAllText(path, Encoding.UTF8));
        }
        public static void Write(string path, object value)
        {
            string temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            byte[] bytes = Encoding.UTF8.GetBytes(Serializer.Serialize(value));
            using (FileStream stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
            if (File.Exists(path)) File.Replace(temp, path, null);
            else File.Move(temp, path);
        }
        public static void Append(string path, object value)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(Serializer.Serialize(value) + "\n");
            using (FileStream stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read))
            {
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush(true);
            }
        }
        public static string Hash(string path)
        {
            using (FileStream stream = File.OpenRead(path)) return Hash(stream);
        }
        public static string Hash(Stream stream)
        {
            using (SHA256 sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
        }
        public static bool Same(string a, string b)
        {
            return String.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }
        public static bool Within(string path, string root)
        {
            return Path.GetFullPath(path).StartsWith(Path.GetFullPath(root).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);
        }
        public static string Relative(string root, string relative)
        {
            if (String.IsNullOrEmpty(relative) || relative.Contains("\\") || relative.Contains(":")) throw new IOException("Unsafe update file path.");
            foreach (string part in relative.Split('/'))
            {
                if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                    part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                    Regex.IsMatch(part, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])($|\.)", RegexOptions.IgnoreCase))
                    throw new IOException("Unsafe update file path.");
            }
            string path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', '\\')));
            if (!Within(path, root)) throw new IOException("Update path escapes its application folder.");
            string cursor = path;
            while (cursor != null && (Same(cursor, root) || Within(cursor, root)))
            {
                if ((File.Exists(cursor) || Directory.Exists(cursor)) && (File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Update paths must not contain junctions or links.");
                if (Same(cursor, root)) break;
                cursor = Path.GetDirectoryName(cursor);
            }
            return path;
        }
        public static bool Matches(string path, Entry entry)
        {
            return entry != null && File.Exists(path) && new FileInfo(path).Length == entry.size && Hash(path) == entry.sha256.ToLowerInvariant();
        }
        public static Inventory Manifest(string root, string version)
        {
            Inventory inventory = Read<Inventory>(Relative(root, "bundle-manifest.json"));
            if (inventory == null || inventory.schema != 1 || inventory.version != version || inventory.files == null || inventory.files.Count > 30000 ||
                !inventory.files.ContainsKey("Gr8Launcher.exe") || !inventory.files.ContainsKey("updater.exe") || !inventory.files.ContainsKey("VERSION"))
                throw new IOException("The application bundle inventory is invalid.");
            HashSet<string> paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, Entry> item in inventory.files)
            {
                Relative(root, item.Key);
                if (!paths.Add(item.Key) || item.Key.Equals("bundle-manifest.json", StringComparison.OrdinalIgnoreCase) || item.Value == null ||
                    item.Value.size < 0 || !Regex.IsMatch(item.Value.sha256 ?? "", "^[0-9a-fA-F]{64}$"))
                    throw new IOException("The bundle inventory has invalid file entries.");
            }
            return inventory;
        }
        public static void Verify(string root, Inventory inventory)
        {
            foreach (KeyValuePair<string, Entry> item in inventory.files)
                if (!Matches(Relative(root, item.Key), item.Value)) throw new IOException("An application file is missing or modified: " + item.Key);
            if (File.ReadAllText(Relative(root, "VERSION")).Trim().TrimStart('\uFEFF') != inventory.version)
                throw new IOException("VERSION does not match the bundle inventory.");
        }
    }

    internal static class Processes
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        struct PROCESSENTRY32
        {
            public uint dwSize, cntUsage, th32ProcessID;
            public IntPtr th32DefaultHeapID;
            public uint th32ModuleID, cntThreads, th32ParentProcessID;
            public int pcPriClassBase;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
        }
        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
        [DllImport("kernel32.dll", CharSet = CharSet.Auto)] static extern bool Process32First(IntPtr snapshot, ref PROCESSENTRY32 entry);
        [DllImport("kernel32.dll", CharSet = CharSet.Auto)] static extern bool Process32Next(IntPtr snapshot, ref PROCESSENTRY32 entry);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        public static Identity Identify(Process process)
        {
            return new Identity { pid = process.Id, started = process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString() };
        }
        public static Process Find(Identity identity)
        {
            if (identity == null) return null;
            try
            {
                Process process = Process.GetProcessById(identity.pid);
                if (!process.HasExited && Identify(process).started == identity.started) return process;
                process.Dispose();
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }
            return null;
        }
        public static List<Identity> Tree(Process parent, int excludedRoot = 0)
        {
            Dictionary<int, int> parents = new Dictionary<int, int>();
            IntPtr snapshot = CreateToolhelp32Snapshot(2, 0);
            if (snapshot == new IntPtr(-1)) throw new IOException("Could not identify launcher child processes.");
            try
            {
                PROCESSENTRY32 entry = new PROCESSENTRY32 { dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32)) };
                if (Process32First(snapshot, ref entry)) do { parents[(int)entry.th32ProcessID] = (int)entry.th32ParentProcessID; } while (Process32Next(snapshot, ref entry));
            }
            finally { CloseHandle(snapshot); }
            HashSet<int> ids = new HashSet<int> { parent.Id };
            bool added;
            do
            {
                added = false;
                foreach (KeyValuePair<int, int> item in parents)
                    if (item.Key != excludedRoot && ids.Contains(item.Value)) added |= ids.Add(item.Key);
            } while (added);
            List<Identity> result = new List<Identity>();
            foreach (int id in ids)
            {
                try { using (Process p = Process.GetProcessById(id)) result.Add(Identify(p)); }
                catch (ArgumentException) { }
                catch (InvalidOperationException) { }
            }
            return result;
        }
        public static void Wait(List<Identity> identities, int seconds)
        {
            Stopwatch watch = Stopwatch.StartNew();
            while (watch.Elapsed.TotalSeconds < seconds)
            {
                bool alive = false;
                foreach (Identity identity in identities) using (Process p = Find(identity)) alive |= p != null;
                if (!alive) return;
                Thread.Sleep(100);
            }
            throw new IOException("The launcher is still running. No update files were replaced.");
        }
        public static void OtherCopies(string appDir, HashSet<int> allowed)
        {
            foreach (Process process in Process.GetProcesses())
            {
                using (process)
                {
                    if (allowed.Contains(process.Id)) continue;
                    string path;
                    try { path = process.MainModule.FileName; }
                    catch (Exception) { continue; }
                    if (Data.Same(path, Path.Combine(appDir, "Gr8Launcher.exe")))
                        throw new IOException("Another copy of this launcher is running. Close it before updating.");
                }
            }
        }
    }

    internal sealed class Engine
    {
        readonly string handoffPath, operation;
        readonly Action<string, string, double> report;
        readonly Handoff h;
        Journal journal;
        string stageRoot, backup;
        bool changed;
        Process launched;
        List<Identity> launchedTree = new List<Identity>();
        public Engine(string path, Action<string, string, double> progress)
        {
            handoffPath = Path.GetFullPath(path);
            operation = Path.GetDirectoryName(handoffPath);
            report = progress;
            h = Data.Read<Handoff>(handoffPath);
            if (h == null || h.schema != 1 || !Regex.IsMatch(h.operation_id ?? "", "^[0-9a-f]{32}$") ||
                !Regex.IsMatch(h.nonce ?? "", "^[0-9a-f]{32}$") || Path.GetFileName(operation) != h.operation_id ||
                Path.GetFileName(handoffPath) != "handoff.json" || !Data.Same(Application.StartupPath, operation) ||
                (h.mode != "portable" && h.mode != "installed") ||
                !Regex.IsMatch(h.package_hash ?? "", "^[0-9a-f]{64}$") || Data.Within(operation, h.app_dir) || Data.Same(operation, h.app_dir))
                throw new IOException("The updater handoff is invalid.");
            if (!Regex.IsMatch(h.old_version ?? "", @"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$") ||
                !Regex.IsMatch(h.new_version ?? "", @"^\d+\.\d+\.\d+$")) throw new IOException("Invalid update versions.");
            string cache = Path.GetDirectoryName(Path.GetDirectoryName(operation));
            if (Path.GetFileName(Path.GetDirectoryName(operation)) != "operations" ||
                !Data.Within(h.state_path, Path.Combine(cache, "installations")) ||
                Path.GetFileName(h.state_path) != "state.json" || !Data.Within(h.package, operation)) throw new IOException("Unsafe update cache paths.");
            Data.Relative(h.app_dir, "Gr8Launcher.exe");
            if (h.mode == "portable")
            {
                string expected = Path.Combine(Path.GetDirectoryName(h.app_dir), ".gr8-update-" + h.operation_id);
                if (!Data.Same(h.staging, expected)) throw new IOException("Unsafe update staging directory.");
                stageRoot = Path.Combine(h.staging, "payload", "Gr8Launcher");
                backup = Path.Combine(h.staging, "backup");
                Data.Relative(h.staging, "payload/Gr8Launcher/VERSION");
            }
        }
        void Result(string status, string message)
        {
            Data.Write(Path.Combine(operation, "result.json"), new { schema = 1, operation_id = h.operation_id, status = status, message = message, recovery = Path.Combine(operation, "Recover.cmd") });
        }
        public void RecordFailure(string message, bool recover)
        {
            if (recover || !File.Exists(Path.Combine(operation, "result.json"))) Result(recover ? "recovery_required" : "failed", message);
        }
        void SaveJournal(string phase)
        {
            journal.phase = phase;
            Data.Write(Path.Combine(operation, "journal.json"), journal);
        }
        public void Run(bool recover)
        {
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                if (new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator))
                    throw new IOException("Reopen the launcher normally before updating. Only setup should request administrator approval.");
            string key;
            using (SHA256 sha = SHA256.Create()) key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(h.app_dir).ToLowerInvariant()))).Replace("-", "");
            using (Mutex mutex = new Mutex(false, @"Global\Gr8LauncherUpdate-" + key))
            {
                bool owned;
                try { owned = mutex.WaitOne(0); }
                catch (AbandonedMutexException) { owned = true; }
                if (!owned) throw new IOException("Another update is already in progress for this installation.");
                try
                {
                    if (recover) { Recover(); return; }
                    if (File.Exists(Path.Combine(operation, "journal.json")) || File.Exists(Path.Combine(operation, "result.json")))
                        throw new IOException("This update operation has already been used. Start a new download.");
                    Inventory old = Data.Manifest(h.app_dir, h.old_version);
                    Data.Verify(h.app_dir, old);
                    if (Data.Hash(Path.Combine(operation, "updater.exe")) != old.files["updater.exe"].sha256.ToLowerInvariant())
                        throw new IOException("The staged updater does not match this installation.");
                    Process original = Processes.Find(h.launcher);
                    if (original == null || !Data.Same(original.MainModule.FileName, Path.Combine(h.app_dir, "Gr8Launcher.exe")))
                        throw new IOException("The original launcher process is invalid.");
                    List<Identity> originals;
                    // The launcher spawns this helper; exclude its entire subtree from shutdown waits.
                    using (original) using (Process helper = Process.GetCurrentProcess())
                        originals = Processes.Tree(original, helper.Id);
                    Processes.OtherCopies(h.app_dir, new HashSet<int>(originals.Select(p => p.pid)));
                    report("Preparing update", "Checking the downloaded package...", 0.08);
                    using (FileStream packageLock = new FileStream(h.package, FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        if (Data.Hash(packageLock) != h.package_hash) throw new IOException("The downloaded update failed its integrity check.");
                        if (h.mode == "portable") Prepare(old);
                        else ValidateInstalled();
                        Data.Write(Path.Combine(operation, "ready.json"), new { operation_id = h.operation_id, nonce = h.nonce, process = Processes.Identify(Process.GetCurrentProcess()) });
                        report("Waiting for launcher", "The launcher will close before files are updated.", 0.15);
                        Processes.Wait(originals, 30);
                        Processes.OtherCopies(h.app_dir, new HashSet<int>());
                        Data.Verify(h.app_dir, old);
                        foreach (string path in old.files.Keys.Select(p => Data.Relative(h.app_dir, p)))
                            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None)) { }
                        if (h.mode == "portable") ApplyPortable();
                        else ApplyInstalled();
                    }
                }
                catch (Exception error)
                {
                    string message = error.Message;
                    if (changed && h.mode == "portable")
                    {
                        try { Rollback(); Result("rolled_back", "The update could not start. Your previous launcher was restored."); Launch(false); }
                        catch (Exception recoveryError) { Result("recovery_required", message + " Recovery: " + recoveryError.Message); throw; }
                        throw new IOException("The update could not start. Your previous launcher was restored.", error);
                    }
                    if (!File.Exists(Path.Combine(operation, "result.json"))) Result(h.mode == "installed" && changed ? "repair_required" : "failed", message);
                    if (h.mode == "installed" && launched != null) StopLaunched();
                    if (Processes.Find(h.launcher) == null)
                    {
                        try
                        {
                            string version = File.ReadAllText(Path.Combine(h.app_dir, "VERSION")).Trim();
                            Data.Verify(h.app_dir, Data.Manifest(h.app_dir, version));
                            if (h.mode != "installed" || !changed) Launch(false);
                        }
                        catch (Exception) { }
                    }
                    throw;
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
        void Prepare(Inventory old)
        {
            Inventory next = Data.Manifest(stageRoot, h.new_version);
            Data.Verify(stageRoot, next);
            HashSet<string> actual = new HashSet<string>(Directory.GetFiles(stageRoot, "*", SearchOption.AllDirectories).Select(p => p.Substring(stageRoot.Length + 1).Replace('\\', '/')), StringComparer.OrdinalIgnoreCase);
            if (!actual.SetEquals(next.files.Keys.Concat(new[] { "bundle-manifest.json" }))) throw new IOException("The staged payload contains unlisted files.");
            long needed = old.files.Values.Sum(entry => entry.size) + 32 * 1024 * 1024;
            if (new DriveInfo(Path.GetPathRoot(h.staging)).AvailableFreeSpace < needed) throw new IOException("Not enough disk space to back up this installation.");
            journal = new Journal { schema = 1, operation_id = h.operation_id, phase = "prepared", actions = new List<FileAction>() };
            HashSet<string> names = new HashSet<string>(old.files.Keys, StringComparer.OrdinalIgnoreCase);
            names.UnionWith(next.files.Keys);
            if (names.Count != old.files.Keys.Union(next.files.Keys).Count()) throw new IOException("The update changes file name casing ambiguously.");
            foreach (string path in names.OrderBy(p => p == "Gr8Launcher.exe" ? 1 : 0).ThenBy(p => p, StringComparer.Ordinal))
            {
                Entry before, after;
                old.files.TryGetValue(path, out before);
                next.files.TryGetValue(path, out after);
                if (before == null && (File.Exists(Data.Relative(h.app_dir, path)) || Directory.Exists(Data.Relative(h.app_dir, path))))
                    throw new IOException("An update file would overwrite an unrelated file: " + path);
                journal.actions.Add(new FileAction { path = path, old_file = before, new_file = after });
            }
            journal.actions.Add(new FileAction { path = "bundle-manifest.json", old_file = Describe(Path.Combine(h.app_dir, "bundle-manifest.json")), new_file = Describe(Path.Combine(stageRoot, "bundle-manifest.json")) });
            Directory.CreateDirectory(backup);
            SaveJournal("prepared");
        }
        static Entry Describe(string path) { return new Entry { size = new FileInfo(path).Length, sha256 = Data.Hash(path) }; }
        void ApplyPortable()
        {
            changed = true;
            SaveJournal("applying");
            int completed = 0;
            Stopwatch lastReport = Stopwatch.StartNew();
            foreach (FileAction action in journal.actions)
            {
                string target = Data.Relative(h.app_dir, action.path);
                string saved = Data.Relative(backup, action.path);
                // The durable journal already records the complete move plan.
                // Append completion records rather than rewriting every file's
                // metadata after every move (which would cost quadratic I/O).
                if (action.old_file != null)
                {
                    if (!Data.Matches(target, action.old_file)) throw new IOException("A managed file changed during the update.");
                    Directory.CreateDirectory(Path.GetDirectoryName(saved));
                    File.Move(target, saved);
                    action.backed_up = true;
                    Data.Append(Path.Combine(operation, "moves.jsonl"), new { path = action.path, phase = "backed_up" });
                }
                if (action.new_file != null)
                {
                    string source = Data.Relative(stageRoot, action.path);
                    if (!Data.Matches(source, action.new_file)) throw new IOException("A staged file changed during the update.");
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    File.Move(source, target);
                    action.installed = true;
                    Data.Append(Path.Combine(operation, "moves.jsonl"), new { path = action.path, phase = "installed" });
                }
                completed++;
                if (lastReport.ElapsedMilliseconds >= 60 || completed == journal.actions.Count)
                {
                    report("Updating launcher", "Keeping your profiles and game files in place.", 0.2 + 0.6 * completed / journal.actions.Count);
                    lastReport.Restart();
                }
            }
            Data.Verify(h.app_dir, Data.Manifest(h.app_dir, h.new_version));
            SaveJournal("awaiting_startup");
            Launch(true);
            AwaitStartup();
            SaveJournal("succeeded");
            Result("succeeded", "Gr8 Launcher was updated to v" + h.new_version + ".");
            Cleanup();
            report("Update complete", "Your launcher is ready.", 1);
        }
        void ValidateInstalled()
        {
            if (h.scope != "allusers" && h.scope != "currentuser") throw new IOException("Invalid installation scope.");
            RegistryHive hive = h.scope == "allusers" ? RegistryHive.LocalMachine : RegistryHive.CurrentUser;
            bool matches = false;
            foreach (RegistryView view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
                using (RegistryKey root = RegistryKey.OpenBaseKey(hive, view))
                using (RegistryKey key = root.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\{449A7BDC-D374-4AAB-8D31-D699214AC088}_is1"))
                {
                    string location = key == null ? null : key.GetValue("InstallLocation") as string;
                    matches |= location != null && Data.Same(location, h.app_dir);
                }
            if (!matches) throw new IOException("This setup no longer matches the registered installation.");
        }
        void ApplyInstalled()
        {
            report("Installing update", h.scope == "allusers" ? "Windows may ask for administrator approval." : "Updating this user installation.", 0.4);
            ProcessStartInfo info = new ProcessStartInfo(h.package)
            {
                UseShellExecute = true,
                Arguments = "/SILENT /NORESTART /NORESTARTAPPLICATIONS /NOCLOSEAPPLICATIONS /RESTARTEXITCODE=3010 /" + h.scope.ToUpperInvariant() + " /DIR=\"" + h.app_dir + "\""
            };
            if (h.scope == "allusers") info.Verb = "runas";
            using (Process setup = Process.Start(info))
            {
                changed = true;
                setup.WaitForExit();
                if (setup.ExitCode == 3010)
                {
                    Result("restart_required", "Setup needs a Windows restart. Restart Windows when convenient, then reopen the launcher.");
                    throw new IOException("Setup needs a Windows restart before this update can finish.");
                }
                if (setup.ExitCode != 0) throw new IOException("Setup was cancelled or failed (code " + setup.ExitCode + "). Use setup to repair the launcher if needed.");
            }
            Data.Verify(h.app_dir, Data.Manifest(h.app_dir, h.new_version));
            Launch(true);
            AwaitStartup();
            Result("succeeded", "Gr8 Launcher was updated to v" + h.new_version + ".");
            report("Update complete", "Your launcher is ready.", 1);
        }
        void Launch(bool update)
        {
            ProcessStartInfo info = new ProcessStartInfo(Data.Relative(h.app_dir, "Gr8Launcher.exe")) { WorkingDirectory = h.app_dir, UseShellExecute = false };
            info.EnvironmentVariables.Remove("GR8_UPDATE_HANDOFF");
            info.EnvironmentVariables.Remove("GR8_UPDATE_NONCE");
            if (update)
            {
                info.EnvironmentVariables["GR8_UPDATE_HANDOFF"] = handoffPath;
                info.EnvironmentVariables["GR8_UPDATE_NONCE"] = h.nonce;
            }
            launched = Process.Start(info);
            launchedTree = Processes.Tree(launched);
        }
        void AwaitStartup()
        {
            report("Reopening launcher", "Waiting for the launcher window to load...", 0.9);
            Stopwatch watch = Stopwatch.StartNew();
            while (watch.Elapsed.TotalSeconds < 60)
            {
                if (launched.HasExited) throw new IOException("The updated launcher closed before confirming startup.");
                launchedTree = Processes.Tree(launched);
                string path = Path.Combine(operation, "ack.json");
                if (File.Exists(path))
                {
                    Ack ack = Data.Read<Ack>(path);
                    if (ack != null && ack.operation_id == h.operation_id && ack.nonce == h.nonce && ack.version == h.new_version &&
                        ack.process != null && ack.process.pid == launched.Id && ack.process.started == Processes.Identify(launched).started) return;
                }
                Thread.Sleep(100);
            }
            throw new IOException("The updated launcher did not confirm startup in time.");
        }
        void StopLaunched()
        {
            foreach (Identity id in launchedTree.AsEnumerable().Reverse())
                using (Process process = Processes.Find(id)) if (process != null) process.CloseMainWindow();
            Thread.Sleep(500);
            foreach (Identity id in launchedTree.AsEnumerable().Reverse())
                using (Process process = Processes.Find(id)) if (process != null) { process.Kill(); process.WaitForExit(5000); }
            Processes.Wait(launchedTree, 5);
        }
        void Rollback()
        {
            report("Restoring previous version", "Your backup will be kept until recovery finishes.", 0.3);
            StopLaunched();
            Processes.OtherCopies(h.app_dir, new HashSet<int>());
            SaveJournal("rolling_back");
            foreach (FileAction action in journal.actions.AsEnumerable().Reverse())
            {
                string target = Data.Relative(h.app_dir, action.path);
                string saved = Data.Relative(backup, action.path);
                if (File.Exists(saved))
                {
                    if (!Data.Matches(saved, action.old_file)) throw new IOException("A backup file failed verification: " + action.path);
                    if (File.Exists(target))
                    {
                        if (!Data.Matches(target, action.new_file)) throw new IOException("A changed file requires manual recovery: " + action.path);
                        File.Delete(target);
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    File.Move(saved, target);
                }
                else if (action.old_file != null)
                {
                    if (!Data.Matches(target, action.old_file)) throw new IOException("The previous file could not be restored: " + action.path);
                }
                else if (File.Exists(target))
                {
                    if (!Data.Matches(target, action.new_file)) throw new IOException("An update file changed and needs manual recovery: " + action.path);
                    File.Delete(target);
                }
            }
            Data.Verify(h.app_dir, Data.Manifest(h.app_dir, h.old_version));
            SaveJournal("rolled_back");
        }
        void Recover()
        {
            Processes.OtherCopies(h.app_dir, new HashSet<int>());
            string path = Path.Combine(operation, "journal.json");
            if (h.mode == "portable" && File.Exists(path))
            {
                journal = Data.Read<Journal>(path);
                if (journal == null || journal.schema != 1 || journal.operation_id != h.operation_id || journal.actions == null || journal.actions.Count > 30001)
                    throw new IOException("The recovery journal is invalid. Keep the backup and install manually.");
                if (journal.phase == "succeeded")
                {
                    Data.Verify(h.app_dir, Data.Manifest(h.app_dir, h.new_version));
                    Result("succeeded", "The update already completed.");
                    Launch(false);
                    return;
                }
                Rollback();
                Result("rolled_back", "Your previous launcher was restored after an interrupted update.");
            }
            else
            {
                string version = File.ReadAllText(Data.Relative(h.app_dir, "VERSION")).Trim();
                Data.Verify(h.app_dir, Data.Manifest(h.app_dir, version));
                Result("recovered", "The launcher is ready. Check for updates again when convenient.");
            }
            Launch(false);
            report("Recovery complete", "Your launcher is reopening.", 1);
        }
        void Cleanup()
        {
            try
            {
                if (!Data.Same(h.staging, Path.Combine(Path.GetDirectoryName(h.app_dir), ".gr8-update-" + h.operation_id))) return;
                // Validate all generated paths before a recursive cleanup; never follow reparse points.
                foreach (string path in Directory.EnumerateFileSystemEntries(h.staging, "*", SearchOption.AllDirectories))
                    Data.Relative(h.staging, path.Substring(h.staging.Length + 1).Replace('\\', '/'));
                Directory.Delete(h.staging, true);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    internal sealed class UpdateWindow : Form
    {
        readonly Color navy = ColorTranslator.FromHtml("#182842"), gold = ColorTranslator.FromHtml("#f5de9a");
        readonly PrivateFontCollection fonts = new PrivateFontCollection();
        readonly Label heading, description, detail;
        readonly Button close;
        double progress;
        bool finished;
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr window, int message, IntPtr wparam, IntPtr lparam);
        public UpdateWindow()
        {
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(550, 310);
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = navy;
            Text = "Gr8 Launcher Update";
            DoubleBuffered = true;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }
            string font = Path.Combine(Application.StartupPath, "PixelifySans.ttf");
            if (File.Exists(font)) fonts.AddFontFile(font);
            Font primary = fonts.Families.Length > 0 ? new Font(fonts.Families[0], 13) : new Font("Segoe UI", 12);
            Label brand = new Label { Text = "GR8 LAUNCHER", ForeColor = gold, BackColor = Color.Transparent, Font = primary, Location = new Point(24, 18), AutoSize = true };
            brand.MouseDown += Drag;
            Controls.Add(brand);
            close = new Button { Text = "×", ForeColor = gold, BackColor = navy, FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 16), Location = new Point(506, 6), Size = new Size(36, 32), Enabled = false, TabStop = false };
            close.FlatAppearance.BorderSize = 0;
            close.FlatAppearance.MouseOverBackColor = navy;
            close.Click += delegate { Close(); };
            Controls.Add(close);
            heading = new Label { Text = "Preparing update", ForeColor = gold, BackColor = Color.Transparent, Font = fonts.Families.Length > 0 ? new Font(fonts.Families[0], 25) : new Font("Segoe UI", 22), Location = new Point(28, 74), Size = new Size(494, 44) };
            description = new Label { Text = "Getting your launcher ready...", ForeColor = Color.FromArgb(215, 220, 231), BackColor = Color.Transparent, Font = new Font("Segoe UI", 11), Location = new Point(30, 131), Size = new Size(490, 50) };
            detail = new Label { ForeColor = Color.FromArgb(146, 157, 177), BackColor = Color.Transparent, Font = new Font("Segoe UI", 9), Location = new Point(30, 232), Size = new Size(490, 54) };
            Controls.AddRange(new Control[] { heading, description, detail });
            MouseDown += Drag;
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (!finished) e.Cancel = true; };
        }
        void Drag(object sender, MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left) { ReleaseCapture(); SendMessage(Handle, 0xA1, new IntPtr(2), IntPtr.Zero); }
        }
        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (LinearGradientBrush ambient = new LinearGradientBrush(ClientRectangle, navy, ColorTranslator.FromHtml("#454641"), 315)) e.Graphics.FillRectangle(ambient, ClientRectangle);
            using (SolidBrush surface = new SolidBrush(Color.FromArgb(66, 0, 0, 0))) e.Graphics.FillRectangle(surface, 30, 202, 490, 7);
            using (SolidBrush fill = new SolidBrush(gold)) e.Graphics.FillRectangle(fill, 30, 202, (int)(490 * progress), 7);
            using (Pen edge = new Pen(Color.FromArgb(38, 245, 222, 154))) e.Graphics.DrawRectangle(edge, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
            base.OnPaint(e);
        }
        public void Report(string title, string message, double fraction)
        {
            if (InvokeRequired) { BeginInvoke(new Action<string, string, double>(Report), title, message, fraction); return; }
            heading.Text = title;
            description.Text = message;
            progress = Math.Max(0, Math.Min(1, fraction));
            detail.Text = "Profiles, accounts, and Minecraft files stay in their existing folders.";
            Invalidate();
        }
        public void Complete(bool success, string message, string recovery)
        {
            if (InvokeRequired) { BeginInvoke(new Action<bool, string, string>(Complete), success, message, recovery); return; }
            finished = true;
            close.Enabled = true;
            if (!success) { heading.Text = "Update needs attention"; description.Text = message; detail.Text = "Recovery files: " + recovery; }
            else { var timer = new System.Windows.Forms.Timer { Interval = 1800 }; timer.Tick += delegate { timer.Stop(); timer.Dispose(); Close(); }; timer.Start(); }
            Invalidate();
        }
        public void SavePreview(string path)
        {
            using (Bitmap bitmap = new Bitmap(ClientSize.Width, ClientSize.Height)) { DrawToBitmap(bitmap, ClientRectangle); bitmap.Save(path); }
        }
        public void EnablePreview() { finished = true; close.Enabled = true; }
        protected override void Dispose(bool disposing)
        {
            if (disposing) fonts.Dispose();
            base.Dispose(disposing);
        }
    }

    internal static class Program
    {
        static void CleanupInstalled(string oldPath, string appDir)
        {
            string current = File.ReadAllText(Data.Relative(appDir, "VERSION")).Trim();
            Inventory next = Data.Manifest(appDir, current);
            Data.Verify(appDir, next);
            Inventory old = Data.Read<Inventory>(oldPath);
            if (old == null || old.schema != 1 || old.files == null || old.files.Count > 30000)
                throw new IOException("The previous bundle inventory is invalid.");
            HashSet<string> retained = new HashSet<string>(next.files.Keys, StringComparer.OrdinalIgnoreCase);
            HashSet<string> deletedDirs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, Entry> item in old.files)
            {
                string path = Data.Relative(appDir, item.Key);
                if (!retained.Contains(item.Key) && Data.Matches(path, item.Value))
                {
                    File.Delete(path);
                    string dir = Path.GetDirectoryName(path);
                    if (!Data.Same(dir, appDir)) deletedDirs.Add(dir);
                }
            }
            foreach (string dir in deletedDirs.OrderByDescending(d => d.Length))
            {
                try
                {
                    string cursor = dir;
                    while (cursor != null && !Data.Same(cursor, appDir) && Data.Within(cursor, appDir))
                    {
                        if (Directory.Exists(cursor) && !Directory.EnumerateFileSystemEntries(cursor).Any())
                        {
                            Directory.Delete(cursor);
                            cursor = Path.GetDirectoryName(cursor);
                        }
                        else break;
                    }
                }
                catch (Exception) { }
            }
        }
        [STAThread]
        static void Main(string[] args)
        {
            // Invoked only by Inno Setup using its protected copy of the old
            // inventory and the new helper already installed in {app}.
            if (args.Length == 3 && args[0] == "--cleanup-installed")
            {
                try
                {
                    if (!Data.Same(Application.StartupPath, args[2])) throw new IOException("Cleanup must use the installed helper.");
                    CleanupInstalled(args[1], args[2]);
                    Environment.ExitCode = 0;
                }
                catch (Exception) { Environment.ExitCode = 1; }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (UpdateWindow window = new UpdateWindow())
            {
                window.Shown += delegate
                {
                    if (args.Length >= 1 && args[0] == "--preview")
                    {
                        window.Report("Updating launcher", "Installing v2.4.0. Your launcher will reopen shortly.", 0.64);
                        window.EnablePreview();
                        if (args.Length == 3 && args[1] == "--screenshot") { window.SavePreview(args[2]); window.Complete(true, "", ""); }
                        return;
                    }
                    Task.Run(delegate
                    {
                        Engine engine = null;
                        try
                        {
                            if (args.Length != 2 || (args[0] != "--apply" && args[0] != "--recover")) throw new IOException("Start the updater from Gr8 Launcher or use its recovery shortcut.");
                            engine = new Engine(args[1], window.Report);
                            engine.Run(args[0] == "--recover");
                            window.Complete(true, "", "");
                        }
                        catch (Exception error)
                        {
                            if (engine != null) try { engine.RecordFailure(error.Message, args[0] == "--recover"); } catch (Exception) { }
                            window.Complete(false, error.Message, args.Length == 2 ? Path.GetDirectoryName(args[1]) : Application.StartupPath);
                        }
                    });
                };
                Application.Run(window);
            }
        }
    }
}
