# CubeMX coexistence: one owner and regeneration checks

[简体中文](CUBEMX.zh-CN.md)

New safeguards in `2.3.0-dev1` source, not the published RC6 EXE. The tool cannot
stop a separate CubeMX process overwriting files, and does not rewrite `.ioc`.
Protection combines isolated source directories, preserved integration blocks,
ownership checks, and explicit previewed recovery.

## Why adding one pin could remove FreeRTOS

On 2026-09-25 we reproduced this with the installed CubeMX: FREERTOS disabled in
IOC, tool-installed kernel building successfully, then only PA0 input added.
Regeneration deleted `Middlewares` while Keil still referenced its files, and
overwrote the SVC/PendSV guards outside USER CODE regions. It was not just a naming collision.

New CubeMX installs keep libraries in `KPS/ThirdParty/<component>` and FreeRTOS
task files in `KPS/FreeRTOS/App/freertos_app.c/.h`. New exception aliases live in
the interrupt file's preserved USER CODE Includes block. Only confirmed-empty
CubeMX stubs are renamed in that translation unit; vectors still use the real
FreeRTOS assembly handlers. Do not add application work to those empty stubs.
Existing projects are not silently relocated by a normal installation.

## Choose one owner per middleware

| Component | CubeMX owns it | Keil Port Studio owns it |
| --- | --- | --- |
| FreeRTOS | Keep CubeMX kernel/config/tasks; do not install FreeRTOS or RT-Thread again | Leave FREERTOS disabled in IOC; tool creates tasks/startup |
| FatFS / LwIP | Retain CubeMX core, config and drivers | Disable that middleware in IOC; use project-local tool copies and board-specific drivers |
| USB | ST USB library already owns a controller | Resolve controller, ISR and VBUS ownership before adding TinyUSB; different library names do not mean separate hardware |
| LVGL / LittleFS / RTT etc. | CubeMX may still initialize peripherals | Tool owns middleware sources; hardware integration remains board-specific |

Checks inspect IOC `Mcu.IPn` entries for FREERTOS/FATFS/LWIP/USB_DEVICE/USB_HOST
and common middleware definitions in enabled project sources. Disabling an IOC
option without removing/regenerating old sources may still be rejected. This is
not full Pack management or C dependency analysis. Renamed custom cores and
intentional dual-controller USB setups require manual review.

## Workflow

1. Work in a copy or clean Git baseline. Configure clocks, pins and peripherals in CubeMX.
2. Choose middleware owners, generate, then verify the baseline project.
3. Close Keil. Preview/install with this tool, then validate the application on hardware.
4. Before another CubeMX generation, commit/back up the project and `.keil-port-tool`
   state. For legacy `Middlewares/Third_Party` copies, use **Project tools → Isolate
   legacy components before CubeMX…** first. It copies current project sources,
   including user edits, not a freshly downloaded SDK. Old library trees remain
   unreferenced; old FreeRTOS app files are retained as `.kps_migrated_bak` backups.
   The transaction can be rolled back through Safety & recovery. Preserve
   ownership state. Enable Keep User Code, but do not treat it as complete protection:
   exception guards, project XML, macros and include paths may still change.
5. After generation, do not flash immediately. Open **Project tools → Project health
   check (read-only)**, or run:

```powershell
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --doctor
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --doctor-json .\regen-check.json
```

6. For missing recorded references, paths, defines or uniquely identifiable hooks,
   use **Project tools → Restore integration after CubeMX…** and confirm its diff.
   It does not restore an old entire main.c or project XML: newly generated pins
   and peripherals remain intact. Recheck, build and hardware-test. Do not delete the manifest to hide
   findings or blindly rerun installation to overwrite user work.

```powershell
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-protect --dry-run
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-protect
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-recover --dry-run
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-recover
```

These are separate operations, not installation flags. Changed integration code,
conflicting define values, disabled files, renamed targets and ambiguous matches
stop recovery. Missing source requires a pre-generation Git/complete backup first;
an old SDK/template cannot recover subsequent user edits. Select all Targets because
source files may be shared. Transactions do not undo CubeMX itself.

`.keil-port-tool` can contain local SDK paths and is normally gitignored. Include it
in a private complete backup, not a forced public Git upload. A Git commit alone
does not necessarily retain ownership/transaction records.

## Finding codes

- `CUBEMX_MIDDLEWARE_OWNER`: IOC and this tool would own the same middleware.
- `FOREIGN_MIDDLEWARE_OWNER`: an active middleware core/entry is not registered as tool-owned.
- `OWNED_STARTUP_DRIFT`: a recorded source patch is missing or modified, possibly
  due to regeneration or manual edits; the check does not assume which caused it.
- `INSTALLED_REFERENCE_LOST`, `INSTALLED_INCLUDE_LOST`, `INSTALLED_DEFINE_LOST`:
  installed references, paths or macros were removed, disabled or changed.
- `GENERATED_FILE_LOST / INSTALLED_SOURCE_LOST`: recorded generated/source file missing.
- `CUBEMX_LEGACY_DIRECTORY`: isolate copies in generator-owned directories first.
- `CUBEMX_USER_CODE_NOT_PRESERVED`: IOC explicitly disables Keep User Code.
- `OWNERSHIP_RECORD_INVALID`: ownership state/files are unreadable or invalid.

On errors, middleware/project-setting operations stop before downloading, copying
or writing. Plain file addition stays independent, not an endorsement of existing
middleware correctness. Ordinary task-body edits are not rejected merely because
a whole-file hash changes; owned patch areas and project entries are checked.
Health checks never write. Isolation and recovery require separate preview/confirmation.

## Actual validation

- Native FreeRTOS and CMSIS-V2: actual CubeMX generation, install, user task edit,
  PA0 addition and regeneration. All 707 KPS files in each fixture retained their
  hashes, including user edits, and the new GPIO configuration was generated.
- Both before/after AC5 builds: zero errors and warnings; ownership checks clean.
- F407 hardware: ten RAM samples over about 22 seconds for each variant; task count,
  RTOS tick and HAL tick continuously advanced at about 1000/s, with HAL/RTOS
  differing by at most one tick. Flashing was verified/reset and the accepted SPL
  display firmware was restored afterward.
- Regression tests cover cancellation, idempotency, rollback, user-source preservation,
  refused conflicts and real GUI button backends.
- An intact legacy-layout fixture was isolated, regenerated by real CubeMX and had
  its missing legacy exception guards recovered. Full AC5 rebuild: zero errors/warnings,
  without restoring an old entire project or reverting the new GPIO.

- RT-Thread 5.2.2 also underwent actual CubeMX regeneration after adding PA0.
  All 127 isolated source/config/task files retained their hashes and user task edits.
  Regeneration removed the legacy-style PendSV/HardFault guards; drift was detected.
  Explicit post-generation recovery restored the guards while retaining the new GPIO.
  Before and recovered full AC5 builds both had 0 errors and 2 upstream warnings.
- That recovered RT-Thread firmware was programmed, verified and reset. Ten RAM
  samples over about 22 seconds showed the default task heartbeat near 1/s, RTOS/HAL
  ticks near 1000/s with at most one tick difference, and a zero assertion line.
  Accepted SPL display firmware was restored, verified and reset afterward. No
  storage/network/USB operations were performed.

RT-Thread currently still requires checking and explicitly recovering exception
guards after regeneration; this is not transparent two-way synchronization.
Evidence is limited to these F407/AC5/FreeRTOS/RT-Thread configurations, not every
component, CubeMX release or project layout.

## Not promised

No two-way IOC sync, CubeMX plugin, automatic post-generation execution or arbitrary
three-way code merge. Custom projects may escape static detection. Regression tests
that simulate overwritten files and tests that actually run CubeMX must be reported
separately; a fabricated overwrite is not a real CubeMX execution.
