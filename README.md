# Gr8 Launcher

A sleek, lightweight, and modern Minecraft launcher built with Python and [Flet](https://flet.dev/).

[![Download Latest Release](https://img.shields.io/github/v/release/jaydeeppatil23/Gr8Launcher?label=Latest%20Release&style=for-the-badge&logo=github&color=2ea44f)](https://github.com/jaydeeppatil23/Gr8Launcher/releases/latest)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flet](https://img.shields.io/badge/UI-Flet-FF6F00?style=for-the-badge)](https://flet.dev)

---

## ✨ Features

- **Multi-Loader Support**: Install and launch official releases, snapshots, and popular mod loaders:
  - Fabric
  - Quilt
  - Forge
  - NeoForge
- **Custom Skin & Cape Integration**:
  - Full **Ely.by** account authentication and skin syncing.
  - Offline & local custom skin management (PNG upload, Steve/Alex models).
  - Built-in Java companion agent (`gr8-skin-bridge`) ensuring custom skins render seamlessly in multiplayer and offline sessions.
- **Profile & Banner Customization**:
  - Import existing profiles automatically from the official Minecraft launcher (`launcher_profiles.json`).
  - Customizable profile banners and visual cards.
  - Custom usernames and offline UUID generation.
- **Modern Animated UI**:
  - 8 Minecraft-inspired dark palettes (*Redstone*, *Sculk*, *Gold*, *Lush*, *Amethyst*, *Cherry Blossom*, *Earth 1*, and *Earth 2*).
  - Smooth animated navigation pill and profile preview transitions.
  - Curated Minecraft-themed splash quotes on the home screen.
- **Configurable Runtime & Performance**:
  - Memory allocation slider with auto-detected system RAM limits.
  - Custom Java executable / runtime selector.
  - Custom window resolution and fullscreen toggles.
  - Clean background launch (`javaw` on Windows) with real-time download and installation speed tracking.
- **Offline Resilience**:
  - Automatic network state detection.
  - Seamless offline play for all locally installed game versions and skins.

---

## 📋 Prerequisites

- **Python 3.10+**
- **Java Runtime Environment (JRE)**: Appropriate Java version for your target Minecraft release (Java 8, 17, or 21+).

---

## 🚀 Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/jaydeeppatil23/Gr8Launcher.git
   cd Gr8Launcher
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the launcher**:
   ```bash
   python main.py
   ```

---

## 📂 Project Structure

```text
├── main.py               # Application entrypoint & page orchestration
├── launcher.py           # Game installation, verification, and launch logic
├── storage.py            # Persistent settings and profile manager (.minecraft/gr8launcher)
├── colors.py             # Palette and theme engine
├── banners.py            # Profile banners & visual customization
├── ely_auth.py           # Ely.by authentication integration
├── skins.py              # Skin rendering, caching, and companion injection
├── state.py              # Shared application state
├── ui_components.py      # Reusable UI widgets and dialogs
├── ui_installations.py   # Profile and version manager view
├── ui_options.py         # Settings and launcher options view
├── ui_skins.py           # Skin management interface
├── .assets/              # Fonts, default skins, thoughts quotes, and icons
└── tools/                # Java skin-bridge agent & compilation tools
```

---

## 📦 Releases & Version History

For release downloads, changelogs, and updates, check out the **[GitHub Releases](https://github.com/jaydeeppatil23/Gr8Launcher/releases)** page.

---

## 📄 License

This project is licensed under the [GNU General Public License v3.0](LICENSE).

---

## ⚠️ Disclaimer

Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.
