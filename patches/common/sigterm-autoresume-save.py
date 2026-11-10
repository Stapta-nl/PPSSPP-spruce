#!/usr/bin/env python3
"""Save a state to a launcher-chosen path when PPSSPP is asked to quit.

Opt-in: set PPSSPP_AUTORESUME_STATE=<absolute path>. Unset, nothing changes.

When set and a game is running (or paused), SDL_QUIT (which is what SIGTERM,
SIGINT and the window close button turn into) no longer quits at once. The
SDL main loop first saves a state to <path>.tmp with SaveState::Save(), waits
for the save callback (at most 8 s), then:
  success: rename <path>.tmp -> <path>, create empty <path>.done
  failure or timeout: remove <path>.tmp, create empty <path>.fail
and quits as usual. With no game running it quits immediately and writes no
files. User save slots and iCurrentStateSlot are never touched. All file work
happens on the main loop thread, never in a signal handler.

Written against PPSSPP v1.20.3, where SaveState has no IsBusy(). The save
callback runs right after the state file is written, which is the only
completion signal there is. SaveState::Save() does not write a thumbnail
(only SaveSlot() does), so there is none to wait for.
"""
import sys

PATH = 'SDL/SDLMain.cpp'
MARKER = 'PPSSPP_AUTORESUME_STATE'

with open(PATH) as f:
    src = f.read()

if MARKER in src:
    print(f'{PATH}: autoresume save already applied, nothing to do')
    sys.exit(0)


def replace_once(src, old, new, what):
    if src.count(old) != 1:
        print(f'ERROR: cannot find {what} in {PATH} (found {src.count(old)}x)', file=sys.stderr)
        sys.exit(1)
    return src.replace(old, new, 1)


# --- 1. Include SaveState.h ---
# X11 headers (via SDL_syswm.h) #define Status as int, breaking enum class Status in SaveState.h.
src = replace_once(src, '''#include "Core/System.h"
''', '''#include "Core/System.h"
#ifdef Status
#undef Status
#endif
#include "Core/SaveState.h"
''', 'the Core/System.h include')

# --- 2. State and helpers ---
src = replace_once(src, '''static bool g_QuitRequested = false;
static bool g_RestartRequested = false;
''', '''static bool g_QuitRequested = false;
static bool g_RestartRequested = false;

// If PPSSPP_AUTORESUME_STATE is set to a path and a game is running, quitting first saves a state there.
// The state is written to <path>.tmp and renamed to <path> on success, followed by an empty <path>.done.
// On failure or timeout, <path>.fail is written instead. Lets a launcher resume exactly where the game was.
static const double AUTORESUME_TIMEOUT_S = 8.0;
static std::atomic<int> g_autoResumeResult{0};  // 0 = pending, 1 = saved, -1 = failed
static double g_autoResumeDeadline = 0.0;  // 0 = not started
static Path g_autoResumePath;

// Returns true if a save has been started (or is still running), in which case quitting is deferred.
static bool StartAutoResumeSave() {
	if (g_autoResumeDeadline != 0.0)
		return true;
	const char *env = getenv("PPSSPP_AUTORESUME_STATE");
	if (!env || !*env)
		return false;
	if (!PSP_IsInited() || (GetUIState() != UISTATE_INGAME && GetUIState() != UISTATE_PAUSEMENU))
		return false;

	g_autoResumePath = Path(env);
	File::Delete(g_autoResumePath.WithExtraExtension(".done"), true);
	File::Delete(g_autoResumePath.WithExtraExtension(".fail"), true);
	g_autoResumeDeadline = time_now_d() + AUTORESUME_TIMEOUT_S;
	// The callback runs on the emulation thread after the file has been written. It is never
	// called if the save is refused (hardcore mode, netplay), which the timeout then covers.
	SaveState::Save(g_autoResumePath.WithExtraExtension(".tmp"), -1, [](SaveState::Status status, std::string_view) {
		g_autoResumeResult = status == SaveState::Status::FAILURE ? -1 : 1;
	});
	return true;
}

// Called from the main loop. Requests the quit once the save has finished or timed out.
static void PollAutoResumeSave() {
	if (g_autoResumeDeadline == 0.0 || g_QuitRequested)
		return;
	const Path tmp = g_autoResumePath.WithExtraExtension(".tmp");
	if (g_autoResumeResult == 1) {
		if (File::Rename(tmp, g_autoResumePath)) {
			File::CreateEmptyFile(g_autoResumePath.WithExtraExtension(".done"));
		} else {
			g_autoResumeResult = -1;
		}
	} else if (g_autoResumeResult == 0 && time_now_d() < g_autoResumeDeadline) {
		return;
	}
	if (g_autoResumeResult != 1) {
		File::Delete(tmp, true);
		File::CreateEmptyFile(g_autoResumePath.WithExtraExtension(".fail"));
	}
	g_QuitRequested = true;
}
''', 'the g_QuitRequested/g_RestartRequested declarations')

# --- 3. SDL_QUIT ---
src = replace_once(src, '''\tcase SDL_QUIT:
\t\tg_QuitRequested = 1;
\t\tbreak;
''', '''\tcase SDL_QUIT:
\t\tif (!StartAutoResumeSave())
\t\t\tg_QuitRequested = 1;
\t\tbreak;
''', 'the SDL_QUIT case')

# --- 4. Vulkan (message pump) loop ---
src = replace_once(src, '''\t\t\t\t} while (SDL_PollEvent(&event));
\t\t\t}

\t\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\t\tbreak;
''', '''\t\t\t\t} while (SDL_PollEvent(&event));
\t\t\t}

\t\t\tPollAutoResumeSave();
\t\t\tif (g_QuitRequested || g_RestartRequested)
\t\t\t\tbreak;
''', 'the Vulkan event loop')

# --- 5. GL (main thread renders) loop ---
src = replace_once(src, '''\t\t\twhile (SDL_PollEvent(&event)) {
\t\t\t\tProcessSDLEvent(window, event, &inputTracker);
\t\t\t}
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
''', 'the OpenGL event loop')

with open(PATH, 'w') as f:
    f.write(src)
print(f'Patched {PATH}: save state to $PPSSPP_AUTORESUME_STATE before quitting')
