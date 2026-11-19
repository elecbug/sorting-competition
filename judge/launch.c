#define _GNU_SOURCE
#include <errno.h>
#include <grp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

static void limit(int resource, rlim_t soft, rlim_t hard) {
    struct rlimit value = {soft, hard};
    if (setrlimit(resource, &value)) _exit(125);
}

/* Only this root-owned native parent reports metrics on FD 3. The measured
 * child forks AFTER the Python address space has been replaced by exec.
 * The candidate closes FD 3, then drops UID before executing student code. */
int main(int argc, char **argv) {
    if (argc < 8) return 125;
    if (setsid() < 0) return 125;
    pid_t child = fork();
    if (child < 0) return 125;
    if (child > 0) {
        int status;
        struct rusage usage;
        while (wait4(child, &status, 0, &usage) < 0) {
            if (errno != EINTR) return 125;
        }
        long long micros = (long long)usage.ru_utime.tv_sec * 1000000 + usage.ru_utime.tv_usec
            + (long long)usage.ru_stime.tv_sec * 1000000 + usage.ru_stime.tv_usec;
        int code = WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
        if (dprintf(3, "%lld %ld %d\n", micros, usage.ru_maxrss, code) < 0) return 125;
        return 0;
    }
    close(3);
    rlim_t cpu = strtoull(argv[1], NULL, 10);
    limit(RLIMIT_CORE, 0, 0);
    limit(RLIMIT_CPU, cpu, cpu + 1);
    rlim_t memory = strtoull(argv[2], NULL, 10);
    limit(RLIMIT_AS, memory, memory);
    rlim_t output = strtoull(argv[3], NULL, 10);
    limit(RLIMIT_FSIZE, output, output);
    limit(RLIMIT_NOFILE, 64, 64);
    int uid = atoi(argv[5]);
    rlim_t processes = strtoull(argv[4], NULL, 10);
    if (uid >= 0 && processes) limit(RLIMIT_NPROC, processes, processes);
    if (chdir(argv[6])) return 125;
    if (uid >= 0 && (setgroups(0, NULL) || setgid((gid_t)uid) || setuid((uid_t)uid))) return 125;
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) return 125;
    execv(argv[7], &argv[7]);
    return 125;
}
