"""What a child does to itself between fork and running untrusted code. Runs as root, ends unprivileged.

Order matters: namespaces and mounts need privileges, so they come first; rlimits are set before dropping
the uid (RLIMIT_NPROC is per uid); no_new_privs and the uid drop come last.
"""

import ctypes
import os
import resource
from dataclasses import dataclass

from crv.domain.policy import ResourcePolicy

_MS_NOSUID = 0x2
_MS_NODEV = 0x4
_MS_REC = 0x4000
_MS_PRIVATE = 1 << 18
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36

_libc = ctypes.CDLL(None, use_errno=True)


@dataclass(frozen=True)
class Isolation:
    """What this host lets us do. Without root the sandbox degrades to rlimits only, and says so."""

    network: bool  # the zygote is in its own (empty) network namespace
    uid_drop: bool
    private_tmp: bool

    @property
    def complete(self) -> bool:
        return self.network and self.uid_drop and self.private_tmp


def is_root() -> bool:
    return os.geteuid() == 0


def isolate_network() -> bool:
    """Called once in the zygote: every child it forks inherits an empty network namespace."""
    if not is_root():
        return False
    try:
        os.unshare(os.CLONE_NEWNET)
        return True
    except OSError:
        return False


def _mount(source: str, target: str, fstype: str | None, flags: int, data: str | None) -> None:
    rc = _libc.mount(
        source.encode(),
        target.encode(),
        fstype.encode() if fstype else None,
        flags,
        data.encode() if data else None,
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"mount {target}: {os.strerror(err)}")


def private_tmp(size_mb: int) -> bool:
    """Fresh tmpfs on /tmp, /var/tmp and /dev/shm in a private mount namespace: nothing a task writes is seen by
    the next one, so rollouts cannot pass answers to each other through the file system."""
    if not is_root():
        return False
    os.unshare(os.CLONE_NEWNS)
    _mount("none", "/", None, _MS_REC | _MS_PRIVATE, None)
    for target in ("/tmp", "/var/tmp", "/dev/shm"):
        if os.path.isdir(target):
            _mount("tmpfs", target, "tmpfs", _MS_NOSUID | _MS_NODEV, f"size={size_mb}m,mode=1777")
    return True


def set_limits(policy: ResourcePolicy, file_mb: int, open_files: int) -> None:
    mem = policy.memory_mb * 1024 * 1024
    cpu = max(1, int(policy.cpu_s + 0.999))
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))  # SIGXCPU at the soft limit, SIGKILL one second later
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_mb * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (open_files, open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if is_root():
        resource.setrlimit(resource.RLIMIT_NPROC, (policy.max_processes, policy.max_processes))


def drop_privileges(uid: int) -> bool:
    if not is_root():
        return False
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)
    return True


def no_new_privileges() -> None:
    _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)


def die_with_parent() -> None:
    _libc.prctl(_PR_SET_PDEATHSIG, 9, 0, 0, 0)


def become_subreaper() -> None:
    """Orphaned descendants (a daemonised grandchild) are re-parented to the zygote instead of PID 1. Container
    init processes often never reap, and zombies still count against RLIMIT_NPROC for their uid."""
    _libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)


def reap_all() -> None:
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def scrub_environment() -> None:
    os.environ.clear()
    os.environ.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"})


def count_processes(uid: int) -> int:
    """Live (non-zombie) processes owned by uid."""
    n = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status") as f:
                zombie = False
                for line in f:
                    if line.startswith("State:"):
                        zombie = line.split()[1] in ("Z", "X")
                    elif line.startswith("Uid:"):
                        if int(line.split()[1]) == uid and not zombie:
                            n += 1
                        break
        except OSError:
            continue
    return n
