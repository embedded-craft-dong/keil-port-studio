# First GitHub publication

[简体中文](PUBLISH.zh-CN.md)

Start with a `v2.2.0-rc6` CubeMX/HAL adaptation prerelease: keep source in Git and portable ZIPs in Releases.
Do not upload the entire local workspace, vendor SDKs or raw `hardware_tests/` evidence.

## Before uploading

Confirm account, repository name, visibility and MIT attribution (currently
`Keil Port Studio contributors`). Run `python tools/audit_publication.py` and the
regression suite, and manually review staged files and history; the scanner is not
a complete secret detector. Check whether commit author/email metadata may be public.
Using a GitHub noreply address does not rewrite older commits automatically.
Keep failed and unverified items in the [RC6 notes](RELEASE-NOTES-2.2.0-rc6.md) and [scope](SUPPORT.en.md).
Use the [build guide](RELEASE.en.md); rebuild whenever reviewed source changes.

## Publish source

Create an empty GitHub repository. With existing local history, avoid creating a
separate remote README/license history. In the tool's Git panel select the tool's
source folder, review status, select intended files, stage, inspect changes and commit.
Use the new repository's actual HTTPS/SSH remote and confirm the push. Do not force-push.
Authenticate through trusted Git/GitHub flows, never tokens embedded in remote URLs/configuration.

Include `keil_port_tool.py`, `kps_core/`, `docs/`, `tests/`, `tools/`, `.github/`,
`.gitignore`, the READMEs, `CONTRIBUTING.md`, `LICENSE`, `THIRD-PARTY-NOTICES.md`
and `requirements-build.txt`. Review staged files: ignore rules do not untrack history.
Exclude releases/build caches, local logs/settings, virtual environments, secrets, SDKs and raw hardware archives.

## Draft a prerelease

In Releases, create a draft for `v2.2.0-rc6` targeting the commit matching the built
source. Suggested title: `Keil Port Studio 2.2.0-rc6 — CubeMX/HAL`.
Use the RC6 notes, mark it as a prerelease and attach these from one build:

- `KeilPortStudio-Windows-x64.zip`
- `KeilPortStudio-source.zip`
- `SHA256SUMS.txt`

Review draft attachments, notes and tag before publishing. See GitHub's official
[release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
Distribute binaries as release assets rather than Git history; see the
[file-size guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

## After uploading

Check actual Actions results; local tests are not proof of hosted CI success.
Redownload assets, verify SHA256, extract the entire folder and check documentation links.
Publish fixes as a new version instead of silently replacing an existing build.
Use Issue templates for redacted reproductions and board evidence.

These are instructions only; no repository, commit, push or release is created automatically.
