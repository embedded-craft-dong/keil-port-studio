# Windows build and verification

[简体中文](RELEASE.zh-CN.md)

The `2.3.0-dev2` prerelease provides source and Windows packages, adding F1 HAL/SPL automatic driver ports to dev1. See the [release notes](RELEASE-NOTES-2.3.0-dev2.md).
The published `2.2.0-rc6` is a CubeMX/HAL adaptation prerelease, not certification of all devices/components.
RC6 fixes console encoding and Windows short-path selection issues found by the first hosted CI run.
See [RC6 notes](RELEASE-NOTES-2.2.0-rc6.md) and [scope](SUPPORT.en.md).
Historical fixes are documented in the [reliability notes](RELIABILITY.en.md) and
[RC4 release notes](RELEASE-NOTES-2.2.0-rc4.md); exact hardware results are in the
[hardware matrix](HARDWARE-MATRIX.en.md). This page is for developers building or maintaining the project.

## Build on Windows

Use a separate virtual environment. The current local builder uses Python 3.14 x64;
the source's Python 3.8+ baseline is distinct from build-dependency requirements.
Pinned dependencies do not guarantee byte-for-byte reproducible binaries.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python tools\build_release.py releases\2.3.0-dev2
.venv\Scripts\python tools\verify_release.py releases\2.3.0-dev2
```

The output directory must not already exist. Outputs:

- `app/KeilPortStudio/KeilPortStudio.exe` and required runtime files;
- `KeilPortStudio-Windows-x64.zip` (complete portable app);
- `KeilPortStudio-source.zip` (allowlisted source, tests, docs, build scripts);
- `SHA256SUMS.txt` and application `BUILD-INFO.json`.

An allowlist selects source archive contents, excluding raw hardware evidence,
test projects, SDKs and build caches. Allowlists and automated scans do not guarantee
the absence of private information or credentials; review the actual archive contents.
Review staged files before publishing: ignore rules do not untrack existing files.

The portable package is unsigned, has no installer and does not require administrator
rights. Git is optional and external, not bundled. Desktop diagnostic logs live in
`%APPDATA%/KeilPortStudio/logs` and may contain paths/output: redact before sharing and
clean up when appropriate. Project transaction backups do not back up MCU Flash/SD data.

Development builds use Windows extended paths at the source-tree copy/hash and
transaction directory backup/cleanup I/O boundaries, handling deep SDK files over
260 characters without changing system long-path policy. Prefixes are not written
to Keil project references or manifests. Short project roots are still recommended:
this does not establish arbitrary long-path support in old Keil/compiler tools,
nor does it certify every path operation in this application.

## Verification requirements

1. Run `python tools/audit_publication.py` to check allowlisted contents, common privacy patterns and documentation links; manually review changes and archives.
2. Run the `tests/test_*.py` regression scripts. Tk tests need a desktop and Git tests need Git; real Keil builds require explicit opt-in.
3. Check artifacts with `verify_release.py`, then extract and launch the EXE to test file addition, previews, export and recovery.
4. Retain third-party licenses and record the build environment, source revision, checksums and test scope. Do not relicense third-party code as MIT.

The [RC6 hosted CI record](https://github.com/embedded-craft-dong/keil-port-studio/actions/runs/35721027566)
covers automated regression, not hardware or clean-environment acceptance. Clean Windows
acceptance without Python/Git is still incomplete; changing PATH is not a substitute.
Software-controlled USB Host VBUS shutoff failed on the test board. Successful ordinary
thumb-drive I/O and physical power-loss retention are separate results, not certification of all hardware combinations.
