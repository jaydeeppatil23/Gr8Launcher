# Mods page

The Mods sidebar entry opens a manager for one Minecraft profile. The Library's
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
3. **Tabs:** Discover and Installed with its mod count. A quiet “Powered by
   Modrinth” label appears beside the tabs in Discover.
4. **Toolbar:** the shared `SimpleInput` at its normal intrinsic height, with no
   Mods-specific height or padding overrides, 5 px corners, and a mode-specific
   search hint; All/Enabled/Disabled, Add JARs, and Refresh for
   Installed, or a labeled sort selector for Discover, initially Popular.
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
   loaders, and the Modrinth link remain on the details page. Action buttons and
   the artwork/title area use the click cursor.
7. **Footer:** Discover pagination (20 results per page), hidden when unavailable.
   Successful removal shows an Undo action in the shared notification host.

## Working behavior

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
  used on the details page. Recognized Modrinth projects can fill missing
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
- Modrinth responses are cached in a bounded, thread-safe in-memory cache for
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
- Modrinth results match the profile's exact Minecraft version and Fabric,
  Forge, NeoForge, or Quilt loader. Server-only projects are excluded from search.
  Stable releases are preferred, with a compatible prerelease used if necessary.
- Clicking a mod opens Overview, Versions, and Gallery (when available). Overview
  includes full Markdown documentation, authors, license, categories, environment,
  downloads, local file details, and available source/wiki/issues/community links.
  Local JARs are identified on Modrinth by checksum when possible; unmatched JARs
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
- Selecting another installed Modrinth version shows the exact replacement and
  required dependencies before confirmation. A successful replacement retains the
  previous JAR in launcher trash and preserves the mod's enabled state. A failed
  commit restores the previous JAR and mod index.
  Imported or renamed JARs identified by checksum can also use this replacement
  flow. The managed-mod index changes only when an installation is confirmed;
  an optional checksum identity cache also supports external JAR recognition.
- Check updates manually looks for newer Modrinth versions matching the profile's
  Minecraft version and loader. The release dropdown in Versions also controls
  update suggestions: Stable excludes prereleases; All releases includes them;
  Beta and Alpha offer only that release type. Change it and run Check updates
  again for the newest matching publication. Changing the selection clears
  previous check results. Update actions in mod details follow the same choice.
  An older release is never presented as an update to a newer installed
  prerelease. Each available update has an Update
  action that opens the existing version/dependency confirmation and backup flow.
  Disabled mods stay disabled after updating. Unknown JARs and failed online
  checks are reported as incomplete. Checking updates downloads no JARs and never
  changes an installed file. Mod details also expose an Update action when their
  loaded version history contains a newer matching version.
- Update checks are saved in the instance's `gr8launcher/mod-updates.json`, including
  checks that found no updates. Navigation and launcher restarts restore the
  matching result without another Modrinth check, and the last checked release
  filter is restored when reopening an instance. Saved results are scoped to
  Minecraft version, loader/build, and release filter and are discarded when the
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
  and rechecks Modrinth conflicts before enabling Install. Installed and bundled
  dependencies use a lock icon and an Installed label, show their existing
  versions, and are kept without another download. Compatibility checks run
  during planning and installation; the confirmation omits their details.
  Canceling downloads nothing. Existing compatible dependencies are reused; disabled or
  conflicting dependencies need attention in Installed.
  Reused dependencies still have their own requirements checked. Modrinth's
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
  Modrinth version requirement that disagrees with an installed dependency blocks
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
  Modrinth-hosted files, including renamed JARs, and store a bounded optional
  `gr8launcher/mod-identities.json` cache. Cached identities survive restart and
  are reused only when the actual checksum matches; replacing a JAR externally
  invalidates its former identity. A corrupt identity cache does not block local
  file actions. Unknown files retain their local metadata, enable/disable, and
  remove actions, with limited online dependency/update checks made explicit.
  Failed optional lookups do not block unrelated installations. Unsupported bulk
  lookup endpoints fall back to individual checksum requests. Files changed
  during a check, after an update check, or after install confirmation require a
  fresh review. A profile version changed after confirmation also stops the install.

This initial page does not offer automatic updates or uninstall dependencies.

## Validation

These commands use local scripts in the ignored `temp/` directory. The scripts
are not included in a clone or required by the GitHub build.

```powershell
python -B -m unittest temp.test_mod_markdown temp.test_mod_services temp.test_mod_backend_audit temp.test_mod_bundled_dependencies temp.test_mod_fallbacks temp.test_ui_mods temp.test_ui_mod_list temp.test_ui_motion temp.test_ui_progress temp.test_ui_toasts
```
