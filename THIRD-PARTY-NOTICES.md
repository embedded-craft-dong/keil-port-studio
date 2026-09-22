# Third-party notices / 第三方许可说明

Keil Port Studio's own source is MIT. That does not relicense imported middleware,
Keil/ARM tools, board support code, Python or Git.

本工具的 MIT 许可不覆盖用户导入的中间件、芯片库、Keil/ARM 工具或 Git。
发布包不包含 Keil 编译器、Git 安装器、板卡固件或中间件源码。

## Portable runtime

The portable Windows build includes CPython and Tcl/Tk, plus PyInstaller's bootloader.
Their license texts (and build-tool notices) are supplied in `runtime-licenses`.
PyInstaller's bootloader exception permits distributing generated applications under
their own license; retain applicable third-party notices. The source archive does not
require PyInstaller for normal execution. `requirements-build.txt` lists build dependencies.

## Imported components

Common upstream licenses include FreeRTOS (MIT), RT-Thread (Apache-2.0), LVGL (MIT),
LittleFS (BSD-3-Clause), LwIP (BSD-style), TinyUSB (MIT), and CMSIS-DSP (Apache-2.0).
FatFs uses its upstream permissive notice; SEGGER RTT has its own redistribution terms.
These labels are orientation, not a substitute for the license at your exact source
revision. Vendor wrappers, drivers, fonts, examples and bundled dependencies may differ.

Keep copied license headers and upstream LICENSE/COPYING files. Use the project's
license-report tool as an inventory aid, not a legal certification or complete SBOM.
Review every component/version before redistribution or company use.
