"""Bounded native admission for the managed-worktree lifecycle owner.

This module never selects worktrees or grants release. The caller owns policy,
generation, merge proof and durable intent; errors mean retain, not absence.
"""

import contextlib
import ctypes as C
import errno
import functools
import hashlib
import os
from pathlib import Path
import platform
import selectors
import signal
import stat
import struct
import subprocess
import time


class Retain(RuntimeError):
    pass


LEAF_LIMIT = 8 * 1024 * 1024
PASS_LIMIT = 512 * 1024 * 1024
ENTRY_LIMIT = 131072
DEADLINE = float("inf")


def tick(until):
    if time.monotonic() >= min(until, DEADLINE):
        raise Retain("admission-deadline")


def file_identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns, getattr(s, "st_flags", 0))


@functools.lru_cache(maxsize=1)
def xattr_reader():
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        raise Retain("xattr-namespace-unqualified-platform")
    lib = C.CDLL("/usr/lib/libSystem.B.dylib" if system == "Darwin" else None, use_errno=True)
    fn = lib.listxattr if system == "Darwin" else lib.llistxattr
    fn.argtypes = [C.c_char_p, C.c_void_p, C.c_size_t] + ([C.c_int] if system == "Darwin" else [])
    fn.restype = C.c_ssize_t
    # NOFOLLOW | SHOWCOMPRESSION: the default Darwin list hides compression
    # attributes, which must not escape recovery-data admission.
    return fn, (0x21,) if system == "Darwin" else ()


def disposable_metadata(path, until):
    tick(until)
    buffer = C.create_string_buffer(16384)
    fn, options = xattr_reader()
    C.set_errno(0)
    size = fn(os.fsencode(path), buffer, C.sizeof(buffer), *options)
    if size < 0 or size > C.sizeof(buffer) or C.get_errno():
        raise Retain("xattr-names-unavailable-or-limit")
    raw = buffer.raw[:size]
    if raw and not raw.endswith(b"\0"):
        raise Retain("xattr-name-list-invalid")
    names = raw[:-1].split(b"\0") if raw else []
    # Native Git creates this OS tracking attribute on fresh checkout/admin
    # files. Every other name may own recovery data; values remain unread.
    if len(names) != len(set(names)) or any(name != b"com.apple.provenance" for name in names):
        raise Retain("unclassified-extended-attributes")
    return [os.fsdecode(name) for name in sorted(names)]


def read_file(path, until, limit=LEAF_LIMIT):
    tick(until)
    named = os.lstat(path)
    if not stat.S_ISREG(named.st_mode) or named.st_size > limit:
        raise Retain("file-type-or-size-unsupported")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        held = os.fstat(fd)
        if file_identity(held) != file_identity(named):
            raise Retain("file-replaced")
        data = bytearray()
        while True:
            tick(until)
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise Retain("file-size-limit")
        if (file_identity(os.fstat(fd)) != file_identity(held)
                or file_identity(os.lstat(path)) != file_identity(held)):
            raise Retain("file-changed")
        return bytes(data), file_identity(held)
    finally:
        os.close(fd)


def supervise(command, *, cwd, env, timeout=30, limit=1024 * 1024, input_data=None):
    """Own one process group; bounded failure cleanup never signals other groups."""
    child = selector = None
    output = [bytearray(), bytearray()]
    started = time.monotonic()
    until = min(started + timeout, DEADLINE)
    proof = {"pid": None, "started_monotonic": started, "reaped": False}
    failure = None
    try:
        tick(until)
        if input_data is not None and (not isinstance(input_data, bytes) or len(input_data) > LEAF_LIMIT):
            raise Retain("child-input-limit")
        child = subprocess.Popen(command, cwd=cwd, env=env,
                                 stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        proof["pid"] = child.pid
        selector = selectors.DefaultSelector()
        for i, stream in enumerate((child.stdout, child.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, i)
        sent = 0
        if input_data is not None:
            os.set_blocking(child.stdin.fileno(), False)
            selector.register(child.stdin, selectors.EVENT_WRITE, "input")
        while selector.get_map():
            tick(until)
            for key, _ in selector.select(min(0.1, max(0, until - time.monotonic()))):
                if key.data == "input":
                    if sent < len(input_data):
                        try:
                            sent += os.write(key.fd, input_data[sent:sent + 65536])
                        except BlockingIOError:
                            continue
                    if sent == len(input_data):
                        selector.unregister(key.fileobj)
                        child.stdin.close()
                    continue
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    output[key.data].extend(chunk)
                    if sum(map(len, output)) > limit:
                        raise Retain("child-output-limit")
        child.wait(timeout=max(0.001, until - time.monotonic()))
    except Exception as error:
        failure = error
    finally:
        if selector is not None:
            try:
                selector.close()
            except Exception as error:
                failure = failure or error
        if child is not None:
            # A child may exit while one of its descendants retains the pipes.
            # The fresh process group belongs to this invocation only.
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    failure = failure or error
            try:
                child.wait(timeout=2)
                proof["reaped"] = True
            except subprocess.TimeoutExpired as error:
                failure = failure or error
            for stream in (child.stdin, child.stdout, child.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError as error:
                    failure = failure or error
            try:
                os.killpg(child.pid, 0)
                proof["group_absent"] = False
                failure = failure or Retain("owned-child-group-survives")
            except ProcessLookupError:
                proof["group_absent"] = True
            except OSError as error:
                proof["group_absent"] = None
                failure = failure or error
            proof["returncode"] = child.returncode
        proof.update(ended_monotonic=time.monotonic(),
                     stdout_sha256=hashlib.sha256(output[0]).hexdigest(),
                     stderr_sha256=hashlib.sha256(output[1]).hexdigest())
    result = subprocess.CompletedProcess(command, proof.get("returncode"),
                                         bytes(output[0]), bytes(output[1]))
    result.proof = proof
    result.failure = type(failure).__name__ if failure else None
    return result


def index_entries(data):
    """Admit ordinary SHA-1 index v2/v3 before ANY native index consumer."""
    if (len(data) < 32 or data[:4] != b"DIRC"
            or hashlib.sha1(data[:-20]).digest() != data[-20:]):
        raise Retain("raw-index-invalid")
    version, count = struct.unpack_from(">II", data, 4)
    if version not in (2, 3) or count > ENTRY_LIMIT:
        raise Retain("raw-index-format-unsupported")
    result, cursor, previous = {}, 12, b""
    for _ in range(count):
        start = cursor
        if cursor + 62 > len(data) - 20:
            raise Retain("raw-index-truncated")
        mode = struct.unpack_from(">I", data, cursor + 24)[0]
        oid = data[cursor + 40:cursor + 60].hex()
        flags = struct.unpack_from(">H", data, cursor + 60)[0]
        cursor += 62
        # Ordinary non-cone sparse checkout keeps a full v3 index with only
        # SKIP_WORKTREE. Present skipped files still receive full byte checks.
        if flags & 0xB000 or mode not in (0o100644, 0o100755, 0o120000):
            raise Retain("raw-index-flags-or-mode-unsupported")
        skipped = False
        if flags & 0x4000:
            if version != 3 or cursor + 2 > len(data) - 20:
                raise Retain("raw-index-extended-invalid")
            extended = struct.unpack_from(">H", data, cursor)[0]
            cursor += 2
            if extended & ~0x4000:
                raise Retain("raw-index-flags-or-mode-unsupported")
            skipped = bool(extended & 0x4000)
        end = data.find(b"\0", cursor, len(data) - 20)
        if end < 0:
            raise Retain("raw-index-name-invalid")
        raw_name = data[cursor:end]
        name = os.fsdecode(raw_name)
        if (not name or raw_name <= previous or name.startswith("/") or any(p in ("", ".", "..", ".git")
                for p in name.split("/")) or name in result
                or min(end - cursor, 0xFFF) != (flags & 0xFFF)):
            raise Retain("raw-index-name-invalid")
        cursor = start + ((end + 1 - start + 7) // 8) * 8
        if data[end:cursor] != b"\0" * (cursor - end):
            raise Retain("raw-index-padding-invalid")
        result[name] = (mode, oid, skipped)
        previous = raw_name
    while cursor < len(data) - 20:
        if cursor + 8 > len(data) - 20:
            raise Retain("raw-index-extension-invalid")
        kind = data[cursor:cursor + 4]
        size = struct.unpack_from(">I", data, cursor + 4)[0]
        cursor += 8 + size
        if kind != b"TREE" or cursor > len(data) - 20:
            raise Retain("raw-index-extension-unsupported")
    return result


def tree_entries(raw):
    result = {}
    for item in raw.split("\0"):
        if not item:
            continue
        metadata, name = item.split("\t", 1)
        mode, kind, oid = metadata.split()
        if kind != "blob" or int(mode, 8) not in (0o100644, 0o100755, 0o120000):
            raise Retain("tree-mode-unsupported")
        result[name] = (int(mode, 8), oid)
    return result


def inventory(snap, git, until, *, discard_ignored=()):
    index, index_id = read_file(Path(snap["gitdir"]) / "index", until)
    entries = index_entries(index)
    head = tree_entries(git(snap["path"], "ls-tree", "-rz", "--full-tree", "HEAD"))
    if any(name == root or name.startswith(root + "/") for name in head for root in discard_ignored):
        raise Retain("discarded-artifact-overlaps-tracked-source")
    if {name: entry[:2] for name, entry in entries.items()} != head:
        raise Retain("staged-or-index-only-changes")
    expected_dirs = {""}
    for name in head:
        expected_dirs.update(str(p) for p in Path(name).parents if str(p) != ".")
    rows = {"": [*file_identity(Path(snap["path"]).lstat()),
                  disposable_metadata(snap["path"], until)]}
    used, dependency = 0, None
    discarded_links = {}
    stack = [Path(snap["path"])]
    while stack:
        directory = stack.pop()
        tick(until)
        before = directory.lstat()
        if before.st_dev != snap["path_id"][0]:
            raise Retain("nested-mount")
        children = []
        with os.scandir(directory) as scan:
            for child in scan:
                tick(until)
                if len(rows) + len(children) >= ENTRY_LIMIT:
                    raise Retain("inventory-entry-limit")
                children.append(child)
        children.sort(key=lambda entry: entry.name)
        for child in children:
            tick(until)
            path = Path(child.path)
            name = str(path.relative_to(snap["path"]))
            details = path.lstat()
            discarded = any(name == root or name.startswith(root + "/") for root in discard_ignored)
            rows[name] = [*file_identity(details), [] if discarded else disposable_metadata(path, until)]
            if discarded:
                # Finalized task artifacts are disposable, but their symlink
                # targets and any nested repository or mount are not ours.
                if ".git" in Path(name).parts or details.st_dev != snap["path_id"][0]:
                    raise Retain("discarded-artifact-contains-repository-or-mount")
                if stat.S_ISDIR(details.st_mode):
                    stack.append(path)
                elif stat.S_ISLNK(details.st_mode):
                    destination = str(path.resolve())
                    if not (destination == snap["path"] or destination.startswith(snap["path"] + "/")):
                        discarded_links[destination] = (
                            list(file_identity(os.stat(destination))[:2])
                            if os.path.exists(destination) else None)
                elif not stat.S_ISREG(details.st_mode):
                    raise Retain("discarded-artifact-has-live-or-unknown-file-type")
                continue
            if name == ".git":
                data, _ = read_file(path, until, 4096)
                if data != ("gitdir: " + snap["gitdir"] + "\n").encode():
                    raise Retain("worktree-pointer-changed")
            elif stat.S_ISDIR(details.st_mode):
                if name not in expected_dirs:
                    raise Retain("unclassified-ignored-directory")
                stack.append(path)
            elif name not in head:
                if name != "node_modules" or not stat.S_ISLNK(details.st_mode):
                    raise Retain("unclassified-untracked-or-ignored-content")
                destination = str(path.resolve(strict=True))
                if destination == snap["path"] or destination.startswith(snap["path"] + "/"):
                    raise Retain("dependency-donor-inside-checkout")
                donor = Path(destination).stat()
                if not stat.S_ISDIR(donor.st_mode) or donor.st_uid != os.getuid():
                    raise Retain("dependency-donor-is-not-directory")
                dependency = {"link": os.readlink(path), "destination": destination,
                              "identity": [donor.st_dev, donor.st_ino]}
            else:
                mode, oid = head[name]
                if stat.S_ISLNK(details.st_mode) and mode == 0o120000:
                    data = os.fsencode(os.readlink(path))
                elif stat.S_ISREG(details.st_mode) and mode in (0o100644, 0o100755):
                    if bool(details.st_mode & 0o111) != (mode == 0o100755):
                        raise Retain("working-mode-changed")
                    data, _ = read_file(path, until, min(LEAF_LIMIT, PASS_LIMIT - used))
                else:
                    raise Retain("working-type-changed")
                used += len(data)
                if used > PASS_LIMIT or hashlib.sha1(
                        b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != oid:
                    raise Retain("working-bytes-changed-or-limit")
        if file_identity(directory.lstat()) != file_identity(before):
            raise Retain("inventory-directory-changed")
    if not {name for name, entry in entries.items() if not entry[2]}.issubset(rows):
        raise Retain("working-file-missing")
    result = {"index": list(index_id), "index_sha256": hashlib.sha256(index).hexdigest(),
              "entries": rows, "dependency": dependency}
    if discard_ignored:
        result["discarded_links"] = discarded_links
    return result


def fields(kind, names):
    return [(name, kind) for name in names.split()]


# Darwin proc_info.h ABI. Full structures keep vnode identity available even
# when the kernel's MAXPATHLEN path buffer cannot represent a complete path.
class BSD(C.Structure):
    _fields_ = fields(C.c_uint32, "flags status xstatus pid ppid uid gid ruid rgid svuid svgid reserved") + [
        ("comm", C.c_char * 16), ("name", C.c_char * 32),
    ] + fields(C.c_uint32, "nfiles pgid jobc tdev tpgid") + [
        ("nice", C.c_int32), ("start_sec", C.c_uint64), ("start_usec", C.c_uint64)]


class VStat(C.Structure):
    _fields_ = [("dev", C.c_uint32), ("mode", C.c_uint16), ("nlink", C.c_uint16),
                ("ino", C.c_uint64), ("uid", C.c_uint32), ("gid", C.c_uint32)] + fields(
        C.c_int64, "atime atimens mtime mtimens ctime ctimens birth birthns size blocks"
    ) + fields(C.c_uint32, "blksize flags gen rdev") + [("spare", C.c_int64 * 2)]


class VPath(C.Structure):
    _fields_ = [("stat", VStat), ("type", C.c_int32), ("pad", C.c_int32),
                ("fsid", C.c_int32 * 2), ("path", C.c_char * 1024)]


class CWD(C.Structure):
    _fields_ = [("cwd", VPath), ("root", VPath)]


class FD(C.Structure):
    _fields_ = [("number", C.c_uint32), ("kind", C.c_uint32)]


class FDPath(C.Structure):
    _fields_ = [("openflags", C.c_uint32), ("status", C.c_uint32),
                ("offset", C.c_int64), ("type", C.c_int32), ("guard", C.c_uint32),
                ("vnode", VPath)]


class ThreadPath(C.Structure):
    _fields_ = [("times", C.c_uint64 * 2), ("scheduling", C.c_int32 * 8),
                ("name", C.c_char * 64), ("vnode", VPath)]


class Region(C.Structure):
    _fields_ = fields(C.c_uint32, "protection max_protection inheritance flags") + [
        ("offset", C.c_uint64), ("counters", C.c_uint32 * 14),
        ("address", C.c_uint64), ("size", C.c_uint64), ("vnode", VPath)]


def platform_key():
    return {"system": platform.system(), "release": platform.release(),
            "machine": platform.machine()}


class Darwin:
    """Same real/effective UID coverage; selected errors never become absence."""
    def __init__(self, until):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise Retain("holder-backend-unqualified-platform")
        if [C.sizeof(t) for t in (BSD, VStat, VPath, CWD, FDPath, ThreadPath, Region)] != [
                136, 136, 1176, 2352, 1200, 1288, 1272]:
            raise Retain("holder-native-ABI-mismatch")
        self.until = until
        self.lib = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        for name in ("proc_listpids", "proc_pidinfo", "proc_pidfdinfo", "proc_pidfileportinfo"):
            fn = getattr(self.lib, name)
            fn.restype = C.c_int
        self.lib.proc_listpids.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.c_int]
        self.lib.proc_pidinfo.argtypes = [C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int]
        self.lib.proc_pidfdinfo.argtypes = [C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_int]
        self.lib.proc_pidfileportinfo.argtypes = [C.c_int, C.c_uint32, C.c_int, C.c_void_p, C.c_int]
        self.calls = 0

    def call(self, fn, *args):
        tick(self.until)
        self.calls += 1
        if self.calls > 200000:
            raise Retain("holder-native-call-limit")
        C.set_errno(0)
        count = fn(*args)
        return count, C.get_errno()

    def info(self, pid, flavor, kind, arg=0, *, end=False):
        value = kind()
        count, error = self.call(self.lib.proc_pidinfo, pid, flavor, arg,
                                 C.byref(value), C.sizeof(value))
        # REGIONPATHINFO returns EINVAL at the address-space end. ESRCH is
        # process churn, never a successful end-of-regions observation.
        if end and count == 0 and error == errno.EINVAL:
            return None
        if count != C.sizeof(value) or error:
            raise Retain(f"holder-visibility-unknown:pid={pid}:flavor={flavor}:errno={error}")
        return value

    def array(self, pid, flavor, kind, cap):
        array = (kind * (cap + 1))()
        count, error = self.call(self.lib.proc_pidinfo, pid, flavor, 0,
                                 C.byref(array), C.sizeof(array))
        if error or count < 0 or count % C.sizeof(kind) or count >= C.sizeof(array):
            raise Retain(f"holder-list-unknown:pid={pid}:flavor={flavor}:errno={error}")
        return list(array[:count // C.sizeof(kind)])

    def population(self):
        result = set()
        for flavor in (4, 5):  # PROC_UID_ONLY and PROC_RUID_ONLY, both required.
            array = (C.c_int * 4097)()
            count, error = self.call(self.lib.proc_listpids, flavor, os.getuid(),
                                     C.byref(array), C.sizeof(array))
            if error or count <= 0 or count % 4 or count >= C.sizeof(array):
                raise Retain("holder-process-population-unknown")
            result.update(pid for pid in array[:count // 4] if pid > 0)
        if os.getpid() not in result or len(result) > 4096:
            raise Retain("holder-process-population-incomplete")
        return result

    @staticmethod
    def birth(info):
        return [info.pid, info.start_sec, info.start_usec, info.uid, info.ruid]

    def references(self, pid, before):
        cwd = self.info(pid, 9, CWD)
        yield "cwd", cwd.cwd
        yield "root", cwd.root
        for flavor, method in ((1, self.lib.proc_pidfdinfo), (14, self.lib.proc_pidfileportinfo)):
            descriptors = self.array(pid, flavor, FD, 16384)
            for item in descriptors:
                if item.kind != 1:  # PROX_FDTYPE_VNODE, independent of access flags.
                    continue
                value = FDPath()
                count, error = self.call(method, pid, item.number, 2,
                                         C.byref(value), C.sizeof(value))
                if error or count != C.sizeof(value):
                    raise Retain(f"holder-fd-unknown:pid={pid}:errno={error}")
                yield "fd" if flavor == 1 else "fileport", value.vnode
            if [(d.number, d.kind) for d in descriptors] != [
                    (d.number, d.kind) for d in self.array(pid, flavor, FD, 16384)]:
                raise Retain("holder-descriptor-population-changed")
        if before.flags & 0x100:  # PROC_FLAG_THCWD
            threads = self.array(pid, 6, C.c_uint64, 8192)
            for thread in threads:
                yield "thread-cwd", self.info(pid, 10, ThreadPath, thread).vnode
            if threads != self.array(pid, 6, C.c_uint64, 8192):
                raise Retain("holder-thread-population-changed")
        address = 0
        for _ in range(65536):
            region = self.info(pid, 8, Region, address, end=True)
            if region is None:
                break
            yield "mapping", region.vnode
            next_address = region.address + region.size
            # XNU vm_map_region_synthesize_guard_object_hole can return an empty
            # guard at the next real entry. Requery that exact address, not past it.
            empty_guard = (region.size == 0 and region.address > address
                           and (region.protection, region.max_protection, region.inheritance,
                                region.flags, region.offset) == (0, 0, 2, 0, 0)  # VM_INHERIT_NONE
                           and list(region.counters) == [0, 0, 31, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0]
                           and not any(bytes(region.vnode)))  # VM_MEMORY_GUARD, SM_EMPTY; no vnode
            if (not region.size and not empty_guard) or next_address <= address or next_address >= 2**64:
                raise Retain(f"holder-region-iteration-invalid:pid={pid}:cursor={address}:"
                             f"address={region.address}:size={region.size}:flags={region.flags}:"
                             f"offset={region.offset}:user_tag={region.counters[2]}:"
                             f"share_mode={region.counters[9]}:depth={region.counters[13]}")
            address = next_address
        else:
            raise Retain("holder-region-limit")

    def observe(self, domains, inodes, known=()):
        initial = self.population()
        known = {tuple(row) for row in known}
        selected = initial | {row[0] for row in known}
        births, holders = {}, []
        for pid in sorted(selected):
            before = self.info(pid, 3, BSD)
            birth = self.birth(before)
            if pid != before.pid or (pid in initial and os.getuid() not in (before.uid, before.ruid)):
                raise Retain("holder-process-identity-changed")
            if any(row[0] == pid for row in known) and tuple(birth) not in known:
                raise Retain("known-holder-identity-changed")
            births[pid] = birth
            for kind, vnode in self.references(pid, before):
                key = (vnode.stat.dev, vnode.stat.ino)
                path = os.fsdecode(bytes(vnode.path))
                if key in inodes or any(path == root or path.startswith(root + "/") for root in domains):
                    holders.append({"identity": birth, "kind": kind})
            after = self.info(pid, 3, BSD)
            if self.birth(after) != birth or bool(after.flags & 0x100) != bool(before.flags & 0x100):
                raise Retain("holder-process-changed")
        if self.population() != initial:
            raise Retain("holder-process-population-changed")
        for pid, birth in births.items():
            if self.birth(self.info(pid, 3, BSD)) != birth:
                raise Retain("holder-process-changed")
        # libproc skips submap vnodes and can suppress vnode acquisition errors.
        # Observed references remain useful; empty output cannot clear mappings.
        return {"scope": "real-or-effective-uid-and-known-processes", "processes": len(births),
                "holders": holders, "mapping_coverage": "unqualified",
                "native_calls": self.calls, "platform": platform_key()}


def non_granting_acl(path, until):
    if platform.system() != "Darwin":
        raise Retain("native-access-boundary-unqualified")
    lib = C.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.acl_get_fd_np.argtypes = [C.c_int, C.c_int]
    lib.acl_get_fd_np.restype = C.c_void_p
    lib.acl_get_entry.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_void_p)]
    lib.acl_get_tag_type.argtypes = [C.c_void_p, C.POINTER(C.c_int)]
    lib.acl_to_text.argtypes = [C.c_void_p, C.POINTER(C.c_ssize_t)]
    lib.acl_to_text.restype = C.c_void_p
    lib.acl_free.argtypes = [C.c_void_p]
    named = os.lstat(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    acl = None
    try:
        if file_identity(os.fstat(fd)) != file_identity(named):
            raise Retain("native-acl-object-changed")
        C.set_errno(0)
        acl = lib.acl_get_fd_np(fd, 0x100)
        if not acl:
            if C.get_errno() == errno.ENOENT:
                return None
            raise Retain("native-acl-unavailable")
        entry = C.c_void_p()
        # Darwin returns 0 for an entry and -1/EINVAL at the end (unlike Linux).
        # DENY-only ACLs cannot grant access beyond the checked POSIX modes;
        # macOS uses one on ordinary home directories to deny deletion.
        for index in range(1025):
            tick(until)
            C.set_errno(0)
            result = lib.acl_get_entry(acl, 0 if index == 0 else -1, C.byref(entry))
            if result == -1 and C.get_errno() == errno.EINVAL:
                break
            if result != 0 or index == 1024:
                raise Retain("native-acl-unavailable")
            tag = C.c_int()
            if lib.acl_get_tag_type(entry, C.byref(tag)) != 0 or tag.value != 2:
                raise Retain("native-acl-grants-or-unknown")
        length = C.c_ssize_t()
        text = lib.acl_to_text(acl, C.byref(length))
        if not text:
            raise Retain("native-acl-unavailable")
        try:
            if not 8 <= length.value <= 65536 or C.string_at(text, 8) != b"!#acl 1\n":
                raise Retain("native-acl-flags-present")
            return hashlib.sha256(C.string_at(text, length.value)).hexdigest()
        finally:
            lib.acl_free(text)
    finally:
        if acl:
            lib.acl_free(acl)
        after = os.fstat(fd)
        os.close(fd)
        if file_identity(named) != file_identity(after) or file_identity(named) != file_identity(os.lstat(path)):
            raise Retain("native-acl-object-changed")


def access_boundary(path, until):
    """A private component protects each domain; shared system parents may be 0755."""
    path = Path(path)
    if not path.is_absolute() or str(path.resolve(strict=True)) != str(path):
        raise Retain("access-boundary-linked")
    result, private = [], False
    for current in reversed((path, *path.parents)):
        tick(until)
        s = current.lstat()
        if (not stat.S_ISDIR(s.st_mode) or s.st_uid not in (0, os.getuid())
                or s.st_mode & 0o022):
            # A root-owned sticky temporary parent is safe only ABOVE an owned
            # private boundary; fixtures and native /private/tmp use this shape.
            if not (s.st_uid == 0 and s.st_mode & stat.S_ISVTX and stat.S_ISDIR(s.st_mode)):
                raise Retain("access-boundary-writable")
        acl = non_granting_acl(current, until)
        private |= s.st_uid == os.getuid() and not s.st_mode & 0o077
        result.append([str(current), [s.st_dev, s.st_ino, s.st_mode, s.st_uid,
                                     s.st_gid, getattr(s, "st_flags", 0)], acl])
    if not private:
        raise Retain("owner-private-access-boundary-required")
    return result


def admin_inventory(snap, git, until):
    root = Path(snap["gitdir"])
    rows = {"": [[*file_identity(root.lstat()), disposable_metadata(root, until)], None]}
    used = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        tick(until)
        before = directory.lstat()
        entries = []
        with os.scandir(directory) as scan:
            for entry in scan:
                tick(until)
                if len(rows) + len(entries) >= 1024:
                    raise Retain("admin-lock-or-population-unsupported")
                entries.append(entry)
        for entry in entries:
            path = Path(entry.path)
            s = path.lstat()
            attrs = disposable_metadata(path, until)
            name = str(path.relative_to(root))
            if len(rows) >= 1024 or path.name.endswith(".lock") or s.st_dev != snap["gitdir_id"][0]:
                raise Retain("admin-lock-or-population-unsupported")
            if stat.S_ISDIR(s.st_mode):
                if name not in ("logs", "refs", "info"):
                    raise Retain("admin-recovery-directory-present")
                stack.append(path)
                rows[name] = [[*file_identity(s), attrs], None]
            elif stat.S_ISREG(s.st_mode) and name in (
                    "HEAD", "index", "commondir", "gitdir", "locked", "ORIG_HEAD", "logs/HEAD",
                    "config.worktree", "info/sparse-checkout", "COMMIT_EDITMSG", "AUTO_MERGE"):
                data, identity = read_file(path, until, min(LEAF_LIMIT, PASS_LIMIT - used))
                used += len(data)
                if name == "AUTO_MERGE":
                    # Successful ort rebases leave this tree ref. A divergent
                    # conflict snapshot is recovery data, not disposable state.
                    tree = git(snap["path"], "rev-parse", snap["head"] + "^{tree}")
                    if data != os.fsencode(tree + "\n"):
                        raise Retain("auto-merge-tree-recovery-required")
                if name == "COMMIT_EDITMSG":
                    message = git(snap["path"], "show", "-s", "--format=%B%x00", snap["head"])
                    if not message.endswith("\0"):
                        raise Retain("commit-message-proof-invalid")
                    expected = os.fsencode(message[:-1])
                    if data != expected:
                        # Editor commits leave status comments in this file.
                        # Reuse Git's cleanup; unsupported scissors/verbose
                        # residue and substantive drafts remain recovery data.
                        mode = git(snap["path"], "config", "--default", "default", "--get", "commit.cleanup")
                        comment = git(snap["path"], "config", "--default", "#", "--get", "core.commentChar")
                        if mode not in ("default", "strip", "whitespace") or comment == "auto":
                            raise Retain("commit-cleanup-mode-unqualified")
                        options = () if mode == "whitespace" else ("--strip-comments",)
                        cleaned = git(snap["path"], "stripspace", *options, input_data=data, raw=True)
                        if cleaned != expected:
                            raise Retain("commit-message-recovery-required")
                    if file_identity(path.lstat()) != identity:
                        raise Retain("commit-message-recovery-required")
                if name == "config.worktree":
                    keys = git(snap["path"], "config", "--file", str(path), "--no-includes",
                               "--null", "--name-only", "--list").split("\0")
                    allowed = {"core.sparsecheckout", "core.sparsecheckoutcone", "index.sparse",
                               "dotfiles.sparseprofile", "dotfiles.sparseprofilefile"}
                    if any(key and key.lower() not in allowed for key in keys):
                        raise Retain("worktree-config-recovery-required")
                    if file_identity(path.lstat()) != identity:
                        raise Retain("worktree-config-changed")
                if name in ("ORIG_HEAD", "logs/HEAD"):
                    # All referenced commits must remain named by the preserved
                    # branch. The caller checks reachability before removal.
                    refs = data.split() if name == "ORIG_HEAD" else [
                        oid for line in data.splitlines() for oid in line.split()[:2]]
                    rows[name] = [[*identity, attrs], hashlib.sha256(data).hexdigest(),
                                  sorted(set(os.fsdecode(x) for x in refs if x != b"0" * 40))]
                else:
                    rows[name] = [[*identity, attrs], hashlib.sha256(data).hexdigest()]
            else:
                raise Retain("admin-recovery-content-unknown")
        if file_identity(directory.lstat()) != file_identity(before):
            raise Retain("admin-directory-changed")
    return rows


@contextlib.contextmanager
def maintainer_lock(codex_home):
    parent = Path(codex_home) / "locks"
    if not parent.exists():
        parent.mkdir(mode=0o700)
    s = parent.lstat()
    if (not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022
            or str(parent.resolve()) != str(parent)):
        raise Retain("maintainer-coordination-path-unsafe")
    lock = parent / "agent-worktree-maintain.lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as error:
        raise Retain("maintainer-lock-present") from error
    identity = file_identity(lock.lstat())[:2]
    pidfile = lock / "pid"
    fd = None
    try:
        fd = os.open(pidfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        pid_identity = file_identity(os.fstat(fd))[:2]
        os.write(fd, (str(os.getpid()) + "\n").encode())
        os.fsync(fd)
        yield
    finally:
        if fd is not None:
            os.close(fd)
            if (file_identity(lock.lstat())[:2] == identity
                    and file_identity(pidfile.lstat())[:2] == pid_identity):
                pidfile.unlink()
                lock.rmdir()
