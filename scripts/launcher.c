/*
 * Native launcher stub for WhisperMe.app.
 *
 * This binary must STAY ALIVE as the bundle's main process: macOS (TCC)
 * attributes permissions like Microphone and Accessibility to the app's
 * responsible process, and children inherit that attribution. If this
 * process exec'd uv instead of spawning it, the app's permission identity
 * would become "uv" (or zsh) and grants made to "WhisperMe" in System
 * Settings would never match — permission prompts would loop forever.
 *
 * UV_PATH and REPO_DIR are baked in at build time by scripts/build-app.sh.
 */

#include <errno.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static pid_t child_pid = 0;

static void forward_signal(int sig) {
    if (child_pid > 0) {
        kill(child_pid, sig);
    }
}

int main(void) {
    /* Locate our bundle root from the executable path:
       .../WhisperMe.app/Contents/MacOS/whisperme -> .../WhisperMe.app */
    char exe[4096] = {0};
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) == 0) {
        char bundle[4096];
        strlcpy(bundle, exe, sizeof(bundle));
        for (int i = 0; i < 3; i++) {
            char *slash = strrchr(bundle, '/');
            if (slash != NULL) {
                *slash = '\0';
            }
        }
        setenv("WHISPERME_APP_BUNDLE", bundle, 1);
    }

    /* Finder/login-item launches get a minimal PATH. */
    const char *path = getenv("PATH");
    char newpath[8192];
    snprintf(newpath, sizeof(newpath),
             "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:%s",
             path != NULL ? path : "");
    setenv("PATH", newpath, 1);

    char *child_argv[] = {
        (char *)UV_PATH, "run", "--directory", (char *)REPO_DIR, "whisperme", NULL,
    };
    int rc = posix_spawn(&child_pid, UV_PATH, NULL, NULL, child_argv, environ);
    if (rc != 0) {
        fprintf(stderr, "whisperme launcher: posix_spawn(%s) failed: %s\n",
                UV_PATH, strerror(rc));
        system("/usr/bin/osascript -e 'display alert \"WhisperMe cannot start\" "
               "message \"The uv tool or the whisperme checkout was not found. "
               "Re-run scripts/install.sh from the whisperme checkout.\" as critical'");
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
