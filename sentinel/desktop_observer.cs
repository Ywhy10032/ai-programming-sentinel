// UTF-8。只读 Windows 无障碍控件，不点击、不输入、不截屏、不读取对话正文。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public class DesktopObserver {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    static JavaScriptSerializer Json = new JavaScriptSerializer();
    static HashSet<string> Set(params string[] names) { return new HashSet<string>(names, StringComparer.OrdinalIgnoreCase); }
    static HashSet<string> Running = Set("Stop generating", "Stop response", "Stop streaming", "Cancel generation", "停止生成", "停止回答", "停止响应", "结束生成");
    static HashSet<string> Send = Set("Send", "Send message", "Send prompt", "发送", "发送消息", "提交");
    static HashSet<string> Allow = Set("Allow once", "Allow", "Approve", "Accept", "允许一次", "允许", "批准", "确认执行");
    static HashSet<string> Deny = Set("Deny", "Reject", "拒绝", "不允许");
    static HashSet<string> Retry = Set("Retry", "Try again", "重试", "重新尝试");
    static HashSet<string> Completed = Set("Response completed", "Response complete", "回答完成", "回答已完成", "生成完成");
    static HashSet<string> Failed = Set("Something went wrong", "Network error", "Request failed", "网络错误", "请求失败", "生成失败");
    static string Provider(string exe) {
        string n = Path.GetFileNameWithoutExtension(exe).ToLowerInvariant();
        switch(n) {
            case "chatgpt": return "chatgpt";
            case "claude": return "claude";
            case "deepseek harness": case "deepseek-harness": case "dsh-desktop": return "deepseek";
            case "zcode": return "zcode";
            case "cursor": return "cursor";
            case "windsurf": case "devin": return "windsurf";
            case "trae": case "trae cn": return "trae";
            case "cherry studio": case "cherrystudio": return "cherry";
            case "lm studio": case "lmstudio": return "lmstudio";
            case "jan": return "jan";
            case "qwen": case "qianwen": case "千问": return "qwen";
            case "kimi": return "kimi";
            case "doubao": case "豆包": return "doubao";
            case "gemini": return "gemini";
            case "poe": return "poe";
            case "perplexity": return "perplexity";
            case "grok": return "grok";
            default: return null;
        }
    }
    static string Identity(AutomationElement e) { return String.Join(".", e.GetRuntimeId()); }
    static void SaveIcon(string exe, string provider, string dir) {
        if (String.IsNullOrEmpty(dir)) return;
        string file = Path.Combine(dir, provider+".png");
        if (File.Exists(file)) return;
        Directory.CreateDirectory(dir);
        using (Icon icon = Icon.ExtractAssociatedIcon(exe)) {
            if (icon != null) using (Bitmap bitmap = icon.ToBitmap()) bitmap.Save(file, System.Drawing.Imaging.ImageFormat.Png);
        }
    }
    [MTAThread]
    public static void Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        string iconDir = args.Length > 0 ? args[0] : null;
        // 可选白名单只用于诊断，默认包含目录内客户端。不会扫描浏览器或其他应用。
        HashSet<string> filter = args.Length > 1 ? Set(args[1].Split(',')) : null;
        EnumWindows(delegate(IntPtr h, IntPtr unused) {
            if (!IsWindowVisible(h)) return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            string exe, provider;
            try { exe = Process.GetProcessById((int)pid).MainModule.FileName; provider = Provider(exe); }
            catch { return true; }
            if (provider == null || (filter != null && !filter.Contains(provider))) return true;
            var item = new Dictionary<string,object>();
            item["provider"] = provider; item["window"] = h.ToInt64().ToString(); item["pid"] = pid;
            item["visible"] = !IsIconic(h); item["readable"] = false;
            try { SaveIcon(exe, provider, iconDir); } catch { }
            if (IsIconic(h)) { Console.WriteLine(Json.Serialize(item)); return true; }
            try {
                AutomationElement root = AutomationElement.FromHandle(h);
                var request = new CacheRequest();
                request.Add(AutomationElement.NameProperty);
                request.Add(AutomationElement.AutomationIdProperty);
                request.Add(AutomationElement.IsEnabledProperty);
                request.Add(AutomationElement.IsOffscreenProperty);
                request.Add(AutomationElement.ControlTypeProperty);
                request.TreeScope = TreeScope.Element;
                AutomationElementCollection elements;
                using(request.Activate()) {
                    elements = root.FindAll(TreeScope.Descendants, new OrCondition(
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.StatusBar),
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Document),
                        new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Edit)));
                }
                // 控件过多的编辑器不盲目扫描：超限明确报告不可读。
                if(elements.Count > 1800) throw new InvalidOperationException();
                bool running=false, send=false, allow=false, deny=false, retry=false, failed=false, complete=false;
                string scope=""; int matched=0;
                foreach(AutomationElement e in elements) {
                    var v=e.Cached;
                    if(v.IsOffscreen) continue;
                    if(v.ControlType == ControlType.Document) { scope += Identity(e)+";"; continue; }
                    if(v.ControlType == ControlType.Edit) continue; // 不读输入框内容。
                    string name=(v.Name ?? "").Trim();
                    // 不导出其他控件名字或标题。只输出这些状态布尔值。
                    if(v.ControlType == ControlType.Button && v.IsEnabled) {
                        running |= Running.Contains(name) || name.StartsWith("Stop generating (", StringComparison.OrdinalIgnoreCase)
                            || name.StartsWith("停止生成（", StringComparison.OrdinalIgnoreCase)
                            || v.AutomationId == "stop-button" || v.AutomationId == "stop-generating-button";
                        send |= Send.Contains(name);
                        allow |= Allow.Contains(name); deny |= Deny.Contains(name); retry |= Retry.Contains(name);
                        if(Running.Contains(name) || Send.Contains(name)) matched++;
                    }
                    if(v.ControlType == ControlType.StatusBar) {
                        complete |= Completed.Contains(name); failed |= Failed.Contains(name);
                    }
                }
                item["readable"]=running || send || (allow&&deny) || failed || complete;
                item["scope"]=scope;
                item["running"]=running; item["ready"]=send;
                item["waiting"]=allow&&deny;
                item["error"]=failed&&retry;
                item["complete"]=complete;
            } catch { item["readable"]=false; }
            Console.WriteLine(Json.Serialize(item));
            return true;
        }, IntPtr.Zero);
    }
}
