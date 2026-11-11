# Windows builds and GitHub releases

GitHub Actions builds Gr8 Launcher with Flet and PyInstaller, then wraps the
application folder in an Inno Setup installer. The executable uses an **onedir**
bundle, and its company metadata and the installer's publisher are **Gr8 Projects**.
Users can choose an installer or a portable ZIP; neither requires Python.
Minecraft still requires a suitable Java runtime.

## What runs automatically

| Trigger | Result |
| --- | --- |
| Push to `main` with a new version in `VERSION` | Build packages, refresh the README screenshot, automatically create the version tag, and publish downloads and an illustrated release note. |
| Push to `main` without changing a released version | Build preview downloads and refresh the README screenshot; keep the published release as it is. |
| Push to `master`, or open/update a pull request | Build a Windows x64 ZIP, installer, and screenshot; upload them to the workflow run. |
| Run the workflow manually | Build downloads and a screenshot using the supplied preview version. |
| Use a prerelease version such as `2.2.0-beta.1` in `VERSION` | Automatically publish a GitHub prerelease on the next push to `main`. |
| Push a tag such as `v2.2.0` manually | Build and publish that tagged version; manual tags are also supported. |

The workflow is [release.yml](../.github/workflows/release.yml). Its release and preview jobs
use GitHub's built-in token with `contents: write`; you do not need to create a
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
version and commit, and preserves its existing notes while refreshing the generated
launcher preview section. The workflow checks that an
existing tag points to the built commit before updating its release. Publication
runs are serialized so pushes cannot race to publish the same version.

Before uploading, [check_release_tag.py](../tools/check_release_tag.py) queries
the exact remote Git tag. A missing tag is allowed and will be created at the
built commit. Existing lightweight and annotated tags must resolve to that commit;
a real mismatch reports both commit SHAs. Connection and authentication failures
stop publication with their own errors.

If an earlier run incorrectly reported a conflict for a tag that does not exist,
keep the intended version in `VERSION` and commit/push the workflow fix. The new
run uses the corrected check; rerunning the old commit uses its old workflow.

To try the pipeline before publishing, open **Actions → Build and release Windows
packages → Run workflow**. Select a branch and enter a version such as
`0.0.0-dev`. Download `windows-x64` from the completed run's **Artifacts** section
and try the installer and extracted portable app. Manual branch builds upload
downloads to Actions without creating a release.

## Agent-authored release notes

Coding agents record completed user-facing changes in small Markdown fragments
under `docs/changes/unreleased/`, following
[the notes guide](changes/README.md) and the repository's `AGENTS.md` rules.
No OpenAI or other model API key is required in CI: the agents write notes while
working, and the workflow combines their text when building a release.

For a publishing build, [release_notes.py](../tools/release_notes.py) snapshots the
fragments at the exact source commit. After packages are published, the workflow
adds a managed **What's new** section to the GitHub release description. Existing
GitHub-generated notes, handwritten text, and the separate screenshot section are
preserved. The launcher reads this description through the Releases API.

After notes and screenshot publication succeed, the workflow creates an archive
at `docs/changes/releases/<version>.md` on the default branch and removes only
the consumed fragments whose contents still match the build snapshot. New or
edited notes stay pending. The archive uses a normal fast-forward commit and
never overwrites newer branch changes. Its built-in workflow token and
`[skip ci]` commit message prevent an extra release/build loop.

Preview builds, failed builds/publication, and merely editing `VERSION` do not
clear notes. Reruns reuse the same snapshot and archive without duplication.
If there are no fragments, the release keeps GitHub's generated notes. Stable
releases and prereleases both consume their own notes after successful publication.

Branch protection must permit the workflow's archive commit; if it rejects the
write, pending fragments remain and the archive step fails rather than losing
notes. Resolve that failure before the next release or archive through the
repository's normal review process.

## Automatic screenshots

Every build runs [capture_screenshot.py](../tools/capture_screenshot.py) after
packaging. It opens the real Flet launcher in headless Edge or Chrome at 1100×650,
using a temporary offline `Player` account and sample Survival/Fabric profiles.
It uses bundled artwork and skins, disables Discord presence and ambient motion,
and waits for the home screen to render before saving the PNG. Local launcher
accounts, settings, caches, and Minecraft installations are kept in a separate
directory from this fixture. Capture failures fail the build, so a blank preview
cannot replace the published image. The screenshot is included in the Actions
build artifact; `SHA256SUMS.txt` covers the downloadable ZIP and installer.

After a successful push build on the repository's default branch (`main`),
[publish_screenshot.py](../tools/publish_screenshot.py) updates the generated
`launcher-preview` branch with the PNG and a `source.json` identifying the built
commit and version. The README loads its image from that branch. The workflow
creates the branch automatically on its first successful run; no personal token
or manual branch setup is needed. The branch contains only preview files, and
its updates do not trigger another build. Older reruns are skipped when `main`
has newer commits. GitHub's image cache may take a few minutes to show an update.

Published releases archive the PNG on the generated `release-previews` branch
and embed its immutable raw commit URL in the release notes. The screenshot
does not appear in release Assets; those contain the ZIP, installer, and checksums.
Older screenshot commits remain in the branch history, so later releases cannot
change an earlier release's preview. Republishing with this workflow removes an
old PNG attachment after the notes have been updated to the archived image.
Reruns replace only the section between
`<!-- gr8-launcher-preview:start -->` and `<!-- gr8-launcher-preview:end -->`;
generated changelogs and handwritten notes outside those markers are preserved.
Pull requests and manual preview runs capture images but do not update the README
or publish releases.

To capture a preview locally after installing `requirements-build.txt`:

```powershell
python tools/capture_screenshot.py --output docs/screenshots/gr8-launcher-home.png
```

Edge or Chrome must be installed. Pass `--browser "C:\path\to\msedge.exe"` if
auto-detection cannot find it. `docs/screenshots/gr8-launcher-home.png` remains a
bundled preview for offline viewing; the online README uses the generated branch.

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

The builder also compiles the standalone C# `updater.exe` with the system Windows
.NET Framework compiler, copies it beside `Gr8Launcher.exe`, and generates
`bundle-manifest.json` with the managed payload's file sizes and SHA-256 hashes.
No additional Python runtime or .NET SDK download is needed for the helper.
See [updater.md](updater.md) for the update flow and recovery behavior. The first
updater-capable release must be installed manually by users of older launchers.

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

On upgrades with a previous bundle inventory, setup preserves that inventory
before copying files. The installed helper removes obsolete managed files whose
hashes still match the old inventory. User-added and modified files are preserved.

The installer creates a Start menu shortcut in the **Gr8 Projects** group,
offers a desktop shortcut, and registers an uninstaller. Launcher settings and
Minecraft instances stay in their existing Minecraft data folders when the
application is uninstalled.

Update [requirements-build.txt](../requirements-build.txt) when upgrading the
launcher or packaging dependencies, then try a manual build before updating
`VERSION` and pushing a release. The workflow pins GitHub Actions and verifies the compiler download's
SHA-256 hash. When upgrading either, update the corresponding pinned commit or
compiler version and hash together.
