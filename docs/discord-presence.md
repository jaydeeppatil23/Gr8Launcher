# Discord Rich Presence

Gr8 Launcher uses Discord application `1559999125200568320`. Presence is enabled
by default and can be turned off in **Settings → General → Discord Rich Presence**.
The Discord desktop app must be running and allow activity sharing.

Presence shows the current launcher page, installation or launch preparation,
and **Playing Minecraft** with the actual Minecraft version and mod loader
(including its version). Starting Minecraft resets the elapsed timer. Game
activity takes priority over navigation; exiting Minecraft restores the current
launcher page. A small player face appears beside the launcher logo, with the
player's name on hover. Account credentials, custom profile names, and game
paths are not included.

The portrait comes from the cached skin, including its hat layer. It refreshes
when accounts are switched or Ely.by skins are synced. A running game retains
the launched player's face even if you switch accounts in the launcher. The
skin bridge supplies the actual selected skin for custom/default game skins.
Offline accounts use the launcher's default Steve portrait.

Updates are coalesced to at most one every 15 seconds. Discord can be opened
after the launcher; failed connections retry every 15 seconds. An idle connection
is checked every minute so restarting Discord also reconnects automatically.
Missing Discord or its optional Python dependency does not interrupt launching.

Use **Hide Launcher** or **Keep Launcher Open** to retain presence during play.
**Close Launcher** closes the connection and clears presence. Turning off the
setting also clears it immediately, without waiting for the update interval.

## Application configuration

The public application ID lives in `discord_presence.py`. To test another
application without changing the source, set `GR8_DISCORD_APPLICATION_ID` before
starting the launcher. No bot token, OAuth login, or client secret is required.
The activity name comes from the application's name in the
[Discord Developer Portal](https://discord.com/developers/applications).

The large image uses the project's existing logo URL. To use an uploaded Discord
asset instead, upload `.assets/icons/gr8_icon.png` under the application's Rich
Presence assets and set `DISCORD_LOGO` to its asset key.

The launcher uploads only the face thumbnail to Catbox, then Discord fetches and
proxies its public image URL. Each distinct face is uploaded once; the URL is
cached in `discord_portraits.json` in the launcher data folder. The full skin
file and account credentials stay local. Uploads happen in the background while
Discord presence is enabled and connected. A failed upload retries after five
minutes and a rejected portrait falls back to page/game presence.

