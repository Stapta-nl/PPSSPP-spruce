# Handoff: save a state on SIGTERM for NextUI auto-resume

Status: **design and evidence only, no patch written yet.** A fresh session should be able to implement the patch from this file alone. Tracking PR in the pak repo: <https://github.com/Stapta-nl/minui-psp/pull/6> (see its "Notes" comment).

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
  2. Wait until the save is written **and** its thumbnail is finished. Use `!SaveState::IsBusy()`. If the PPSSPP-side fix below is in the tree, also wait on `IsSaveStateScreenshotPending()`. Give up after about 8 s.
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

Off-device (headless build works in the cloud; the SDL build needs SDL3 there):
1. Patch applies cleanly to `v1.20.3` (`python3 patches/common/sigterm-autoresume-save.py` in a checkout).
2. With the env var set: start a game, send SIGTERM, check `<path>` and `<path>.done` exist, the `.ppst` loads with `--state`, and the process exits with the status it had before.
3. Without the env var: SIGTERM behaves exactly as before (no files).
4. Menus only (no game): immediate exit, no files.
5. Save refused or failing (for example an unwritable path): `.fail` is written and PPSSPP still exits within the timeout.

On-device (TrimUI Smart Pro), with the pak's shim in place:
1. Power off during a game: `auto_resume.txt`, the autoresume `.ppst` and `.done` exist after the SD card is pulled; boot resumes at the power-off moment.
2. Menu quit: no marker. Deep sleep and wake: unchanged.
3. Forced power cut: boots to the menu.
4. Power off with the pause menu open: state is saved.
5. Thumbnail of a normal manual save is intact after a save-then-exit.

## Open questions for the implementer

- Exact `SaveState::Save` / `IsBusy` / screenshot-pending names in v1.20.3 versus master.
- Whether the thumbnail path for a path-based save is derived from the state path, and whether it should be written at all for the autoresume file.
- Whether to use an env var or a command-line flag (`--autoresume-state=`) for upstream.
- Which tree builds first, this patch or Stapta-nl/ppsspp#1, and where the shared wait logic lives.
