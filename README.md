# GoAwayBCA

**Install and manage Windows on Intel Macs from macOS, without making a USB installer.**

[Download Beta 3](https://github.com/jaegermeister-dev/GoAwayBCA/releases/latest) · [Report an issue](https://github.com/jaegermeister-dev/GoAwayBCA/issues) · [r/GoAwayBCA](https://www.reddit.com/r/GoAwayBCA/) · [Changelog](CHANGELOG.md)

GoAwayBCA Beta 3 supports installing **Windows 8.1, Windows 10 and Windows 11** to existing internal GPT partitions, with experimental USB destinations using pure EFI booting. It can download drivers, manage installations and maintain a Windows boot menu for multiple installations on the same drive.

**Windows 7 and Windows 8 are not yet supported. Support is being actively developed for a future update.**

This is beta software. Make a complete backup before using it. Tested configurations are listed below; other configurations are not guaranteed to work.

## Tested hardware and validation

The existing internal-installation workflow was physically tested with Windows 8.1 and Windows 10 on:

| Mac | Installation host | Storage tested | Windows boot-menu testing |
| --- | --- | --- | --- |
| MacBookPro11,3 | Mavericks | Original SATA-class internal storage | Multiple installations and switching between them |
| MacBookPro11,4 | Mojave, with SIP enabled | NVMe with 4096-byte logical sectors | Multiple installations and switching between them |
| MacBookPro11,5 | Tahoe | Original SATA-class internal storage | Separate installation/removal cycles; menu work also tested during development |

Monterey was also used during development and testing. This does **not** mean every combination of Mac, macOS, Windows and storage was tested. Sector handling is based on the drive's reported logical sector size, not an assumption that SATA means 512 bytes or NVMe means 4096 bytes.

Windows 11 is available to try with compatibility guidance; it was not part of this physical installation test matrix. Other Mac models receive model-specific guidance and may require diagnostics and fixes. Compatibility database information can be incomplete or inaccurate during beta development.

These are the project owner’s physical tests. Community reports are separate evidence, not an expansion of this confirmed matrix. Beta 2’s new diagnostic firmware was checked in virtual firmware environments with 512/4096-byte sectors and EFI 1.10 as well as newer EFI. Backup/restore and portable USB preparation were checked using disposable filesystem images and both bundled runtimes. **These checks are not physical USB boot or restored-Windows acceptance tests. The new USB and backup/restore features remain experimental and untested on real installations.**

## What Beta 3 includes

- Installation from a Windows ISO directly onto an existing empty exFAT or NTFS partition. No USB installer is required.
- Mac model confirmation and compatibility guidance using the selected Windows version, detected CPU capabilities, firmware architecture and drive geometry.
- Driver download, preparation and reuse.
- A Windows-only EFI boot menu with custom entry names, rebuild and rename tools.
- Installation management, including uninstalling installations whose recorded origin is recognised by the app.
- Menu refreshes that scan for Windows installations added or removed outside the app. An unreadable partition is not treated as proof that an installation was removed.
- Saved Files, for reviewing and choosing which cached downloads and preparation files to delete.
- **Advanced** controls for EFI partition mounting, hardware reports, menu deletion and a new Windows-partition-specific OpenShell diagnostic tool.
- Experimental USB installation with Internal/External destination tabs and a separate Windows menu on each physical drive.
- Experimental Windows partition backup and restore, with source/destination comparison and preserved installation provenance.
- Experimental boot-configuration recovery and guarded menu deletion when no Windows installations remain.
- One administrator authorization per running app session, plus SIP-aware disk-layout checks and manual confirmation options.

**GoAwayBCA does not create or resize partitions.** You must prepare them yourself. It formats only the selected installation or uninstall destination after confirmations. It does not replace OpenCore or automatically restart the Mac.

## Requirements and tested configurations

- An Intel Mac with a 64-bit CPU and 64-bit EFI firmware.
- Intel OS X Lion 10.7 through macOS Tahoe is the targeted host range. Older versions within that range have not all been tested.
- A pure GPT disk and an existing eligible internal or USB exFAT/NTFS partition. Hybrid MBR and MBR-based Windows installations are unsupported.
- A Windows ISO suitable for your CPU, firmware and model, sufficient free space for preparation, and a Windows licence where required.

Use an **official, unmodified ISO** and verify its hash yourself. The app does not enforce an original image; customised images can behave differently.

**T1 Macs:** not officially supported by this tool. Reports may be considered, without a guarantee of a fix. **T2 Macs:** unsupported; lowering startup security does not guarantee compatibility, and T2-specific support is not planned.

## Before changing partitions

Back up existing Windows installations before repartitioning, even if they use pure GPT/EFI. Confirm that the disk is not hybrid MBR. Where practical, free space and shrink an existing Windows partition from **Windows Disk Management** before creating another partition. This is a precaution; it does not mean Disk Utility necessarily breaks every pure EFI installation.

A normal **protective MBR** on a GPT disk is expected and is not the same as a hybrid MBR. Read the launch guidance carefully. Giving an incorrect manual declaration can put existing installations at risk. GoAwayBCA does not convert disk layouts.

## Install Windows

1. Download and extract **GoAwayBCA-Beta-3.zip**, then open **GoAwayBCA.app**. The app bundles its runtime and tools; you do not need to install Python or a filesystem driver.
2. Approve the macOS administrator prompt and complete the disk-layout check or manual confirmations. See [SIP and disk-layout checks](docs/TROUBLESHOOTING.md#sip-and-disk-layout-checks).
3. Choose your ISO and click **Start Windows installation**. Confirm your actual Mac model identifier. Do not substitute a different model to bypass a warning.
4. Follow the edition, compatibility, driver and menu-name prompts. Choose your empty destination partition and carefully review its size and identity before confirming formatting.
5. Wait for the completion dialog. Restart when ready. With OpenCore, choose **Windows**; with the Mac's own startup picker, hold **Option/Alt** and choose **Windows** or **EFI Boot**. Then select the intended installation in the Windows menu.
6. Finish normal Windows account setup, install the prepared drivers as appropriate, and restart into that installation again to confirm it works.
7. Back in macOS, use **Saved Files** to review downloads and preparation files for deletion. They are kept until you choose to remove them.

### Installing more than one Windows version

**Finish one installation at a time, including account setup, drivers and a successful restart, before preparing the next.** Windows can change its boot configuration during setup. This order may not always be necessary, but it is recommended to let each installation finish those changes first.

Existing EFI Windows installations can be considered for import into the menu after warnings and confirmation. Back up first. Installing Windows with another tool later can replace boot files or the menu entry point. Return to GoAwayBCA and rebuild the menu if needed; recovery cannot be guaranteed for every layout.

The menu is refreshed when you install, uninstall or rebuild through GoAwayBCA. Changes made outside the app are not noticed until its next scan/refresh. Review proposed changes and entry names. Installations not recognised as originating from the app cannot be uninstalled by it: back up and remove them yourself, then rebuild the menu.

**EFI backups are not backups of Windows partitions, applications or personal files.**

## New in Beta 3

Beta 3 fixes diagnostic discovery and stalled scans, shows the correct app version in the title bar, and creates short folders such as **Test-001**. Existing diagnostic tools and logs stay intact. Create a new tool with Beta 3 to use the updated collectors; an older tool is not updated in place.

Unavailable PCI devices now produce a clear partial-inventory warning instead of making the collector appear to have aborted. Actual read/save errors remain errors. Both diagnostic options were exercised on MacBookPro11,5; the selected Windows handoff completed without a returned EFI error. This improves troubleshooting and does not claim a boot fix for other models.

## Diagnostics and experimental tools

### Diagnose a Windows boot failure

Choose **Advanced → Create Windows EFI diagnostic tool…**, then select the exact Windows partition. GoAwayBCA creates a new diagnostic folder on that drive’s EFI partition and saves OpenShell instructions to your Desktop. Run the generated **WindowsDiagnostics.efi** yourself in OpenShell:

1. **Collect diagnostics only** records firmware, disk/sector, PCI and ACPI information.
2. **Collect diagnostics and try the selected Windows installation** records the same information, then selects that installation through its existing GoAwayBCA menu records and attempts the boot. It flushes logs before handoff and records errors returned by EFI.

This is a diagnostic tool, not an automatic fix. It does not change OpenCore, boot defaults or partition layouts. Option 2 uses the menu’s normal per-installation BCD selection; Windows may then perform its usual boot/setup writes. It cannot diagnose a Windows kernel hang after handoff beyond the last saved stage. A missing or ambiguous menu entry is logged and will not be guessed. Read [the full OpenShell guide](docs/EFI-DIAGNOSTICS.md), and review reports for personal information before sharing.

### Install onto USB — experimental

The destination window has **Internal** and **External** tabs. The External tab groups partitions by USB drive. Only eligible empty exFAT/NTFS partitions on GPT drives can be selected. MBR drives and incompatible partitions remain visible but disabled. GoAwayBCA never creates or resizes partitions.

You can select experimental portable-Windows preparation or normal preparation. This does not certify a drive for Windows To Go. Firmware, USB controllers, adapters, fixed/removable reporting, power and performance can prevent booting. Connect directly for the first boot and keep the drive connected while Windows is running. The app checks geometry through the actual connection, including 512/4096-byte logical sectors, and rechecks identity before writes.

**Each physical drive has its own menu.** Installing/removing Windows on one drive refreshes that drive’s list. Rebuild Boot Menu asks which drive to refresh, showing its saved entry names and the proposed new list. Other drives’ menus are left alone. See [USB destinations](docs/USB-DESTINATIONS.md).

### Back up or restore a Windows partition — experimental

Use **Back Up / Restore…** on the main window. Backups contain the selected Windows partition’s files, applications, settings and filesystem metadata, plus source-drive metadata and installation provenance. They are not whole-disk or EFI backups.

**Restore to the literal same physical drive whenever possible. The feature is experimental even then.** Restoring onto a different drive or changing internal/USB connection is more uncertain. Before writing, the app compares identity, logical and physical sectors, connection/controller/adapter information, capacity and layout; unknown values are shown as unknown. Unsupported or unsafe conversions stop before destination writes.

The source partition is temporarily unmounted for backup and read without modifying it. Restore formats/overwrites only the selected eligible destination after three confirmations, then refreshes that drive’s Windows menu. Keep the original installation and another copy of the backup until the restored system has been tested.

Backups preserve original provenance and append restore history. An installation made outside GoAwayBCA does **not** gain app uninstall privileges just because it was backed up and restored. See [backup and restore](docs/BACKUP-RESTORE.md).

## Drivers and compatibility

Driver selection depends on the confirmed Mac model and selected Windows version. Apple's driver availability does not guarantee that every driver will work with a different Windows version or boot configuration.

If installing a Windows version newer than that Mac's official Boot Camp maximum, read the warning. Prefer letting Windows Update find drivers first, then install only missing drivers individually from their folders. An older main Boot Camp setup program can install drivers that cause instability on newer Windows. Keep backups and installer logs. See [driver troubleshooting](docs/TROUBLESHOOTING.md#drivers).

## Limitations and future work

Beta 3 does **not** add Leopard support or support for 64-bit Macs with 32-bit EFI. Further installation optimisations, camera fixes, sleep/resume repairs, dock Ethernet fixes and Linux/GRUB interaction testing remain intended future work. Plans are not release dates or guarantees.

Linux coexistence has not been tested, whether Linux was installed before, between or after Windows. GRUB, OpenCore and the Mac's Option/Alt picker may present different entries or fail to boot an OS. Back up before changing boot configurations, try both pickers where available, and report the installation order and results. OpenShell can sometimes launch an existing EFI loader directly, but is not a guaranteed repair. GoAwayBCA's menu lists Windows only.

## Reports, source and licence

Use [GitHub Issues](https://github.com/jaegermeister-dev/GoAwayBCA/issues) for reports. Include the saved log, a diagnostic report and the information in [reporting a problem](docs/REPORTING.md). Review files for personal information before sharing them. Nothing is uploaded automatically.

The release includes matching source and pinned build inputs. [BUILDING.md](BUILDING.md) explains exact app reproduction and the separate dependency source materials. The published app ZIP matches the documented Beta 3 source and build inputs.

GoAwayBCA's own code is licensed under **GPLv3**. Third-party components retain their original licences and notices; see [THIRD-PARTY.md](THIRD-PARTY.md). The app is provided without warranty. Windows, macOS and other product names belong to their respective owners; this project is not affiliated with Apple or Microsoft.
