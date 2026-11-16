// GoAwayBCA's macOS 10.7 timestamp adapters for the bundled extractor.
// Keep file/symlink selection and omitted timestamps; old APIs retain microseconds.
#ifndef GoAwayBCA_LEGACY_TIME_H
#define GoAwayBCA_LEGACY_TIME_H
#if defined(__APPLE__) && __ENVIRONMENT_MAC_OS_X_VERSION_MIN_REQUIRED__ < 101300
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/time.h>

static inline bool GoAwayBCATimeValues(
    const struct timespec times[2], const struct stat &previous, struct timeval values[2])
{
  struct timeval now;
  bool haveNow = false;
  for (unsigned i = 0; i < 2; ++i)
  {
    struct timespec value = times[i];
    if (value.tv_nsec == UTIME_OMIT)
      value = i == 0 ? previous.st_atimespec : previous.st_mtimespec;
    else if (value.tv_nsec == UTIME_NOW)
    {
      if (!haveNow && gettimeofday(&now, NULL) != 0)
        return false;
      haveNow = true;
      values[i] = now;
      continue;
    }
    else if (value.tv_nsec < 0 || value.tv_nsec >= 1000000000L)
    {
      errno = EINVAL;
      return false;
    }
    values[i].tv_sec = value.tv_sec;
    values[i].tv_usec = static_cast<suseconds_t>(value.tv_nsec / 1000);
  }
  return true;
}

static inline int GoAwayBCAFutimens(int fd, const struct timespec times[2])
{
  if (!times)
    return futimes(fd, NULL);
  struct stat previous;
  if (fstat(fd, &previous) != 0)
    return -1;
  if (times[0].tv_nsec == UTIME_OMIT && times[1].tv_nsec == UTIME_OMIT)
    return 0;
  struct timeval values[2];
  if (!GoAwayBCATimeValues(times, previous, values))
    return -1;
  return futimes(fd, values);
}

static inline int GoAwayBCAPathTimes(
    const char *path, const struct timespec times[2], int flags)
{
  if (flags != 0 && flags != AT_SYMLINK_NOFOLLOW)
  {
    errno = EINVAL;
    return -1;
  }
  const bool noFollow = flags == AT_SYMLINK_NOFOLLOW;
  if (!times)
    return noFollow ? lutimes(path, NULL) : utimes(path, NULL);
  struct stat previous;
  if ((noFollow ? lstat(path, &previous) : stat(path, &previous)) != 0)
    return -1;
  if (times[0].tv_nsec == UTIME_OMIT && times[1].tv_nsec == UTIME_OMIT)
    return 0;
  struct timeval values[2];
  if (!GoAwayBCATimeValues(times, previous, values))
    return -1;
  return noFollow ? lutimes(path, values) : utimes(path, values);
}
#endif
#endif
