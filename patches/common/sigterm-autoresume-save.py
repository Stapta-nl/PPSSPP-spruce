#!/usr/bin/env python3
"""Save a state to a launcher-chosen path when PPSSPP is asked to quit.

Opt-in: set PPSSPP_AUTORESUME_STATE=<absolute path>. Unset, nothing changes.
PPSSPP_AUTORESUME_TIMEOUT_MS (default 6000) bounds the wait.

SDL_QUIT (SIGTERM, SIGINT, window close) with a game loaded no longer quits at
once. The request to save a state to <path>.tmp is handed to the main thread
(SaveState::Save, as the UI does), then the SDL main loop waits for the save
callback. A game that was loaded ends with exactly one of:
  <path> + empty <path>.done   (.tmp renamed to <path>)
  empty <path>.fail            (.tmp removed)
and then PPSSPP quits as usual. Other quit paths (menu exit, restart) that
fire during the wait are held back until that is settled.

.fail is written at once, without waiting, when the save is known to be
impossible: pause menu without "run behind pause menu" (emulation, and with it
SaveState::Process(), does not run there), exception state, kernel not
running, netplay, or RetroAchievements hardcore mode. No game running (menus,
still booting): quit at once, no files. User slots and iCurrentStateSlot are
never touched. No file work happens in a signal handler.

Written against PPSSPP v1.20.3 (SaveState::Callback has 2 arguments there; the
lambda also accepts more). v1.20.3 has no SaveState::IsBusy(); the callback
runs right after the state file is written. SaveState::Save() writes no
thumbnail, so there is none to wait for.
"""
import sys

PATH = 'SDL/SDLMain.cpp'

# (what, old, new). Every edit is checked by its applied form, see below.
EDITS = []


def edit(what, old, new):
    EDITS.append((what, old, new))


edit('the Core/System.h include', '''#include "Core/System.h"
''', '''#include "Core/System.h"
#ifdef Status
#undef Status
#endif
#include "Core/SaveState.h"
#include "Core/RetroAchievements.h"
#include "Core/HLE/sceKernel.h"
#include "Core/HLE/sceNet.h"
''')

edit('the g_QuitRequested/g_RestartRequested declarations', '''static bool g_QuitRequested = false;
static bool g_RestartRequested = false;
''', '''static bool g_QuitRequested = false;
static bool g_RestartRequested = false;

// If PPSSPP_AUTORESUME_STATE is set to a path and a game is loaded, quitting via SDL_QUIT first saves a state.
// It is written to <path>.tmp and renamed to <path> on success, followed by an empty <path>.done.
// Otherwise (error, timeout, or a save that cannot work right now) an empty <path>.fail is written.
// Lets a launcher resume exactly where the game was. PPSSPP_AUTORESUME_TIMEOUT_MS overrides the 6 s wait.
static std::atomic<int> g_autoResumeResult{0};  // 0 = pending, 1 = saved, -1 = failed
static std::atomic<bool> g_autoResumeAbandoned{false};  // set once we stopped waiting
static bool g_autoResumeStarted = false;
static bool g_autoResumeActive = false;
static double g_autoResumeDeadline = 0.0;
static Path g_autoResumePath;

static void FinishAutoResume(bool success) {
	const Path tmp = g_autoResumePath.WithExtraExtension(".tmp");
	const Path done = g_autoResumePath.WithExtraExtension(".done");
	const Path fail = g_autoResumePath.WithExtraExtension(".fail");
	g_autoResumeActive = false;
	g_autoResumeAbandoned = true;
	if (success && File::Rename(tmp, g_autoResumePath) && File::CreateEmptyFile(done))
		return;
	File::Delete(done, true);
	File::Delete(tmp, true);
	if (!File::CreateEmptyFile(fail))
		ERROR_LOG(Log::System, "Autoresume: could not write %s", fail.c_str());
}

// Returns true if a save is under way, in which case quitting is deferred until PollAutoResumeSave() is done.
static bool StartAutoResumeSave() {
	if (g_autoResumeStarted)
		return g_autoResumeActive;
	const char *env = getenv("PPSSPP_AUTORESUME_STATE");
	const GlobalUIState state = GetUIState();
	if (!env || !*env || !PSP_IsInited() || (state != UISTATE_INGAME && state != UISTATE_PAUSEMENU && state != UISTATE_EXCEPTION))
		return false;

	g_autoResumeStarted = true;
	g_autoResumePath = Path(env);
	File::Delete(g_autoResumePath.WithExtraExtension(".done"), true);
	File::Delete(g_autoResumePath.WithExtraExtension(".fail"), true);

	// SaveState::Process() only runs while the emulation runs: not in the pause menu (unless it runs behind), not after an exception.
	// SaveState::Save() drops a refused save without calling back, so check for that here rather than time out.
	const bool emuRuns = state == UISTATE_INGAME || (state == UISTATE_PAUSEMENU && g_Config.bRunBehindPauseMenu);
	const bool hardcoreBlocks = Achievements::HardcoreModeActive() && !g_Config.bAchievementsSaveStateInHardcoreMode;
	if (!emuRuns || !__KernelIsRunning() || !NetworkAllowSaveState() || hardcoreBlocks) {
		FinishAutoResume(false);
		return false;
	}

	const char *timeoutEnv = getenv("PPSSPP_AUTORESUME_TIMEOUT_MS");
	int timeoutMs = timeoutEnv ? atoi(timeoutEnv) : 0;
	g_autoResumeDeadline = time_now_d() + (timeoutMs > 0 ? timeoutMs : 6000) * 0.001;
	g_autoResumeActive = true;
	// Like the UI, request the save from the main thread. The callback runs on the emulation thread after the file is written.
	const Path tmp = g_autoResumePath.WithExtraExtension(".tmp");
	System_RunOnMainThread([tmp]() {
		SaveState::Save(tmp, -1, [tmp](SaveState::Status status, auto &&...) {
			if (g_autoResumeAbandoned) {
				File::Delete(tmp, true);  // Too late, nobody waits for this anymore.
			} else {
				g_autoResumeResult = status == SaveState::Status::FAILURE ? -1 : 1;
			}
		});
	});
	return true;
}

// Called from the main loop before each quit check. While a save is under way it holds back any quit,
// including ones that did not come from SDL_QUIT, so that .done or .fail is always settled first.
static void PollAutoResumeSave() {
	if (!g_autoResumeActive)
		return;
	g_QuitRequested = false;
	g_RestartRequested = false;
	const int result = g_autoResumeResult;
	if (result == 0 && time_now_d() < g_autoResumeDeadline)
		return;
	FinishAutoResume(result == 1);
	g_QuitRequested = true;
}
''')

edit('the SDL_QUIT case', '''\tcase SDL_QUIT:
\t\tg_QuitRequested = 1;
\t\tbreak;
''', '''\tcase SDL_QUIT:
\t\tif (!StartAutoResumeSave())
\t\t\tg_QuitRequested = 1;
\t\tbreak;
''')

edit('the Vulkan event loop', '''\t\t\t\t} while (SDL_PollEvent(&event));
\t\t\t}

\t\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\t\tbreak;
''', '''\t\t\t\t} while (SDL_PollEvent(&event));
\t\t\t}

\t\t\tPollAutoResumeSave();
\t\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\t\tbreak;
''')

edit('the first quit check of the OpenGL event loop', '''\t\t\twhile (SDL_PollEvent(&event)) {
\t\t\t\tProcessSDLEvent(window, event, &inputTracker);
\t\t\t}
\t\t}
\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\tbreak;
\t\tif (emuThreadState == (int)EmuThreadState::DISABLED) {
\t\t\tNativeFrame(graphicsContext);
\t\t}
\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\tbreak;
''', '''\t\t\twhile (SDL_PollEvent(&event)) {
\t\t\t\tProcessSDLEvent(window, event, &inputTracker);
\t\t\t}
\t\t}
\t\tPollAutoResumeSave();
\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\tbreak;
\t\tif (emuThreadState == (int)EmuThreadState::DISABLED) {
\t\t\tNativeFrame(graphicsContext);
\t\t}
\t\tPollAutoResumeSave();
\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\tbreak;
''')


def main():
    with open(PATH, encoding='utf-8', newline='') as f:
        src = f.read()

    # An edit counts as applied when its new text is there. The old text of some
    # edits is part of the new text, so never test the old text of an applied edit.
    applied = [new in src for _, _, new in EDITS]
    if all(applied):
        print(f'{PATH}: autoresume save already applied, nothing to do')
        return
    if any(applied) or 'PPSSPP_AUTORESUME_STATE' in src:
        missing = [w for (w, _, _), a in zip(EDITS, applied) if not a]
        print(f'ERROR: {PATH} is half-patched (missing: {", ".join(missing)}); start from a clean checkout', file=sys.stderr)
        sys.exit(1)

    for what, old, new in EDITS:
        if src.count(old) != 1:
            print(f'ERROR: cannot find {what} in {PATH} (found {src.count(old)}x)', file=sys.stderr)
            sys.exit(1)
        src = src.replace(old, new, 1)

    with open(PATH, 'w', encoding='utf-8', newline='') as f:
        f.write(src)
    print(f'Patched {PATH}: save state to $PPSSPP_AUTORESUME_STATE before quitting')


if __name__ == '__main__':
    main()
