# Mods page

The Mods sidebar entry has a content selector for **Mods** and
[**Modpacks**](modpacks.md). Mods opens a manager for one Minecraft profile. The Library's
Manage Mods button opens the profile from that row. The Mods profile selection is
independent of the profile selected for Play.

Preview screenshots use isolated sample data:
[Installed](screenshots/gr8-launcher-mods-installed.png),
[Browse](screenshots/gr8-launcher-mods-browse.png),
[Profile selector](screenshots/gr8-launcher-mods-selector.png),
[Mod details](screenshots/gr8-launcher-mods-details.png),
[Versions](screenshots/gr8-launcher-mods-versions.png), and
[Unavailable profile](screenshots/gr8-launcher-mods-unavailable.png).
The [Undo notification](screenshots/gr8-launcher-mods-undo.png) restores a removed mod.
Details previews also include the [larger window](screenshots/gr8-launcher-mods-details-large.png)
and [formatted content](screenshots/gr8-launcher-mods-details-content.png).

## Layout

The custom title bar and left navigation stay visible. The content has 20 px left
and 24 px right padding and fills the remaining window at 1100 × 650 or larger.

1. **Header:** “Mods” in 32 px Pixelify Sans, with a compact frosted profile
   selector on the right. It shows the profile artwork/name and Minecraft version
   with its loader; build details stay in the selection page.
2. **Profile selection:** the header selector opens a separate Library-style
   page using the shared profile rows, badges, build details, and scrollable
   `ProfileList`. Select chooses the Mods target; Selected and a gold outline
   mark the current one.
   The compact chevron/Back control matches Add Account and keeps the current
   profile, query, filters, and scroll position.
3. **Content:** the header's acrylic selector switches between Mods and Modpacks.
   Pack browsing works without choosing a profile; installing a pack creates its
   own instance with pinned Minecraft and loader versions.
   **Tabs:** Discover and Installed with its mod count. Discover shows a quiet
   attribution label for the selected provider, Modrinth or CurseForge.
4. **Toolbar:** the shared `SimpleInput` at its normal intrinsic height, with no
   Mods-specific height or padding overrides, 5 px corners, and a mode-specific
   search hint; All/Enabled/Disabled, Add JARs, and Refresh for
   Installed, or source and sort selectors for Discover, initially Modrinth and
   Popular. CurseForge also shows a key button for provider setup.
   Closed selectors match the search field's 48 px height, dark background,
   and 5 px corners. The sort label sits inside its field alongside the value
   and chevron.
   Discover searches automatically while typing and also accepts Enter.
   Filter/sort menus retain their acrylic 14 px backdrop blur, with the native
   menu width constrained to match its rows so hover feedback stays inside it.
5. **Feedback:** the [shared bottom progress bar](progress.md) handles searches,
   detail fetches, and downloads across navigation. It animates for unknown
   totals and reports measured bytes/percentage during mod downloads, including
   dependencies. The page shows a compatible
   result count in Discover or enabled/disabled counts in Installed. The header
   provides the profile's version/loader context.
6. **Scrollable rows:** the existing `ProfileList` and frosted 5 px profile surface
   with 48 px artwork, 16 px horizontal padding, and muted supporting text.
   Installed rows show name, description when available, version, size,
   enable switch, and removal. The filename remains available in details and search.
   Clicking the artwork/title area
   opens the full mod page; switches and action buttons remain independent.
   Discover rows show title, a single description line, author, and abbreviated
   downloads. A 44 px accent-filled download icon installs the mod; installed
   projects show a disabled check icon. Tooltips identify both actions. Categories, supported
   loaders, and the provider's project link remain on the details page. Action buttons and
   the artwork/title area use the click cursor.
7. **Footer:** Discover pagination (20 results per page), hidden when unavailable.
   Successful removal shows an Undo action in the shared notification host.

## Working behavior

- Mods supplied by an installed modpack show **Pack managed**. Individual update
  checks skip those files, and version replacement reviews cannot change their
  pins. Install another pack version from Modpacks to get its corresponding mod
  versions. Enable, disable, removal, and adding custom JARs remain available;
  separately added mods can use ordinary update checks.

- Discover supports Modrinth and CurseForge. Each source retains its query,
  sorting, and page when switching; stale responses cannot replace the other
  source's results. Installed combines both providers and local JARs.
  Modrinth project/version IDs retain their existing format. CurseForge IDs are
  namespaced as `cf:project` and `cf:project:file`, preventing collisions in
  installed identities, dependency planning, and update caches.
- CurseForge requires a launcher API key. Open **Mods → Discover → CurseForge →
  key button**, then follow the official CurseForge console link and save the
  key. The native credential vault stores it separately from account sessions;
  if that vault is unavailable, the key lasts only for this launcher session.
  It is never written to profile/settings files. Deployments can supply
  `GR8_CURSEFORGE_API_KEY` instead. API keys are sent only to the CurseForge API,
  with API redirects disabled. The launcher does not use Prism's bundled key.
- CurseForge files without an API download URL require a manual download, as
  in Prism. The review links to each exact official file and asks for the
  downloaded JARs. Selection verifies the full checksum, size, and archive,
  then repeats native dependency planning before enabling Install. Files are
  copied into the shared staging transaction; the user's downloads stay in
  place. Wrong or changed files fail verification. Additional required files
  discovered during planning must also be selected. Restricted CDN URLs are
  never synthesized or bypassed.
- `SimpleInput` and `SimpleButton` live in `ui_controls.py` and are imported by
  the launcher and Mods. Selectors and buttons, profile surfaces, list controls,
  brief `StaggeredReveal` transitions, dialog host, and toast host are reused.
  Normal searching retains matching row objects. Back from a mod page restores
  the list query, filter, and scroll position.
  Disk scans and searches run during the entrance animation; their row updates
  wait for it to finish. Installed/Discover transitions animate a stable list
  body so filtering or refreshed metadata cannot reset the fade. The Mods page
  stays mounted across sidebar navigation. Only visible rows and a small scroll
  buffer are constructed, with spacers preserving the full scrollbar range.
  Returning from a sidebar tab recreates the scroll viewport while retaining
  the row cache and saved position. Events from an old viewport are ignored;
  background identity changes wait until the entrance has finished.
  Row changes are sent together with page feedback.
  Rapid navigation cancels stale loads and scroll restoration, and the Library's
  Manage Mods shortcut prepares only the requested profile.
- Fabric, Quilt, Forge, NeoForge, and legacy mod artwork is read from the JAR's
  metadata without extracting files. Bundled icons work offline and are also
  used on the details page. Recognized provider projects can fill missing
  artwork/descriptions in the background; failures keep local actions available.
  Mods with no usable artwork retain the generic icon.
  Large embedded logos are resized to at most 128 px during the background scan,
  preserving aspect ratio and transparency. Animated logos use their first frame.
  Image decode hints also bound remote icon sizes, so opening or revisiting the
  list does not decode full-size artwork for each 48 px icon.
- Only profiles explicitly marked as instances can manage mods. Selecting a
  non-instance profile does not scan or create the global mods folder. The page
  explains that global mods can load in other profiles and cause incompatible
  versions, conflicts, or crashes, and offers Create instance. That action opens
  the normal profile form with Separate Instance Folder enabled.

- The game folder is resolved using the same helper as launching Minecraft.
  Profiles that use the same instance folder share their mods.
- Installed search is immediate. Discover search waits 350 ms after typing and
  can also be submitted with Enter. Sort and paging preserve the query.
  Stale network results cannot overwrite another profile or tab.
- Provider responses are cached in bounded, thread-safe in-memory caches for
  five minutes (30 seconds for searches), including unrecognized checksum results.
  Concurrent requests for the same data share one transfer, and worker threads
  reuse HTTP connections. Details fetch project/history/members concurrently;
  cached full history can satisfy loader/game filters locally. Install planning
  batches external-JAR identities and fetches independent optional candidates and
  names with at most four workers. Returning to an already checked dependency
  selection in the same dialog reuses its plan immediately. Every plan retains
  its original snapshot; installation still hashes files and enforces native
  requirements before committing. Refresh, Retry, and manual Check updates clear
  cached responses so they obtain fresh data. Request failures are not cached.
- Both providers' files match the profile's exact Minecraft version and its supported
  loader tags. Quilt includes Fabric mods; NeoForge includes Forge mods only for
  Minecraft 1.20.1. Discover, version history, installation, and updates share
  this policy. Modrinth excludes server-only projects from search; CurseForge
  version selection excludes files explicitly tagged only for servers.
  Stable releases are preferred, with a compatible prerelease used if necessary.
- Dependency providers include the selected installed version's runtime libraries,
  inherited classpath, bundled JARs, and native provided aliases. Loader-supplied
  libraries satisfy native requirements without adding duplicate files to `mods/`.
  The confirmation identifies these dependencies as provided by the profile.
  Fabric planning also checks native dependency ranges and conflicts before
  confirmation. Automatic version selection considers enabled dependencies and
  runtime libraries, so an older compatible release can be selected when needed.
  Required native dependencies omitted from a provider's listing are resolved using
  a bounded project lookup/search, with the actual JAR's mod ID and version checked
  before inclusion. Ambiguous matches require an explicit dependency choice;
  existing dependencies are never silently replaced or enabled.
  Profile metadata and runtime provider checksums are captured during planning
  and rechecked before installation. Runtime libraries stay read-only.
  JSON metadata in mod JARs accepts literal line breaks and tabs inside strings,
  matching Fabric's reader. Online checks, local scans, bundled libraries, and
  staged validation share this parser and retain dependency and structure checks.
- Clicking a mod opens Overview, Versions, and Gallery (when available). Overview
  includes full Markdown documentation, authors, license, categories, environment,
  downloads, local file details, and available source/wiki/issues/community links.
  Local JARs are identified on Modrinth by checksum, then on CurseForge when
  configured. CurseForge uses Prism's whitespace-filtered Murmur2 fingerprint
  algorithm to find candidates, followed by a full cryptographic checksum and
  size check. Fingerprints alone never establish identity. Unmatched JARs
  retain their local metadata and explicitly report unavailable online history.
- The details header groups 64 px artwork, title, author, target loader/version,
  a short summary, and a 44 px Install action. Descriptions and changelogs use
  system typography with smaller headings and readable line spacing. The overview
  description fills the available width beside the 236 px metadata panel, with
  a consistent 24 px gap as the window resizes. Metadata and external links share
  a compact frosted panel; both the description and panel scroll independently.
- `mod_markdown.py` converts embedded HTML to native Markdown before rendering.
  It preserves links, images, nested lists, tables, warnings, FAQ contents, and
  code examples. HTML presentation attributes are omitted; embedded media is
  offered as a link. Existing Markdown stays intact, including fenced/indented
  code and inline code. Conversion uses Python's standard library and is cached.
- Versions includes the full project history with search, release filtering,
  a compatibility switch, and pagination of 20 rows. Each version opens its
  changelog, loader/game requirements, file details, and install action.
  Incompatible versions remain readable and cannot be installed into this instance.
  The release dropdown is the only release filter: Stable by default, All releases,
  Beta, or Alpha. Changing it resets version pagination and keeps the selected
  option across navigation. Shared selectors have a dark background and 5 px
  corners by default, including the Versions filter and Settings selectors.
- Selecting another installed provider version shows the exact replacement and
  required dependencies before confirmation. A successful replacement retains the
  previous JAR in launcher trash and preserves the mod's enabled state. A failed
  commit restores the previous JAR and mod index.
  Imported or renamed JARs identified by checksum can also use this replacement
  flow. The managed-mod index changes only when an installation is confirmed;
  an optional checksum identity cache also supports external JAR recognition.
- Check updates uses Modrinth's bulk checksum update endpoint for known files,
  with Minecraft version, supported loaders, and release filters. CurseForge
  mods use their own project file histories; their hashes are excluded from the
  Modrinth bulk request. A provider outage still allows checks and unrelated
  installs from the other source. Project history
  is fetched when the bulk lookup is unavailable, a file has no bulk result, or
  the latest candidate cannot run with the profile's Loader/runtime. It looks
  for newer provider versions matching the profile's
  Minecraft version, loader, and exact Fabric Loader build. Fabric candidates are
  checked against the Loader requirements in their JAR metadata, including bundled
  mods. If a newer release needs a newer Loader, selection falls back to the newest
  matching release that the profile can run. If no compatible newer release exists,
  the installed version stays in place and no update is offered. Automatic installs
  and unpinned dependency downloads use the same Loader fallback; choosing a specific
  version still installs that version only if it passes validation.
  The release dropdown in Versions also controls
  update suggestions: Stable excludes prereleases; All releases includes them;
  Beta and Alpha offer only that release type. Change it and run Check updates
  again for the newest matching publication. Changing the selection clears
  previous check results. Update actions in mod details follow the same choice.
  An older release is never presented as an update to a newer installed
  prerelease. Each available update has an Update
  action that opens the existing version/dependency confirmation and backup flow.
  Disabled mods stay disabled after updating. Unknown JARs and failed online
  checks are reported as incomplete. Checking updates previews Fabric metadata using
  cached CDN byte ranges without installing a mod. Servers that ignore ranges can
  provide a small archive for metadata inspection. A preview transport failure,
  including a server ignoring ranges for a larger JAR, produces a warning and
  keeps provider-matched candidates available. Such checks do not replace a saved
  successful check. The installation review displays the warning; downloaded
  JARs still pass native validation before any installed files change. Invalid
  ranges, checksums, and malformed new metadata remain errors. Checking never
  changes an installed file.
  Mod details also expose an Update action when their
  loaded version history contains a newer matching version.
- Update checks are saved in the instance's `gr8launcher/mod-updates.json`, including
  checks that found no updates. Navigation and launcher restarts restore the
  matching result without another online check, and the last checked release
  filter is restored when reopening an instance. Saved results are scoped to
  Minecraft version, loader/build, installed version ID, runtime providers, and
  release filter and are discarded when the
  installed JAR names, enabled states, or checksums change. A `Saved check` note
  identifies restored results; Refresh clears the current saved check so Check
  updates can request fresh results. Failed online checks do not replace a saved
  successful check.
  When updates exist, Check updates becomes **Update all**. It opens one review
  of the version replacements, download size, and required/optional dependencies.
  All selected mod versions are resolved together, including shared version pins;
  unchanged dependencies are reused and disabled mods stay disabled. Confirmation
  downloads and stages one batch, checks the complete resulting mod set, and
  commits through the existing rollback journal. Canceling downloads nothing;
  a failed download, native requirement, or commit keeps the previous installed
  mods intact.
- Install first resolves a compatible version and its required dependencies.
  The compact confirmation shows the target instance, selected version, download
  count/size, and dependency rows. Required dependencies use a lock icon. Optional
  dependencies start unchecked; selecting one also resolves its required libraries
  and rechecks provider conflicts before enabling Install. Installed and bundled
  dependencies use a lock icon and an Installed label, show their existing
  versions, and are kept without another download. Compatibility checks run
  during planning and installation; the confirmation omits their details.
  Canceling downloads nothing. Existing compatible dependencies are reused; disabled or
  conflicting dependencies need attention in Installed.
  Reused dependencies still have their own requirements checked. An exact file
  already installed from the other provider is reused by checksum while keeping
  its original managed identity and update source. Different files with the same
  native mod ID cannot be installed as duplicates. Provider
  declared conflicts and existing enabled mods' requirements are checked against
  the resulting set, including requirements pinned to a particular version.
  Enabled libraries bundled inside declared Fabric, Quilt, or Jar-in-Jar
  archives can satisfy requirements without another download. Bundled libraries
  are identified by checksum and do not replace their parent mod's index entry.
  A disabled or replaced parent cannot supply a library that will be absent after
  installation. An unrelated mod's already-missing dependency does not block
  other installs; requirements affected by the proposed batch remain enforced.
  Dependency errors use mod/project names when available.
- Dependency versions are never upgraded or downgraded automatically. An exact
  provider version requirement that disagrees with an installed dependency blocks
  the proposal and asks the user to choose another mod version or review it in
  Installed. Before committing downloaded files, native Fabric/Quilt predicates,
  Forge/NeoForge numeric Maven ranges, and native conflicts are checked against
  the resulting enabled mod set, including declared bundled libraries and aliases.
  This also protects existing mods whose requirements are affected by an explicit
  mod update or removal of a bundled library. Optional native requirements are
  checked when their target is present. Unsupported constraints stop the batch
  rather than assuming compatibility; unchanged pre-existing missing requirements
  do not block unrelated installs. Native checks require the staged JARs, so an
  incompatible download may be rejected after transfer, while installed JARs and
  the index remain unchanged. These checks depend on authors' declarations and
  cannot guarantee compatibility when metadata is missing or incorrect.
  Native Forge/NeoForge TOML inspection uses `tomllib`, with the conditional
  `tomli` runtime dependency on Python 3.10.
  Archives with multiple loader manifests use the target loader's native
  metadata, including bundled JARs. Quilt checks support dependency alternatives,
  combined conflicts, and `unless` conditions. Changes to a condition provider
  recheck existing dependents. Unreadable native metadata in an existing JAR is
  reported in the review and does not block unrelated installations; checks for
  that existing file are limited. Newly downloaded malformed metadata still
  blocks installation.
- Fabric installs and Add JARs inspect native `fabric.mod.json` requirements for
  the profile's exact Fabric Loader version, including declared bundled JARs.
  Required dependencies that are reused are also checked. Numeric comparisons,
  prereleases, wildcard ranges, `~`, `^`, intersections, and alternative arrays
  follow Fabric's predicate semantics. A mismatch stops the entire batch before
  any installed files are replaced and explains how to update the profile's
  loader or choose an older mod version. An unknown loader version or unsupported
  requirement needs attention rather than silently passing the check. Downloaded
  JARs are staged for this inspection; update discovery itself only knows the
  Minecraft/loader tags. The launcher does not automatically change the loader.
- Downloads run off the UI thread, verify Modrinth checksums and sizes, and stage
  the entire batch before committing. Failed downloads or writes leave existing
  mods intact. The launcher prevents Play and Repair during mod operations.
  Downloads do not hold folder locks; commits lock only their instance. On
  Windows, separate launcher processes share that lock. If installed JARs change
  after confirmation, installation stops and requests a fresh confirmation.
  Commits publish complete JARs and keep a recovery journal. An interrupted commit
  is recovered before the next scan, mutation, or game launch. Files changed
  externally after interruption are kept and require manual recovery.
- Add JARs imports multiple local archives in the desktop launcher. It rejects
  invalid archives, duplicate filenames, and duplicate known mod IDs. Fabric
  Loader requirements are checked; other local compatibility declarations remain
  the loader's responsibility.
  Duplicate checks include every declared mod in multi-mod Forge, NeoForge, and
  legacy JARs, and also apply to Undo.
- Disable renames `example.jar` to `example.jar.disabled`. Enable reverses it.
  Disabled suffixes are recognized regardless of capitalization. Minecraft
  applies these changes on its next launch.
- Remove requires confirmation and moves the JAR to the selected game folder's
  `gr8launcher/mod-trash/<token>/`. Each removal notification's Undo action
  restores that exact JAR, including its enabled state, even if the selected
  profile changes to Vanilla or a global profile. The original instance folder
  must still exist. Dismissed/expired removals remain in trash for manual recovery.
  Existing files are never replaced by Undo, and actions still respect the
  running-game lock.
- The managed-mod index lives in `gr8launcher/mods.json` alongside the selected
  game folder. Downloads, imports, toggles, and removals are blocked while
  Minecraft is starting or running. Vanilla and missing-instance profiles show
  instructions for selecting or creating a usable modded profile.
- JARs copied directly into the instance's mods folder appear on refresh. Local
  metadata remains available offline. Background checksum lookups identify
  provider-hosted files, including renamed JARs, and store a bounded optional
  `gr8launcher/mod-identities.json` cache. Cached identities survive restart and
  are reused only when the actual checksum matches; replacing a JAR externally
  invalidates its former identity. A corrupt identity cache does not block local
  file actions. Unknown files retain their local metadata, enable/disable, and
  remove actions, with limited online dependency/update checks made explicit.
  Failed optional lookups do not block unrelated installations. Unsupported bulk
  lookup endpoints fall back to individual checksum requests. Files changed
  during a check, after an update check, or after install confirmation require a
  fresh review. A profile version changed after confirmation also stops the install.

Updates require confirmation; removing a mod does not automatically remove its
dependencies. CurseForge support uses the existing requests and native keyring
dependencies. Compatibility checks depend on authors' metadata and cannot
guarantee that every mod combination will launch successfully.

## Validation

These commands use local scripts in the ignored `temp/` directory. The scripts
are not included in a clone or required by the GitHub build.

```powershell
python -B -m unittest temp.test_mod_markdown temp.test_mod_services temp.test_mod_backend_audit temp.test_mod_bundled_dependencies temp.test_mod_fallbacks temp.test_ui_mods temp.test_ui_mod_list temp.test_ui_motion temp.test_ui_progress temp.test_ui_toasts
python -B -m unittest temp.test_curseforge_services temp.test_ui_curseforge temp.test_mod_provider_credentials
```
