# Project health check (read-only, development version)

[简体中文](DOCTOR.zh-CN.md)

Available in source starting at `2.3.0-dev1`; the published RC6 EXE does not include it.

## GUI

Select a project and Target, then open **Project tools → Project health check (read-only)**.
The scan runs in a worker. Read the scrollable report or export JSON. Inspection does not
modify projects, sources, IOC files, transactions or firmware; it neither downloads SDKs nor builds.
Only an explicit export creates a report, and existing files are never overwritten. Choose a new name.

## CLI

```powershell
python keil_port_tool.py Board.uvprojx --doctor --target Debug --doctor-language en
python keil_port_tool.py Board.uvprojx --doctor-json health.json
python keil_port_tool.py Board.uvprojx --doctor --doctor-ioc Board.ioc
```

Repeat `--target` as needed; by default each Target is inspected separately, with no cross-Target
pin merging. Health checks cannot be combined with porting, settings, uninstall, rollback,
other exports or builds. Exit 0 means the report was produced, **not hardware acceptance**;
automation should read JSON `summary` and `issues`.
`--dry-run` cannot be combined with the file-writing `--doctor-json` option.

## Initial scope

- Missing referenced files and duplicate files within a Target.
- Missing Target-level Include directories and unresolved path variables.
- Explicit HAL AF GPIO initialization and SPL `GPIO_PinAFConfig` in enabled, referenced C/C++ files.
- IOC files in the project directory (also the parent for common MDK layouts). Multiple candidates
  are not guessed; select one explicitly through CLI.
- Files/groups with `IncludeInBuild=0` are excluded. Existing XML namespace handling and GBK source reading are supported.

For example, PD5 assigned to both UART2 and FSMC produces `PIN_AF_CONFLICT`, listing
files, line numbers, peripherals and Target. Review LCD write-control and UART ownership.
The check **does not rewire, edit IOC or select alternate pins**.

## Interpretation and limits

- `error`: a referenced file is missing; other separately invoked operations are not automatically blocked.
- `warning`: candidate pin conflict, duplicate reference, missing Include or skipped source; review required.
- `info`: unresolved path variables or ambiguous IOC selection.

A candidate does not prove simultaneous peripheral use: initialization may never be called,
may be conditionally compiled, or IOC may be stale. Review the startup flow. Repeated
configuration of the same peripheral is not treated as a different-peripheral conflict.

No full C preprocessor, call graph, header initializers, complex macros/aliases, direct registers,
external static libraries, file-level Include overrides or physical wiring are analyzed.
Only recognizable literal `#if 0/1` branches are excluded. Limits are 2 MiB per file,
32 MiB total source and 1500 unique source files; skipped reads are reported.
No findings does not certify pin correctness, compilation, scheduling, memory or runtime behavior.

Reports contain project paths and source locations. Redact them before sharing; do not upload private projects.
