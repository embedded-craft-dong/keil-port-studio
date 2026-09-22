# CubeMX/HAL adaptation scope

[简体中文](SUPPORT.zh-CN.md)

## Intended projects

This release primarily targets STM32 HAL + Keil MDK `.uvprojx/.uvproj` projects generated
by STM32CubeMX. This is a scope statement, not certification of every CubeMX release,
MCU, board or component combination. Consult the [hardware matrix](HARDWARE-MATRIX.en.md)
for exact evidence; the main hardware target is STM32F407.

| Project or feature | Boundary |
| --- | --- |
| CubeMX/HAL + Keil | Primary integration path; entry points, clocks, interrupts and drivers still need review |
| SPL / custom layouts | General project management is independent; automatic porting and hardware acceptance are incomplete |
| RT-Thread | Selected kernel version on single-core Cortex-M4; not full BSP/package management |
| CMSIS Solution / RTE / Packs | No complete management; not a replacement for official generators |
| CubeMX `.ioc` | Not updated; regeneration requires manual reconciliation and retesting |

## Preflight and regeneration

1. Use a project copy or committed Git baseline. Close Keil and verify the original project builds and runs.
2. Check Target, MCU and compiler. Automatic integration depends on `Core/Src/main.c`, referenced
   interrupt sources and CubeMX `USER CODE` markers. Do not force past missing/ambiguous anchors or rejected patches.
3. Preview changes and read warnings. Tool completion or a successful build is not proof of runtime correctness.
4. Avoid duplicate middleware already managed by CubeMX, RTE or manual porting. Check scheduler,
   SysTick/SVC/PendSV and USB controller/driver ownership. Conflict detection cannot cover all custom code.
5. Implement hardware interfaces using the [post-porting guide](POST-PORTING.en.md), then test functionality,
   sustained operation and power-loss behavior.
6. Back up before CubeMX regeneration. Review project references, configuration, startup and interrupt changes,
   then rebuild and retest. `USER CODE` markers do not guarantee preservation of every tool modification.

## Disclosed limitations

- SPL can run these middleware libraries; this tool's complete automatic integration has not been verified.
  Adding markers alone is not sufficient adaptation.
- Software-controlled USB3 VBUS shutoff failed on the test board, with unresolved cause. Passing thumb-drive
  I/O and actual power-loss retention are separate results.
- Clean Windows without Python/Git remains untested; the EXE is unsigned; hosted CI must actually run after upload.
- No complete middleware offline bundle is included. Select complete local SDKs per component;
  missing dependencies may trigger network preparation. Offline guides do not mean all migration is offline.

Use repository Issue templates with sanitized versions, Target, logs and reproduction steps; do not submit credentials or private projects.
