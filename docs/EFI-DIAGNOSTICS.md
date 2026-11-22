# Windows EFI diagnostics

This tool collects evidence for a selected Windows partition. It does not install a boot fix or automatically reboot.

1. Open **Advanced → Create Windows EFI diagnostic tool…**.
2. Select the affected Windows partition by its title, identifier and drive. Review the confirmation.
3. Keep the generated **OpenShell instructions** saved on your Desktop. They identify the exact EFI partition GUID and diagnostic folder. The EFI executable is bound to the disk and partition GUIDs, not temporary disk numbers.
4. Restart into OpenShell. If using OpenCore, tools may appear after pressing Space in the picker. OpenShell must already be available; GoAwayBCA does not install or modify OpenCore.
5. Enter `map -r`. Inspect the `fsN:` mappings and locate the exact directory in your instructions. Do not assume `fs0:`.
6. Change to that directory and enter `WindowsDiagnostics.efi`.
7. Press **1** to collect only, or **2** to collect and attempt the selected Windows boot. Escape returns to OpenShell.
8. After the test, return to macOS and use **Advanced → Mount / Unmount EFI** to mount the specified EFI partition. Copy the whole diagnostic folder, including `boot-*.log`, PCI output and ACPI capture directories. Attach it with the macOS report and operation log to your GitHub issue after checking for personal information.

The tool creates a new numbered log for each invocation and flushes it before handing over to Windows. It records the firmware revision, actual sector geometry, persistent disk/partition identities, hardware inventory and boot stages. It accepts EFI 1.10 as well as newer firmware where the required protocols are available; it does not require one exact firmware version.

Option 1 creates diagnostic files only. Option 2 additionally uses the existing menu’s normal BCD selection for the chosen Windows entry. It does not repair missing menu records or guess between duplicated partition identities. Returned EFI failures are recorded. A crash or hang inside Windows after handoff may leave only the last flushed pre-boot stage; this is not a kernel debugger. Windows can perform normal boot/setup writes after handoff.

No report is uploaded automatically. The tool does not change OpenCore, firmware boot defaults, the partition map or unrelated operating systems.

## Creating a newer tool

Beta 3 uses short folders such as `EFI/WinDeploy/Diagnostics/Test-001`, then the next unused number. Creating a tool again preserves existing diagnostic folders and logs. Earlier long-named folders are also kept. Use the **new folder named in the latest instructions**: older tools keep their original target and collector versions.

## Understanding PCI warnings

An **unavailable device** means firmware exposed a device handle but the device did not respond with a usable vendor identifier. The collector skips that handle, keeps the information it could read and records the address and counts. A partial-inventory warning is not itself a Windows boot failure. Genuine read or save errors still appear as failures. The main boot log includes the PCI summary so these outcomes are distinguishable.

The scan reports which partition and stage it is reading. If a scan cannot read a partition, save the operation log and diagnostic scan report rather than treating it as proof that Windows is absent. Do not bypass an EFI filesystem-health failure; preserve a backup and investigate the reported problem before writing more files.
