# Create a Word process with startup add-ins and Normal.dotm disabled. Bind only
# through that process's native document window, never through the global ROT.
function Start-IsolatedBuildWord {
    $wordExe = Join-Path ${env:ProgramFiles} 'Microsoft Office\root\Office16\WINWORD.EXE'
    if (-not (Test-Path -LiteralPath $wordExe)) { throw "Word executable not found: $wordExe" }
    if (-not ('NplWordWindow' -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class NplWordWindow {
    public delegate bool EnumProc(IntPtr hwnd, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int size);
    [DllImport("oleacc.dll")] public static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint id, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out object obj);
    public static object Find(int processId) {
        object result = null;
        EnumWindows(delegate(IntPtr parent, IntPtr unused) {
            uint pid; GetWindowThreadProcessId(parent, out pid);
            if (pid != processId) return true;
            EnumChildWindows(parent, delegate(IntPtr child, IntPtr unused2) {
                var name = new StringBuilder(256); GetClassName(child, name, 256);
                if (name.ToString() == "_WwG") {
                    Guid iid = new Guid("00020400-0000-0000-C000-000000000046");
                    object native;
                    if (AccessibleObjectFromWindow(child, 0xfffffff0, ref iid, out native) == 0) { result = native; return false; }
                }
                return true;
            }, IntPtr.Zero);
            return result == null;
        }, IntPtr.Zero);
        return result;
    }
}
"@
    }
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ('NPL-WordLaunch-' + [Guid]::NewGuid().ToString('N') + '.rtf')
    [IO.File]::WriteAllText($scratch, '{\rtf1\ansi NPL build workspace}')
    $existing = @(Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object Id)
    $buildProcess = Start-Process -FilePath $wordExe -ArgumentList '/x', '/a', '/q', ('"' + $scratch + '"') -WindowStyle Hidden -PassThru
    if ($existing -contains $buildProcess.Id) { throw 'Word launch reused an existing user process; refusing automation' }
    try {
        $deadline = (Get-Date).AddSeconds(35)
        $native = $null
        while ((Get-Date) -lt $deadline -and -not $buildProcess.HasExited) {
            $native = [NplWordWindow]::Find($buildProcess.Id)
            if ($null -ne $native) { break }
            Start-Sleep -Milliseconds 250
        }
        if ($null -eq $native) { throw 'Cannot bind to the isolated Word process' }
        $application = $native.GetType().InvokeMember('Application', [Reflection.BindingFlags]::GetProperty, $null, $native, $null)
        [uint32]$owner = 0
        [void][NplWordWindow]::GetWindowThreadProcessId([IntPtr]$native.Hwnd, [ref]$owner)
        if ($owner -ne $buildProcess.Id) { throw 'Word automation PID mismatch; refusing automation' }
        $application.AutomationSecurity = 3
        $application.DisplayAlerts = 0
        $application.Visible = $false
        $application.Documents.Item(1).Close(0)
        Remove-Item -LiteralPath $scratch -Force
        return @{ Application = $application; Process = $buildProcess }
    } catch {
        if (-not $buildProcess.HasExited) { Stop-Process -Id $buildProcess.Id -Force }
        Remove-Item -LiteralPath $scratch -Force -ErrorAction SilentlyContinue
        throw
    }
}
