# Changelog

## 0.4.0-beta.4 — 23 November 2026

- Create `Test1.efi`, `Test2.efi` and `Test3.efi` for the exact Windows partition selected by the user.
- Each EFI automatically collects reports and attempts Windows immediately when launched; no numbered menu or start prompt.
- Compare current memory loading, firmware file-path loading, and EFI-partition boot context using the same selected Windows loader.
- Save separate numbered logs, raw returned EFI status and bounded exit text; report the underlying ACPI collection error and last table visited.
- Preserve every older diagnostic folder and log. New instructions identify the new folder and require a restart between tests.
- Keep Windows deployment, drivers, management, the normal boot menu and OpenCore handling unchanged. This is diagnostic functionality, not a confirmed boot fix for the reported older Mac.

## 0.3.0-beta.3 — 22 November 2026

- Fix Windows detection when creating EFI diagnostics, including immediately after an installation and after reopening the app.
- Bound registry-header reads and helper cleanup so the diagnostic scan cannot wait indefinitely; report the current scan stage.
- Show the packaged version in the application window title.
- Use short diagnostic folders such as `Test-001`. Creating a new tool preserves earlier tools and logs; use the newly generated folder for the latest test.
- Report unavailable PCI devices as a partial inventory with their addresses and counts. Genuine read/save failures remain errors, and the main boot log includes the PCI result.
- Keep the existing Windows installation, driver, uninstall and normal boot-menu behaviour. This release does not add Windows 7 or Windows 8 support.

The updated tool was created through the actual app and both diagnostic options were exercised on MacBookPro11,5. The boot-attempt log reached the selected Windows loader without a returned EFI error. Additional checks cover both bundled Python runtimes and isolated firmware environments with 512/4096-byte logical sectors and EFI 1.10. These results do not establish that the older Mac from a community report can now boot Windows: Beta 3 provides the corrected diagnostic collection needed to investigate it.

## 0.2.0-beta.2 — 19 November 2026

- Add Windows-partition-specific OpenShell EFI diagnostics: collect only, or collect and attempt the selected Windows boot, with logs flushed before handoff.
- Add experimental USB destinations, Internal/External tabs, actual-drive geometry checks, portable preparation and separate menus per physical drive.
- Add experimental Windows partition backup/restore with verified archives, source/destination comparisons, retained origin and restore history, and per-drive menu refresh.
- Add Backup / Restore and Advanced controls; rename the main action to Start Windows installation.
- Add safe Cmd+Q handling. Manual EFI mounting defaults to read/write; an existing read-only mount can be normally unmounted/remounted with consent.
- Use the new GoAwayBCA icon and expand the tested-hardware, troubleshooting and feature documentation.
- Keep the normal Windows menu firmware and the internal installation path; no installation optimisations or new older-host/firmware support.

Windows 7 and Windows 8 are not yet supported; support is being actively developed for a future update. The new USB and backup/restore paths are experimental and have not been physically boot-tested. Tests used disposable filesystem images, both bundled runtimes, native UI checks and isolated firmware VMs; they did not install Windows on the owner’s Macs.

## 0.1.0-beta.1 — 16 November 2026

First public beta of GoAwayBCA.

- Install Windows 8.1, 10 and 11 from macOS to existing internal GPT partitions using pure EFI booting.
- Confirm the Mac model and assess the Windows image, CPU capabilities, firmware and drive geometry.
- Download and prepare model-appropriate drivers, with saved-download reuse and compatibility guidance.
- Maintain a Windows-only boot menu with named entries, rebuild/rename tools and scans for externally added or removed installations.
- Manage recognised installations, review saved files for deletion, mount/unmount EFI partitions and collect diagnostics.
- Provide experimental boot-configuration recovery and guarded boot-menu deletion.
- Reuse administrator authorization for the running app session.
- Offer automatic disk-layout checks or manual declarations when SIP may prevent disk reads. **Continue as is / SIP is disabled** attempts the existing automatic check with current permissions and still stops on failed or incompatible checks.
- Include public-facing guidance, matching source, pinned build inputs and checksums. No private SSH/VNC test tools are bundled.

Windows 7 and Windows 8 are not yet supported; support is being actively developed for a future update.

Not included: USB installation destinations, Windows partition backup/restore, Leopard support, 32-bit EFI support or Linux/GRUB validation. Windows 11 and Mac models outside the documented test matrix are not claimed as physically tested.

This release retains the tested installation and boot-menu behaviour. The published source reproduces the approved app using the recorded toolchain and pinned dependency inputs; it does not substitute newly rebuilt dependency or firmware binaries.
