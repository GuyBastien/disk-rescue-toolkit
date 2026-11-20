#!/usr/bin/env python3
"""exfat_salvage.py - recover files *with their names* from a damaged exFAT image.

When an exFAT drive loses its root directory, upcase table or allocation bitmap,
Linux says "failed to recognize exfat type" and Windows asks you to format it.
File carvers like PhotoRec can still find the data, but every file comes back
as f1234567.jpg with no folder and no name.

This tool scans a raw image of the drive for exFAT directory entries that
survived, rebuilds as much of the folder tree as it can, and copies each file
out using the cluster chain stored in the FAT.

It only READS the image. Always work on an image (made with ddrescue), never on
the damaged drive itself.

Usage:
    python3 exfat_salvage.py scan    IMAGE                  # what can be recovered
    python3 exfat_salvage.py recover IMAGE OUTPUT_DIR       # copy files out

Options:
    --partition-offset BYTES   start of the exFAT partition inside the image
                               (default: read from the MBR/GPT, else 0)

Limits:
    - Files whose directory entry was erased cannot get their name back:
      use PhotoRec for those.
    - Folders whose own entry was erased keep their contents but lose their
      name: they are written as lost_dir_<cluster>.
    - Fragmented files depend on the FAT; if the FAT is damaged too, those
      files may come out corrupted. Check important files after recovery.

Pure Python 3.8+, no dependencies. MIT License.
"""
import argparse
import collections
import os
import re
import struct
import sys
import time

ENTRY_FILE = 0x85
ENTRY_STREAM = 0xC0
ENTRY_NAME = 0xC1
CHUNK = 64 * 1024 * 1024
# A file entry set: 0x85, then a stream extension (0xC0) and a name (0xC1)
ENTRY_SET = re.compile(rb"\x85[\x02-\x12].{30}\xc0.{31}\xc1", re.S)


def fail(msg):
    sys.exit(f"error: {msg}")


# ---------------------------------------------------------------- volume ---

class Volume:
    """Reads the exFAT boot sector and the FAT of a partition inside an image."""

    def __init__(self, path, offset=None):
        self.path = path
        self.f = open(path, "rb")
        self.size = os.path.getsize(path)
        self.offset = self._find_partition() if offset is None else offset
        self.f.seek(self.offset)
        bs = self.f.read(512)
        if bs[3:11] != b"EXFAT   ":
            fail(f"no exFAT boot sector at offset {self.offset}. "
                 "Try --partition-offset, or the backup boot sector at +6144 bytes.")
        (_, fat_off, fat_len, heap_off, self.cluster_count,
         self.root_cluster) = struct.unpack("<QIIIII", bs[72:100])
        bps = 1 << bs[108]
        self.cluster_size = bps * (1 << bs[109])
        self.heap = self.offset + heap_off * bps
        self.f.seek(self.offset + fat_off * bps)
        raw = self.f.read(fat_len * bps)
        self.fat = struct.unpack(f"<{len(raw) // 4}I", raw[: len(raw) // 4 * 4])

    def _find_partition(self):
        self.f.seek(0)
        mbr = self.f.read(1024)
        if mbr[3:11] == b"EXFAT   ":
            return 0  # image of a partition, not a whole disk
        if mbr[512:520] == b"EFI PART":  # GPT: first partition entry
            lba = struct.unpack("<Q", mbr[512 + 72:512 + 80])[0]
            self.f.seek(lba * 512)
            entry = self.f.read(128)
            return struct.unpack("<Q", entry[32:40])[0] * 512
        if mbr[510:512] == b"\x55\xaa":  # MBR: first partition
            return struct.unpack("<I", mbr[454:458])[0] * 512
        return 0

    def cluster_offset(self, c):
        return self.heap + (c - 2) * self.cluster_size

    def cluster_of(self, pos):
        return (pos - self.heap) // self.cluster_size + 2

    def chain(self, first, contiguous, nbytes):
        """Clusters of a file: contiguous run, or follow the FAT."""
        need = max(1, -(-nbytes // self.cluster_size))
        if contiguous:
            return list(range(first, first + need))
        out, seen, c = [], set(), first
        while 2 <= c < self.cluster_count + 2 and len(out) < need and c not in seen:
            out.append(c)
            seen.add(c)
            c = self.fat[c] if c < len(self.fat) else 0
        return out


# ------------------------------------------------------------------ scan ---

def checksum(entries):
    s = 0
    for i, b in enumerate(entries):
        if i in (2, 3):
            continue
        s = (((s << 15) | (s >> 1)) + b) & 0xFFFF
    return s


def scan(vol, progress=True):
    """Find every valid in-use file/folder entry set in the image."""
    found = {}
    start = time.time()
    pos = vol.heap
    while pos < vol.size:
        vol.f.seek(pos)
        buf = vol.f.read(CHUNK + 32 * 19)
        for m in ENTRY_SET.finditer(buf):
            i = m.start()
            if (pos + i - vol.heap) % 32:
                continue
            n = buf[i + 1]
            ent = buf[i:i + 32 * (n + 1)]
            if len(ent) < 32 * (n + 1) or checksum(ent) != struct.unpack("<H", ent[2:4])[0]:
                continue
            attr = struct.unpack("<H", ent[4:6])[0]
            name_len = ent[35]
            name = b"".join(ent[64 + 32 * j + 2:64 + 32 * j + 32] for j in range(n - 1))
            found[pos + i] = {
                "pos": pos + i,
                "name": name.decode("utf-16-le", "replace")[:name_len],
                "dir": bool(attr & 0x10),
                "contiguous": bool(ent[33] & 0x02),
                "cluster": struct.unpack("<I", ent[52:56])[0],
                "size": struct.unpack("<Q", ent[56:64])[0],
            }
        pos += CHUNK
        if progress:
            pct = min(100, (pos - vol.heap) * 100 / max(1, vol.size - vol.heap))
            print(f"\rscanning {pct:5.1f}%  {len(found)} entries  {time.time() - start:.0f}s",
                  end="", file=sys.stderr, flush=True)
    if progress:
        print(file=sys.stderr)
    return list(found.values())


# ------------------------------------------------------------------ tree ---

SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean(name):
    name = SAFE.sub("_", name).rstrip(" .")
    return name or "_"


def build_paths(vol, entries):
    """Give every file a relative output path, rebuilding folders where possible."""
    dir_of_cluster = {}
    by_start = {}
    for e in entries:
        if e["dir"] and e["cluster"] >= 2:
            by_start.setdefault(e["cluster"], e)
            for c in vol.chain(e["cluster"], e["contiguous"], e["size"]):
                dir_of_cluster.setdefault(c, e["cluster"])

    memo = {}

    def folder(start, depth=0):
        """Path of the folder whose data starts at cluster `start`."""
        if start in memo:
            return memo[start]
        e = by_start.get(start)
        if start == vol.root_cluster:
            path = ""
        elif e is None or depth > 64:
            path = f"lost_dir_{start}"
        else:
            parent = dir_of_cluster.get(vol.cluster_of(e["pos"]))
            if parent is None:
                parent = vol.cluster_of(e["pos"])  # parent folder lost: group by its cluster
            path = os.path.join(folder(parent, depth + 1), clean(e["name"])) if parent != start else clean(e["name"])
        memo[start] = path
        return path

    used = set()
    files = []
    for e in entries:
        if e["dir"]:
            continue
        parent = dir_of_cluster.get(vol.cluster_of(e["pos"]), vol.cluster_of(e["pos"]))
        path = os.path.join(folder(parent), clean(e["name"]))
        key = path.lower()  # stay safe on case-insensitive targets (exFAT, NTFS, Windows)
        if key in used:
            base, ext = os.path.splitext(path)
            k = 2
            while f"{base} ({k}){ext}".lower() in used:
                k += 1
            path = f"{base} ({k}){ext}"
            key = path.lower()
        used.add(key)
        files.append((path, e))
    return files


# --------------------------------------------------------------- recover ---

def recover(vol, files, out):
    ok = bad = written = 0
    log = open(os.path.join(out, "_salvage_errors.txt"), "w", encoding="utf-8")
    start = time.time()
    for i, (rel, e) in enumerate(files, 1):
        dest = os.path.join(out, rel)
        try:
            clusters = vol.chain(e["cluster"], e["contiguous"], e["size"]) if e["size"] else []
            need = -(-e["size"] // vol.cluster_size)
            if len(clusters) < need:
                raise ValueError("cluster chain incomplete (FAT damaged?)")
            if clusters and vol.cluster_offset(clusters[-1]) + vol.cluster_size > vol.size + vol.cluster_size:
                raise ValueError("data is past the end of the image")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            left = e["size"]
            with open(dest, "wb") as fh:
                for c in clusters:
                    vol.f.seek(vol.cluster_offset(c))
                    block = vol.f.read(min(vol.cluster_size, left))
                    fh.write(block)
                    left -= len(block)
                    if left <= 0:
                        break
            ok += 1
            written += e["size"]
        except (OSError, ValueError) as err:
            bad += 1
            log.write(f"{rel}: {err}\n")
        if i % 100 == 0 or i == len(files):
            print(f"\rrecovering {i}/{len(files)} files  {written / 1e9:.2f} GB  "
                  f"{time.time() - start:.0f}s", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    log.close()
    return ok, bad, written


# ------------------------------------------------------------------ main ---

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["scan", "recover"])
    ap.add_argument("image", help="raw image of the drive (from ddrescue or dd)")
    ap.add_argument("output", nargs="?", help="output folder (recover only)")
    ap.add_argument("--partition-offset", type=int, default=None, metavar="BYTES")
    args = ap.parse_args()

    if args.command == "recover":
        if not args.output:
            fail("recover needs an OUTPUT_DIR")
        if os.path.exists(args.output) and os.listdir(args.output):
            fail(f"{args.output} is not empty")
        os.makedirs(args.output, exist_ok=True)

    if not os.path.isfile(args.image):
        fail(f"{args.image} not found (make an image first: see docs/linux.md, step 4)")
    vol = Volume(args.image, args.partition_offset)
    print(f"exFAT volume at offset {vol.offset}, cluster size {vol.cluster_size} bytes, "
          f"{vol.cluster_count} clusters", file=sys.stderr)

    entries = scan(vol)
    files = build_paths(vol, entries)
    folders = sum(e["dir"] for e in entries)
    total = sum(e["size"] for _, e in files)
    lost = len({p.split(os.sep)[0] for p, _ in files if p.startswith("lost_dir_")})
    print(f"found {len(files)} files ({total / 1e9:.2f} GB) and {folders} folders; "
          f"{lost} folders lost their name")

    if args.command == "scan":
        top = collections.Counter(p.split(os.sep)[0] for p, _ in files)
        for name, n in top.most_common(25):
            print(f"  {n:6d}  {name}")
        return

    ok, bad, written = recover(vol, files, args.output)
    print(f"recovered {ok} files ({written / 1e9:.2f} GB), {bad} failed "
          f"(see {os.path.join(args.output, '_salvage_errors.txt')})")


if __name__ == "__main__":
    main()
