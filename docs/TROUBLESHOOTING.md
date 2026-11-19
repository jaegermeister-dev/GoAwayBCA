# Troubleshooting

## SIP and disk-layout checks

GoAwayBCA never changes System Integrity Protection (SIP).

When SIP is disabled, or the macOS version predates SIP, the app can attempt its automatic read-only check. If SIP is enabled, partially enabled or could not be identified conclusively, the launch dialog offers:

- **Disable SIP instructions:** shows how to temporarily disable SIP in Recovery. This reduces system protection; read the guidance and reenable SIP afterwards.
- **Keep SIP enabled:** continues to manual disk-layout questions and confirmations. Answer them accurately; the app has not inspected MBR contents in this mode.
- **Continue as is / SIP is disabled:** attempts the same automatic read-only check using current permissions. This is useful when SIP is already disabled but detection was inconclusive. It does not declare SIP disabled, bypass a failed check or accept an incompatible layout.

If the automatic check cannot read a disk, save the exact error. If it detects hybrid MBR or an MBR-based Windows installation, do not proceed. Back up and restore a pure GPT/EFI layout using an appropriate method first; this app does not convert layouts.

## No eligible destination

Prepare an existing empty internal exFAT or NTFS partition on a GPT drive. The app does not create or resize partitions and does not accept a partition containing Windows as an empty destination.

If such a partition already exists, save the log. In Settings, the alternate partition-detection order can help with a detection problem. Report the macOS version, disk layout and results. An unreadable partition must not be treated as empty.

## Drivers

Check that you confirmed the correct Mac identifier. Driver downloads do not guarantee that an older Apple package supports a newer Windows version.

For Windows versions above your Mac's official Boot Camp maximum, prefer letting Windows Update find drivers first, then install only missing drivers individually. If an older all-in-one Boot Camp installer causes crashes or boot loops, recover from backup or reinstall Windows and try that individual-driver approach. Save the installer logs and identify the package that caused the failure.

Complete driver installation and restart successfully before preparing another Windows installation.

## Missing or failing boot entries

Record the exact menu entry and error. Try both the Mac's Option/Alt picker and OpenCore where available. You do not need to install or modify OpenCore just to submit a report. Please report the original failure even if another route works.

Rebuild Boot Menu can rescan Windows installations and update its entries, including changes made outside the app. It cannot guarantee recovery after another installer changes boot files. Keep the saved operation report and EFI backup. Do not delete unfamiliar EFI files manually.

Linux/GRUB coexistence is untested. OpenShell may be used to try launching an existing OS EFI loader directly if you know the correct device and path; it is not a guaranteed repair. Include Linux distribution, bootloader and installation-order details in reports.

## Slow preparation or low space

Read the current log and progress information. Large images, driver downloads and verification can take time. Do not delete preparation files while an operation is using them.

Use Saved Files between operations to inspect cached downloads and ISO preparations. Delete only what you no longer need. Keep reusable driver downloads if you plan to use them again. A completion message means Windows files have been deployed; the subsequent Windows boot and setup are separate steps.

## Administrator prompt cancelled

Cancelling authorization should restore the window's controls without starting an operation. You can try again. If controls stay unavailable, save the log and report it.

## macOS blocks the app from opening

Download only from the project's release page and check SHA256SUMS.txt. The app is not Developer ID notarized. Use macOS's normal per-app approval process if you choose to open it; do not disable Gatekeeper globally. If it still fails to open, report the exact message and macOS version.
