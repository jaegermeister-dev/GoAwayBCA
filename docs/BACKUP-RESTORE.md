# Experimental Windows partition backup and restore

The feature supports Windows 8.1, 10 and 11. It is experimental even when restoring to the same drive, and more uncertain when moving between drives or internal/USB connections. Keep the original installation and another backup until the restored Windows system has been tested.

## Make a backup

1. Finish Windows setup and shut Windows down fully. Return to macOS.
2. Choose **Back Up / Restore… → Back Up…** and select the intended installation.
3. Review its source drive and provenance, then save the backup somewhere outside that Windows partition with enough free space.
4. The source is temporarily unmounted and read consistently. Windows and EFI files are not changed. Wait for successful verification before treating the file as a complete backup.

The archive contains the selected NTFS partition’s used data and filesystem metadata. It is not a whole-disk image or EFI-partition backup. It also records the original installation provenance, the current backup-source location and accumulated backup/restore history.

## Restore a backup

1. Prepare an empty exFAT/NTFS destination partition on a GPT internal or USB drive yourself.
2. Choose **Back Up / Restore… → Restore…** and select the archive.
3. Choose the destination and review the detected differences. **The literal same physical drive is recommended**, not merely another drive of the same internal/USB category.
4. Review the menu name, backup provenance, preparation requirements and current/proposed menu lists. All data on the selected destination will be erased after three confirmations.
5. Wait for the completion dialog, then test the restored installation before removing the original backup.

The comparison includes drive identity/serial/model, logical and physical sectors, internal/USB connection, available controller/adapter details, capacity, offsets and partition layout. Missing hardware information is reported as unknown. Connection-reported USB serial numbers are not treated as proof of exact physical-drive identity.

Different-drive and sector-size restores are allowed only when the app can prepare and verify the conversion first. Some filesystem features cannot be carried across a file-based sector conversion; those cases stop rather than silently discard metadata. Drivers, Windows activation, boot behaviour and portable-disk policies may still need attention after a move. Windows may run a filesystem check following resizing; let it finish. A USB-to-internal restore retains portable policies, so other internal volumes may remain offline until reviewed in Windows Disk Management.

Only the selected partition is overwritten. The selected drive’s EFI Windows menu is backed up and refreshed after the restore. OpenCore and other drives’ menus are not replaced.

## Provenance survives repeated backups

The original installation origin is retained. The current backup source describes where the latest backup was actually taken; restore history records each destination instead of overwriting the origin. A new backup from a previously restored installation therefore retains the earlier history while comparing future restores with the latest source.

Backups of installations created outside GoAwayBCA remain externally created after restoration. They do not gain uninstall privileges. Remove those installations manually after backing up, then use Rebuild Boot Menu to refresh the drive’s list. Metadata alone is not sufficient to grant ownership.
