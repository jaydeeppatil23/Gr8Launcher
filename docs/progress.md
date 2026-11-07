# Shared progress

The bottom progress bar stays available on every launcher page. Minecraft
installation and repair, mod searches and details, mod downloads and file
changes, skin refresh, sign-in, and version fetches use the same `ProgressHost`.
Its navy/gold styling matches the existing download footer.
The status and detail text above the bar appear only on Home. Other pages keep
the bar without the text row, including during active work. Returning to Home
restores the current task or idle status.

Unknown totals use an animated bar without a percentage. Measured work uses a
filled bar; mod downloads report bytes across the complete batch, including
required dependencies. Verification and installation return to the animated
state. Brief operations finish before the default 180 ms display delay, avoiding
flashes during cached lookups and quick local changes.

Minecraft installation and repair publish tracker snapshots through one UI loop.
Per-file callbacks stay in the logs and cannot reset measured progress to the
animated state or replace the stage label with an asset hash.
Measured fills animate on the client over 240 ms, retargeting smoothly when new
counts arrive. Progress snapshots retain the measured value regardless of how
often callbacks or UI reads occur. Returning to idle cancels the animation.
While measured work is incomplete, a 90px feathered gradient beam moves from left
to right inside the gold fill, including when counts pause. It reuses v1.2.0's
gradient shape with a navy tint. Each sweep animates on the client; the launcher
only resets it between sweeps. Unknown totals, completion, idle, and shutdown
stop the shimmer.

Concurrent tasks retain separate handles. The highest priority is displayed,
with the newest task winning ties and a `+N more` indicator for other visible
tasks. Finishing the displayed operation resumes the next one. When all work
finishes, the bar resets and restores the idle status, including
"Minecraft is running..." when appropriate. Success and failure feedback stays
in the existing notification host.

| Operation | Priority |
| --- | ---: |
| Minecraft launch/repair, mod downloads and file changes | 100 |
| Mod version/dependency planning, sign-in | 80 |
| Explicit skin refresh | 50 |
| Mod search/details, explicit release check | 40 |
| Background skin sync, Minecraft/loader versions, artwork | 10-20 |

Leaving a mod search or details page immediately releases its fetch handle.
Late responses cannot revive it or replace the current page's data. Downloads
continue across navigation and release their handle after completion or failure.
Refreshing a hidden Mods page does not start another visible fetch animation.
Existing Minecraft cancellation controls retain their behavior.

## Using the API

Pass the host to a UI component through its constructor. `main.py` also exposes
the current host through `state.progress_state["host"]` for shared integrations.
Use the context manager so exceptions and cancellation release the operation:

```python
import asyncio

from ui_progress import track_progress

async def refresh_skin(progress_host):
    with track_progress(progress_host, "Refreshing skin...", priority=50):
        await asyncio.to_thread(fetch_skin)

async def download_mods(progress_host, manager, client, plan):
    with track_progress(progress_host, "Downloading mods...", priority=100) as task:
        await asyncio.to_thread(manager.install, client, plan, task)
```

- `host.begin(label, ...)` returns a handle for work whose lifetime spans callbacks.
  Call `task.finish()` in `finally` and when replacing or abandoning a fetch.
- `task.update(label, value=0.5, detail="...")` updates an operation. Omitted
  fields retain their values; explicit `value=None` selects the animated state.
- `task.report(completed, total)` calculates progress from measured counts.
  Unknown or non-positive totals select the animated state.
- `task.on_bytes(completed, total)` adds downloaded/total MB and progress.
- Calling `task("Verifying mods...")` updates the status and selects the
  animated state, clearing the previous measurement.
- `show_percentage=False` avoids duplicate percentages when a legacy status
  already includes one. `delay=0` displays urgent operations immediately.
- A missing host produces a no-op handle, so standalone components keep working.

Worker threads can report progress directly. The host locks its logical state,
coalesces updates, and changes Flet controls on the page loop. Call `host.start()`
after mounting and `host.close()` on shutdown. Finished handles ignore subsequent
updates; shutdown clears all operations.

## Validation

These commands use local scripts in the ignored `temp/` directory. The scripts
are not included in a clone or required by the GitHub build.

```powershell
python -B -m unittest temp.test_ui_progress temp.test_ui_mods temp.test_mod_services
python -B temp/check_progress_motion.py
python -B temp/check_progress_motion.py --shimmer-only
py -3.14 -B temp/check_mods_ui.py --progress-only
```

The browser check uses isolated data and mocked downloads/skin responses. It
verifies [search loading](screenshots/gr8-launcher-progress-fetch.png),
[measured downloads](screenshots/gr8-launcher-progress-download.png),
[a larger window](screenshots/gr8-launcher-progress-download-large.png),
[navigation during a download](screenshots/gr8-launcher-progress-navigation.png),
[idle restoration](screenshots/gr8-launcher-progress-idle.png), and
[skin refresh](screenshots/gr8-launcher-progress-skin.png).
The motion check samples rendered fill positions at 1100x650 and 1366x768,
including interrupted transitions, unknown progress, and immediate idle reset.
The shimmer check verifies moving beams at a steady percentage, clipping to the
fill, left-to-right movement, gradient fading, and stopping at completion.
