# Experimental USB destinations

Beta 2 can prepare Windows 8.1, 10 or 11 on an existing empty exFAT/NTFS partition on a GPT USB drive. This is experimental and has not passed physical Windows USB boot acceptance tests. Have a separate backup.

Use **Start Windows installation**, then choose the **External** tab when selecting a destination. Drives are grouped with their partitions. MBR drives are disabled; incompatible or occupied partitions on a GPT drive are disabled individually. Only the selected partition is formatted after confirmations. Prepare the layout yourself beforehand; the app never partitions a drive.

Choose either **portable Windows preparation** or normal preparation when prompted. Portable mode adjusts only a private copy of the installed SYSTEM registry: boot driver flags, portable-system status, the SAN policy and hibernation/fast-startup settings. Internal installations retain their existing preparation path. No claim of Microsoft-certified Windows To Go support is made.

A USB drive working in macOS does not prove it can boot Windows. Firmware support, controller drivers, enclosure translation, fixed/removable reporting, hubs, power and random-write performance all matter. A different adapter can report a different logical sector size. Connect directly for initial testing, keep the same connection throughout the operation and never disconnect the boot drive while Windows is running.

The app checks actual drive identity and logical sectors through its connection. It handles 512- and 4096-byte logical sectors without inferring the answer from the Mac model or storage bus. A changed identity or geometry during an operation stops further writes.

Each drive’s Windows menu contains entries for that physical drive only. Rebuild Boot Menu displays each drive’s current saved entry names before selection and compares the selected menu with a fresh scan. Saved entries are not proof that Windows still exists. The app refreshes additions/removals found outside GoAwayBCA whenever it updates a menu; it does not monitor changes continuously.

Windows 7 and Windows 8 are not yet supported; support is being actively developed for a future update.
