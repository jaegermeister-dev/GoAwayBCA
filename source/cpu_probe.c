/* Read-only x86_64 CPU evidence. No privileged instructions or OS changes.
 * CPUID bit definitions: Intel SDM, CPUID instruction reference.
 * PREFETCHW is tested in a separate short-lived process because accepting
 * its encoding is not equivalent to advertising a functional prefetch hint.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>

#if !defined(__x86_64__)
#error This helper is for the Intel x86_64 macOS application only.
#endif

static void cpuid(uint32_t leaf, uint32_t out[4]) {
    __asm__ volatile("cpuid" : "=a"(out[0]), "=b"(out[1]), "=c"(out[2]), "=d"(out[3])
                     : "a"(leaf), "c"(0));
}

static void json_string(const char *text) {
    const unsigned char *p = (const unsigned char *)text;
    putchar('"');
    for (; *p; ++p) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p < 32 || *p > 126) printf("\\u%04x", (unsigned int)*p);
        else putchar(*p);
    }
    putchar('"');
}

static void invalid_instruction(int signal_number) {
    (void)signal_number;
    _exit(77);
}

static int prefetch_test(void) {
    struct sigaction action;
    unsigned char scratch[128] = {0};
    memset(&action, 0, sizeof(action));
    action.sa_handler = invalid_instruction;
    if (sigemptyset(&action.sa_mask) || sigaction(SIGILL, &action, NULL)) return 78;
    /* 0f 0d /1 with a real readable/writable address. This is a cache hint;
       it does not modify scratch or any files, devices, firmware or settings. */
    __asm__ volatile("prefetchw (%0)" : : "r"(scratch) : "memory");
    return 0;
}

int main(int argc, char **argv) {
    uint32_t leaf0[4], leaf1[4] = {0}, ext0[4], ext1[4] = {0}, data[4];
    char vendor[13] = {0}, brand[49] = {0};
    unsigned int i;
    if (argc == 2 && strcmp(argv[1], "--prefetchw") == 0) return prefetch_test();
    if (argc != 1) return 64;
    cpuid(0, leaf0);
    memcpy(vendor, &leaf0[1], 4); memcpy(vendor + 4, &leaf0[3], 4); memcpy(vendor + 8, &leaf0[2], 4);
    if (leaf0[0] >= 1) cpuid(1, leaf1);
    cpuid(0x80000000u, ext0);
    if (ext0[0] >= 0x80000001u) cpuid(0x80000001u, ext1);
    if (ext0[0] >= 0x80000004u) {
        for (i = 0; i < 3; ++i) {
            cpuid(0x80000002u + i, data);
            memcpy(brand + i * 16, data, 16);
        }
    }
    printf("{\"schema\":1,\"vendor\":"); json_string(vendor);
    printf(",\"brand\":"); json_string(brand);
    printf(",\"max_basic\":%u,\"max_extended\":%u,", leaf0[0], ext0[0]);
    printf("\"leaf1\":[%u,%u,%u,%u],\"extended1\":[%u,%u,%u,%u]}\n",
           leaf1[0], leaf1[1], leaf1[2], leaf1[3], ext1[0], ext1[1], ext1[2], ext1[3]);
    return ferror(stdout) ? 74 : 0;
}
