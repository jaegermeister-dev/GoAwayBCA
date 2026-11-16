# Third-party components

GoAwayBCA's own code uses GPLv3. The bundled third-party components retain their original licences; the root licence does not replace them. Original notices remain in the app, the source tree and the dependency-source archive.

The release uses these components:

| Component | Purpose | Source and notices |
| --- | --- | --- |
| CPython 3.7.17 | Runtime for older host versions | Dependency-source archive, `python-legacy`; original notices in the app |
| CPython 3.11.17 / python-build-standalone | Runtime for newer hosts | Dependency-source archive, `python-modern`, including upstream build source and input metadata |
| NTFS-3G/ntfsprogs 2026.9.28 | NTFS file and partition operations | Dependency-source archive, `ntfs-wim` |
| wimlib 1.14.5 | Windows image handling | Dependency-source archive, `ntfs-wim` |
| hivex 1.3.24 | Offline registry handling | Dependency-source archive, `hivex` |
| 7-Zip 26.04 | Publisher package extraction | Dependency-source archive, `7zip-modern` and `7zip-legacy`; includes original licence/UnRAR notices |
| innoextract 1.9, Boost 1.83.0, xz/liblzma | Publisher package extraction | Dependency-source archive, `inno-modern` and `inno-portable` |
| OpenNtfsDxe from OpenCore 1.0.8 | EFI filesystem reader | [Upstream source](https://github.com/acidanthera/OpenCorePkg/tree/1.0.8); preserved notices in the app and source materials |
| EfiFs 1.13, GNU-EFI and GRUB source components | EFI filesystem reader for additional sector layouts | `third-party/EfiFs`, including exact source archives, submodule revisions and local build recipe |

The Python distributions include additional libraries with their own notices. The dependency-source archive preserves those notices and source inputs. Some bootstrap inputs are compiler binaries or Python wheels; the archive is not described as source-only.

**GoAwayBCA-Beta-1-Dependency-Sources.zip** contains the upstream archives, notices, build recipes and local compatibility patches. The original upstream source archives and notices are preserved unchanged. Local recipe names have been normalised for this project; private machine reports and historical release notes are not part of the publication.

The main application recipe uses pinned dependency binaries to reproduce the approved release. Individual dependency rebuilds can differ with compiler, SDK and build settings. See [BUILDING.md](BUILDING.md) for the precise reproduction claim.

Windows ISOs and Apple driver packages are supplied by the user or downloaded separately as appropriate. Their licences and activation requirements are independent of GoAwayBCA's licence. This repository does not grant additional rights to third-party software.
