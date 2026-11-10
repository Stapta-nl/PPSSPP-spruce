# Handoff: save a state on SIGTERM for NextUI auto-resume

Status: **patch written** (`patches/common/sigterm-autoresume-save.py`, check script `tools/test-autoresume-patch.sh`), not yet run on a device. Verified off-device on x86_64 Linux: applies to a fresh v1.20.3 checkout (also with the PVR patches after it); the full `PPSSPPSDL` target (SDL 2.30, GL) compiled and linked; the `SDLMain.cpp` translation unit of the pixel2 pinned commit `d357e6a` passes `-fsyntax-only`; under Xvfb with software GL on `pspautotests/demos/cube.iso`, SIGTERM and SIGINT in-game give `<path>` + `<path>.done` and exit 0 in about 0.6 s, the pause menu gives `.fail` at once (or `.done` with `RunBehindPauseMenu`), the 1 ms timeout and a failing rename give `.fail` with no `.tmp`, and with the variable unset or no game running nothing is written. **Not verified:** loading the written state back with `--state`, a real hardcore/netplay refusal, other quit paths firing during the wait, the Vulkan (message pump) loop at runtime (compiled only, no Vulkan driver here), any ARM/TrimUI build, any device. The sections below are the original specification; "Implementation notes and answers" at the end records what v1.20.3 actually offers and where the patch differs.

## Goal

When the device is powered off during a PSP game, NextUI should boot straight back into that game at the exact moment of power-off, the way its built-in (libretro) cores do. For that PPSSPP must write a save state *as part of being shut down*, to a path the pak chooses, and must tell the pak when that write is finished.

The pak side (`minui-psp`, `launch.sh`) already writes the NextUI marker `auto_resume.txt`. What is missing is the state. Today PPSSPP exits on SIGTERM without saving, so the pak can only resume the newest state that already exists.

## Where this patch goes

- New file `patches/common/sigterm-autoresume-save.py`, in the style of the existing patch scripts (Python that rewrites a source file in the PPSSPP checkout and exits non-zero if an anchor is not found). Apply it from the build scripts you need (at least `build-pvr.sh`, which builds `PPSSPPSDL_TrimUI` for the TrimUI Brick/TSP/Smart Pro).
- Base: PPSSPP `v1.20.3`, as pinned in `build-pvr.sh`. A later backport to upstream `hrydgard/ppsspp` is the goal, so keep the change small and written like upstream code (no spruce-specific names in the C++).
- A previous, similar patch existed: `patches/common/sigusr1-save-and-quit.py`, removed in commit `d06d5e2` (2026-09-02). View it with `git show d06d5e2^:patches/common/sigusr1-save-and-quit.py`. The removal commit gives no reason. The patch was written for spruceOS's GameSwitcher, which is likely why it was dropped. Its history (commits `6961449` .. `30edcbb`, 2026-03-29/30) shows the author tried several "synchronous save" variants and went back to plain async `SaveSlot` plus a callback. Reuse the idea, but not the callback-then-quit ordering (see "Why not quit in the callback").

## Required behaviour

Opt-in through an environment variable, so other launchers are unaffected:

- `PPSSPP_AUTORESUME_STATE=<absolute path>` (name is a proposal). Unset means PPSSPP behaves exactly as today.
- When set, and a game is running or paused (`UISTATE_INGAME` or `UISTATE_PAUSEMENU`), **SIGTERM** (SDL turns it into `SDL_QUIT`) and SIGINT start "save, then quit":
  1. Save a state to `<path>.tmp` through `SaveState::Save(Path, slot, callback)` (it exists in master at `Core/SaveState.cpp:562`; confirm the v1.20.3 signature).
  2. Wait until the save is written **and** its thumbnail is finished. Use `!SaveState::IsBusy()`. If the PPSSPP-side fix below is in the tree, also wait on `IsSaveStateScreenshotPending()`. Give up after the timeout (6 s by default; see the notes at the end).
  3. On success, rename `<path>.tmp` to `<path>`, write an empty `<path>.done`, then quit normally. On failure or timeout, write `<path>.fail` (and remove the `.tmp`), then quit anyway.
- If no game is running (menus), quit immediately, with no files written.
- Do not save when RetroAchievements hardcore mode or netplay refuses the save. The callback reports `FAILURE`; treat that as the `.fail` case.
- Never touch the user's slots (`StateSlot` and the files `<GAMEID>_<slot>.ppst`). The path is independent of the slot number, on purpose: hotkeys can only save to the current slot, which is why the hotkey route does not map to NextUI's dedicated slot 9.
- Signal handling: SDL only installs its SIGTERM/SIGINT handler while the signal is still at its default action, and PPSSPP currently catches both (device `SigCgt` shows bits 2 and 15). Handle it in the SDL event loop (`SDL_QUIT` in `SDL/SDLMain.cpp`), with a flag for the timeout. Do not do file work inside the signal handler.

### Why not quit in the save callback

The save callback fires when the state is written, but the thumbnail is scheduled separately (`Core/SaveState.cpp:746`, `ScheduleScreenshot`) and written by a fire-and-forget I/O task. The old SIGUSR1 patch quit in the callback, so a thumbnail can be cut off. For auto-resume only the `.ppst` is needed, but the Game Switcher preview in the pak reads that thumbnail. A related bug report and fix exist: <https://github.com/Stapta-nl/ppsspp/pull/1> (draft, "Savestates: Don't lose the state or its thumbnail when exiting right after a save"; headless-tested only, not run on a device). It adds `ExitWhenSaved()` in `UI/EmuScreen.cpp`, moves thumbnail writing onto the savestate worker, and notes two exit paths that skip the wait: the window close button (the same SDL_QUIT that SIGTERM produces) and `bPauseExitsEmulator`. This patch must supply its own wait on the SIGTERM path. Check with whoever works on that PR which tree is built first and avoid conflicting edits in `SDL/SDLMain.cpp` and `UI/EmuScreen.cpp`.

## Pak side (for reference, lives in minui-psp)

Not part of this patch, but it fixes the contract:

- `launch.sh` exports `PPSSPP_AUTORESUME_STATE=<state dir>/autoresume/<rom file>.ppst`, so the file stays out of the "newest save" auto-load (`AutoLoadSaveState = 2`) and out of `*.ppst` globs used by the Game Switcher preview. A `load_undo.ppst` already sits in the same folder on devices.
- Resume: NextUI's `autoResume()` writes `9` to `/tmp/resume_slot.txt` and relaunches the ROM through the pak. If that file says `9` and the autoresume state exists, the pak starts PPSSPP with `--state=<file>`. `--state=FILE` is present in the shipped binary (checked with `grep -c -e '--state'`, 2 hits). A normal open does not use it.
- Hold the power-off: a `bin/poweroff` shim creates `/tmp/psp-poweroff-req`, waits up to about 10 s for `/tmp/psp-poweroff-ok`, then `exec /sbin/poweroff` by absolute path. `launch.sh` creates the ok file after PPSSPP has exited and the marker is written and synced.
- Fail safe: no `.done` means no marker and no resume (device boots to the normal menu). NextUI deletes the marker before launching the game, so a bad state cannot loop.

## Facts confirmed on a TrimUI Smart Pro (tg5040)

NextUI-20261013-0, TinaLinux 4.9.191, `procd` as init, PPSSPP v1.20.3, SDL 2.30.8.

- power-control 1.1.0's `shutdown` script: `kill $(cat /tmp/emulator_pid)` (SIGTERM to PPSSPP), show "Powering off", `sync`, `poweroff`. It does not wait. `/tmp/emulator_pid` holds PPSSPP's own pid. `suspend` uses the same file for STOP/CONT, so do not repoint it.
- A logging shim in `PSP.pak/bin/poweroff` **was called** by `shutdown` on two real power-offs (PATH there has `PSP.pak/bin` before `/sbin`). Pak processes, including PPSSPP, were still exiting at that moment, so the shim gives the pak the time it needs.
- The shipped PPSSPP binary: no SIGUSR1 handler (`SigCgt` bit 10 clear), no `game.state.*` strings, `--state` supported.
- Save states are about 8.6 MB each, five slots (`SaveStateSlotCount = 5`), `EnableStateUndo = True`. A 20 MB write plus `sync` took 0.97 s on the SD card.
- FAT mtimes were unreliable (one-second steps), so detect completion with the `.done` file, not timestamps.
- Tools missing on the device: `pkill`, `od`, `stat`, `timeout`, `python3`, `jq`. Present: `pgrep`, `pidof`, `killall`, `xxd`, `hexdump`, `awk`, `curl`, `nc`.
- The tester's hotkeys (Select+R1 and Select+Start for Save State, Select+Start for Exit App) were set by hand and are not defaults, so nothing may depend on them.

## Shorter routes considered

- **Hotkey injection** (write input events to `/dev/input/event3`, as the minui-psp e2e tests do): no PPSSPP change, but it needs PPSSPP to survive SIGTERM (untested), saves only to the current slot, does nothing in the pause menu, and depends on hand-set hotkeys that `pak.json` cannot ship (`controls.ini` is in `update_ignore`).
- **Upstream master `game.state.save`** with `wait:true` (commits `9720041e`, `4bfeb59f`, `5bbeb625`): a real completion reply, but it needs the unreleased master plus the debugger port, which listens on all interfaces.
- **This patch** keeps everything local and gives a clear done/fail signal, which is why it is the recommended route.

## Test plan

Off-device (the SDL2 build and an Xvfb run work in the cloud; see the status line for what was actually run):
1. Patch applies cleanly to `v1.20.3` (`python3 patches/common/sigterm-autoresume-save.py` in a checkout).
2. With the env var set: start a game, send SIGTERM, check `<path>` and `<path>.done` exist, the `.ppst` loads with `--state`, and the process exits with the status it had before.
3. Without the env var: SIGTERM behaves exactly as before (no files).
4. Menus only (no game): immediate exit, no files.
5. Save refused or failing (for example an unwritable path): `.fail` is written and PPSSPP still exits within the timeout.

On-device (TrimUI Smart Pro), with the pak's shim in place:
1. Power off during a game: `auto_resume.txt`, the autoresume `.ppst` and `.done` exist after the SD card is pulled; boot resumes at the power-off moment.
2. Menu quit: no marker. Deep sleep and wake: unchanged.
3. Forced power cut: boots to the menu.
4. Power off with the pause menu open: **`.fail` at once, no state** (see Limits; the state is saved only with `RunBehindPauseMenu` on). The pak must then not write the marker.
5. Thumbnail of a normal manual save is intact after a save-then-exit.

## Open questions for the implementer

- Exact `SaveState::Save` / `IsBusy` / screenshot-pending names in v1.20.3 versus master.
- Whether the thumbnail path for a path-based save is derived from the state path, and whether it should be written at all for the autoresume file.
- Whether to use an env var or a command-line flag (`--autoresume-state=`) for upstream.
- Which tree builds first, this patch or Stapta-nl/ppsspp#1, and where the shared wait logic lives.

## Implementation notes and answers (v1.20.3)

The patch is `patches/common/sigterm-autoresume-save.py`; `tools/test-autoresume-patch.sh <fresh checkout>` re-runs the apply, anchor and (if SDL2 dev files and cmake are installed) compile checks. It edits only `SDL/SDLMain.cpp`: includes, about 90 lines of helpers, the `SDL_QUIT` case, and `PollAutoResumeSave()` calls before the quit checks of the Vulkan message pump (1) and the GL main-thread loop (2). The literal `PPSSPP_AUTORESUME_STATE` is in the binary as a string literal. Re-applying is a no-op; a half-applied file is refused with its own message.

### Contract

For a loaded game (`PSP_IsInited()` and `UISTATE_INGAME`, `UISTATE_PAUSEMENU` or `UISTATE_EXCEPTION`) a `SDL_QUIT` ends with exactly one of: `<path>` + empty `<path>.done`, or empty `<path>.fail`, and then PPSSPP quits with its usual exit status. Stale `.done`/`.fail` are removed first. With no game (menus, still booting) or the variable unset: quit at once, no files.

### Flow

1. `StartAutoResumeSave()` (on the SDL thread). If the save is known to be impossible it writes `.fail` and quits at once, without waiting: pause menu without `RunBehindPauseMenu`, exception state, `__KernelIsRunning()` false, `!NetworkAllowSaveState()`, or hardcore mode without `bAchievementsSaveStateInHardcoreMode`. Those are the conditions under which `SaveState::Save()` would silently drop the request.
2. Otherwise it sets the deadline and, via `System_RunOnMainThread`, calls `SaveState::Save(<path>.tmp, -1, callback)` on the thread the UI uses. The callback (emulation thread, right after the file is written) only stores a result; if we already gave up it deletes `<path>.tmp` instead.
3. `PollAutoResumeSave()` in the main loop waits for the result or the deadline, then renames `.tmp` to `<path>` and creates `<path>.done`. Any failure (callback failure, timeout, rename, creating `.done`) removes `.tmp` and `.done` and creates `.fail`. Then it sets `g_QuitRequested`.
4. While a save is under way the poll clears `g_QuitRequested`/`g_RestartRequested` each iteration, so a menu exit, restart or `EXIT_APP` arriving during the wait cannot cut it short; the quit happens after `.done`/`.fail` is settled. A second SIGTERM during the wait is ignored.

Slot files and `iCurrentStateSlot` are never touched (`Save()` with slot `-1`, used only for on-screen messages). No file work happens in a signal handler (SDL's handler only queues `SDL_QUIT`). The callback lambda takes `(SaveState::Status, auto &&...)`, so it compiles with the 2-argument v1.20.3 callback and with a longer one.

### Timing budget

The wait is 6 s by default, overridable with `PPSSPP_AUTORESUME_TIMEOUT_MS` (positive integer, milliseconds). The pak's poweroff shim holds power for about 10 s (maybe 15 s), and PPSSPP's own shutdown (emulation thread stop, audio, config save) comes on top of the wait, so keep the timeout well below the shim's limit minus that shutdown time. A normal save here took about 0.6 s end to end.

### Open questions, resolved

- **Names in v1.20.3.** `SaveState::Save(const Path &, int slot, Callback)`, callback type `std::function<void(Status, std::string_view)>`. There is no `IsBusy()` and no `IsSaveStateScreenshotPending()` in v1.20.3 (only master / Stapta-nl/ppsspp#1). The patch does not need them.
- **Completion signal.** `SaveState::Process()` runs on the emulation/UI thread from `EmuScreen::RunEmulation` (or `Core_ProcessStepping`) and calls the callback right after `CChunkFileReader::Save()` returned. `Save()` does not fsync; the pak's `sync` covers that.
- **Thumbnail.** Only `SaveSlot()` schedules a screenshot; path-based `Save()` does not. The autoresume file has no thumbnail and there is nothing to wait for, so the thumbnail race cannot happen here. A Game Switcher preview must come from elsewhere (for example the newest slot thumbnail), or a later change can call `ScheduleScreenshot()` with a callback and wait on that.
- **Env var or flag.** Environment variable (the pak already exports it). A `--autoresume-state=` flag next to `--state=` would be a small addition for upstream; not done.
- **Which tree first.** Independent of Stapta-nl/ppsspp#1: only `SDL/SDLMain.cpp` is touched and nothing from that PR is used. Its `ExitWhenSaved()` does not cover this path, which is why this patch has its own wait.

### Limits

- **Pause menu.** `SaveState::Process()` only runs from `EmuScreen::RunEmulation`, which is skipped while the pause screen is on top (`ShouldRunEmulation`) unless `RunBehindPauseMenu` is on (or ad-hoc networking forces it). The UI's own save button closes the pause screen for the same reason. Calling `Process()` from the SDL loop would race the render/emulation threads, so the patch does not. Result: power-off with the pause menu open writes `.fail` at once, and the pak must not resume. With `RunBehindPauseMenu` on, the state is saved (tested).
- A save refused by hardcore mode or netplay, an exception state, or a stopped kernel gives `.fail` at once (checked before calling `Save()`). `Achievements::IsBlockingExecution()` is not checked; it ends as a timeout.
- If the emulation thread stalls or the game exits during the wait, the timeout applies and `.fail` is written. A save that finishes after the timeout deletes its own `.tmp`; if the process exits in the middle of that write, a partial `.tmp` can remain.
- `rename` is atomic on one filesystem, but nothing is fsynced here; a power cut before the pak's `sync` can lose the file. `.done` is created after the rename, never before.
- Window close and SIGINT take the same path as SIGTERM. SIGKILL and power cuts cannot be handled.
- **Vulkan loop.** There the SDL thread is only a message pump. The save request is marshalled to the UI thread with `System_RunOnMainThread` (what screenshot callbacks use), while the checks in `StartAutoResumeSave()` and the file work in `PollAutoResumeSave()` run on the pump thread and read globals (`GetUIState()`, config) without locks, like the rest of `ProcessSDLEvent`. This loop compiles but was not run here.
- Compile status: full `PPSSPPSDL` x86_64/SDL2/GL build of v1.20.3 and the `SDLMain.cpp` translation unit of the pixel2 pinned commit `d357e6a` (`-fsyntax-only`, same 2-argument callback there). No ARM build was made.

### Which builds apply it

No build script change was needed: `build-pvr.sh` (the Brick/TSP/Smart Pro binary `PPSSPPSDL_TrimUI`), `build-tsps.sh`, `build-64.sh`, `build-a30.sh`, `build-flip.sh`, `build-h700.sh`, `build-rgb30.sh` and `build-pixel2.sh` all run every `patches/common/*.py`, and all build the `PPSSPPSDL` target, so all pick it up. None skips it.
