# Gr8 Launcher

A sleek, lightweight, and modern Minecraft launcher built with Python and [Flet](https://flet.dev/).

[![Download Latest Release](https://img.shields.io/github/v/release/jaydeeppatil23/Gr8Launcher?label=Download%20Latest%20Release&style=for-the-badge&logo=github&color=2ea44f)](https://github.com/jaydeeppatil23/Gr8Launcher/releases/latest)

---

## ✨ Features

- **Multi-Loader Support**: Install and play Vanilla releases, snapshots, and popular mod loaders:
  - Fabric
  - Quilt
  - Forge
  - NeoForge
- **Official Profile Import**: Automatically detects and imports existing installations from the official Minecraft launcher (`launcher_profiles.json`).
- **Offline Profiles**: Custom username setup, offline UUID generation, and personalized profile pictures.
- **Theming Engine**: 8 Minecraft-inspired dark palettes:
  - *Redstone*, *Sculk*, *Gold*, *Lush*, *Amethyst*, *Cherry Blossom*, *Earth 1*, and *Earth 2*.
- **Configurable Runtime**:
  - Memory allocation (RAM slider with auto-detected system limit)
  - Custom Java executable path selection
  - Custom window resolution and fullscreen toggle
  - Post-launch actions (hide, keep open, or close launcher)
  - Real-time logging output
- **Offline Resilience**: Automatic connection monitoring with full offline support for already installed versions.

---

## 📋 Prerequisites

- **Python 3.10+**
- **Java Runtime Environment (JRE)**: Appropriate Java version for your target Minecraft release (e.g., Java 8, 17, or 21).

---

## 🚀 Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/jaydeeppatil23/Gr8_Launcher_dev.git
   cd Gr8_Launcher_dev
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Launch the application**:
   ```bash
   python main.py
   ```

---

## 📂 Project Structure

```text
├── main.py          # UI entrypoint, views, and navigation (Flet)
├── launcher.py      # Minecraft installation, verification, and launch logic
├── storage.py       # Configuration, profiles, and persistence management
├── colors.py        # Theme definitions and color palettes
├── .assets/         # Fonts, icons, and asset files
└── .data/           # Saved settings, installations, and profile options
```

---

## 📝 Changelog

### Version 1.1.0

#### 🚀 Installation & Download Improvements
- **Real-Time Download ETA & Speed**: Live download speed (`files/s`) and accurate remaining time estimation are now displayed directly alongside the launch progress.
- **Multi-Stage Progress Tracking**: Progress bar now accurately tracks and smooths transitions across Libraries, Game Assets, and Java Runtime downloads.
- **Visual Activity Feedback**: Progress indicator automatically transitions to an active pulse during network-heavy file operations and decompression.

#### 🎨 UI & Navigation Overhaul
- **Modern Animated Navigation Bar**: Redesigned tab navigation with a centered bar and a smooth sliding indicator pill between **Home**, **Profiles**, and **Options**.
- **Fluid Profile Selection**: Added smooth sliding row highlights and seamless fade transitions when selecting and previewing game profiles.
- **Startup Entrance Animation**: Introduced a subtle ripple effect that cascades interface elements into place on startup.
- **Refined Terminology**: Standardized terminology throughout the UI from "Installations" to "Profiles" for greater clarity and consistency.
- **Window Controls Polish**: Added responsive accent-themed hover highlights to window minimize, maximize, and close buttons.

#### 🛠️ Game Launch & Windows Fixes
- **Clean Game Startup (`javaw`)**: Minecraft now launches using `javaw` on Windows, eliminating unwanted background terminal/command prompt windows.
- **Game Window Visibility**: Fixed an issue where the initial Minecraft window could fail to display or remain hidden on launch.
- **Smoother Offline Handling**: Immediate offline detection on startup with non-intrusive UI updates when network connectivity changes.

#### 💡 Content & Licensing
- **Inspirational Splash Thoughts**: Added thoughtful, Minecraft-inspired quotes and life lessons to the home screen.
- **Open Source Licensing**: Gr8 Launcher is now officially licensed under the GNU General Public License v3.0 (GPL-3.0).

---

## 📄 License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

---

## ⚠️ Disclaimer

Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.
