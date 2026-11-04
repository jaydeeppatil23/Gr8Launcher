# Windows builds and GitHub releases

GitHub Actions builds Gr8 Launcher with Flet and PyInstaller, then wraps the
application folder in an Inno Setup installer. The executable uses an **onedir**
bundle, and its company metadata and the installer's publisher are **Gr8 Projects**.
Users can choose an installer or a portable ZIP; neither requires Python.
Minecraft still requires a suitable Java runtime.

## What runs automatically

| Trigger | Result |
| --- | --- |
| Push to `main` or `master`, or open/update a pull request | Build a Windows x64 ZIP and installer; upload them to the workflow run. |
| Run the workflow manually | Build downloads using the supplied preview version. |
| Push a tag such as `v2.0.0` | Build downloads, create a GitHub release with generated notes, and attach the ZIP, installer, and checksums. |
| Push a tag such as `v2.0.0-beta.1` | Build and publish a GitHub prerelease. |

The workflow is [release.yml](../.github/workflows/release.yml). Its release job
uses GitHub's built-in token with `contents: write`; you do not need to create a
personal access token or add a repository secret.

## Publish a release

Commit and push the application source, `.assets` files, Java companion JAR, and
build files first. Actions builds the committed source at the tag, so local edits
and untracked files must be committed before they can appear in a release.

From the commit you want to release:

```powershell
git tag -a v2.0.0 -m "Gr8 Launcher 2.0.0"
git push origin v2.0.0
```

Open the repository's **Actions** tab to follow the build. When it succeeds,
the release appears under **Releases** with:

- `Gr8Launcher-2.0.0-windows-x64-setup.exe`
- `Gr8Launcher-2.0.0-windows-x64.zip`
- `SHA256SUMS.txt`

Use a new version tag for each release. Versions use `major.minor.patch` with
an optional prerelease suffix, for example `v2.0.0-rc.1`. Rerunning a release
workflow replaces the attachments for that tag and preserves existing release
notes.

To try the pipeline before publishing, open **Actions → Build and release Windows
packages → Run workflow**. Select a branch and enter a version such as
`0.0.0-dev`. Download `windows-x64` from the completed run's **Artifacts** section
and try the installer and extracted portable app. Manual branch builds upload
downloads to Actions without creating a release.

## Build locally on Windows

Use Windows x64 Python 3.14 and install [Inno Setup](https://jrsoftware.org/isdl.php).
The workflow uses Inno Setup 6.7.4; the installer script also supports Inno Setup 7.

```powershell
py -3.14 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe tools\build_windows.py --version 2.0.0
```

If the compiler is installed in a custom location, add
`--iscc "C:\path\to\ISCC.exe"` or set the `ISCC_PATH` environment variable.
To build just the portable ZIP, use `--portable-only`; Inno Setup is then optional.
Outputs are written to `dist/`, and temporary build files are kept in `build/`.

The package includes `.assets`, the tracked `tools/gr8-skin-bridge/gr8-skin-bridge.jar`,
the Flet desktop client, the Windows credential backend, and the launcher license.
An isolated build environment keeps unrelated packages out of the downloads.

## Installer behavior and maintenance

New installations default to `%ProgramFiles%\Gr8Launcher`, usually
`C:\Program Files\Gr8Launcher`. The installer selects installation for all users
by default, which requests administrator approval. Users can also choose a
current-user installation or a custom destination folder.

Future installers remember the previous installation's scope, directory, and
shortcut choices. For example, an installation in `D:\Games\Gr8Launcher` is
updated in that same folder. The destination selection page is skipped on updates;
the ready-to-install page still shows the destination. Earlier current-user
installations also keep their installation scope and folder when updated.

Keep the fixed `AppId` in [windows.iss](../packaging/windows.iss) unchanged across
versions: Inno Setup uses it to locate the registered installation. This detection
applies to versions installed through the installer; portable ZIP copies have no
installer registration.

The installer creates a Start menu shortcut in the **Gr8 Projects** group,
offers a desktop shortcut, and registers an uninstaller. Launcher settings and
Minecraft instances stay in their existing Minecraft data folders when the
application is uninstalled.

Update [requirements-build.txt](../requirements-build.txt) when upgrading the
launcher or packaging dependencies, then try a manual build before tagging a
release. The workflow pins GitHub Actions and verifies the compiler download's
SHA-256 hash. When upgrading either, update the corresponding pinned commit or
compiler version and hash together.
