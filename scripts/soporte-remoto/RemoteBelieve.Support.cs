using System;
using System.Text;
using System.IO;
using System.Runtime.InteropServices;

// Only metadata is queried. Opening a placeholder's contents could download it.
public static class BelieveSupport {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint QueryDosDevice(string name, StringBuilder target, int size);
    [DllImport("kernel32.dll")] static extern uint GetLogicalDrives();
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern uint GetDriveType(string path);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool GetVolumeInformation(string root, StringBuilder name, int size, out uint serial,
        out uint maxName, out uint flags, StringBuilder fs, int fsSize);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int id);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token, int kind, out int info, int size, out int returned);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("shell32.dll")] public static extern void SHChangeNotify(uint kind, uint flags, IntPtr item1, IntPtr item2);
    [StructLayout(LayoutKind.Sequential)] public struct Platform { public uint Build, Revision, Integration; }
    [DllImport("cldapi.dll")] public static extern int CfGetPlatformInfo(out Platform info);
    [DllImport("cldapi.dll", CharSet=CharSet.Unicode)]
    static extern int CfGetSyncRootInfoByPath(string path, int kind, IntPtr info, uint size, out uint returned);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct Registration {
        public uint Size;
        [MarshalAs(UnmanagedType.LPWStr)] public string Name;
        [MarshalAs(UnmanagedType.LPWStr)] public string Version;
        public IntPtr RootIdentity; public uint RootLength;
        public IntPtr FileIdentity; public uint FileLength;
        public Guid ProviderId;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct Policies { public uint Size; public ushort Hydration, HydrationModifier, Population, PopulationModifier;
        public uint InSync, HardLink, PlaceholderManagement; }
    [DllImport("cldapi.dll", CharSet=CharSet.Unicode)]
    static extern int CfRegisterSyncRoot(string path, ref Registration registration, ref Policies policies, uint flags);
    [DllImport("cldapi.dll", CharSet=CharSet.Unicode)] static extern int CfUnregisterSyncRoot(string path);

    public static string Device(string letter) {
        var text = new StringBuilder(32768);
        if (QueryDosDevice(letter + ":", text, text.Capacity) != 0) return text.ToString();
        int error = Marshal.GetLastWin32Error();
        if (error != 2 && error != 3) throw new System.ComponentModel.Win32Exception(error);
        uint mask = GetLogicalDrives();
        if (mask == 0) throw new IOException("No se pudo consultar la lista de unidades.");
        if ((mask & (1u << (letter[0] - 'A'))) != 0) return "OCUPADA_SIN_DESTINO";
        return "";
    }
    public static string Volume(string root) {
        uint serial, maxName, flags;
        var fs = new StringBuilder(64);
        uint kind = GetDriveType(root);
        if (kind != 2 && kind != 3) return "Tipo=" + kind + " (no es disco local)";
        if (!GetVolumeInformation(root, null, 0, out serial, out maxName, out flags, fs, fs.Capacity))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return fs.ToString();
    }
    // -1 means unknown. Never assume that access denied means a normal process.
    public static int Elevated(int pid) {
        IntPtr process = OpenProcess(0x1000, false, pid), token = IntPtr.Zero;
        if (process == IntPtr.Zero) return -1;
        try {
            if (!OpenProcessToken(process, 8, out token)) return -1;
            int value, returned;
            return GetTokenInformation(token, 20, out value, 4, out returned) ? value : -1;
        } finally { if (token != IntPtr.Zero) CloseHandle(token); CloseHandle(process); }
    }
    public static string UserSid(int pid) {
        IntPtr process = OpenProcess(0x1000, false, pid), token = IntPtr.Zero;
        if (process == IntPtr.Zero) return "";
        try {
            if (!OpenProcessToken(process, 8, out token)) return "";
            using (var identity = new System.Security.Principal.WindowsIdentity(token)) { return identity.User.Value; }
        } finally { if (token != IntPtr.Zero) CloseHandle(token); CloseHandle(process); }
    }
    public static string Provider(string path) {
        IntPtr buffer = Marshal.AllocHGlobal(1028);
        try {
            uint length;
            int hr = CfGetSyncRootInfoByPath(path, 2, buffer, 1028, out length);
            if (hr < 0) return "HRESULT=0x" + hr.ToString("X8");
            return Marshal.PtrToStringUni(IntPtr.Add(buffer, 4), 256).TrimEnd('\0') + " / " +
                Marshal.PtrToStringUni(IntPtr.Add(buffer, 516), 256).TrimEnd('\0');
        } finally { Marshal.FreeHGlobal(buffer); }
    }
    // A brand new, empty diagnostic directory only. Never unregister the real cache.
    public static string Probe(string parent) {
        string path = Path.Combine(parent, "prueba-nube-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        bool registered = false;
        string result;
        try {
            var registration = new Registration { Name = "Remote Believe diagnostico", Version = "1" };
            registration.Size = (uint)Marshal.SizeOf(typeof(Registration));
            var policies = new Policies { Hydration = 2, Population = 2, InSync = 256 };
            policies.Size = (uint)Marshal.SizeOf(typeof(Policies));
            int hr = CfRegisterSyncRoot(path, ref registration, ref policies, 2u | 4u);
            registered = hr >= 0;
            result = "CfRegisterSyncRoot=0x" + hr.ToString("X8");
            if (registered) {
                hr = CfUnregisterSyncRoot(path);
                result += "; CfUnregisterSyncRoot=0x" + hr.ToString("X8");
                registered = hr < 0;
            }
        } finally {
            // Nonrecursive: unexpected files are always preserved.
            if (!registered && Directory.Exists(path)) Directory.Delete(path, false);
        }
        return result;
    }
}
