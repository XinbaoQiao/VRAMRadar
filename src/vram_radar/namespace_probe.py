"""Bounded, read-only ownership fallback for an isolated Linux PID view.

The remote program uses only the Python standard library. It reads existing
procfs mounts; it never mounts a filesystem or changes privileges. A host procfs
is accepted only when PID 1 belongs to Linux's initial PID namespace (the
PID_NS_INIT_INO constant in include/uapi/linux/nsfs.h). Device-access rows remain
separate from nvidia-smi allocations unless that host view proves a PID mapping.
"""

REMOTE_NAMESPACE_PROBE = r'''
import os, re, sys
try:
    import pwd
except ImportError:
    pwd = None

INIT_PID_NS = "pid:[4026531836]"

def read(path, limit=16384):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""

def link(path):
    try:
        return os.readlink(path)
    except OSError:
        return ""

def ticks(root, pid):
    text = read(root + "/" + pid + "/stat", 4096)
    head, sep, tail = text.rpartition(") ")
    fields = tail.split()
    if not sep or head.split(" ", 1)[0] != pid or len(fields) < 20 or not fields[19].isdigit():
        return ""
    return fields[19]

def status(root, pid):
    fields = {}
    for line in read(root + "/" + pid + "/status").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key] = value.split()
    return fields

def host_roots(root):
    roots = []
    for line in read(root + "/self/mountinfo", 65536).splitlines()[:1024]:
        before, sep, after = line.partition(" - ")
        fields = before.split()
        if not sep or not after.startswith("proc ") or len(fields) < 6 or fields[3] != "/":
            continue
        path = re.sub(r"\\(040|011|012|134)", lambda m: chr(int(m[1], 8)), fields[4])
        if not path.startswith("/") or any(ord(c) < 32 for c in path) or path in roots:
            continue
        if link(path + "/1/ns/pid") == INIT_PID_NS:
            roots.append(path)
        if len(roots) >= 8:
            break
    return roots

def metadata(root, pid):
    before = ticks(root, pid)
    fields = status(root, pid)
    uid = (fields.get("Uid") or [""])[0]
    if not before or not uid.isdigit():
        return None
    try:
        user = pwd.getpwuid(int(uid)).pw_name if pwd else uid
    except (KeyError, OSError):
        user = uid
    command = read(root + "/" + pid + "/cmdline").replace("\x00", " ").strip()
    command = command or read(root + "/" + pid + "/comm", 256).strip()
    # Commands must not inject protocol records, even before hex encoding.
    command = " ".join(command.split())
    if not command:
        return None
    elapsed = "-"
    try:
        hz = os.sysconf("SC_CLK_TCK")
        up = float(read(root + "/uptime", 256).split()[0])
        if hz > 0 and up >= int(before) / hz:
            elapsed = str(int(up - int(before) / hz))
    except (AttributeError, ValueError, OSError, IndexError):
        pass
    if ticks(root, pid) != before:
        return None
    return before, fields, "%s %s %s %s %s" % (pid, uid, user, elapsed, command)

def resolve(root, host_root, pid):
    # Recheck the mount binding each time; PID numbers alone are insufficient.
    if link(host_root + "/1/ns/pid") != INIT_PID_NS:
        return None
    before = ticks(host_root, pid)
    host = metadata(host_root, pid)
    if not before or not host or before != host[0]:
        return None
    visible_pid = "-"
    row = host[2]
    process_ns = link(host_root + "/" + pid + "/ns/pid")
    ns_pids = host[1].get("NSpid", [])
    current_ns = link(root + "/self/ns/pid")
    if (current_ns and process_ns == current_ns and ns_pids and ns_pids[0] == pid
            and all(p.isdigit() and int(p) > 0 for p in ns_pids)):
        candidate = ns_pids[-1]
        local = metadata(root, candidate)
        if (local and local[0] == before and link(root + "/" + candidate + "/ns/pid") == current_ns
                and local[1].get("NSpid", [None])[0] == candidate
                and local[1].get("Uid") == host[1].get("Uid")):
            visible_pid = candidate
            row = pid + local[2][len(candidate):]
    # The process and the mapping must survive both metadata reads.
    if (ticks(host_root, pid) != before or status(host_root, pid).get("NSpid") != ns_pids
            or link(host_root + "/" + pid + "/ns/pid") != process_ns
            or link(host_root + "/1/ns/pid") != INIT_PID_NS):
        return None
    if visible_pid != "-" and ticks(root, visible_pid) != before:
        return None
    boot = read(host_root + "/sys/kernel/random/boot_id", 64).strip()
    identity = "VRAM_ID %s %s\n" % (boot, before) if re.fullmatch(r"[0-9a-fA-F-]{36}", boot) else ""
    return identity + "VRAM_HOSTPROC " + visible_pid + "\nVRAM_PROC\n" + row

def device_users(root, uid, excluded):
    rows, limited = [], False
    try:
        entries = os.scandir(root)
    except OSError:
        return rows, limited
    with entries:
        count = 0
        for entry in entries:
            pid = entry.name
            if not pid.isdigit():
                continue
            count += 1
            if count > 512 or len(rows) >= 32:
                limited = True
                break
            if pid in excluded or (status(root, pid).get("Uid") or [""])[0] != uid:
                continue
            before = ticks(root, pid)
            try:
                fds = os.scandir(entry.path + "/fd")
            except OSError:
                continue
            uses_gpu = False
            with fds:
                for n, fd in enumerate(fds):
                    if n >= 64:
                        limited = True
                        break
                    if re.fullmatch(r"/dev/nvidia(?:[0-9]+|-uvm(?:-tools)?)", link(fd.path)):
                        uses_gpu = True
                        break
            if not uses_gpu:
                continue
            item = metadata(root, pid)
            if item and before and before == item[0] == ticks(root, pid):
                rows.append((pid, "VRAM_PROC\n" + item[2]))
    return rows, limited

def probe(root, pids, uid):
    roots = host_roots(root)
    resolved, excluded = {}, {str(os.getpid()), str(os.getppid())}
    for pid in pids[:128]:
        for host_root in roots:
            result = resolve(root, host_root, pid)
            if result:
                resolved[pid] = result
                match = re.search(r"^VRAM_HOSTPROC ([0-9]+)$", result, re.M)
                if match:
                    excluded.add(match[1])
                break
    rows, limited = device_users(root, uid, excluded) if len(resolved) < len(pids) else ([], False)
    return resolved, rows, limited

if __name__ == "__main__":
    pids = [p for p in sys.argv[1].split() if p.isdigit()]
    resolved, rows, limited = probe("/proc", pids, sys.argv[2])
    for pid, text in resolved.items():
        print("META|%s|OK|%s" % (pid, text.encode().hex()))
    text = "\n".join("%s|%s" % (pid, data.encode().hex()) for pid, data in rows)
    print("GPU_ACCESS_HEX=" + text.encode().hex())
    print("GPU_ACCESS_LIMITED=" + str(int(limited)))
    print("GPU_ACCESS_SUPPORTED=1")
'''
