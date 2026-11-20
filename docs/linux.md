# Repair a USB drive or hard disk on Linux

All commands run in a terminal. Commands with `sudo` ask for your password.
Replace `sdX` with your drive (for example `sdb`) and `sdX1` with its partition.

> **Always check the name first with `lsblk`.** Names change every time you
> plug a drive in. A wrong letter in `mkfs`, `dd` or `sfdisk` erases the wrong disk.

## 1. Identify the drive

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,TRAN
```

Look at `SIZE`, `MODEL` and `TRAN` (`usb` for external drives) to be sure.

## 2. Find out what is wrong (read-only)

```bash
sudo dmesg | grep -iE 'sdX|exfat|ntfs|fat|ext4|I/O error|reset|disconnect' | tail -30
```

| Message in `dmesg` | Meaning | Go to |
|---|---|---|
| `wrong fs type, bad superblock` | the file system is damaged | [step 4](#4-make-an-image-before-any-repair) |
| `exFAT-fs: failed to load alloc-bitmap` / `no upcase table` | exFAT metadata destroyed | [step 4](#4-make-an-image-before-any-repair), then [exfat_salvage](../tools/exfat_salvage.py) |
| `I/O error, dev sdX, sector ...` | bad sectors, the drive is failing | [step 4](#4-make-an-image-before-any-repair) **now**, with `ddrescue` |
| `USB disconnect` repeating | cable, port, power or USB autosuspend | [step 3](#3-usb-drives-that-disconnect) |
| No message, but `lsblk -f` shows a partition with **no FSTYPE** (often after Windows asked to format) | the partition table was rewritten, the data may still be there | [step 10](#10-windows-asked-to-format-the-drive) |
| `Write Protect is on` | hardware lock or dying flash | check the switch on SD cards; otherwise the drive is at end of life |

You can also run the read-only checker from this repository:

```bash
sudo ./scripts/diskcheck.sh sdX
```

For hard disks and SSDs, check their health:

```bash
sudo apt install smartmontools
sudo smartctl -H -A /dev/sdX
```

`Reallocated_Sector_Ct`, `Current_Pending_Sector` or `Offline_Uncorrectable` above 0 means
the disk is wearing out. Copy your data and replace it.

## 3. USB drives that disconnect

If `dmesg` shows `USB disconnect` again and again (sometimes for the built-in webcam too),
the computer is cutting the USB port. A drive cut off while writing ends up corrupted.

1. Plug the laptop charger in.
2. Plug the drive straight into the computer: no hub, no extension cable.
3. Turn off USB autosuspend until the next reboot:

   ```bash
   echo -1 | sudo tee /sys/module/usbcore/parameters/autosuspend
   ```

4. Do not plug two power-hungry drives (external SSD + USB key) at the same time.

To make the autosuspend change permanent, add `usbcore.autosuspend=-1` to
`GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, then run `sudo update-grub`.

## 4. Make an image before any repair

**Never repair a drive that holds important files before copying it.** A failed repair can
make things worse. Work on an image instead: the original stays untouched.

You need free space larger than the **whole drive** (not just the used space), on
**another** disk.

```bash
sudo apt install gddrescue
sudo ddrescue -n /dev/sdX /path/to/other/disk/drive.img /path/to/other/disk/drive.map
```

- `ddrescue` keeps going over bad sectors, unlike `dd`.
- The `.map` file records progress: run the same command again to resume.
- At the end, `bad areas: 0` and `pct rescued: 100%` means a perfect copy.

For a failing drive, add a second pass that retries the bad areas:

```bash
sudo ddrescue -d -r3 /dev/sdX drive.img drive.map
```

### Clonezilla: for a drive that still works

[Clonezilla](https://clonezilla.org) is a live system you boot from a USB key. It copies a
whole disk to an image (`device-image`) or to another disk (`device-device`), for example
to move to a new SSD or to keep a backup of a computer.

| | ddrescue | Clonezilla |
|---|---|---|
| Damaged file system or bad sectors | yes, made for it | no: it needs a readable file system (or falls back to a slow raw copy) |
| Copies | every sector, even empty ones | only the used space, compressed: smaller image |
| Result | one raw `.img` file, ready for fsck, TestDisk, exfat_salvage | a Clonezilla folder, to restore with Clonezilla |
| Runs | from your normal Linux | from its own boot key |

Use **ddrescue** to rescue a broken drive, **Clonezilla** to clone or back up a healthy one.
Clonezilla shows the disks by name and size: check twice which one is the **source**,
restoring the wrong way erases the good disk.

## 5. Repair the file system

Run these on the image (attach it as a loop device) or, if you have an image, on the drive.
`-n` only checks; remove it to repair.

| File system | Check only | Repair |
|---|---|---|
| exFAT | `sudo fsck.exfat -n /dev/sdX1` | `sudo fsck.exfat -y /dev/sdX1` |
| FAT32 | `sudo fsck.vfat -n /dev/sdX1` | `sudo fsck.vfat -a /dev/sdX1` |
| NTFS | `sudo ntfsfix -n /dev/sdX1` | `sudo ntfsfix -d /dev/sdX1`, then `chkdsk /f` on Windows |
| ext4 | `sudo e2fsck -n /dev/sdX1` | `sudo e2fsck -p /dev/sdX1` |

Work on a copy of the image:

```bash
cp drive.img drive-copy.img
udisksctl loop-setup -f drive-copy.img     # prints /dev/loopN
sudo fsck.exfat -y /dev/loopNp1
udisksctl mount -b /dev/loopNp1
```

## 6. Recover files

Try them in this order:

1. **The repaired copy**: if step 5 worked, mount it and copy your files.
2. **exfat_salvage** (exFAT only): gets files back **with their names and folders** when
   the root folder is gone.

   ```bash
   python3 tools/exfat_salvage.py scan drive.img
   python3 tools/exfat_salvage.py recover drive.img recovered/
   ```

3. **TestDisk**: rebuilds lost partitions and undeletes files on FAT, exFAT, NTFS, ext.

   ```bash
   sudo apt install testdisk
   testdisk drive.img
   ```

4. **PhotoRec** (comes with TestDisk): finds files by their content. It works even when
   everything else fails, but files lose their names (`f1234567.jpg`).

   ```bash
   photorec drive.img
   ```

5. **Deleted file on NTFS**:

   ```bash
   sudo ntfsundelete /dev/sdX1 -s                 # list
   sudo ntfsundelete /dev/sdX1 -u -m 'name*' -d /other/disk
   ```

   On an SSD, Windows sends TRIM when a file is deleted: the data is usually gone for good.

Always write recovered files to **another** disk, never to the damaged one.

## 7. Check that the drive is not fake or faulty

Cheap USB keys often lie about their size: a "64 GB" key that really holds 8 GB.
Everything written past the real size is lost.

```bash
sudo apt install f3
sudo f3probe --destructive --time-ops /dev/sdX     # quick, ERASES the drive
```

- `The device is the real thing`: real capacity.
- `The device is a counterfeit`: use the `f3fix` command it prints to shrink the partition
  to the real size.

`f3probe` checks the size, not long-term reliability. For a full test (several hours):

```bash
f3write /media/$USER/LABEL && f3read /media/$USER/LABEL
```

`Data LOST: 0.00 Byte` means every byte came back. Anything else: replace the drive.

## 8. Wipe and format

When your files are safe, start fresh. **This erases everything on the drive.**

```bash
# new partition table with one partition on the whole drive
echo 'type=7' | sudo sfdisk --label dos /dev/sdX      # type 7 = exFAT/NTFS
sudo mkfs.exfat -L MYDRIVE /dev/sdX1                   # works on Windows, macOS, Linux, TVs
```

Other formats:

| Use | Command |
|---|---|
| Linux only | `sudo mkfs.ext4 -L MYDRIVE /dev/sdX1` (use `type=83`) |
| Windows only, big files | `sudo mkfs.ntfs -f -L MYDRIVE /dev/sdX1` |
| Old devices, files < 4 GB | `sudo mkfs.vfat -F 32 -n MYDRIVE /dev/sdX1` (use `type=c`) |

## 9. Remove leftover Windows partitions from an external disk

A disk that used to hold Windows keeps an EFI partition (100 MB), a Microsoft reserved
partition (16 MB) and a recovery partition (500 MB). Windows may then hide it or give it
no drive letter. Your files are on the big "basic data" partition.

1. Check that your computer does **not** boot from that disk:

   ```bash
   efibootmgr -v          # entries must point to the internal disk's partition UUID
   findmnt /boot/efi      # must be the internal disk
   ```

2. Save the partition table, then delete only the small partitions:

   ```bash
   lsblk -o NAME,SIZE,PARTTYPENAME /dev/sdX
   sudo sfdisk -d /dev/sdX > sdX-partitions-backup.txt
   udisksctl unmount -b /dev/sdX3
   sudo sfdisk --delete /dev/sdX 1 2 4     # the numbers of the small partitions
   ```

The data partition is not moved, so your files stay. To undo:
`sudo sfdisk /dev/sdX < sdX-partitions-backup.txt`.

## 10. Windows asked to format the drive

When Windows cannot read a drive, it shows *"You need to format the disk before you can use
it"*. **Always click Cancel.** It only means Windows does not understand the drive.

If the format was started and then stopped (you cancelled, or the USB cable disconnected),
Windows may have written a new partition table before formatting anything. The new
partition shows up with no file system, but your files are still on the disk, at the
place where the old partition started.

1. Look at the drive. A partition with an empty `FSTYPE`, or a type that does not match
   (`FAT16` on a 240 GB disk), is the sign:

   ```bash
   lsblk -f /dev/sdX
   sudo sfdisk -l /dev/sdX
   ```

2. Find where the old partition started.
   - If you saved the table before (`sfdisk -d`, see [step 9](#9-remove-leftover-windows-partitions-from-an-external-disk)),
     the `start=` value is in that file.
   - Otherwise, run `sudo testdisk /dev/sdX`, then *Analyse* → *Quick Search*: it lists the
     partitions it finds with their start sector. Do not press *Write* yet.

3. Check that the file system is really there (read-only). Replace `START` with the
   sector number:

   ```bash
   sudo dd if=/dev/sdX bs=512 skip=START count=1 status=none | head -c 16 | xxd
   ```

   You should see `NTFS`, `EXFAT` or `MSDOS5.0`/`FAT32` in the text on the right.

4. Mount it read-only, **without changing the partition table**, and look at your files.
   `SIZE` is the size of the old partition in sectors:

   ```bash
   L=$(sudo losetup -r -f --show -o $((START*512)) --sizelimit $((SIZE*512)) /dev/sdX)
   sudo ntfsfix -n $L                  # NTFS only: checks, changes nothing
   sudo mkdir -p /mnt/rescue && sudo mount -o ro $L /mnt/rescue
   ls /mnt/rescue
   ```

   If the files are there, copy the important ones to another disk now.

5. Put the old partition table back. First save the wrong one, so you can undo:

   ```bash
   sudo umount /mnt/rescue; sudo losetup -d $L
   sudo sfdisk -d /dev/sdX > sdX-wrong-table.txt
   printf 'label: gpt\nstart=START, size=SIZE, type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\n' \
       | sudo sfdisk --wipe-partitions never /dev/sdX
   lsblk -f /dev/sdX                   # the partition shows its file system again
   ```

   - `EBD0A0A2-...` is "Microsoft basic data" (NTFS, exFAT, FAT32).
   - If the disk used MBR before (TestDisk says *Intel*), use
     `printf 'label: dos\nstart=START, size=SIZE, type=7\n'` instead.
   - If you have the saved table from before, use it instead:
     `sudo sfdisk --wipe-partitions never /dev/sdX < sdX-partitions-backup.txt`.
   - `--wipe-partitions never` stops sfdisk from erasing the file system signature it finds.

   With TestDisk, step 5 is: select the partition it found → *Write*.

Only the first sectors of the disk change: your files are not moved.
If Windows still asks to format after this, the USB adapter or cable may be the cause
(see [step 3](#3-usb-drives-that-disconnect)).

## 11. Prevent it next time

- When Windows or a TV asks to format or "initialize" a drive that has your files: **Cancel**.
- Save the partition table of your external disks, it takes one second:
  `sudo sfdisk -d /dev/sdX > sdX-partitions-backup.txt`.

- Always **eject** a drive before unplugging it.
- Keep two copies of anything important, on two different devices.
- Make a full image of your computer with Clonezilla from time to time
  ([step 4](#clonezilla-for-a-drive-that-still-works)).
- Prefer known brands (SanDisk, Kingston, Samsung, Crucial) bought from real shops.
