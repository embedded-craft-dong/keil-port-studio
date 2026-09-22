# RC2/RC3 reliability work and validation limits

[简体中文](RELIABILITY.zh-CN.md)

No new middleware was added. This iteration prioritizes false-success startup integration and unsafe removal of user code.

## Changed behavior

- Critical startup patches report explicit outcomes: applied, already correct, not applicable, conflict or unsupported. An unverified critical patch rejects planning; the combined operation writes no project files.
- Automatic SVC/PendSV takeover accepts empty stubs, not user logic. Comments and strings do not count as calls. Unknown preprocessor structures, duplicate definitions or custom startup control flow can require manual integration. These are conservative checks, not a complete C parser or proof of runtime correctness.
- The manifest records actual project-relative source paths and owned edit fragments. Uninstall no longer guesses `stm32f4xx_it.c`. It reverses only uniquely located owned edits, preserving unrelated changes; edited/ambiguous owned regions stop removal.
- Legacy records without ownership for changes to existing sources are not guessed. Review a transaction rollback or migrate manually after backing up. Rollback can overwrite changes made after that transaction.
- XML is reparsed before writing and checked against the planned tree, Target identities and untouched Targets. External project edits prevent overwrite. Serialization still uses ElementTree: **byte-for-byte minimal XML diffs are not implemented**.
- Downloads, transaction planning/copying/hashing/writing, uninstall, rollback and Keil builds run on a worker. Logs, progress and confirmation dialogs return to Tk's owning thread. Busy operations prevent re-entry, language changes and closing. Cancellation is available at the diff preview, not as forced interruption during writes.
- Final download failures explain how to configure a mirror/proxy or use local sources. Reverse translation collisions now fail explicitly rather than silently losing keys.

## Source layout

`keil_port_tool.py` retains the entry point, CLI and compatible function exports. The new `kps_core/` contains:

- `source_patches.py`: GUI-independent startup/exception text patches.
- `ownership.py`: reversible edit records and conservative removal.
- `runtime.py`: thread-local operation callbacks.
- `errors.py`: shared exception type.

Keep `kps_core` beside the entry script; copying only the PY entry point is no longer sufficient. Core modules import without initializing GUI or download configuration. GUI, XML and all component tasks have not yet been fully separated; further extraction is incremental and test-driven.

## Verification performed

New tests exercise unknown/partial patches, comment false positives, user exception logic, non-F4 uninstall paths, edit protection, external XML edits, unselected Targets, translation collisions and LwIP ownership. Real Tk event-loop tests check worker heartbeat responsiveness, confirmations, errors, re-entry and close protection, not just the existence of callbacks.

Hardware: the existing Puzhong STM32F407ZGT6, ARMCC 5.06 update 7, UART2:

- Fresh RT-Thread 5.2.2 kernel: verified flash, then 3 resets, each with 23 passing checks including 2,000 ordered queue messages, floating-point context switching, software timers and HAL/RTOS timebases.
- Fresh FreeRTOS 10.5.1 + CMSIS-V2: verified flash, then 3 resets, each with 95 passing checks covering notifications, queues, semaphores, mutexes, timers, message/stream buffers, task lifecycle and heap reclamation; continuing message exchange reported zero errors.
- FreeRTOS compiled with 0 errors/0 warnings. RT-Thread retained 2 upstream AC5 warnings; they were not hidden.
- A fresh RT-Thread + LVGL 8.4 + CMSIS-DSP + RTT combination was flashed/verified and reset 3 times, with 19 serial checks passing per reset and zero DMA errors. Full rebuild: 0 errors/25 third-party source warnings. The user confirmed no visible defects in this build, separately from frame counters.
- All 18 isolated software test scripts passed, including a real Keil build. The candidate EXE was extracted into a Unicode/space-containing path and launched with only System32 on PATH. Actual desktop clicks exercised scanning, preview cancellation, confirmed writes to a disposable project, language switching and English offline guides; manifest/backups were checked on disk. Python remains installed on this machine, so this is not a clean-VM test.

Raw logs and device identifiers remain local and outside public source archives. The release validation record supplements these results with the final combined-display and extracted-binary checks.

## Not certified by these results

RC3 adds the listed board's three storage modes, native USB and FreeRTOS/RT-Thread
network rechecks, plus read-only storage validation after a real power cycle; see the
[hardware matrix](HARDWARE-MATRIX.en.md). This is not coverage of every MCU/compiler/
component combination. USB Host, IPv6/MQTT/TLS, arbitrary display drivers,
MDK6/CMSIS Solution, RTE/Pack management and CubeMX `.ioc` synchronization are not
certified. Large tree-widget population and some UI operations can still pause briefly.
There is no forced cancellation throughout a transaction. An independent clean-Windows
acceptance without installed Python/Git remains on the release checklist.

Run regressions with per-process evidence:

```powershell
python tools/run_tests.py test-results
```

The output directory must not exist. CI retains failed/timed-out runs and rejects unsafe Tk thread-finalization errors. Passing tests does not replace wiring checks or post-porting integration instructions.
