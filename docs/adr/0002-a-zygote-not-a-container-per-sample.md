# 2. A clean zygote forks each sample, instead of a container per sample

**Status:** accepted

## Context

eval-framework runs each sample through llm-sandbox: code is copied into a pooled Docker container and run with
`docker exec`. On the benchmark host that costs about 0.13 s per sample once the pool is warm, plus about 80 s to
build the pool. Pooled containers are also reused across samples, so files a sample leaves in `/tmp` are visible
to the next one.

## Decision

Each worker owns a zygote: a separate, pre-warmed Python process with no network namespace and no task data.
- It forks one child per sample. The child enters a private mount namespace with fresh tmpfs mounts, sets
  rlimits, drops to the worker slot's own uid, sets `no_new_privs` and scrubs its environment.
- Sources and tests reach the child over its own socket after the fork, so the zygote never holds anything a
  later child could find in memory.
- After each sample, every process of that uid is killed. The zygote is a subreaper, so zombies are reaped too.

## Consequences

- Fork plus hardening costs milliseconds instead of a `docker exec`.
- Nothing carries over between samples: there is a test for that.
- The service needs SYS_ADMIN, SETUID, SETGID and KILL in its pod. The Kubernetes manifest drops everything else
  and adds an egress-deny NetworkPolicy as a second wall.
- This is process isolation on a shared kernel, not a VM. For untrusted code from outside the company, run the
  pods under gVisor or Kata (`runtimeClassName`).
