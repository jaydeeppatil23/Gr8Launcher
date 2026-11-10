using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Gr8 Launcher Uninstall")]
[assembly: AssemblyDescription("Removes Gr8 Launcher from this computer")]
[assembly: AssemblyProduct("Gr8 Launcher")]
[assembly: AssemblyCompany("Gr8 Projects")]
[assembly: AssemblyCopyright("GNU GPL v3")]

namespace Gr8Uninstall
{
    internal static class Theme
    {
        public static readonly Color Navy = Color.FromArgb(24, 40, 66);
        public static readonly Color NavyDeep = Color.FromArgb(12, 22, 38);
        public static readonly Color Card = Color.FromArgb(31, 51, 82);
        public static readonly Color CardEdge = Color.FromArgb(55, 78, 112);
        public static readonly Color Gold = Color.FromArgb(245, 222, 154);
        public static readonly Color GoldBright = Color.FromArgb(255, 235, 176);
        public static readonly Color Text = Color.FromArgb(240, 244, 250);
        public static readonly Color Muted = Color.FromArgb(142, 155, 178);
        public static readonly Color Good = Color.FromArgb(129, 199, 132);
        public static readonly Color Danger = Color.FromArgb(229, 115, 115);

        public static GraphicsPath RoundRect(RectangleF r, float radius)
        {
            GraphicsPath path = new GraphicsPath();
            float d = radius * 2f;
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }

        public static void FillRound(Graphics g, RectangleF r, float radius, Color color)
        {
            using (GraphicsPath path = RoundRect(r, radius))
            using (SolidBrush brush = new SolidBrush(color))
                g.FillPath(brush, path);
        }

        public static void StrokeRound(Graphics g, RectangleF r, float radius, Color color)
        {
            using (GraphicsPath path = RoundRect(r, radius))
            using (Pen pen = new Pen(color, 1f))
                g.DrawPath(pen, path);
        }

        private static PrivateFontCollection pixelFonts;
        private static GCHandle pixelFontHandle;
        private static FontFamily pixelFamily;

        public static Font LoadPixelFont(float size, FontStyle style)
        {
            try
            {
                if (pixelFamily == null)
                {
                    Assembly asm = Assembly.GetExecutingAssembly();
                    using (Stream stream = asm.GetManifestResourceStream("Gr8Setup.font.ttf"))
                    {
                        if (stream != null)
                        {
                            byte[] data = new byte[stream.Length];
                            stream.Read(data, 0, data.Length);
                            pixelFontHandle = GCHandle.Alloc(data, GCHandleType.Pinned);
                            pixelFonts = new PrivateFontCollection();
                            pixelFonts.AddMemoryFont(pixelFontHandle.AddrOfPinnedObject(), data.Length);
                            if (pixelFonts.Families.Length > 0)
                                pixelFamily = pixelFonts.Families[0];
                        }
                    }
                }
                if (pixelFamily != null)
                    return new Font(pixelFamily, size, style);
            }
            catch
            {
            }
            return new Font("Segoe UI", size, style);
        }

        public static Image LoadLogo()
        {
            try
            {
                Assembly asm = Assembly.GetExecutingAssembly();
                using (Stream stream = asm.GetManifestResourceStream("Gr8Setup.icon.png"))
                {
                    if (stream != null)
                    {
                        using (Image source = Image.FromStream(stream))
                            return new Bitmap(source);
                    }
                }
            }
            catch
            {
            }
            return null;
        }
    }

    internal sealed class PillButton : Control
    {
        public bool Primary;
        public bool Danger;
        private bool hovered;

        public PillButton()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            TabStop = true;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            hovered = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hovered = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            RectangleF r = new RectangleF(0, 0, Width - 1, Height - 1);
            Color fill;
            Color text;
            if (Danger)
            {
                fill = hovered ? Color.FromArgb(220, 96, 92) : Color.FromArgb(176, 62, 60);
                text = Color.White;
            }
            else if (Primary)
            {
                fill = hovered ? Theme.GoldBright : Theme.Gold;
                text = Theme.Navy;
            }
            else
            {
                fill = hovered ? Color.FromArgb(48, 72, 108) : Theme.Card;
                text = hovered ? Theme.Gold : Theme.Text;
            }
            Theme.FillRound(g, r, 5f, fill);
            if (!Primary && !Danger)
                Theme.StrokeRound(g, r, 5f, Theme.CardEdge);
            TextRenderer.DrawText(g, Text, Font, new Rectangle(0, 0, Width, Height), text,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPadding | TextFormatFlags.EndEllipsis);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Enter || e.KeyCode == Keys.Space)
            {
                OnClick(EventArgs.Empty);
                e.Handled = true;
            }
            base.OnKeyDown(e);
        }
    }

    internal sealed class PixelCheck : Control
    {
        private bool checkedState;
        private bool hovered;

        public bool Checked
        {
            get { return checkedState; }
            set
            {
                if (checkedState == value) return;
                checkedState = value;
                Invalidate();
            }
        }

        public PixelCheck()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            TabStop = true;
            Height = 24;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            hovered = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            hovered = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnClick(EventArgs e)
        {
            Checked = !Checked;
            Focus();
            base.OnClick(e);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Enter)
            {
                Checked = !Checked;
                e.Handled = true;
            }
            base.OnKeyDown(e);
        }

        protected override void OnGotFocus(EventArgs e)
        {
            Invalidate();
            base.OnGotFocus(e);
        }

        protected override void OnLostFocus(EventArgs e)
        {
            Invalidate();
            base.OnLostFocus(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            RectangleF box = new RectangleF(0, (Height - 15) / 2f, 15, 15);
            Color fill = checkedState
                ? (hovered ? Theme.GoldBright : Theme.Gold)
                : (hovered ? Color.FromArgb(48, 72, 108) : Theme.Card);
            Theme.FillRound(g, box, 3f, fill);
            if (checkedState)
            {
                using (Pen pen = new Pen(Theme.Navy, 2f))
                {
                    pen.StartCap = LineCap.Round;
                    pen.EndCap = LineCap.Round;
                    g.DrawLines(pen, new PointF[] {
                        new PointF(box.X + 3.5f, box.Y + 7.5f),
                        new PointF(box.X + 6.5f, box.Y + 10.5f),
                        new PointF(box.X + 11.5f, box.Y + 4.5f)
                    });
                }
            }
            else
            {
                Theme.StrokeRound(g, box, 3f, hovered ? Theme.Gold : Theme.CardEdge);
            }
            if (Focused)
                Theme.StrokeRound(g, new RectangleF(box.X - 2, box.Y - 2, box.Width + 4, box.Height + 4),
                    3f, Color.FromArgb(130, Theme.Gold));
            TextRenderer.DrawText(g, Text, Font, new Rectangle(25, 0, Width - 25, Height),
                ForeColor, TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPadding | TextFormatFlags.EndEllipsis);
        }
    }

    internal sealed class UninstallForm : Form
    {
        private const int TitleHeight = 44;

        private readonly string version;
        private readonly string installDir;
        private readonly Image logo;
        private readonly Font pixelLarge;
        private readonly Font pixelMedium;
        private readonly Font pixelSmall;
        private readonly Font body;
        private readonly Font bodyBold;
        private readonly Font bodySmall;

        private PixelCheck dataCheck;
        private PillButton removeButton;
        private PillButton keepButton;
        private PillButton closeButton;
        private Label statusLabel;
        private Label percentLabel;
        private Label fileLabel;
        private Label progressHint;
        private PillButton minimizeButton;
        private PillButton closeWindowButton;

        private int view;
        private int percent;
        private readonly List<Control> confirmControls = new List<Control>();

        private T Track<T>(T control) where T : Control
        {
            confirmControls.Add(control);
            return control;
        }

        public UninstallForm()
        {
            version = Application.ProductVersion;
            installDir = Path.GetDirectoryName(Application.ExecutablePath);

            logo = Theme.LoadLogo();
            pixelLarge = Theme.LoadPixelFont(27f, FontStyle.Bold);
            pixelMedium = Theme.LoadPixelFont(17f, FontStyle.Bold);
            pixelSmall = Theme.LoadPixelFont(10.5f, FontStyle.Regular);
            body = new Font("Segoe UI", 9.5f);
            bodyBold = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            bodySmall = new Font("Segoe UI", 8.25f);

            Text = "Remove Gr8 Launcher";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(680, 468);
            MinimumSize = new Size(680, 468);
            MaximumSize = new Size(680, 468);
            DoubleBuffered = true;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                     ControlStyles.OptimizedDoubleBuffer, true);
            BackColor = Theme.NavyDeep;
            Font = body;
            KeyPreview = true;

            BuildTitleBar();
            BuildConfirmView();
            BuildProgressView();
            BuildDoneView();
            ShowConfirmView();

            Shown += delegate { removeButton.Focus(); };
        }

        private void BuildTitleBar()
        {
            Label title = new Label();
            title.Text = "GR8 LAUNCHER  /  REMOVE";
            title.Font = pixelSmall;
            title.ForeColor = Theme.Muted;
            title.BackColor = Color.Transparent;
            title.AutoSize = false;
            title.Bounds = new Rectangle(32, 13, 360, 20);
            Controls.Add(title);

            minimizeButton = new PillButton();
            minimizeButton.Text = "_";
            minimizeButton.Font = bodyBold;
            minimizeButton.Bounds = new Rectangle(584, 8, 34, 28);
            minimizeButton.Click += delegate { WindowState = FormWindowState.Minimized; };
            Controls.Add(minimizeButton);

            closeWindowButton = new PillButton();
            closeWindowButton.Text = "x";
            closeWindowButton.Font = bodyBold;
            closeWindowButton.Danger = true;
            closeWindowButton.Bounds = new Rectangle(624, 8, 34, 28);
            closeWindowButton.Click += delegate { Close(); };
            Controls.Add(closeWindowButton);

            Panel drag = new Panel();
            drag.BackColor = Color.Transparent;
            drag.Bounds = new Rectangle(0, 0, 580, TitleHeight);
            drag.MouseDown += delegate
            {
                ReleaseCapture();
                SendMessage(Handle, 0xA1, 0x2, 0);
            };
            Controls.Add(drag);
            drag.BringToFront();
        }

        private Label MakeLabel(string text, Font font, Color color, Rectangle bounds)
        {
            Label label = new Label();
            label.Text = text;
            label.Font = font;
            label.ForeColor = color;
            label.BackColor = Color.Transparent;
            label.AutoSize = false;
            label.Bounds = bounds;
            label.TextAlign = ContentAlignment.MiddleLeft;
            Controls.Add(label);
            return label;
        }

        private void BuildConfirmView()
        {
            Track(MakeLabel("Remove Gr8 Launcher?", pixelLarge, Theme.Gold, new Rectangle(32, 112, 600, 40)));
            Label location = Track(MakeLabel("Installed at " + installDir,
                bodySmall, Theme.Muted, new Rectangle(32, 154, 616, 20)));
            location.AutoEllipsis = true;

            Track(MakeLabel("THIS WILL REMOVE", pixelSmall, Theme.Gold, new Rectangle(52, 204, 300, 18)));
            Track(MakeLabel("The launcher and its bundled files", body, Color.FromArgb(214, 222, 234),
                new Rectangle(52, 228, 560, 22)));
            Track(MakeLabel("Desktop and Start menu shortcuts", body, Color.FromArgb(214, 222, 234),
                new Rectangle(52, 252, 560, 22)));
            Track(MakeLabel("The entry in Windows Apps & Features", body, Color.FromArgb(214, 222, 234),
                new Rectangle(52, 276, 560, 22)));

            dataCheck = new PixelCheck();
            dataCheck.Text = "Also delete launcher profiles and settings (worlds and mods stay)";
            dataCheck.Font = body;
            dataCheck.ForeColor = Theme.Text;
            dataCheck.Bounds = new Rectangle(52, 340, 580, 26);
            Controls.Add(dataCheck);
            Track(dataCheck);

            Track(MakeLabel("Your Minecraft worlds, installed mods and Java installations are never touched.",
                bodySmall, Color.FromArgb(102, 116, 138), new Rectangle(52, 370, 580, 20)));

            removeButton = new PillButton();
            removeButton.Text = "Remove Gr8 Launcher";
            removeButton.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            removeButton.Danger = true;
            removeButton.Bounds = new Rectangle(32, 402, 300, 44);
            removeButton.Click += delegate { BeginUninstall(); };
            Controls.Add(removeButton);
            Track(removeButton);

            keepButton = new PillButton();
            keepButton.Text = "Keep it";
            keepButton.Font = body;
            keepButton.Bounds = new Rectangle(348, 402, 300, 44);
            keepButton.Click += delegate { Close(); };
            Controls.Add(keepButton);
            Track(keepButton);
        }

        private void BuildProgressView()
        {
            statusLabel = MakeLabel("Removing Gr8 Launcher...", pixelMedium, Theme.Gold,
                new Rectangle(32, 172, 616, 32));
            statusLabel.TextAlign = ContentAlignment.MiddleCenter;

            percentLabel = MakeLabel("0%", pixelLarge, Theme.Gold,
                new Rectangle(32, 220, 616, 50));
            percentLabel.TextAlign = ContentAlignment.MiddleCenter;

            fileLabel = MakeLabel("Closing the launcher...", bodySmall, Theme.Muted,
                new Rectangle(32, 290, 616, 20));
            fileLabel.TextAlign = ContentAlignment.MiddleCenter;

            progressHint = MakeLabel("This only takes a moment.", body, Theme.Text,
                new Rectangle(32, 356, 616, 24));
            progressHint.TextAlign = ContentAlignment.MiddleCenter;
        }

        private void BuildDoneView()
        {
            closeButton = new PillButton();
            closeButton.Text = "Close";
            closeButton.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            closeButton.Primary = true;
            closeButton.Bounds = new Rectangle(190, 364, 300, 44);
            closeButton.Click += delegate { Close(); };
            Controls.Add(closeButton);
        }

        private void ShowConfirmView()
        {
            view = 0;
            SetConfirmControls(true);
            if (closeButton != null) closeButton.Visible = false;
            if (statusLabel != null)
            {
                statusLabel.Visible = false;
                percentLabel.Visible = false;
                fileLabel.Visible = false;
                progressHint.Visible = false;
            }
            Invalidate();
        }

        private void SetConfirmControls(bool visible)
        {
            foreach (Control control in confirmControls)
                control.Visible = visible;
        }

        private void ShowProgressView()
        {
            view = 1;
            percent = 0;
            SetConfirmControls(false);
            closeButton.Visible = false;
            statusLabel.Visible = true;
            percentLabel.Visible = true;
            percentLabel.Text = "0%";
            fileLabel.Visible = true;
            fileLabel.Text = "Closing the launcher...";
            progressHint.Visible = true;
            Invalidate();
        }

        private void ShowDoneView()
        {
            view = 2;
            SetConfirmControls(false);
            statusLabel.Visible = false;
            percentLabel.Visible = false;
            fileLabel.Visible = true;
            fileLabel.Text = "The app folder is being cleaned up in the background.";
            progressHint.Visible = false;
            closeButton.Visible = true;
            Invalidate();
        }

        private void BeginUninstall()
        {
            bool removeData = dataCheck.Checked;
            removeButton.Enabled = false;
            keepButton.Enabled = false;
            dataCheck.Enabled = false;
            ShowProgressView();
            Thread worker = new Thread(new ThreadStart(delegate { RemoveWorker(removeData); }));
            worker.IsBackground = true;
            worker.Start();
        }

        private void RemoveWorker(bool removeData)
        {
            bool failed = false;
            string failure = null;
            try
            {
                Report("Closing the launcher...", 12, "Ending running processes");
                CloseRunningLauncher();

                Report("Removing shortcuts...", 32, "Desktop and Start menu");
                DeleteShortcut(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    "Gr8 Launcher.lnk"));
                DeleteShortcut(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "Gr8 Launcher.lnk"));

                Report("Cleaning up Windows...", 54, "Removing the Apps & Features entry");
                try
                {
                    Registry.CurrentUser.DeleteSubKeyTree(
                        @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gr8Launcher", false);
                }
                catch
                {
                }

                if (removeData)
                {
                    Report("Removing launcher data...", 72, ".minecraft\\gr8launcher");
                    TryDeleteData();
                }

                Report("Finishing up...", 92, "Scheduling folder cleanup");
                ScheduleFolderRemoval();
                Report("Done", 100, "Gr8 Launcher was removed");
            }
            catch (Exception error)
            {
                failed = true;
                failure = error.Message;
            }

            if (IsDisposed) return;
            try
            {
                BeginInvoke((MethodInvoker)delegate
                {
                    if (failed)
                    {
                        ShowConfirmView();
                        removeButton.Enabled = true;
                        keepButton.Enabled = true;
                        dataCheck.Enabled = true;
                        MessageBox.Show(this, "Gr8 Launcher could not be fully removed:\n\n" + failure,
                            "Remove Gr8 Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }
                    else
                    {
                        ShowDoneView();
                    }
                });
            }
            catch
            {
            }
        }

        private void Report(string status, int value, string detail)
        {
            percent = value;
            if (IsDisposed) return;
            try
            {
                BeginInvoke((MethodInvoker)delegate
                {
                    statusLabel.Text = status;
                    percentLabel.Text = value + "%";
                    fileLabel.Text = detail;
                    Invalidate();
                });
            }
            catch
            {
            }
        }

        private static void CloseRunningLauncher()
        {
            List<int> roots = new List<int>();
            foreach (Process process in Process.GetProcessesByName("Gr8Launcher"))
            {
                roots.Add(process.Id);
                try
                {
                    process.CloseMainWindow();
                    if (!process.WaitForExit(2500))
                        process.Kill();
                }
                catch
                {
                }
            }
            if (roots.Count == 0) return;

            try
            {
                using (ManagementObjectSearcher searcher = new ManagementObjectSearcher(
                    "SELECT ProcessId, ParentProcessId FROM Win32_Process WHERE Name = 'flet.exe'"))
                {
                    foreach (ManagementObject item in searcher.Get())
                    {
                        int childId = Convert.ToInt32(item["ProcessId"]);
                        int parentId = Convert.ToInt32(item["ParentProcessId"]);
                        if (!roots.Contains(parentId)) continue;
                        try
                        {
                            Process child = Process.GetProcessById(childId);
                            child.CloseMainWindow();
                            if (!child.WaitForExit(2500))
                                child.Kill();
                        }
                        catch
                        {
                        }
                    }
                }
            }
            catch
            {
            }
        }

        private void DeleteShortcut(string linkPath)
        {
            try
            {
                if (!File.Exists(linkPath)) return;
                string target = ReadShortcutTarget(linkPath);
                if (target != null && !target.StartsWith(installDir, StringComparison.OrdinalIgnoreCase))
                    return;
                File.Delete(linkPath);
            }
            catch
            {
            }
        }

        private static string ReadShortcutTarget(string linkPath)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut",
                    BindingFlags.InvokeMethod, null, shell, new object[] { linkPath });
                string target = (string)shellType.InvokeMember("TargetPath",
                    BindingFlags.GetProperty, null, shortcut, null);
                Marshal.ReleaseComObject(shortcut);
                Marshal.ReleaseComObject(shell);
                return target;
            }
            catch
            {
                return null;
            }
        }

        private static void TryDeleteData()
        {
            try
            {
                string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                string data = Path.Combine(roaming, ".minecraft", "gr8launcher");
                if (Directory.Exists(data))
                    Directory.Delete(data, true);
            }
            catch
            {
            }
        }

        private void ScheduleFolderRemoval()
        {
            string dir = installDir;
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir))
                return;
            string batch = Path.Combine(Path.GetTempPath(),
                "gr8-uninstall-" + Guid.NewGuid().ToString("N") + ".cmd");
            StringBuilder script = new StringBuilder();
            script.AppendLine("@echo off");
            script.AppendLine("setlocal");
            script.AppendLine("set \"TARGET=" + dir + "\"");
            script.AppendLine("set /a tries=0");
            script.AppendLine("ping -n 2 127.0.0.1 >nul");
            script.AppendLine(":retry");
            script.AppendLine("set /a tries+=1");
            script.AppendLine("if not exist \"%TARGET%\" goto cleanup");
            script.AppendLine("rmdir /s /q \"%TARGET%\" >nul 2>&1");
            script.AppendLine("if not exist \"%TARGET%\" goto cleanup");
            script.AppendLine("if %tries% GEQ 20 goto cleanup");
            script.AppendLine("ping -n 2 127.0.0.1 >nul");
            script.AppendLine("goto retry");
            script.AppendLine(":cleanup");
            script.AppendLine("(goto) 2>nul & del \"%~f0\"");
            File.WriteAllText(batch, script.ToString(), Encoding.Default);

            ProcessStartInfo start = new ProcessStartInfo("cmd.exe", "/c \"" + batch + "\"");
            start.CreateNoWindow = true;
            start.UseShellExecute = false;
            start.WorkingDirectory = Path.GetTempPath();
            Process.Start(start);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;

            using (LinearGradientBrush background = new LinearGradientBrush(
                ClientRectangle, Theme.NavyDeep, Theme.Navy, 35f))
                g.FillRectangle(background, ClientRectangle);

            using (GraphicsPath glow = new GraphicsPath())
            {
                glow.AddEllipse(ClientRectangle.Width - 330, -230, 560, 480);
                using (PathGradientBrush brush = new PathGradientBrush(glow))
                {
                    brush.CenterColor = Color.FromArgb(46, 128, 74, 70);
                    brush.SurroundColors = new Color[] { Color.FromArgb(0, 24, 40, 66) };
                    g.FillPath(brush, glow);
                }
            }

            using (Pen line = new Pen(Color.FromArgb(30, 255, 255, 255), 1f))
                g.DrawLine(line, 0, TitleHeight, ClientRectangle.Width, TitleHeight);

            if (logo != null)
            {
                g.InterpolationMode = InterpolationMode.NearestNeighbor;
                g.PixelOffsetMode = PixelOffsetMode.Half;
                g.DrawImage(logo, new Rectangle(32, 40, 48, 48));
            }

            if (view == 1)
                PaintProgress(g);
            else if (view == 2)
                PaintDone(g);
            else
                PaintConfirm(g);

            using (Pen border = new Pen(Color.FromArgb(70, 90, 120, 158), 1f))
                g.DrawRectangle(border, 0, 0, ClientRectangle.Width - 1, ClientRectangle.Height - 1);
        }

        private void PaintConfirm(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(32, 192, 616, 132), 6f, Color.FromArgb(170, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(32, 192, 616, 132), 6f, Color.FromArgb(80, 72, 98, 136));
        }

        private void PaintProgress(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(96, 100, 488, 326), 6f, Color.FromArgb(120, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(96, 100, 488, 326), 6f, Color.FromArgb(70, 72, 98, 136));

            RectangleF track = new RectangleF(148, 274, 384, 8);
            Theme.FillRound(g, track, 4f, Color.FromArgb(90, 0, 0, 0));
            float fill = track.Width * Math.Max(0, Math.Min(100, percent)) / 100f;
            if (fill > 0)
                Theme.FillRound(g, new RectangleF(track.X, track.Y, Math.Max(8f, fill), track.Height),
                    4f, Theme.Gold);
        }

        private void PaintDone(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(120, 100, 440, 326), 6f, Color.FromArgb(150, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(120, 100, 440, 326), 6f, Color.FromArgb(80, 72, 98, 136));

            using (SolidBrush brush = new SolidBrush(Color.FromArgb(42, 129, 199, 132)))
                g.FillEllipse(brush, 316, 132, 48, 48);
            using (Pen pen = new Pen(Theme.Good, 3f))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                g.DrawLines(pen, new PointF[] {
                    new PointF(329, 156), new PointF(338, 165), new PointF(353, 145)
                });
            }
            TextRenderer.DrawText(g, "All clean.", pixelMedium,
                new Rectangle(120, 192, 440, 36), Theme.Gold,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Escape && view == 0)
                Close();
            base.OnKeyDown(e);
        }

        internal void PreviewState(string state, string path)
        {
            if (state == "progress")
            {
                ShowProgressView();
                statusLabel.Text = "Removing Gr8 Launcher...";
                percentLabel.Text = "62%";
                fileLabel.Text = "Desktop and Start menu shortcuts";
                percent = 62;
            }
            else if (state == "done")
            {
                ShowDoneView();
            }
            else
            {
                ShowConfirmView();
            }
            StartPosition = FormStartPosition.Manual;
            Location = new Point(-4000, -4000);
            Show();
            Application.DoEvents();
            using (Bitmap bmp = new Bitmap(ClientSize.Width, ClientSize.Height))
            {
                DrawToBitmap(bmp, new Rectangle(0, 0, ClientSize.Width, ClientSize.Height));
                bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
            }
            Close();
        }

        internal void SilentRemove()
        {
            RemoveWorker(false);
        }

        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();

        [DllImport("user32.dll")]
        private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);
    }

    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length >= 3 && args[0] == "--preview")
            {
                UninstallForm preview = new UninstallForm();
                preview.PreviewState(args[1], args[2]);
                return;
            }
            if (args.Length >= 1 &&
                (args[0] == "/S" || args[0] == "--silent" || args[0] == "/silent"))
            {
                UninstallForm silent = new UninstallForm();
                silent.SilentRemove();
                return;
            }
            Application.Run(new UninstallForm());
        }
    }
}
