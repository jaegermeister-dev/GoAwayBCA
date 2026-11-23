GoAwayBCA Beta 4

Install and manage Windows 8.1, Windows 10 and Windows 11 from Intel macOS, using an existing internal or experimental USB GPT partition and pure EFI booting. A USB installer is not required.
Windows 7 and Windows 8 are not yet supported. Support is being actively developed for a future update.

Before starting
- Make a full backup. This app does not create or resize partitions. Prepare an empty exFAT or NTFS partition yourself; installation formats the selected partition after confirmations.
- Use an official, unmodified Windows ISO and verify its hash yourself. The app does not enforce an original ISO. You are responsible for your Windows licence and activation.
- Hybrid MBR and MBR-based Windows installations are not supported. Read the launch questions carefully. A normal GPT protective MBR is not a hybrid MBR.
- If Windows already exists, back it up before changing partitions. Where possible, shrink its partition from Windows Disk Management. This is a precaution, including for pure EFI Boot Camp installations.

Using the app
1. Open GoAwayBCA and approve administrator authorization. The authorization lasts for this running app session.
2. Complete the disk-layout check or manual confirmations. The app never changes SIP. The option Continue as is / SIP is disabled tries the automatic read-only check with current permissions; it does not bypass a failed check.
3. Choose the ISO, click Start Windows installation, confirm your actual Mac model and follow the Windows edition, driver and destination prompts.
4. Review the destination and all formatting confirmations. Wait for the completion dialog before restarting.
5. Use Windows in OpenCore, or hold Option/Alt at startup and choose Windows or EFI Boot. Complete Windows setup and install the selected drivers.
6. Finish one Windows installation, including drivers and a successful restart, before preparing another. Windows can change boot files during setup. This order is recommended, even where simultaneous preparation might work.
7. Use Saved Files to review preparation files and driver downloads when you no longer need them. Files are not automatically deleted after a successful installation.

Tools include installation management, Windows boot-menu rebuild/rename, EFI mount/unmount, saved-file cleanup, hardware/boot diagnostics and experimental boot-configuration recovery. Review each tool's warnings before confirming. EFI backups do not back up Windows data. The Windows menu does not manage macOS or Linux.

Host range: Intel OS X Lion 10.7 through macOS Tahoe. Testing has included Mavericks, Mojave, Monterey and Tahoe across MacBookPro11,3, 11,4 and 11,5; this does not mean every host/Windows/model combination was tested. Windows 8.1 and 10 installations and menu coexistence have been tested on these Macs, including 4096-byte logical-sector storage on the 11,4. Windows 11 is available with compatibility guidance, but was not part of that installation test matrix. Older hosts and other models may need fixes based on reports.

Beta 4 creates Test1.efi, Test2.efi and Test3.efi for a selected Windows installation. Each automatically collects logs and attempts its boot using a different loading method. Restart between tests. A new numbered diagnostic folder is always used; old tools and logs are preserved. See the saved OpenShell instructions. Returned EFI status/exit text and ACPI collection errors provide more detailed troubleshooting. This update does not claim a boot fix for a specific Mac. Windows installation and the normal menu are unchanged.

Experimental USB destinations and Windows partition backup/restore remain available. Leopard support and installation support for 32-bit EFI Macs are not included. Further installation optimisations and Linux/GRUB interaction testing remain future work.
