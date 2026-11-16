/* Test-only process-local interposer: emulate an absent newer timestamp API. */
#include <dlfcn.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

static void record(const char *event) {
    const char *path = getenv("MWM_TIMESTAMP_PROBE_LOG");
    if (!path || !*path) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd >= 0) { (void)write(fd, event, strlen(event)); close(fd); }
}

static void *missing_utimensat(void *handle, const char *name) {
    if (strcmp(name, "utimensat") == 0) {
        record("missing-utimensat\n");
        return NULL;
    }
    return dlsym(handle, name);
}

static int observed_utimes(const char *path, const struct timeval times[2]) {
    record("utimes-fallback\n");
    return utimes(path, times);
}

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interposers[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)missing_utimensat, (const void *)dlsym},
    {(const void *)observed_utimes, (const void *)utimes}
};
