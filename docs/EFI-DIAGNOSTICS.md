# Windows EFI diagnostics

Use **Advanced → Create Windows EFI diagnostic tool…**, select the Windows partition by its label, size and drive, and confirm. This list identifies candidate partitions from disk metadata; it does not assert that Windows files are readable or that Windows boots. A new **Test-###** folder is created on the associated EFI partition. Existing kits and logs remain untouched. Reinstalling Windows is not necessary to create another kit.

Read the saved OpenShell instructions. Run **Test1.efi**, **Test2.efi**, and **Test3.efi**, restarting between each attempt. Each collects reports and automatically tries the selected Windows installation. Test1 loads the original Windows loader from memory, Test2 asks firmware to load it by file path, and Test3 tries an experimental EFI-partition context. Test3 can fail even when Tests1/2 work.

The tests use an existing matching menu entry when present, bypassing the recognised old experimental helper. Without a matching entry, they check the standard Microsoft BCD for the selected partition, or that partition’s own boot files. Missing or mismatched boot files are logged at runtime. A custom menu is not required. Kits carry their own filesystem readers. Creating a kit never formats Windows or changes normal boot configuration. Running a test can activate a matching menu’s per-Windows BCD; Windows itself may make normal setup/boot writes after handoff.

After testing, return to macOS, mount EFI, and ZIP the entire new Test folder. Include the outcome of each test, your Mac model and whether OpenShell was started through OpenCore. Review logs before sharing. Nothing uploads automatically.

**Advanced → Manage EFI Diagnostic Kits…** shows kits across connected EFI partitions, their recorded app versions, target names and drives. Select entries to mark/unmark them, then review and confirm deletion. The selected kits’ logs are deleted too. Unknown versions stay explicitly unknown; a folder without a recognised target-bound diagnostic executable cannot be removed by this tool. Unselected kits, Windows, OpenCore and other boot files remain untouched.
