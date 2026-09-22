# Release candidate and build

[简体中文](RELEASE.zh-CN.md)

`2.2.0-rc5` is a CubeMX/HAL adaptation prerelease, not certification of all devices/components.
RC5 only updates the version and documentation; not all historical hardware combinations were reflashed.
See [RC5 notes](RELEASE-NOTES-2.2.0-rc5.md) and [scope](SUPPORT.en.md).
The following records describe inherited fixes and historical validation.
It includes USB Host and RTOS USB startup fixes plus independent Device regression
completed after RC3 was frozen. Existing RC3 archives were not overwritten and do
not contain these later changes. See the [RC4 release notes](RELEASE-NOTES-2.2.0-rc4.md).
This iteration hardens startup patches, source-edit ownership, XML validation and
background operations, and starts extracting pure core modules. Fresh FreeRTOS/CMSIS-V2
and RT-Thread kernels were flashed, verified, reset and tested; see the
[reliability notes](RELIABILITY.en.md). Earlier evidence covers specific
storage/USB/network/display configurations; the user confirmed the latest display.
Fresh final-assertion-profile rechecks are listed in the
[hardware matrix](HARDWARE-MATRIX.en.md). Do not transfer results from one
configuration to every release profile.

TinyUSB/LwIP FreeRTOS initialization also now creates tasks outside `configASSERT`.
Generated bodies were compiled and run with host API doubles, assertions on/off.
Fresh USB/network hardware fixtures using these templates passed the listed board
tests. Bare-metal/FreeRTOS/RT-Thread Host file I/O and physical power-loss retention
have now been tested. VBUS software shutoff failed on this board and component-level
diagnosis has stopped; this does not certify other MCU/peripheral combinations.

## Build on Windows

Use a separate virtual environment. The current local builder uses Python 3.14 x64;
the source's Python 3.8+ baseline is distinct from build-dependency requirements.
Pinned dependencies do not guarantee byte-for-byte reproducible binaries.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python tools\build_release.py releases\2.2.0-rc5
.venv\Scripts\python tools\verify_release.py releases\2.2.0-rc5
```

The output directory must not already exist. Outputs:

- `app/KeilPortStudio/KeilPortStudio.exe` and required runtime files;
- `KeilPortStudio-Windows-x64.zip` (complete portable app);
- `KeilPortStudio-source.zip` (allowlisted source, tests, docs, build scripts);
- `SHA256SUMS.txt` and application `BUILD-INFO.json`.

Raw hardware evidence, test projects, SDKs, credentials and build caches are excluded
from the source archive. Local `hardware_tests/` remains on disk but is gitignored.
Review staged files before publishing: ignore rules do not untrack existing files.

The portable package is unsigned, has no installer and does not require administrator
rights. Git is optional and external, not bundled. Desktop diagnostic logs live in
`%APPDATA%/KeilPortStudio/logs` and may contain paths/output: redact before sharing and
clean up when appropriate. Project transaction backups do not back up MCU Flash/SD data.

## Before publishing to GitHub

1. Confirm repository name, copyright attribution, MIT terms and third-party provenance.
2. Run all tests and smoke-test the extracted binary. Clean Windows acceptance without
   Python/Git remains pending suitable contributor hardware; changing PATH is not a
   substitute. Disclose this limitation for the candidate release.
3. Review staged changes for secrets, device identifiers and large/private files.
4. Put source in the repository and binary/source ZIPs plus checksums in Releases.
   Start as a prerelease.
5. Document tested/untested scope. Account login, repository creation and publication
   require the user's decision.

Build and CI configuration do not automatically create a repository, commit, push or publish.
See the [publication guide](PUBLISH.en.md) for the first public upload.
