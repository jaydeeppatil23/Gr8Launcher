# Repository Guidelines

## Project Structure & Module Organization

`main.py` runs the Python/Flet UI. `launcher.py` and `launcher_services.py` handle Minecraft installation and launch; `storage.py` and `credential_store.py` manage persistence. UI components live in `ui_*.py`. Bundled artwork/fonts live in `.assets/`, tests/scratch work in `temp/`, build utilities and the Java skin bridge in `tools/`, installer configuration in `packaging/`, and documentation in `docs/`.

## Build, Test, and Development Commands

Run from the repository root in a virtual environment. Source supports Python 3.10+; Windows packaging uses x64 Python 3.14.

- `py -3 -m venv .venv`, then `.\.venv\Scripts\Activate.ps1`: set up PowerShell development.
- `python -m pip install -r requirements.txt`: install runtime dependencies.
- `python main.py`: launch; Minecraft needs compatible Java.
- `python -m pip install -r requirements-build.txt`: install pinned build/test dependencies.
- `python tools/build_windows.py --portable-only`: create a ZIP in `dist/`. Omit the flag for an Inno Setup installer; see `docs/releasing.md`.

## Launcher Design Language

Follow `design_theme.txt` and existing components:

- **Palette:** navy `#182842`, warm gold/beige `#f5de9a`, subtle ambient glow `#454641`; transparent/acrylic or `ft.Colors.BLACK_26` surfaces.
- **Typography/assets:** use `ps` (Pixelify Sans) for primary UI, system fonts for dense details. Reuse Minecraft artwork/icons and retain font licenses.
- **Shape:** prefer sharp edges or 5px rounded rectangles. Match existing frosted profile, dialog, and toast surfaces; keep borders and hover fills rare while preserving selection/focus feedback.
- **Dropdowns:** use the shared `AcrylicSelector`. Closed fields have a `ft.Colors.BLACK_26` background, 5px corners, 48px height, and a gold label/chevron. Keep popup menus acrylic and anchored to the field width, with no added hover fill or default outline. Use one control for each filter.
- **Layout:** retain left navigation, the custom title bar, banner/skin preview, and compact profile rows. Check 1100x650 and larger windows; keep content scrollable and window controls usable.
- **Motion/feedback:** reuse `StaggeredReveal`, `ProfileDialogHost`, `ProfileList`, and `ToastHost`. Keep transitions brief (180-320ms) and interruptible. Restore state after navigation/canceled drags; use concise labels and meaningful loading/error feedback.

## Coding Style & Naming Conventions

Use four spaces in new modules; preserve existing indentation, including legacy tabs. Use `snake_case`, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Type-hint helpers. No formatter/linter is configured.

## Testing Guidelines

Use `unittest` (`IsolatedAsyncioTestCase` for async UI), `test_*.py` files, and `test_*` methods. No coverage threshold is configured.

- All tests, UI/browser validation scripts, and scratch output stay local in the ignored `temp/` directory, including maintained regression tests.
- Do not add `.gitignore` exceptions for these files or force-add, stage, commit, or push them unless the user explicitly asks to publish tests.
- GitHub build/release workflows must work from a clean checkout without `temp/` or any local test files. Do not add CI steps that depend on them. Keep the actual build, release, and screenshot utilities in `tools/` versioned.
- Document test commands as local-only; their scripts are not included in a clone.

When the local test files are present, run them from the repository root after installing build dependencies:

```powershell
python -B -m unittest temp.test_release_plan temp.test_release_tag temp.test_publish_screenshot temp.test_ui_toasts
```

## Commit & Pull Request Guidelines

Use focused, descriptive subjects like `Add remote tag validation`. PRs should explain changes, link issues, report validation, and include UI screenshots. New `VERSION` values pushed to `main` trigger publication.

## Security & Configuration Tips

Runtime data lives in Minecraft's `gr8launcher/`. Preserve native credential storage/redaction. Exclude tokens, personal game data, and generated `build/`/`dist/` artifacts.
