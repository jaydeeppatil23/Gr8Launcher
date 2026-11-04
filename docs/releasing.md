# Windows builds and GitHub releases

GitHub Actions builds Gr8 Launcher with Flet and PyInstaller, then wraps the
application folder in an Inno Setup installer. The executable uses an **onedir**
bundle, and its company metadata and the installer's publisher are **Gr8 Projects**.
Users can choose an installer or a portable ZIP; neither requires Python.
Minecraft still requires a suitable Java runtime.

## What runs automatically

| Trigger | Result |
| --- | --- |
| Push to `main` with a new version in `VERSION` | Build packages, automatically create the version tag, publish the release, and attach downloads. |
| Push to `main` without changing a released version | Build preview downloads; keep the published release as it is. |
| Push to `master`, or open/update a pull request | Build a Windows x64 ZIP and installer; upload them to the workflow run. |
| Run the workflow manually | Build downloads using the supplied preview version. |
| Use a prerelease version such as `2.2.0-beta.1` in `VERSION` | Automatically publish a GitHub prerelease on the next push to `main`. |
| Push a tag such as `v2.2.0` manually | Build and publish that tagged version; manual tags are also supported. |

The workflow is [release.yml](../.github/workflows/release.yml). Its release job
uses GitHub's built-in token with `contents: write`; you do not need to create a
personal access token or add a repository secret.

## Publish a release

Choose the exact version in the root [VERSION](../VERSION) file. For example,
use `2.0.1` for a small fix or `2.2.0` for the larger update you want to publish.
The workflow uses this value for both the installer and the GitHub release.

Commit and push the application source, `.assets` files, Java companion JAR,
build files, and updated `VERSION` together. From the project folder:

```powershell
Set-Content -LiteralPath VERSION -Value "2.0.1" -Encoding utf8
git add .
git commit -m "Release Gr8 Launcher 2.0.1"
git push origin main
```

The workflow automatically creates `v2.0.1` at the exact commit it built and
publishes the release after the build succeeds. There are no manual tag commands.
Local edits and untracked files must be committed to appear in the downloads.

Open the repository's **Actions** tab to follow the build. When it succeeds,
the release appears under **Releases** with:

- `Gr8Launcher-2.0.1-windows-x64-setup.exe`
- `Gr8Launcher-2.0.1-windows-x64.zip`
- `SHA256SUMS.txt`

Choose a new, higher version for each release. Versions use `major.minor.patch`
with an optional prerelease suffix, for example `2.2.0-rc.1`. The initial
`VERSION` value is `2.0.0`, matching the existing release. Keeping that value while
pushing more commits produces a `2.0.0-dev` preview rather than another release.

Rerunning the original release workflow replaces attachments using that same
version and commit, and preserves its existing notes. The workflow checks that an
existing tag points to the built commit before updating its release. Publication
runs are serialized so pushes cannot race to publish the same version.

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
.\.venv-build\Scripts\python.exe tools\build_windows.py
```

The local builder reads `VERSION` by default. Add `--version 2.2.0-beta.1` to
override the version for a test build. Each package also includes a `VERSION`
file containing the version actually built.

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
launcher or packaging dependencies, then try a manual build before updating
`VERSION` and pushing a release. The workflow pins GitHub Actions and verifies the compiler download's
SHA-256 hash. When upgrading either, update the corresponding pinned commit or
compiler version and hash together.
