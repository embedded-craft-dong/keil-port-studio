# Toolbar and Git test report

## 2026-09-22 / RC4 pre-release software regression

All 19 `test_*.py` scripts passed, without timeouts or detected unsafe Tk finalization.
The explicitly enabled real Keil fixture rebuild finished with zero errors and two
retained upstream RT-Thread warnings. Actual LittleFS/LwIP AC5 checks also passed.
Six publication allowlist/sensitive-pattern/link/version tests were added. Earlier
RC1/RC2 mouse-interaction records below remain historical, not acceptance of the new RC4 ZIP.

2026-09-21 / RC2 addendum: all 18 regression scripts passed, including 5 new real-Tk background-operation tests.
Preview tests now wait for the actual dialog instead of a fixed delay. Unsafe Tk finalization is a test failure.
Actual desktop clicks in the extracted candidate EXE verified scan, preview cancel/confirm, disposable-project writes,
language switching and offline guides. The original RC1 record below is retained; see the [reliability notes](RELIABILITY.en.md).

Date: 2026-09-20. This distinguishes real backend/Tk callback tests from desktop
mouse tests and hardware validation. No test result implies universal board support.

## Toolbar findings and fixes

Initial callback integration tests reproduced four failures: presets lost project
settings, invalid presets partially changed live values, removed scan exclusions
remained active, and export write errors escaped without a visible error dialog.
These were fixed and regression tested. Compilation also now rejects old/stale logs
and output with no fresh error/warning summary. Multi-Target logs have distinct names.

`tests/test_gui_actions.py` invokes actual Tk buttons with real temporary files and
backend operations. Only native file/confirmation dialogs are substituted (plus an
explicit build-failure injection). It covers settings/restart/cancel/validation,
presets and pending selections, Markdown/CSV/JSON exports, licenses, gitignore,
preview/cancel/uninstall/rollback, logs and build errors.

Actual Windows mouse checks opened Settings, Presets, Project tools, Safety & recovery,
and Logs, exported an inventory using the native Save dialog, and launched Keil from
the button. Destructive desktop clicks were not performed: uninstall/rollback were
verified through isolated callback integration tests instead.

The copied RT-Thread fixture's full rebuild: **0 errors, 2 upstream warnings**.
An incremental desktop-button build reported **0 errors, 0 warnings** (not a full rebuild).
Source originals and firmware on the board were not modified by these GUI tests.

## Git and localization

`test_git_panel.py` uses real Git and Tk callbacks. A local bare repository and a second
checkout verify push/fetch/pull, dirty-worktree rejection, diverged history rejection
and non-fast-forward push rejection. Chinese/spaced/leading-dash filenames, staging,
unstaging, renames, deletions, history and timeout reporting are exercised. Missing
Git and installer cancellation are simulated; no existing Git was uninstalled.

`test_gui_language.py` checks live switching, selection preservation, canonical preset
values across languages, saved preferences and failed-settings-write handling.
The final local regression run passed all 16 `test_*.py` scripts, with the real Keil
build explicitly enabled. GitHub-hosted CI and clean-machine Git installation are
not represented by this result. Release smoke results are recorded separately.

`test_assert_side_effects.py` compiles and executes generated TinyUSB/LwIP FreeRTOS
initialization bodies with host API doubles, with assertions both enabled and
disabled. This guards task-creation side effects disappearing in release builds;
it is not a physical USB/Ethernet re-test. Installer acceptance/winget routing and
official-site fallback are mocked, while the actual Windows installer remains untested.

## Reproduce

September 26 development-source addendum: actual mouse use exposed an unresponsive
Close button in the packaged Doctor report opened from Project tools. Tk's input
grab remained with the parent. The report now takes the grab and returns it to
the surviving parent on close. A regression first reproduced the failure, then
checked button/title-bar close and Chinese/English entry paths. All 29 test
scripts passed, including the explicitly enabled real Keil fixture build. This
does not replace actual clicks on a rebuilt EXE or clean-Windows acceptance.

Subsequent actual mouse checks on local B05 passed: closing the Doctor report
restored the parent, language switching worked, scanning selected 111 files and
unchecking one showed 110 with partial parent selection, and safety/log windows
opened and closed. Extracted-package CLI add/export/exact rollback, CubeMX recovery
and SPL install checks passed. No GitHub publication or Python/Git-free clean
Windows acceptance is claimed.

```powershell
python tests/test_gui_actions.py
python tests/test_git_panel.py
python tests/test_gui_language.py
# Optional, on a disposable COPY of a project with Keil installed:
$env:KPS_REAL_BUILD_PROJECT='D:\TestCopy\MDK-ARM\Project.uvprojx'
$env:KPS_UV4='D:\Keil_v5\UV4\UV4.exe'
python tests/test_gui_actions.py
```

Tk tests need a graphical desktop. Tests that require Keil are skipped without the
explicit environment setting; Git integration tests skip if Git is unavailable.
