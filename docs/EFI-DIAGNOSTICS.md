# Windows EFI diagnostics

Use **Advanced → Create Windows EFI diagnostic tool…** and select the Windows installation that fails to boot. Review the destination, then confirm creation. The app saves instructions on your Desktop and creates a new numbered folder such as `EFI/WinDeploy/Diagnostics/Test-002` on that drive's EFI partition.

**Existing diagnostic tools and logs are always preserved.** They are not updated in place. Use the exact new folder named in the latest instructions; an old folder keeps its old behaviour and selected Windows target.

The new folder contains three EFI files:

| File | Boot comparison |
| --- | --- |
| `Test1.efi` | Verified original Windows loader bytes with the original loader's device/path. |
| `Test2.efi` | Ask firmware to load that same verified original Windows file through its full device path. |
| `Test3.efi` | Load that same verified original Windows file with an experimental EFI-partition boot context; this may fail where Tests 1 and 2 succeed. |

Beta 5 corrects the EFI diagnostic tests to launch the original Windows loader directly. If the selected menu entry uses the recognised experimental Fix.efi helper, each diagnostic test verifies the saved Original.efi against Entry.bin and bypasses the helper. Unknown helpers, missing originals and mismatched hashes stop the attempt; no helper is silently substituted. Logs identify both the configured menu executable and the actual executable launched, including its SHA256.

Tests 1 and 2 use the original Windows loader's device/path through different loading methods. Test 3 uses an experimental EFI-partition context; it can fail even when the same Windows installation boots with Tests 1 and 2. Restart between tests. Each new diagnostic set gets a new short Test-### folder, preserving every previous tool and log.

This is a diagnostic correction, not a confirmed fix for the reported older Mac. Windows installation, drivers, partition management and the normal boot menu are unchanged. If creating diagnostics reports an unreadable Windows partition, save the full app log and diagnostic-scan report; reinstalling Windows is not a required troubleshooting step. This update does not claim to fix that separately reported scan failure.

Each file **automatically collects reports and attempts the selected Windows boot immediately when run**. There are no numbered choices or start prompt. These are diagnostic comparisons, not confirmed fixes for a particular Mac.

1. Restart into OpenShell using your existing boot environment. GoAwayBCA does not install OpenShell or modify OpenCore.
2. Enter `map -r`, then find the `fsN:` mapping containing the exact diagnostic folder in your saved instructions. Do not assume `fs0:`.
3. Change to that folder and run `Test1.efi`.
4. **Restart before every next test**, even if an attempt fails and returns to OpenShell. Return to the same folder and run `Test2.efi`, then repeat for `Test3.efi`.
5. Record which tests booted, returned an error or hung, and whether you entered OpenShell through OpenCore or another route. Photograph any hang/error screen.
6. Return to macOS, mount the indicated EFI partition, and ZIP the **entire new Test folder**, including all logs and capture folders. Review it for personal information before sharing.

Each test has separate numbered `test1-*.log`, `test2-*.log` or `test3-*.log` files. Existing logs are not overwritten. PCI/ACPI reports are saved beside them. ACPI inventories record the last table visited and the underlying error if collection stops. PCI reports distinguish unavailable devices from read failures. A collection failure is not by itself proof of the cause of a Windows boot failure.

The tests verify the selected disk, partition and sector size. They use the existing menu's normal per-installation BCD selection, flush logs before handoff, and record returned EFI status and any supplied exit text. They do not replace the menu, patch Windows, change OpenCore, change default boot settings or repartition. Windows itself can perform its normal boot/setup writes after handoff. Missing or ambiguous identities/menu entries stop an attempt rather than guessing.

This is not a Windows kernel debugger. If Windows hangs after handoff, only the last saved stage may be available. Failure reporting cannot guarantee a specific Mac will boot. Restarting between tests prevents temporary EFI state from one attempt contaminating another.
