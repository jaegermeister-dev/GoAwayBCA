# Changelog

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
