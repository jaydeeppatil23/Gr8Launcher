### Added

- Check for newer launcher releases from Settings > About and receive update notifications.
- Download verified updates in the background, with an optional automatic-download setting, and choose when to restart.
- Follow update progress in a window matching the launcher's navy-and-gold style.
- Restore the previous portable launcher if an update fails to start, while preserving profiles, accounts, and Minecraft files.

### Fixed

- Read formatted release notes with headings, lists, and links inside the launcher.
- Prevent updates from timing out after the launcher closes because the updater was waiting for itself to exit.

### Security

- Validate update redirects against GitHub download hosts and the known GitHub release bucket, including regional endpoints.
