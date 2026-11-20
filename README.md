# disk-rescue-toolkit

Simple tools and a step-by-step guide to check, repair and recover a USB drive,
an SD card or a hard disk on Linux.

## What is inside

| File | What it does |
|---|---|
| [docs/linux.md](docs/linux.md) | The guide: find the problem, make an image, repair, recover files, test for fake drives, format |
| [scripts/diskcheck.sh](scripts/diskcheck.sh) | Read-only check of a drive: kernel messages, SMART health, file system check. Changes nothing |
| [tools/exfat_salvage.py](tools/exfat_salvage.py) | Recovers files **with their names and folders** from a broken exFAT drive |

## Quick start

```bash
git clone https://github.com/GuyBastien/disk-rescue-toolkit.git
cd disk-rescue-toolkit

lsblk                              # find your drive, for example sdb
sudo ./scripts/diskcheck.sh sdb    # see what is wrong
```

Then follow [docs/linux.md](docs/linux.md) from the step the check points you to.

## exfat_salvage

When an exFAT drive loses its root folder, Linux says `failed to recognize exfat type`
and Windows asks you to format it. PhotoRec can still find the data, but every file
comes back as `f1234567.jpg`, with no name and no folder.

`exfat_salvage.py` looks for the folder entries that survived and copies the files out
with their real names.

```bash
sudo ddrescue -n /dev/sdb drive.img drive.map        # 1. image the drive (on another disk)
python3 tools/exfat_salvage.py scan drive.img        # 2. see what can come back
python3 tools/exfat_salvage.py recover drive.img recovered/
```

- It only reads the image. The drive is never touched.
- Pure Python 3.8+, no dependencies.
- Files that were directly in the root folder lose their entry with it: use PhotoRec for those.
- Folders whose name was lost come out as `lost_dir_<number>`, with their content inside.

It was written to save a 64 GB USB key whose root folder was erased: 4,654 files came
back with their names.

## Safety

- Check the drive name with `lsblk` before every command. `sdb` today can be `sdc` tomorrow.
- Make an image before any repair, and write recovered files to **another** disk.

## License

MIT
