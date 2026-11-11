# Agent-maintained release notes

For each completed user-facing feature or fix, add a short Markdown file under
`unreleased/` in the same change as the code. Use a descriptive unique name, such
as `launcher-updates.md` or `modpack-download-retry.md`. Different tasks should
use different files so agents can work without editing one shared changelog.

Use one or more of these headings: `### Added`, `### Changed`, `### Fixed`,
`### Removed`, or `### Security`, followed by concise bullets. For example:

```markdown
### Added

- Check for launcher updates from Settings > About.

### Fixed

- Retry interrupted modpack downloads without losing your profile.
```

Describe what changed for users. Skip documentation-only changes and internal
refactors without visible behavior. Update an existing fragment for the same
feature instead of duplicating it. Do not claim planned work as completed.

When a new version publishes, the workflow:

1. Combines the fragments from the exact built commit into the release's
   **What's new** section, preserving other release text and the screenshot.
2. After publication and screenshot updates succeed, saves those notes to
   `releases/<version>.md` on `main`.
3. Removes only consumed fragments whose contents have not changed since the
   build. New or edited fragments remain for the next release.

The workflow uses GitHub's built-in token. Its archive commit does not bump
`VERSION` or start another release. Failed builds/publication leave fragments
intact. Rerunning the original release is safe and does not duplicate its notes.
Prereleases consume their own notes in the same way as stable releases.

Agents must not clear notes when editing `VERSION`, finishing a task, or updating
a user's launcher. Reset happens after a successful project release. The archive
preserves the history; notes are never simply discarded.

If branch protection prevents the workflow from writing its archive commit,
publication may already be complete, but the archive step reports a failure and
pending notes stay intact. Resolve the branch permission or archive through the
repository's normal review process before the next release.

To preview locally without publishing, run from the repository root:

```powershell
python -B tools/release_notes.py prepare --version 2.4.0 --output temp/release-notes.md --snapshot temp/release-notes.json
```

The preview and local-only regression tests belong in ignored `temp/`.
The command above includes uncommitted notes for review. Passing `--commit` reads
only Git blobs from that source commit, as CI does, so Windows checkout line
endings cannot prevent consumed notes from being cleared.
