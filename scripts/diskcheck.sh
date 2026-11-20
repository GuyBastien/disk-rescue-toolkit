#!/usr/bin/env bash
# diskcheck.sh - read-only check of a drive: what it is, what the kernel says,
# SMART health and a file system check that changes nothing.
#
# Usage: sudo ./scripts/diskcheck.sh sdX
set -u

if [ $# -ne 1 ]; then
    echo "usage: sudo $0 sdX   (see the name with: lsblk)" >&2
    exit 1
fi

dev="${1#/dev/}"
if [ ! -b "/dev/$dev" ]; then
    echo "error: /dev/$dev does not exist. Check with: lsblk" >&2
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "error: run it with sudo" >&2
    exit 1
fi

title() { printf '\n=== %s ===\n' "$1"; }

title "Drive"
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,TRAN "/dev/$dev"

title "Kernel messages (last 30)"
dmesg | grep -iE "$dev|exfat|ntfs|fat-fs|ext4|I/O error|reset|disconnect|write protect" | tail -30 \
    || echo "nothing found"

title "SMART health"
if command -v smartctl >/dev/null; then
    smartctl -H -A "/dev/$dev" | grep -iE 'result|Reallocated_Sector|Current_Pending|Offline_Uncorrect|Power_On_Hours|Wear' \
        || echo "no SMART data (normal for most USB keys)"
else
    echo "smartctl not installed: sudo apt install smartmontools"
fi

title "File system check (read-only)"
for part in $(lsblk -lnpo NAME "/dev/$dev"); do
    fs=$(lsblk -no FSTYPE "$part" | head -1)
    [ -z "$fs" ] && continue
    if [ -n "$(lsblk -no MOUNTPOINTS "$part" | tr -d '[:space:]')" ]; then
        echo "$part ($fs): mounted, skipped. Unmount it first: udisksctl unmount -b $part"
        continue
    fi
    echo "--- $part ($fs)"
    case "$fs" in
        exfat) fsck.exfat -n "$part" ;;
        vfat)  fsck.vfat -n "$part" ;;
        ntfs)  ntfsfix -n "$part" ;;
        ext2|ext3|ext4) e2fsck -n "$part" ;;
        *) echo "no read-only check for $fs" ;;
    esac
done

echo
echo "Nothing was changed. See docs/linux.md for what to do next."
