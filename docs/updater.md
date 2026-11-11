# In-launcher updater: implementation plan

Status: Windows implementation added. Stable release discovery, launcher UI,
verified downloads, and a compiled C# helper are implemented. Portable upgrade,
rollback, interrupted recovery, and helper rendering have local validation.
Installed upgrades use Inno Setup and require the installed-copy validation
described below. This feature has not been published in a release.

---

## 1. Goal and Scope

When a newer Gr8 Launcher release is published on GitHub, discover it, notify
the user, download and verify it in the background after consent, and coordinate
application replacement and relaunch.

- **Target platforms:** Windows x64 (installed via Inno Setup and portable `.zip`).
- **Update channel:** GitHub Releases (latest stable release initially).
- **Consent model:** Automatic checks; downloads require an Update action unless the user explicitly enables automatic background downloads. Applying either kind of download requires Restart now. Declining an update never blocks normal launcher use.
- **Development isolation:** Source runs (`python main.py`) and development environments must never attempt file replacement, tamper with repository files, or block workflow velocity.

---

## 2. Existing Repository Support & Findings

| File or Component | Verified Behavior & Constraints |
| --- | --- |
| [`VERSION`](../VERSION) | Single source of truth for release version (e.g. `2.3.0`). |
| [`tools/build_windows.py`](../tools/build_windows.py) | Builds Flet/PyInstaller `onedir` package, writes `VERSION` alongside `Gr8Launcher.exe`, and emits portable `.zip`, setup `.exe`, and `SHA256SUMS.txt`. |
| [`packaging/windows.iss`](../packaging/windows.iss) | Fixed AppId `{449A7BDC-D374-4AAB-8D31-D699214AC088}`; the source escapes the opening brace as `{{`. Remembers installation scope, directory, and tasks. Sets `CloseApplications=yes`, `RestartApplications=no`, and `skipifsilent` on post-install launch. All-user setup requires elevation. |
| [`storage.py`](../storage.py) | Resolves the Minecraft data directory and stores settings in its `gr8launcher/settings.json`, normally `%APPDATA%\.minecraft\gr8launcher\settings.json` on Windows. Keys are explicitly validated on load. |
| [`ui_toasts.py`](../ui_toasts.py) | `ToastHost` provides non-blocking action toasts (e.g. "Update Available", "Update Ready"). |
| [`ui_progress.py`](../ui_progress.py) | Centralized progress management for concurrent background tasks. |
| [`main.py`](../main.py) | Owns startup sequence, settings views, and window lifecycle. |
| [`state.py`](../state.py), [`ui_mods.py`](../ui_mods.py) | `launcher_log_state["running"]` covers launch, installation, repair, and game activity. `ModsPage.busy` includes mod and modpack mutations. |
| [`launcher_updates.py`](../launcher_updates.py), [`ui_updates.py`](../ui_updates.py) | Implement release discovery, verified preparation, source/mock guards, the About card, update dialog, progress, and helper handoff. |
| [`tools/updater/Program.cs`](../tools/updater/Program.cs), [`tools/build_updater.py`](../tools/build_updater.py) | Compile and run the standalone `updater.exe` with a themed progress window, target lock, file journal, startup acknowledgement, and recovery. |

The current PyInstaller 6 `onedir` layout uses `_internal/` for dependencies and
bundled resources, in addition to the root executable and `VERSION`. Actual
package contents must be inventoried during the build; updating only root DLLs
or `Gr8Launcher.exe` is insufficient. Flet also starts a desktop client process.

---

## 3. Development Mode and Safety Boundaries

To ensure updater development does not affect everyday launcher development, testing, or Git hygiene:

### 3.1 Packaged vs. Source Detection

Application packaging state is determined via standard Python frozen detection:

```python
IS_PACKAGED: bool = getattr(sys, "frozen", False)
```

- Automatic application replacement additionally requires Windows x64, a desktop session, a valid application root, and a supported installation record. Frozen detection alone does not authorize replacement of arbitrary directories.
- **When running from source (`python main.py`):**
  - Self-updating and file replacement are **strictly disabled**.
  - Settings / About tab displays: `Running from source (v<VERSION> - dev)`.
  - Manual discovery remains available, but downloading executable updates and applying them are disabled. Enforce this in the service as well as the UI.
- **Simulation / Mock Mode for UI testing:**
  - `GR8_UPDATE_MOCK=1` uses simulated release, progress, cancellation, and result records. It must never invoke setup, write to an installation, terminate real processes, or restart the system. Mock cache state is separate from real state.

### 3.2 Decoupled, Pure-Python Core

- `launcher_updates.py` contains all version parsing, GitHub Releases API communication, ETag caching, checksum verification, and installation discovery.
- **No Flet dependency:** Use Python standard libraries, including `urllib.request`, `hashlib`, `json`, `zipfile`, `pathlib`, `subprocess`, and Windows-specific helpers such as `winreg`. Run blocking I/O in workers and deliver progress to the UI loop through callbacks.
- **Testing hygiene:** All unit tests for updater logic reside in the local-only, git-ignored `temp/` folder (e.g. `temp/test_launcher_updates.py`), adhering to repository testing guidelines.

### 3.3 Cache, Staging, and Installation Isolation

Use this operation layout, with a unique identifier for each download/apply attempt:

```text
%LOCALAPPDATA%\Gr8Launcher\updates\
    discovery.json
    installations\<installation_id>\state.json
    operations\<operation_id>\
        package.part / verified package
        checksums.txt
        updater.exe
        PixelifySans.ttf
        handoff.json
        journal.json
        moves.jsonl
        result.json
        ready.json / ack.json
        Recover.cmd
```

Derive `installation_id` from the canonical application path using Windows path
comparison rules. Global release metadata may be shared; pending operations,
locks, and results must remain specific to the target installation.

Downloads and helper files stay in the user cache. Portable staging and backups
are an explicit exception: place them in an operation-specific directory beside
the application folder on the same volume, such as
`<app_parent>/.gr8-update-<operation_id>/`. This enables same-volume file moves
without putting temporary files inside the payload being replaced.

Check write access and space for both cache and staging before shutdown. If the
parent is not writable, offer manual extraction; do not silently elevate a
portable update. Source and mock runs must never create staging beside a checkout
or modify the repository, `.assets/`, `build/`, or `dist/`.

---

## 4. Release Discovery & Version Handling

### 4.1 Endpoint and Rate-Limit Resiliency

Query GitHub's public releases endpoint:

```text
GET https://api.github.com/repos/jaydeeppatil23/Gr8Launcher/releases/latest
```

- **Headers:** Send a distinct `User-Agent: Gr8Launcher-Updater/<version>` and `Accept: application/vnd.github+json`.
- **ETag caching:** Store the ETag and complete validated release record together, and send `If-None-Match`. On `304 Not Modified`, reuse that record. If it is missing or invalid, repeat once without the ETag. Unauthenticated conditional requests do not guarantee zero rate-limit usage; no GitHub token is embedded in the launcher.
- **Frequency:**
  - Evaluate an automatic check asynchronously 5 seconds after UI startup. Fetch only if the preference is enabled, the last successful check is at least 6 hours old, and no backoff is active.
  - Periodic check interval: every 6 hours if the launcher remains open.
  - Manual check button in Settings > About bypasses the time throttle while respecting backoff headers if rate-limited.

Use bounded connect/read timeouts. Honor `Retry-After` and rate-limit reset
headers, with bounded backoff for transient failures. Do not run a tight retry
loop. Record successful check time only after a validated response or usable
304. Automatic offline failures stay quiet; manual checks show an error rather
than "Up to date." Stale cached information must be labeled as such.

### 4.2 Version Parsing and Ordering

- Read packaged `VERSION` beside `sys.executable`, not from the working directory or `_internal/`. Source display reads the repository `VERSION`. Missing or malformed current versions disable automatic application replacement.
- Normalize an optional leading `v` and validate the same version format as the build tooling. The existing build parser validates syntax; it does not implement ordering.
- Compare major, minor, and patch numerically. At equal numeric versions, a stable release ranks above every prerelease. Do not compare an empty prerelease tuple directly with a populated one.
- Compare prerelease identifiers in order: numeric identifiers numerically, numeric below nonnumeric, nonnumeric lexically, and a shorter matching sequence below a longer one. For example, `rc.10 > rc.2`.
- Require `remote_version > current_version`; never downgrade automatically. Stable discovery can upgrade a matching prerelease to its final release, but must not downgrade a newer development build.

### 4.3 Asset Identification

For a target release `X.Y.Z`, match exact asset names:

- **Installer:** `Gr8Launcher-X.Y.Z-windows-x64-setup.exe`
- **Portable:** `Gr8Launcher-X.Y.Z-windows-x64.zip`
- **Checksums:** `SHA256SUMS.txt`

Never match arbitrary source archives or unrelated release attachments.

Reject drafts, prereleases, invalid tags, and incomplete releases. Require the
appropriate package and exactly one checksum asset before presenting an
installable update. Pin the release ID, tag, package asset ID, checksum asset ID,
URLs, and sizes in a typed release record. A Download action uses that selected
release, not another lookup of "latest."

---

## 5. Download and Integrity Verification

1. **Streaming Download:**
   - Stream to the operation's `package.part`, never a shared `<filename>.part`.
   - Update `ui_progress.py` with bytes received, total size, and download rate.
   - Cancellation is cooperative and bounded by the network read timeout. Close streams before deleting the operation's partial file. Apply a total deadline, size limits, and disk-space checks.
2. **Checksum Verification:**
   - Download `SHA256SUMS.txt` from the pinned release and checksum asset.
   - Compute SHA-256 digest of the downloaded file.
   - Require exactly one valid SHA-256 entry for the exact selected package filename; reject missing, malformed, or duplicate entries.
   - Require the expected downloaded length and hash, then rename the partial file to the verified package name in the same operation directory.
3. **Failure Handling:**
   - If checksum mismatch occurs, delete corrupted `.part` file, log the error, and notify the user with a Retry option. Never execute unverified binaries.

Accept HTTPS URLs from the fixed repository's release infrastructure and validate
redirects against supported GitHub asset hosts. Do not execute commands from
release metadata. Workflow reruns can replace assets: on identity changes or a
checksum mismatch, discard the attempt and rediscover rather than mixing releases.
The helper rechecks the expected hash immediately before application and prevents
package modification between verification and execution where possible.

Initial trust model: trust this repository's GitHub release publication and TLS.
Checksums detect corruption but are not independent authenticity proof if the
repository is compromised. Signed update metadata with a bundled public key and
Windows code signing are separate improvements, not capabilities already present.

---

## 6. Update Execution Architecture

### 6.1 Helper Mechanism: Compiled C# Executable

`tools/updater/Program.cs` builds into a small x64 Windows GUI executable using
the Windows .NET Framework compiler. It uses the .NET Framework provided by
supported Windows 10/11 systems, without bundling Python, installing an SDK,
or running PowerShell. Build it independently with:

```powershell
python -B tools/build_updater.py
```

The result is `build/updater/updater.exe`. The Windows package builder copies it
beside `Gr8Launcher.exe` and inventories it before generating release archives.
The launcher copies its installed helper into an operation directory outside
the application root before handoff. The helper must match the old bundle's hash.

Pass `--apply <handoff.json>` or `--recover <handoff.json>` using argument lists.
Validate the handoff schema, operation ID, canonical paths, mode/scope,
versions/hash, process identities, and nonce. Use direct .NET file operations;
verify every move/delete remains within the operation or managed target.
Application-control restrictions or a missing runtime produce a readiness
failure; the launcher stays open and offers manual updating.

The helper has its own compact navy-and-gold window, a Pixelify Sans heading,
progress bar, and concise status text. The console stays hidden. Close is disabled
during replacement and enabled when attention is needed. A copied font stays
outside the target so replacing assets does not affect the helper's UI.
`--preview` displays simulated progress without changing installations;
`--preview --screenshot <path>` also saves a preview and exits.

The helper must start unelevated. If the launcher was started elevated, require
reopening it normally before automatic application. Elevate only the setup
process when an all-user installation requires it.

### 6.2 Installation Mode Classification

The launcher detects its own installation mode at runtime:

1. **Installed Copy:** Query Windows Registry for Inno Setup AppId:
   - `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{449A7BDC-D374-4AAB-8D31-D699214AC088}_is1`
   - `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{449A7BDC-D374-4AAB-8D31-D699214AC088}_is1`
   - Check if `InstallLocation` matches the directory of the running `Gr8Launcher.exe`.
   - Use the relevant registry view and canonical case-insensitive paths. Retain HKCU/HKLM scope in the installation record; another registered copy does not classify this executable as installed.
2. **Portable Copy:** No matching registration, with a valid packaged application root and bundle inventory. Treat registry access errors or ambiguous records as unknown, not automatically portable.

### 6.3 Safety Gate, Lock, and Handoff

On Restart now, gate application on all of the following:

- `state.launcher_log_state["running"]` is false, covering installation, repair, launch, and game activity.
- `mods_content.busy` is false, covering mod and modpack mutations.
- No competing updater or other launcher copy is using the same installation.

Acquire an installation lock, recheck these conditions, and prevent new launch,
repair, and mutation operations until handoff or cancellation. Keep a UI gate
for normal operations and an OS-level lock for target mutation. Persist the
pending handoff while the launcher holds its lock; that record gates other
starts while lock ownership passes to the helper. The helper must acquire the
target lock before any mutation. Its readiness acknowledgement confirms handoff
validation; it does not imply the old launcher has exited or the lock has passed.

Persist the validated handoff and start the helper. Require a helper-ready
acknowledgement within a bounded timeout before invoking the existing graceful
close path. Failure releases the gate and leaves the launcher open. A successfully
spawned helper process alone is not readiness.

Identify the Python launcher and Flet desktop processes by PID, creation time,
and executable path. Wait for those exact processes to exit and check managed
files are unlocked. A timeout aborts application and produces a recoverable
result. Do not kill processes by name, terminate Java, or continue after a failed
`Wait-Process`. `sys.exit()` inside a worker is not a shutdown protocol.

Every launcher startup must check for an active transaction before normal
operations. Only the helper's designated relaunch, carrying the matching
operation identity, may initialize while its apply lock is held.

---

## 7. Execution: Installed Copies

Use Inno Setup for registered installations; never overwrite Program Files from
the normal launcher process. Apply the safety gate and helper handshake first.
After shutdown, the unelevated helper invokes the verified installer with visible
silent-mode progress and waits for its result.

Required switch policy:

| Switch | Purpose |
| --- | --- |
| `/SILENT` | Show installation progress without the ordinary wizard sequence. Errors and UAC can still appear. |
| `/NORESTART` | Prevent setup from restarting Windows. |
| `/NORESTARTAPPLICATIONS` | Keep relaunch owned by the helper. |
| `/NOCLOSEAPPLICATIONS` | Use the coordinated shutdown; do not automatically close another application or launcher copy. |
| `/CURRENTUSER` or `/ALLUSERS` | Preserve the matching registry entry's scope. All-user setup can request UAC. |
| `/DIR="<canonical_app_dir>"` | Preserve this installation's verified destination. |
| `/RESTARTEXITCODE=3010` | Distinguish a setup requiring a Windows restart from completed application. |

These switches require packaged validation with the current installer. Use
`ProcessStartInfo` with correctly quoted setup arguments and the validated target
directory. Keep `skipifsilent` on the installer's post-install launch entry.

On exit code 0, verify the target `VERSION`, then start the target executable from
the original unelevated helper with the operation identity. Require the startup
acknowledgement described in section 8.3 before reporting success. Standard
The relaunched child inherits the helper's normal-user token. Only the all-user
setup process uses Windows elevation.

UAC cancellation, setup cancellation, and nonzero exit codes produce explicit
results. Reopen the previous installation if it remains valid and runnable; if
setup partially modified it, retain diagnostics and offer repair through setup
or manual installation. Exit code 3010 produces a restart-required result and
must not be treated as a completed, healthy update.

The installer preserves the previous inventory in its protected temporary folder
before copying the new bundle. After copying, it invokes the newly installed
helper's `--cleanup-installed` mode to remove obsolete files only when their old
hashes match. Unknown or modified files stay in place. Cleanup errors fail setup
with a repair message. This mode does not launch the normal updater window.

The installer is not a transactional rollback system. Do not promise
portable-style restoration for installed updates. Clean temporary packages only
after a confirmed result, and retain files needed for retry or repair.

---

## 8. Execution: Portable Copies

Portable updates replace the complete managed application bundle and preserve
unrelated files, legacy `.data/`, screenshots, skins, configs, and game data.
Never remove the entire application folder or merge an incomplete runtime into it.

### 8.1 Bundle Inventory and Archive Validation

Packaging generates `bundle-manifest.json`. It contains a schema version,
application version, and relative path, size, and SHA-256 for every managed payload
file, excluding the manifest itself. Include `Gr8Launcher.exe`, `VERSION`,
`LICENSE`, `README.md`, all `_internal/` contents, and any other actual bundled
files. Inventory the final build output rather than assuming where resources live.
This inventory is for file ownership and integrity, not a hosted update service.

Require valid old and new inventories. The first updater-capable release ships
its inventory and helper; older launchers need manual installation. A missing or
unsupported old inventory disables portable application rather than inventing
ownership from broad filename patterns. Keep the manifest schema backward
compatible or reject releases the installed updater cannot apply.

Before extracting the verified ZIP, validate every entry:

- Exactly one expected `Gr8Launcher/` root; no absolute, drive-qualified, UNC, parent-traversal, alternate-stream, or reserved Windows paths.
- No links, reparse-point traversal, case-insensitive duplicate paths, or file/directory collisions. Resolve destinations inside fresh staging and reject existing redirected directories.
- Bounded file count, expanded size, and compression ratio, with space for staging and backup on the target volume.
- Every extracted file matches the new inventory, with no unlisted payload except the manifest. Validate the staged `VERSION` against the selected release; executable existence alone is not enough.

Verify old managed files against the old inventory before replacement. If users
modified a managed file, or a new payload path collides with an unrelated existing
file, stop and offer manual resolution. Unknown files are preserved. Remove only
old inventory entries absent from the new inventory, not unknown files in their
parent directories. Do not move whole `_internal/`, `.assets/`, or `tools/` folders
unless their full ownership has been established.

### 8.2 Replacement Transaction

1. Validate and extract into the same-volume operation staging directory while
   the launcher remains open. Record the old/new inventories, expected hashes,
   versions, and exact replacement/removal plan in the operation journal.
2. Create a recovery shortcut in the cache operation directory before modifying
   application files. It invokes the cached helper in recovery mode using the
   validated handoff. Expose the shortcut's location in the update dialog and
   failure instructions. Keep the helper independent of target application files.
3. Complete the section 6.3 gate, lock, readiness handshake, and graceful shutdown.
   Confirm the specific old processes are gone and target files are unlocked.
4. Back up old managed files and install staged files using literal, validated
   paths. Flush the complete move plan before mutation and append durable
   completion records to `moves.jsonl`, avoiding full inventory rewrites per file.
   Recovery must handle an interrupted move by checking both locations; operations
   must be idempotent. Preserve the old manifest and install the new one last.
5. Relaunch the updated executable with the operation ID and nonce and await its
   startup acknowledgement. Keep the backup, journal, and recovery entry until
   the result is durably committed.
6. On acknowledgement, record success before cleanup. Remove only this operation's
   backup/staging after verifying their paths; leftover cleanup can happen later.

Per-file replacement is not globally atomic. A journal makes partial changes
recoverable; it does not make a half-written bundle runnable. Do not assume
application startup code can perform recovery if Python dependencies or the
executable are missing after power loss. The standalone recovery entry must work
without the launcher. When the launcher is runnable, its startup reconciliation
must also detect and resolve an abandoned transaction before normal operations.

### 8.3 Startup Acknowledgement and Rollback

The relaunched application acknowledges only after the expected version is loaded,
essential storage is readable, and the Flet client has connected and mounted the
main UI. Include operation ID, nonce, version, and the new process identities.
Network availability and successful Minecraft launch are not startup requirements.

Use a bounded startup timeout, initially 60 seconds and adjustable after packaged
validation. Process survival for three seconds is not sufficient. If the user
closes the new launcher before acknowledgement, treat startup as unconfirmed.

On a failed launch, missing acknowledgement, or replacement failure:

1. Stop only the unconfirmed processes created by this operation, with graceful
   shutdown first. Confirm files are unlocked; otherwise leave recovery pending.
2. Remove newly introduced managed files and restore every backed-up old managed
   file and old manifest using the journal. Preserve unrelated files throughout.
3. Confirm the restored inventory, record rollback, and relaunch the previous
   version with an actionable notification or recovery result.
4. If restoration fails, retain the backup, journal, helper, and recovery shortcut.
   Show their location and recovery instructions. Never delete the only old copy.

Keep startup data changes backward compatible during the acknowledgement window.
Binary rollback cannot reverse destructive settings or profile migrations; a
future incompatible migration needs a separate recovery design.

---

## 9. User Experience and Interface Flow

```text
[Background Check]
        |
        v
[Update Available] -- View --> [Update Dialog]
                                  |
                                Update
                                  |
                                  v
                       [Download and Verification]
                                  |
                                  v
                       [Ready: Restart now / Later]
                                  |
                                  v
                     [Operation Gate and Target Lock]
                       | busy             | idle
                       v                  v
                  [Wait / Later]    [Helper Ready]
                                          |
                                          v
                              [Shutdown and Apply]
                                          |
                                          v
                            [Relaunch and Acknowledge]
                              | success       | failure
                              v               v
                           [Cleanup]    [Rollback / Repair]
```

1. **Non-Intrusive Notifications:** Use the existing toast's single action button for View update or Restart now. Put Later in the dialog/banner; do not assume the shared toast supports two actions. Expired toasts leave update status accessible in Settings > About.
2. **Settings > About Panel Integration:**
   - Displays current version dynamically loaded from `VERSION`.
   - "Check for Updates" button with loading spinner and status feedback ("Checking...", "Up to date", "Update available: v2.4.0").
   - "What's New" release notes viewer using supported Markdown or text. Do not execute remote HTML; constrain link actions to opening validated web URLs.
3. **Safety Gate:** Explain the blocking operation: "Close Minecraft before updating" or "Wait for the installation or mod operation to finish." Recheck the gate when Restart now is clicked, not just when the dialog opens.
4. **Reminders:** Later snoozes for a suggested 24 hours; manual checks still show the release. Do not repeat the same automatic toast within a session or open a modal over another active dialog.
5. **Appearance:** Reuse the existing toast, dialog, progress, and button components. Follow `design_theme.txt` and check the layout at 1100x650 when UI implementation is added.

---

## 10. Persistence & Settings Storage

Update preferences and operational state are stored cleanly in existing storage infrastructure:

- **Settings (`settings.json`):**
  - `check_updates_automatically` (bool, default `True`)
  - `download_updates_automatically` (bool, default `False`)
- **Shared discovery cache:** Last successful check time, ETag, full validated release/assets record, and network backoff deadline.
- **Per-installation state:** Snoozed release/reminder deadline, prepared handoff path, and pending apply handoff path. The handoff contains the canonical root, mode, and scope. Prepared downloads are restored on startup only if they still match the current version and have no terminal result.
- **Per-operation state:** Schema version, operation ID/nonce, selected release and asset identities, expected package size/hash, old/new versions, helper/process identities, inventories, validated staging/backup paths, transaction phase, completed file actions, and result.

Operational state is kept separate from `settings.json` so that update metadata caching never pollutes Minecraft profile settings or roaming profiles.

Add both preferences to `storage.load_settings()` validation and defaults. Write
cache, handoff, journal, acknowledgement, and result records atomically; flush
durable transaction changes before proceeding. Validate all records on read.
Malformed or unknown schemas disable application and preserve recovery files.

Suggested states:

```text
idle -> checking -> up_to_date | available | check_failed
available -> downloading -> verifying -> ready
ready -> handing_off -> applying -> awaiting_startup -> succeeded
applying | awaiting_startup -> rolling_back -> rolled_back | recovery_required
```

Installed failures use cancelled, repair-required, or restart-required results
instead of claiming portable rollback. Download cancellation returns to available.
Cancellation before helper readiness releases the gate; once replacement starts,
finish the transaction or recover rather than abruptly stopping file moves.

Reconcile pending state with the actual running version and operation result on
startup. Acquire the target lock before recovery and distinguish abandoned helpers
from live ones by process identity. An ETag, cached version, or pending package path
alone must never authorize execution. Never store credentials in handoffs or
backups; preserve existing credential storage and notification redaction.

---

## 11. Implementation Sequence

1. **Phase 1: Core Engine (`launcher_updates.py`)**
   - Implement version comparison logic (`compare_versions`, `parse_version`).
   - Implement GitHub API client with ETag headers and timeout handling.
   - Implement package download streamer with SHA-256 verification and cancellation.
   - Implement typed release, installation, operation, and result records with source/mock guards.
   - Add focused local tests for semantic ordering, malformed/incomplete releases, missing current version, 304 without cache, backoff, cancellation, and checksum rejection.
2. **Phase 2: UI & Settings Integration**
   - Replace hardcoded version in [`main.py`](../main.py#L5300) with dynamic version loader.
   - Add update card, manual "Check for Updates" button, and status indicator in Settings > About.
   - Connect non-blocking startup check and `ToastHost` notifications.
   - Add safe developer guard (`IS_PACKAGED`) and mock mode toggle.
3. **Phase 3: Helper Script & Packaging**
   - Build and test `updater.exe` handling installed and portable modes.
   - Embed the compiled helper in package distribution.
   - Generate and ship `bundle-manifest.json` from final package contents before archiving and compiling setup. Keep its schema compatible with prior updater-capable versions.
   - Implement target locking, helper-ready and startup acknowledgements, durable journaling, and the standalone recovery entry.
   - Test file ownership, same-volume staging, archive rejection, interrupted moves, newly added/obsolete files, and unrelated-file preservation with disposable directories and injected failures.
   - Explicitly handle obsolete managed files in installed upgrades as well; the current recursive `ignoreversion` installer copy does not remove them automatically. Preserve unknown files and test any added inventory-based cleanup.
4. **Phase 4: End-to-End Packaged Validation**
   - Use `python tools/build_windows.py --portable-only` for portable packages; build without that flag for setup validation in disposable installations. Override the build version for experiments instead of changing release `VERSION`.
   - Validate portable success, acknowledgement timeout, recovery from partial replacement, and standalone recovery when the application cannot start.
   - Validate current-user setup and all-user setup with UAC, including cancellation, setup failure/restart-required results, exact scope/destination preservation, and unelevated relaunch.
   - Validate blocked helper execution, helper-readiness timeout, lingering Flet processes, concurrent launcher copies, cache/target on different drives, paths with spaces/non-ASCII characters, and read-only destinations.
   - Validate busy-operation gates, explicit download/restart consent, source/mock isolation, and data preservation.

Follow proportionate validation: use focused existing or new local tests for
meaningful behavior, then the packaged checks needed for process replacement and
elevation. Documentation refinement itself needs content and link review, not a
build or dependency installation. Keep all tests and scratch scripts in ignored
`temp/`; CI/release workflows must work from a clean checkout without them.

Developing or testing the updater does not itself authorize a release `VERSION`
bump or publication. Those remain separate release actions.

Local-only checks, when the ignored scripts are present:

```powershell
python -B -m unittest temp.test_launcher_updates
python -B tools/build_updater.py
python -B -m unittest temp.test_native_updater
```

Native checks use fake launchers and disposable folders under `temp/`; they do
not update a registered installation or replace a user's real launcher. These
scripts are not included in a clean clone or required by release workflows.
