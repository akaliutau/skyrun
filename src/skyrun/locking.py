"""Operating-system locks for cooperating clients on a local project filesystem."""

import errno
import os
import time
from contextlib import contextmanager

from skyrun.paths import PROJECT_LOCK_NAME, checked_root, inside


def _lock_windows(fd):
    import msvcrt

    while True:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.05)


@contextmanager
def project_lock(project):
    """Hold a non-reentrant project lock during a snapshot or complete sync apply.

    POSIX clients lock the directory inode, preserving read-only source support.
    Windows clients lock a persistent sidecar; deleting it would split the lock
    between different inodes. The kernel releases either lock on process exit.
    This coordinates local clients, not writers on a remote Modal Volume.
    """
    project = checked_root(project)
    project.mkdir(parents=True, exist_ok=True)
    windows = os.name == "nt"
    if windows:
        lock_path = inside(project, PROJECT_LOCK_NAME, allow_hidden=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
    else:
        fd = os.open(
            project, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
    try:
        if windows:
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            _lock_windows(fd)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if windows:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
