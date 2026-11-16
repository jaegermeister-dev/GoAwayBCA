// Private acceptance fixture; this library is never packaged in the app.
#include "sevenzip_legacy_time.h"
extern "C" int mw_time_probe(const char *, int, int, long, long, long, long, int);
extern "C" long mw_time_omit();
extern "C" long mw_time_now();
extern "C" long mw_time_omit() { return UTIME_OMIT; }
extern "C" long mw_time_now() { return UTIME_NOW; }
extern "C" int mw_time_probe(const char *path, int fd, int mode,
    long asec, long ansec, long msec, long mnsec, int useNull)
{
  const struct timespec times[2] = {{asec, ansec}, {msec, mnsec}};
  const struct timespec *arg = useNull ? NULL : times;
  if (mode == 2)
    return GoAwayBCAFutimens(fd, arg);
  const int flags = mode == 0 ? 0 : mode == 1 ? AT_SYMLINK_NOFOLLOW : -1;
  return GoAwayBCAPathTimes(path, arg, flags);
}
