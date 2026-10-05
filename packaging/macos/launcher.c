/* Main executable of 日食計算機.app: runs Contents/Resources/launch.sh with /bin/sh.
 *
 * Apple's notarization requires the main executable of an app to be signed Mach-O code,
 * so this stub is compiled by tools/build_bundles.py (zig cc, arm64 + x86_64) and the
 * actual start-up logic lives in the shell script next to the tool in Resources/. */
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char exe[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        return 1;
    }
    char *slash = strrchr(real, '/'); /* .../日食計算機.app/Contents/MacOS/launcher */
    if (slash == NULL) {
        return 1;
    }
    *slash = '\0';
    snprintf(script, sizeof script, "%s/../Resources/launch.sh", real);
    execl("/bin/sh", "sh", script, (char *)NULL);
    perror("launch.sh");
    return 1;
}
