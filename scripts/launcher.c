/*
 * Native launcher stub for WhisperMe.app.
 *
 * This binary must STAY ALIVE as the bundle's main process: macOS (TCC)
 * attributes permissions like Microphone and Accessibility to the app's
 * responsible process, and children inherit that attribution. If this
 * process exec'd Python instead of spawning it, the app's permission identity
 * would become "python3.12" and grants made to "WhisperMe" in System
 * Settings would never match — permission prompts would loop forever.
 *
 * The interpreter lives inside the bundle at PYTHON_REL_PATH (baked in by
 * scripts/build-app.sh, but resolved relative to wherever the bundle actually
 * sits). Nothing outside the bundle is referenced, so the app runs on any Mac
 * without uv, Homebrew, or a source checkout.
 */

#include <errno.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static pid_t child_pid = 0;

static void forward_signal(int sig) {
    if (child_pid > 0) {
        kill(child_pid, sig);
    }
}

static void fail(const char *message) {
    char script[2048];
    snprintf(script, sizeof(script),
             "/usr/bin/osascript -e 'display alert \"WhisperMe cannot start\" "
             "message \"%s\" as critical'",
             message);
    system(script);
}

int main(void) {
    /* Locate our bundle root from the executable path:
       .../WhisperMe.app/Contents/MacOS/whisperme -> .../WhisperMe.app */
    char exe[4096] = {0};
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0) {
        fail("Could not determine the application path.");
        return 1;
    }

    char bundle[4096];
    strlcpy(bundle, exe, sizeof(bundle));
    for (int i = 0; i < 3; i++) {
        char *slash = strrchr(bundle, '/');
        if (slash != NULL) {
            *slash = '\0';
        }
    }
    setenv("WHISPERME_APP_BUNDLE", bundle, 1);

    char python[4096];
    snprintf(python, sizeof(python), "%s/%s", bundle, PYTHON_REL_PATH);

    struct stat st;
    if (stat(python, &st) != 0) {
        fail("The bundled Python runtime is missing. Reinstall WhisperMe from "
             "the DMG — copy it to Applications rather than running it from "
             "the disk image.");
        return 1;
    }

    /* Finder/login-item launches get a minimal PATH. */
    const char *path = getenv("PATH");
    char newpath[8192];
    snprintf(newpath, sizeof(newpath),
             "/usr/bin:/bin:/usr/sbin:/sbin:%s", path != NULL ? path : "");
    setenv("PATH", newpath, 1);

    /* A developer shell may export these; they would make the bundled
       interpreter load site-packages from somewhere else entirely. */
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    unsetenv("PYTHONEXECUTABLE");
    unsetenv("PYTHONSTARTUP");

    /* Everything is precompiled at build time. Writing new .pyc files into the
       bundle at runtime would invalidate its code signature and make a
       notarized build start failing Gatekeeper after its first launch. */
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);

    char *child_argv[] = {
        python, "-m", "whisperme", NULL,
    };
    int rc = posix_spawn(&child_pid, python, NULL, NULL, child_argv, environ);
    if (rc != 0) {
        fprintf(stderr, "whisperme launcher: posix_spawn(%s) failed: %s\n",
                python, strerror(rc));
        fail("The bundled Python runtime could not be started.");
        return 1;
    }

    signal(SIGTERM, forward_signal);
    signal(SIGINT, forward_signal);
    signal(SIGHUP, forward_signal);

    int status = 0;
    for (;;) {
        if (waitpid(child_pid, &status, 0) >= 0) {
            break;
        }
        if (errno != EINTR) {
            return 1;
        }
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
