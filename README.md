# Gr8 Launcher

A sleek, lightweight, and modern Minecraft launcher built with Python and [Flet](https://flet.dev/).

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
   pip install flet minecraft-launcher-lib
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

## ⚠️ Disclaimer

Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.
