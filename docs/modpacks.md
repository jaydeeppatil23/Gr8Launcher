# Modpacks

Open **Mods → Content type → Modpacks** to browse complete packs or import a
downloaded archive. The content selector uses the same acrylic field as the
provider and sort controls. The sidebar, title bar, and shared progress bar stay
available.

## Discover and details

- Discover searches **Modrinth** and **CurseForge** independently of the selected
  Minecraft profile. Each source retains its search, sort order, page, and scroll
  position when switching. Search waits 350 ms after typing and accepts Enter.
- Click a pack to read its description, screenshots, and version history. The
  Versions view supports search, release filters, and pagination. Install uses
  the latest stable release by default, falling back to a prerelease when no
  stable release exists; a specific version can be selected in Versions.
- CurseForge uses the existing API-key setup button and native credential vault.
  Its key is sent only to the API. Local CurseForge exports also need API access
  when their manifest references files that must be resolved online.

## Imports and installation

**Import pack** accepts Modrinth `.mrpack` files and CurseForge export ZIPs with a
root `manifest.json`. Other launcher instance ZIP formats are not supported.

The installation review shows the pack version, its Minecraft and loader pins,
download count/size, editable instance name and folder, and optional files.
Optional client files start unchecked. Modrinth server-only files and
`server-overrides` are excluded from client installs; common `overrides` are
applied before `client-overrides`. CurseForge uses its declared overrides folder
and exact project/file IDs. Mods, configs, scripts, resource packs, and shaders
included by the pack are copied to their declared locations.

Every install creates a **new separate instance**. Existing folders and folders
referenced by another profile cannot be adopted or overwritten. A suggested
folder name gains a numeric suffix when another instance already uses it.

CurseForge files without an API download URL appear as manual downloads. Follow
each exact official file link and select the downloaded files. Full checksums,
sizes, and archives are verified before enabling Install. A restricted pack ZIP
is selected and verified before reading its manifest. User downloads stay in
their original location.

All pack content is downloaded and verified in a staging folder outside the
watched instances directory. An owned snapshot protects against a local archive
changing during downloads. File paths are checked for traversal, Windows aliases,
links, conflicting names, and attempts to replace launcher metadata. Downloads
use approved HTTPS content hosts; redirect destinations are checked and provider
API credentials are never sent to content servers.

Archives are limited to 2 GB, an installation to 8 GB, and archives/manifests to
20,000 entries. Free space is checked before staging. The shared progress bar
reports download bytes; Cancel stops the operation before publishing its instance.
Cancel is available from both the catalogue and pack details. Leaving the page
cancels preparation or closes an unconfirmed review; a confirmed install continues
in the background and keeps the launcher operation guard until it finishes.
Failed verification, cancellation, or a failed profile save keeps existing
instances intact. An ownership marker and transaction journal allow startup to
finish cleanup of committed installs or remove owned, unpublished instances.

Successful installation adds the pack to **Library** and **Modpacks → Installed**.
Installed lists all pack instances and exposes Play, Manage pack mods, and Open
instance folder. First Play uses the existing game installation flow to obtain
any missing Minecraft and exact loader files.

## Pack versions and customization

The instance's `gr8launcher/modpack.json` records its source, pack version, game
and loader pins, and owned files with SHA-512 checksums. The original manifest is
saved alongside it. Known CurseForge mod identities are also available to the
existing Mods manager.

Pack-owned JARs show **Pack managed**. Check updates and Update all preserve their
pins, and individual version replacement actions are disabled. Explicit enable,
disable, and removal actions still work. New custom mods are eligible for the
ordinary mod update flow, provided their dependencies do not require replacing
pack-owned files.

Choose another release in Modpacks to install that version as a separate
instance. Existing worlds and customized files stay with the original instance.
Updating a pack in place is a future feature.

The selector can later gain Resource packs and Shaders. Their separate browsing
and management views are future work; assets already bundled in modpacks are
included during installation.

## Local validation

These ignored `temp/` regression scripts are local-only and are not included in a
clean checkout or used by CI:

```powershell
python -B -m unittest temp.test_modpack_services temp.test_ui_modpacks temp.test_curseforge_services temp.test_mod_services temp.test_mod_update_cache
```
