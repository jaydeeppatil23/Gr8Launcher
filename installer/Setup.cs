using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.IO.Compression;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Gr8 Launcher Setup")]
[assembly: AssemblyDescription("Installer for Gr8 Launcher")]
[assembly: AssemblyProduct("Gr8 Launcher")]
[assembly: AssemblyCompany("Gr8 Projects")]
[assembly: AssemblyCopyright("GNU GPL v3")]

namespace Gr8Setup
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
                fill = hovered ? Color.FromArgb(214, 90, 86) : Color.FromArgb(180, 66, 63);
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

    internal sealed class SetupForm : Form
    {
        private const string PayloadMagic = "GR8PAY01";
        private const int TitleHeight = 44;

        private readonly string version;
        private readonly Image logo;
        private readonly Font pixelLarge;
        private readonly Font pixelMedium;
        private readonly Font pixelSmall;
        private readonly Font body;
        private readonly Font bodyBold;
        private readonly Font bodySmall;

        private TextBox pathBox;
        private PillButton browseButton;
        private PixelCheck desktopCheck;
        private PixelCheck launchCheck;
        private PillButton installButton;
        private PillButton cancelButton;
        private PillButton closeDoneButton;
        private PillButton launchNowButton;
        private PillButton openFolderButton;
        private Label statusLabel;
        private Label percentLabel;
        private Label fileLabel;
        private Label progressHint;
        private PillButton minimizeButton;
        private PillButton closeButton;

        private int view;
        private readonly List<Control> installControls = new List<Control>();

        private T Track<T>(T control) where T : Control
        {
            installControls.Add(control);
            return control;
        }

        public SetupForm()
        {
            version = Application.ProductVersion;

            logo = Theme.LoadLogo();
            pixelLarge = Theme.LoadPixelFont(31f, FontStyle.Bold);
            pixelMedium = Theme.LoadPixelFont(19f, FontStyle.Bold);
            pixelSmall = Theme.LoadPixelFont(10.5f, FontStyle.Regular);
            body = new Font("Segoe UI", 9.5f);
            bodyBold = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            bodySmall = new Font("Segoe UI", 8.25f);

            Text = "Gr8 Launcher Setup";
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(820, 540);
            MinimumSize = new Size(820, 540);
            MaximumSize = new Size(820, 540);
            DoubleBuffered = true;
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                     ControlStyles.OptimizedDoubleBuffer, true);
            BackColor = Theme.NavyDeep;
            Font = body;
            KeyPreview = true;

            BuildTitleBar();
            BuildInstallView();
            BuildProgressView();
            BuildDoneView();
            ShowInstallView();

            Shown += delegate
            {
                installButton.Focus();
                pathBox.SelectionStart = 0;
                pathBox.SelectionLength = 0;
            };
        }

        private void BuildTitleBar()
        {
            Label title = new Label();
            title.Text = "GR8 LAUNCHER  /  SETUP";
            title.Font = pixelSmall;
            title.ForeColor = Theme.Muted;
            title.BackColor = Color.Transparent;
            title.AutoSize = false;
            title.Bounds = new Rectangle(48, 13, 360, 20);
            Controls.Add(title);

            minimizeButton = new PillButton();
            minimizeButton.Text = "_";
            minimizeButton.Font = bodyBold;
            minimizeButton.Bounds = new Rectangle(724, 8, 34, 28);
            minimizeButton.Click += delegate { WindowState = FormWindowState.Minimized; };
            Controls.Add(minimizeButton);

            closeButton = new PillButton();
            closeButton.Text = "x";
            closeButton.Font = bodyBold;
            closeButton.Danger = true;
            closeButton.Bounds = new Rectangle(764, 8, 34, 28);
            closeButton.Click += delegate { Close(); };
            Controls.Add(closeButton);

            Panel drag = new Panel();
            drag.BackColor = Color.Transparent;
            drag.Bounds = new Rectangle(0, 0, 720, TitleHeight);
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

        private void BuildInstallView()
        {
            Track(MakeLabel("Gr8 Launcher", pixelLarge, Theme.Gold, new Rectangle(48, 108, 400, 48)));
            Track(MakeLabel("A faster, cleaner way to play Minecraft.",
                new Font("Segoe UI", 12f), Theme.Text, new Rectangle(48, 196, 400, 28)));

            MakeFeature("Install vanilla, Fabric, Forge, NeoForge and Quilt", 242);
            MakeFeature("Search and install mods without leaving the launcher", 280);
            MakeFeature("Ely.by skins, accounts and 3D previews", 318);
            MakeFeature("Profiles, logs and settings that stay out of your way", 356);

            Track(MakeLabel("Minecraft Java Edition  /  GPLv3  /  Not affiliated with Mojang",
                bodySmall, Color.FromArgb(102, 116, 138), new Rectangle(48, 486, 430, 20)));

            Track(MakeLabel("INSTALL LOCATION", pixelSmall, Theme.Gold, new Rectangle(488, 108, 180, 18)));

            browseButton = new PillButton();
            browseButton.Text = "Browse";
            browseButton.Font = bodySmall;
            browseButton.Bounds = new Rectangle(682, 104, 72, 24);
            browseButton.Click += delegate { BrowseFolder(); };
            Controls.Add(browseButton);
            Track(browseButton);

            Panel field = new Panel();
            field.BackColor = Theme.Card;
            field.Bounds = new Rectangle(488, 132, 266, 38);
            Controls.Add(field);
            Track(field);

            pathBox = new TextBox();
            pathBox.Text = DefaultInstallPath();
            pathBox.Font = new Font("Segoe UI", 8.25f);
            pathBox.ForeColor = Theme.Text;
            pathBox.BackColor = Theme.Card;
            pathBox.BorderStyle = BorderStyle.None;
            pathBox.Bounds = new Rectangle(10, 11, 246, 18);
            field.Controls.Add(pathBox);

            desktopCheck = MakeCheck("Create a desktop shortcut", 190, true);
            launchCheck = MakeCheck("Launch Gr8 Launcher when finished", 218, true);

            installButton = new PillButton();
            installButton.Text = "Install Gr8 Launcher";
            installButton.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            installButton.Primary = true;
            installButton.Bounds = new Rectangle(488, 264, 266, 46);
            installButton.Click += delegate { StartInstall(); };
            Controls.Add(installButton);
            Track(installButton);

            Track(MakeLabel("No administrator prompt.", bodySmall, Theme.Muted,
                new Rectangle(488, 322, 266, 18)));
            Track(MakeLabel("Your worlds and mods are never touched.", bodySmall,
                Color.FromArgb(102, 116, 138), new Rectangle(488, 340, 266, 18)));

            cancelButton = new PillButton();
            cancelButton.Text = "Cancel";
            cancelButton.Font = body;
            cancelButton.Bounds = new Rectangle(488, 386, 266, 38);
            cancelButton.Click += delegate { Close(); };
            Controls.Add(cancelButton);
            Track(cancelButton);

            Track(MakeLabel("MINECRAFT", pixelSmall, Theme.Gold, new Rectangle(488, 434, 140, 18)));
            Label platform = Track(MakeLabel("WINDOWS X64", pixelSmall, Theme.Muted,
                new Rectangle(608, 434, 146, 18)));
            platform.TextAlign = ContentAlignment.MiddleRight;
            Track(MakeLabel("Open source, built with Python and Flet.",
                bodySmall, Color.FromArgb(102, 116, 138), new Rectangle(488, 456, 266, 18)));
        }

        private PixelCheck MakeCheck(string text, int y, bool checkedByDefault)
        {
            PixelCheck box = new PixelCheck();
            box.Text = text;
            box.Font = body;
            box.ForeColor = Theme.Text;
            box.Checked = checkedByDefault;
            box.Bounds = new Rectangle(488, y, 266, 24);
            Controls.Add(box);
            Track(box);
            return box;
        }

        private void BuildProgressView()
        {
            statusLabel = MakeLabel("Preparing...", pixelMedium, Theme.Gold,
                new Rectangle(146, 126, 528, 34));
            statusLabel.TextAlign = ContentAlignment.MiddleCenter;

            percentLabel = MakeLabel("0%", pixelLarge, Theme.Gold,
                new Rectangle(146, 164, 528, 54));
            percentLabel.TextAlign = ContentAlignment.MiddleCenter;

            fileLabel = MakeLabel("Reading package...", bodySmall, Theme.Muted,
                new Rectangle(146, 268, 528, 20));
            fileLabel.TextAlign = ContentAlignment.MiddleCenter;

            progressHint = MakeLabel("Keep this window open while Gr8 Launcher is installed.",
                body, Theme.Text, new Rectangle(146, 360, 528, 24));
            progressHint.TextAlign = ContentAlignment.MiddleCenter;
        }

        private void BuildDoneView()
        {
            launchNowButton = new PillButton();
            launchNowButton.Text = "Launch Gr8 Launcher";
            launchNowButton.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            launchNowButton.Primary = true;
            launchNowButton.Bounds = new Rectangle(270, 318, 280, 46);
            launchNowButton.Click += delegate { LaunchInstalled(); };
            Controls.Add(launchNowButton);

            openFolderButton = new PillButton();
            openFolderButton.Text = "Open folder";
            openFolderButton.Font = body;
            openFolderButton.Bounds = new Rectangle(270, 376, 134, 38);
            openFolderButton.Click += delegate { OpenInstallFolder(); };
            Controls.Add(openFolderButton);

            closeDoneButton = new PillButton();
            closeDoneButton.Text = "Close";
            closeDoneButton.Font = body;
            closeDoneButton.Bounds = new Rectangle(416, 376, 134, 38);
            closeDoneButton.Click += delegate { Close(); };
            Controls.Add(closeDoneButton);
        }

        private void MakeFeature(string text, int y)
        {
            Label marker = Track(MakeLabel("", body, Theme.Gold, new Rectangle(48, y + 8, 8, 8)));
            marker.Paint += delegate(object sender, PaintEventArgs e)
            {
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                using (SolidBrush b = new SolidBrush(Theme.Gold))
                    e.Graphics.FillRectangle(b, 0, 0, 7, 7);
            };
            Track(MakeLabel(text, body, Color.FromArgb(214, 222, 234), new Rectangle(64, y, 396, 24)));
        }

        private static string DefaultInstallPath()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string full = Path.Combine(local, "Programs", "Gr8Launcher");
            if (!string.IsNullOrEmpty(local) &&
                full.StartsWith(local + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                return @"%LOCALAPPDATA%" + full.Substring(local.Length);
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (!string.IsNullOrEmpty(profile) &&
                full.StartsWith(profile + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                return @"%USERPROFILE%" + full.Substring(profile.Length);
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            if (!string.IsNullOrEmpty(appData) &&
                full.StartsWith(appData + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                return @"%APPDATA%" + full.Substring(appData.Length);
            return full;
        }

        private static string ResolvePath(string path)
        {
            return Environment.ExpandEnvironmentVariables(path.Trim());
        }

        private void BrowseFolder()
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Choose where Gr8 Launcher should be installed";
                dialog.ShowNewFolderButton = true;
                try
                {
                    string current = ResolvePath(pathBox.Text);
                    if (Directory.Exists(current))
                        dialog.SelectedPath = current;
                }
                catch
                {
                }
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    pathBox.Text = dialog.SelectedPath;
            }
        }

        private void ShowInstallView()
        {
            view = 0;
            SetInstallControls(true);
            if (launchNowButton != null)
            {
                launchNowButton.Visible = false;
                openFolderButton.Visible = false;
                closeDoneButton.Visible = false;
            }
            if (statusLabel != null)
            {
                statusLabel.Visible = false;
                percentLabel.Visible = false;
                fileLabel.Visible = false;
                progressHint.Visible = false;
            }
            Invalidate();
        }

        private void SetInstallControls(bool visible)
        {
            foreach (Control control in installControls)
                control.Visible = visible;
        }

        private void ShowProgressView()
        {
            view = 1;
            SetInstallControls(false);
            statusLabel.Visible = true;
            statusLabel.Text = "Preparing the launcher...";
            percentLabel.Visible = true;
            percentLabel.Text = "0%";
            fileLabel.Visible = true;
            fileLabel.Text = "Reading package...";
            progressHint.Visible = true;
            launchNowButton.Visible = false;
            openFolderButton.Visible = false;
            closeDoneButton.Visible = false;
            Invalidate();
        }

        private void ShowDoneView(bool launched)
        {
            view = 2;
            SetInstallControls(false);
            statusLabel.Visible = false;
            percentLabel.Visible = false;
            fileLabel.Visible = true;
            fileLabel.Text = launched
                ? "Gr8 Launcher is starting. Have fun!"
                : "Gr8 Launcher is ready. Your next world is one click away.";
            progressHint.Visible = false;
            launchNowButton.Visible = !launched;
            openFolderButton.Visible = !launched;
            closeDoneButton.Visible = !launched;
            Invalidate();
        }

        private void StartInstall()
        {
            string target = pathBox.Text.Trim();
            if (target.Length == 0)
            {
                MessageBox.Show(this, "Choose an install location first.", "Gr8 Launcher Setup",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            try
            {
                target = Path.GetFullPath(ResolvePath(target));
                Directory.CreateDirectory(target);
                string probe = Path.Combine(target, ".gr8-write-test");
                File.WriteAllText(probe, "ok");
                File.Delete(probe);
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "That folder cannot be used:\n\n" + error.Message,
                    "Gr8 Launcher Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            installButton.Enabled = false;
            browseButton.Enabled = false;
            pathBox.Enabled = false;
            ShowProgressView();
            Thread worker = new Thread(new ThreadStart(delegate { InstallWorker(target); }));
            worker.IsBackground = true;
            worker.Start();
        }

        private void InstallWorker(string target)
        {
            string tempZip = null;
            try
            {
                Report("Closing a running launcher...", 0, "");
                CloseRunningLauncher();
                tempZip = ExtractPayloadToTemp();

                Report("Unpacking Gr8 Launcher...", 0, "Reading package...");
                Directory.CreateDirectory(target);
                long total = 0;
                using (ZipArchive archive = ZipFile.OpenRead(tempZip))
                {
                    foreach (ZipArchiveEntry entry in archive.Entries)
                        total += Math.Max(1, entry.Length);
                    long done = 0;
                    int lastPercent = -1;
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string destination = Path.GetFullPath(Path.Combine(target, relative));
                        string root = Path.GetFullPath(target);
                        if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                            throw new IOException("The package contains an invalid path.");
                        if (entry.Name.Length == 0)
                        {
                            Directory.CreateDirectory(destination);
                            continue;
                        }
                        Directory.CreateDirectory(Path.GetDirectoryName(destination));
                        entry.ExtractToFile(destination, true);
                        done += Math.Max(1, entry.Length);
                        int percent = (int)Math.Min(99, done * 100 / total);
                        if (percent != lastPercent)
                        {
                            lastPercent = percent;
                            Report("Unpacking Gr8 Launcher...", percent, entry.Name);
                        }
                        else if (done % (4 * 1024 * 1024) < 65536)
                        {
                            Report("Unpacking Gr8 Launcher...", percent, entry.Name);
                        }
                    }
                }

                Report("Creating shortcuts...", 99, "Almost there");
                string exe = Path.Combine(target, "Gr8Launcher.exe");
                if (!File.Exists(exe))
                    throw new FileNotFoundException("The launcher executable is missing from the package.");

                CreateShortcut(Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    "Gr8 Launcher.lnk"), exe, target);
                if (desktopCheck.Checked)
                    CreateShortcut(Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                        "Gr8 Launcher.lnk"), exe, target);

                WriteUninstallEntry(target, exe);
                Report("Finishing up...", 100, "Done");
            }
            catch (Exception error)
            {
                BeginInvoke((MethodInvoker)delegate
                {
                    installButton.Enabled = true;
                    browseButton.Enabled = true;
                    pathBox.Enabled = true;
                    ShowInstallView();
                    MessageBox.Show(this, "Installation failed:\n\n" + error.Message,
                        "Gr8 Launcher Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
                });
                if (tempZip != null)
                    TryDelete(tempZip);
                return;
            }
            finally
            {
                if (tempZip != null)
                    TryDelete(tempZip);
            }

            bool launch = false;
            try
            {
                launch = launchCheck.Checked;
            }
            catch
            {
            }
            BeginInvoke((MethodInvoker)delegate
            {
                installPath = target;
                ShowDoneView(launch);
                if (launch)
                {
                    System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
                    timer.Interval = 700;
                    timer.Tick += delegate
                    {
                        timer.Stop();
                        timer.Dispose();
                        LaunchInstalled();
                        Close();
                    };
                    timer.Start();
                }
            });
        }

        private string installPath;

        private void Report(string status, int percent, string file)
        {
            if (IsDisposed) return;
            try
            {
                BeginInvoke((MethodInvoker)delegate
                {
                    statusLabel.Text = status;
                    percentLabel.Text = percent + "%";
                    fileLabel.Text = file;
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

        private static string ExtractPayloadToTemp()
        {
            string exe = Application.ExecutablePath;
            using (FileStream input = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                if (input.Length < 32)
                    throw new InvalidDataException("The setup package is incomplete.");
                input.Seek(-24, SeekOrigin.End);
                BinaryReader reader = new BinaryReader(input);
                string magic = Encoding.ASCII.GetString(reader.ReadBytes(8));
                if (magic != PayloadMagic)
                    throw new InvalidDataException("The setup package is incomplete.");
                long offset = reader.ReadInt64();
                long length = reader.ReadInt64();
                if (offset <= 0 || length <= 0 || offset + length > input.Length - 24)
                    throw new InvalidDataException("The setup package is invalid.");

                string temp = Path.Combine(Path.GetTempPath(),
                    "gr8launcher-" + Guid.NewGuid().ToString("N") + ".zip");
                input.Seek(offset, SeekOrigin.Begin);
                using (FileStream output = new FileStream(temp, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    byte[] buffer = new byte[1024 * 256];
                    long remaining = length;
                    while (remaining > 0)
                    {
                        int read = input.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (read <= 0) throw new EndOfStreamException();
                        output.Write(buffer, 0, read);
                        remaining -= read;
                    }
                }
                return temp;
            }
        }

        private static void CreateShortcut(string linkPath, string target, string workingDirectory)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut",
                    BindingFlags.InvokeMethod, null, shell, new object[] { linkPath });
                shellType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut,
                    new object[] { target });
                shellType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut,
                    new object[] { workingDirectory });
                shellType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut,
                    new object[] { target + ",0" });
                shellType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut,
                    new object[] { "Gr8 Launcher - Minecraft Java Edition" });
                shellType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
                Marshal.ReleaseComObject(shortcut);
                Marshal.ReleaseComObject(shell);
            }
            catch
            {
            }
        }

        private static void WriteUninstallEntry(string target, string exe)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gr8Launcher"))
                {
                    key.SetValue("DisplayName", "Gr8 Launcher");
                    key.SetValue("DisplayVersion", Application.ProductVersion);
                    key.SetValue("Publisher", "Gr8 Projects");
                    key.SetValue("InstallLocation", target);
                    key.SetValue("DisplayIcon", exe + ",0");
                    key.SetValue("UninstallString", "\"" + Path.Combine(target, "Uninstall.exe") + "\"");
                    key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    key.SetValue("URLInfoAbout", "https://github.com/jaydeeppatil23/Gr8Launcher");
                }
            }
            catch
            {
            }
        }

        private void LaunchInstalled()
        {
            string target = installPath;
            if (target == null)
                target = ResolvePath(pathBox.Text.Trim());
            try
            {
                Process.Start(new ProcessStartInfo(Path.Combine(target, "Gr8Launcher.exe"))
                {
                    WorkingDirectory = target
                });
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "Could not start Gr8 Launcher:\n\n" + error.Message,
                    "Gr8 Launcher Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void OpenInstallFolder()
        {
            string target = installPath;
            if (target == null)
                target = ResolvePath(pathBox.Text.Trim());
            try
            {
                Process.Start("explorer.exe", "\"" + target + "\"");
            }
            catch
            {
            }
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path)) File.Delete(path);
            }
            catch
            {
            }
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
                glow.AddEllipse(ClientRectangle.Width - 360, -220, 620, 520);
                using (PathGradientBrush brush = new PathGradientBrush(glow))
                {
                    brush.CenterColor = Color.FromArgb(46, 90, 128, 176);
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
                g.DrawImage(logo, new Rectangle(48, 42, 54, 54));
            }

            if (view == 0)
                PaintInstall(g);
            else if (view == 1)
                PaintProgress(g);
            else
                PaintDone(g);

            using (Pen border = new Pen(Color.FromArgb(70, 90, 120, 158), 1f))
                g.DrawRectangle(border, 0, 0, ClientRectangle.Width - 1, ClientRectangle.Height - 1);
        }

        private void PaintInstall(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(470, 88, 302, 398), 6f, Color.FromArgb(190, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(470, 88, 302, 398), 6f, Color.FromArgb(90, 72, 98, 136));
        }

        private void PaintProgress(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(146, 100, 528, 300), 6f, Color.FromArgb(120, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(146, 100, 528, 300), 6f, Color.FromArgb(70, 72, 98, 136));

            RectangleF track = new RectangleF(190, 242, 440, 8);
            Theme.FillRound(g, track, 4f, Color.FromArgb(90, 0, 0, 0));
            int percent = 0;
            if (percentLabel != null)
            {
                string value = percentLabel.Text.Replace("%", "");
                int.TryParse(value, out percent);
            }
            float fill = track.Width * Math.Max(0, Math.Min(100, percent)) / 100f;
            if (fill > 0)
                Theme.FillRound(g, new RectangleF(track.X, track.Y, Math.Max(8f, fill), track.Height),
                    4f, Theme.Gold);
        }

        private void PaintDone(Graphics g)
        {
            Theme.FillRound(g, new RectangleF(146, 100, 528, 330), 6f, Color.FromArgb(150, 24, 42, 70));
            Theme.StrokeRound(g, new RectangleF(146, 100, 528, 330), 6f, Color.FromArgb(80, 72, 98, 136));

            using (SolidBrush brush = new SolidBrush(Color.FromArgb(42, 129, 199, 132)))
                g.FillEllipse(brush, 386, 136, 48, 48);
            using (Pen pen = new Pen(Theme.Good, 3f))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                g.DrawLines(pen, new PointF[] {
                    new PointF(399, 160), new PointF(408, 169), new PointF(423, 149)
                });
            }
            TextRenderer.DrawText(g, "You're all set.", pixelMedium,
                new Rectangle(146, 194, 528, 40), Theme.Gold,
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
                statusLabel.Text = "Unpacking Gr8 Launcher...";
                percentLabel.Text = "62%";
                fileLabel.Text = "Gr8Launcher.exe";
            }
            else if (state == "done")
            {
                installPath = path;
                ShowDoneView(false);
            }
            else
            {
                ShowInstallView();
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

        private void InitializeComponent()
        {
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
                SetupForm preview = new SetupForm();
                preview.PreviewState(args[1], args[2]);
                return;
            }
            Application.Run(new SetupForm());
        }
    }
}
