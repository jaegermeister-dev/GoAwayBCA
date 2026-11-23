# Building the approved Beta 5 app

The release source and build inputs reproduce **the contents of the approved GoAwayBCA.app exactly**. The application and CPU probe are compiled from their matching source. Runtime helpers, third-party dependencies, firmware binaries and fixed assets are reused from explicitly pinned inputs.

This is not a claim that every dependency is rebuilt from source during app assembly. The separate dependency-source archive supplies upstream inputs, notices and recipes for that work. Recompiling dependencies with a different toolchain can change their bytes; those replacements are not silently substituted into this release.

## Requirements

- An Intel macOS build host and Python 3.7 or newer.
- Apple Command Line Tools matching `recipe/toolchain.json`: Apple clang 21.0.0 (`clang-2100.1.1.101`), macOS SDK 26.5, x86_64.
- At least 1 GiB of free space for app reconstruction and checks.
- An ordinary user account. Do **not** use sudo.

The minimum OS target of the produced application is separate from the build host. Exact reproduction was verified on Tahoe. End users do not need Python or developer tools to run the app.

## Reproduce the release

Download **GoAwayBCA-Beta-5-Source-and-Build-Inputs.zip** from the [Beta 5 release](https://github.com/jaegermeister-dev/GoAwayBCA/releases/tag/v0.5.0-beta.5), verify its checksum against `SHA256SUMS.txt`, and extract it. In Terminal, enter its source folder and run:

```sh
python3 -I -B build.py \
  --inputs inputs/GoAwayBCA-Beta-5-Build-Inputs.zip \
  --output ../GoAwayBCA-rebuilt
```

The output directory must not already exist and must be outside the source folder. The command verifies the pinned input archive, copies exact resource source/data, compiles the application and CPU probe, and compares every app file, permission mode and symlink against `recipe/APP-FILE-HASHES.json`. It then runs the launch/session tests with both packaged runtimes and the native UI self-test.

A successful run writes `REBUILD-VERIFIED.json` with `exact_app_match: true`. A mismatch stops the process and is listed in `APP-COMPARISON.json`; do not describe such a build as the exact release. The verification does not install Windows, mount EFI or change an installed system.

If using a Git checkout or GitHub's automatic source download, separately download **GoAwayBCA-Beta-5-Build-Inputs.zip** and pass its path with `--inputs`. GitHub's automatic source archives do not include release assets.

The app comparison covers file contents, file modes and symlink targets. ZIP compression, archive timestamps and filesystem metadata can differ when you zip the reconstructed app yourself. The downloadable app ZIP itself remains the exact approved artifact, whose SHA256 is recorded in the release’s `SHA256SUMS.txt`.

## Source layout

- `source/WindowsDeployer.m`, headers and `cpu_probe.c`: native macOS application and CPU probe.
- `source/*.py`: exact Python modules used by the release, including retained compatibility code.
- `source/firmware/` and `source/diagnostic-efi/`: project EFI sources and fixtures. The release retains the tested firmware binaries; app assembly does not recompile them.
- `source/driver-installer-source/` and `source/driver-installer-previous/`: corresponding sources for retained support components. Their presence is not a statement that every retained code path is supported in Beta 5.
- `third-party/EfiFs/`: pinned filesystem-reader sources, submodule sources and build recipe.
- `dependency-recipes/`: component build recipes and compatibility patches. Their upstream source inputs are in the separate dependency-source release asset.
- `recipe/`: app manifest, copy mapping, compiler/SDK record and reproduction result.
- `tests/`: focused launch/session tests used by the release reconstruction.

Windows 7 and Windows 8 are not yet supported; support is being actively developed for a future update. Retained code is not an invitation to bypass the documented release scope.

For ordinary development, make a separate copy and update the recipe and expected manifests deliberately when changing code. The release recipe intentionally rejects unrecorded changes rather than presenting them as the approved build.

## Additional Beta 5 component sources

The diagnostic EFI template and PCI/ACPI collectors are built with Zig `0.17.0-dev.2251+1175a3e99`:

```sh
python3 -B source/build_diagnostic_firmware.py --zig /path/to/zig --output ../diagnostic-rebuild
```

The command checks both collectors and all three diagnostic EFI templates against the pinned release binaries. Each generated user-specific diagnostic tool patches only its fixed target-binding block; the template contains no personal disk identifiers.

The NTFS backup helper sources and build recipe are `source/backup_metadata_probe.c` and `source/build_backup_helper_macos.py`. The dependency-source archive includes the pinned NTFS-3G and pkgconf archives used by that recipe. App assembly retains the recorded helper binaries; rebuilding an upstream helper under a different toolchain need not produce identical bytes.

Additional regression fixtures are in `source/test_*.py`. They exercise synthetic menus, EFI transactions, USB selection, provenance, backup/restore preparation and identity checks without installing Windows. Run them against a staged copy of the rebuilt app’s Resources directory with the test modules overlaid, using either bundled Python runtime. Physical USB boot and restored-Windows acceptance tests remain outstanding and are described as experimental in the README.

The Beta 5 build-input archive retains the verified dependency bytes from Beta 2. Superseded diagnostic inputs are explicitly listed in the recipe and are not used; the current diagnostic binaries and their corresponding source are included in the Beta 5 source tree.

Beta 5 retains all three diagnostic EFI templates and adds diagnostic-only original-loader resolution in source/firmware/menu/direct_windows.zig. Their sources and pinned binaries are in source/firmware/menu and source/diagnostic-efi. These diagnostic variants do not replace the normal boot-menu executable.

To avoid opening a UI self-test window, pass `--skip-ui-check`. Exact byte comparison and both Python runtime checks still run; the receipt records that the UI check was skipped.

Beta 5 was reconstructed with `--skip-ui-check` to avoid interrupting the owner. Its native UI executable is byte-identical to Beta 4. Both packaged runtime checks and the full app byte/mode/symlink comparison passed. The diagnostic firmware was separately rebuilt from its published source and compared with all five pinned EFI binaries.
