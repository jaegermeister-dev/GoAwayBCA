/* Per-launch notification pipe: Darwin shutdown() on an unconnected listening
 * socket does not reliably wake select(). Never use a later run's descriptor. */
#ifndef NMB_AUTHORIZATION_CHANNEL_H
#define NMB_AUTHORIZATION_CHANNEL_H
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/select.h>
#include <mach/mach_time.h>

enum { NMBAuthError=-1, NMBAuthTimeout=0, NMBAuthConnected=1,
       NMBAuthCancelled=2, NMBAuthFailed=3 };

static int NMBMakeAuthorizationChannel(int channel[2]) {
    if (pipe(channel)) return -1;
    if (fcntl(channel[0],F_SETFD,FD_CLOEXEC) || fcntl(channel[1],F_SETFD,FD_CLOEXEC)) {
        close(channel[0]); close(channel[1]); channel[0]=channel[1]=-1; return -1;
    }
    return 0;
}
static void NMBFinishAuthorizationChannel(int ownedWriter, char outcome) {
    ssize_t result;
    do { result=write(ownedWriter,&outcome,1); } while (result<0 && errno==EINTR);
    /* EPIPE is expected if the worker already connected/finished. SIGPIPE is
     * ignored by main. This descriptor belongs only to this authorization. */
    close(ownedWriter);
}
static double NMBMonotonicSeconds(void) {
    mach_timebase_info_data_t scale; mach_timebase_info(&scale);
    return (double)mach_absolute_time()*scale.numer/scale.denom/1e9;
}
static int NMBWaitForAdministrator(int listener, int notification, double timeout) {
    if (listener<0 || notification<0 || listener>=FD_SETSIZE || notification>=FD_SETSIZE)
        return NMBAuthError;
    double deadline=NMBMonotonicSeconds()+timeout;
    for (;;) {
        double remaining=deadline-NMBMonotonicSeconds();
        if (remaining<=0) return NMBAuthTimeout;
        struct timeval wait; wait.tv_sec=(long)remaining;
        wait.tv_usec=(int)((remaining-wait.tv_sec)*1e6);
        fd_set readers; FD_ZERO(&readers); FD_SET(listener,&readers); FD_SET(notification,&readers);
        int result=select((listener>notification?listener:notification)+1,&readers,NULL,NULL,&wait);
        if (result<0 && errno==EINTR) continue;
        if (result<0) return NMBAuthError;
        if (!result) return NMBAuthTimeout;
        if (FD_ISSET(notification,&readers)) {
            char outcome=0; ssize_t count;
            do { count=read(notification,&outcome,1); } while (count<0 && errno==EINTR);
            return count==1 && outcome=='C' ? NMBAuthCancelled : NMBAuthFailed;
        }
        if (FD_ISSET(listener,&readers)) return NMBAuthConnected;
    }
}
#endif
