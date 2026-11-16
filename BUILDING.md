# Building the approved Beta 1 app

The release source and build inputs reproduce **the contents of the approved GoAwayBCA.app exactly**. The application and CPU probe are compiled from their matching source. Runtime helpers, third-party dependencies, firmware binaries and fixed assets are reused from explicitly pinned inputs.

This is not a claim that every dependency is rebuilt from source during app assembly. The separate dependency-source archive supplies upstream inputs, notices and recipes for that work. Recompiling dependencies with a different toolchain can change their bytes; those replacements are not silently substituted into this release.

## Requirements

- An Intel macOS build host and Python 3.7 or newer.
- Apple Command Line Tools matching `recipe/toolchain.json`: Apple clang 21.0.0 (`clang-2100.1.1.101`), macOS SDK 26.5, x86_64.
- At least 1 GiB of free space for app reconstruction and checks.
- An ordinary user account. Do **not** use sudo.

The minimum OS target of the produced application is separate from the build host. Exact reproduction was verified on Tahoe. End users do not need Python or developer tools to run the app.

## Reproduce the release

Download **GoAwayBCA-Beta-1-Source-and-Build-Inputs.zip** from the [Beta 1 release](https://github.com/jaegermeister-dev/GoAwayBCA/releases/tag/v0.1.0-beta.1), verify its checksum against `SHA256SUMS.txt`, and extract it. In Terminal, enter its source folder and run:

```sh
python3 -I -B build.py \
  --inputs inputs/GoAwayBCA-Beta-1-Build-Inputs.zip \
  --output ../GoAwayBCA-rebuilt
```

The output directory must not already exist and must be outside the source folder. The command verifies the pinned input archive, copies exact resource source/data, compiles the application and CPU probe, and compares every app file, permission mode and symlink against `recipe/APP-FILE-HASHES.json`. It then runs the launch/session tests with both packaged runtimes and the native UI self-test.

A successful run writes `REBUILD-VERIFIED.json` with `exact_app_match: true`. A mismatch stops the process and is listed in `APP-COMPARISON.json`; do not describe such a build as the exact release. The verification does not install Windows, mount EFI or change an installed system.

If using a Git checkout or GitHub's automatic source download, separately download **GoAwayBCA-Beta-1-Build-Inputs.zip** and pass its path with `--inputs`. GitHub's automatic source archives do not include release assets.

The app comparison covers file contents, file modes and symlink targets. ZIP compression, archive timestamps and filesystem metadata can differ when you zip the reconstructed app yourself. The downloadable app ZIP itself remains the exact approved artifact, with SHA256:

```text
43c03d27f2487ac61ae806661d7e05c17375636d0047bfa2fe73d313f42b6c3f
```

## Source layout

- `source/WindowsDeployer.m`, headers and `cpu_probe.c`: native macOS application and CPU probe.
- `source/*.py`: exact Python modules used by the release, including retained compatibility code.
- `source/firmware/` and `source/diagnostic-efi/`: project EFI sources and fixtures. The release retains the tested firmware binaries; app assembly does not recompile them.
- `source/driver-installer-source/` and `source/driver-installer-previous/`: corresponding sources for retained support components. Their presence is not a statement that every retained code path is supported in Beta 1.
- `third-party/EfiFs/`: pinned filesystem-reader sources, submodule sources and build recipe.
- `dependency-recipes/`: component build recipes and compatibility patches. Their upstream source inputs are in the separate dependency-source release asset.
- `recipe/`: app manifest, copy mapping, compiler/SDK record and reproduction result.
- `tests/`: focused launch/session tests used by the release reconstruction.

Windows 7 and Windows 8 are not yet supported; support is being actively developed for a future update. Retained code is not an invitation to bypass the documented release scope.

For ordinary development, make a separate copy and update the recipe and expected manifests deliberately when changing code. The release recipe intentionally rejects unrecorded changes rather than presenting them as the approved build.
