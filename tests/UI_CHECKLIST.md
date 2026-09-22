# UI refresh verification

Run on Windows with a graphical desktop:

```powershell
python tests/test_gui_interaction.py
python tests/smoke_gui.py
```

The interaction tests do not download libraries, write Keil projects, access
serial ports or flash target hardware. A screen/display is required; the
interaction test skips when Tk cannot open a display.

Covered automatically:

- All eight navigation entries and nested component pages remain reachable.
- File trees remain at least 140 pixels high at the 980 x 650 minimum test size.
- Enabling a function uses the custom tick element, and Space toggles it.
- FreeRTOS + RT-Thread selection conflict disables execution without silently
  changing either selection. Backend rejection remains in place.
- File/group toggles, partial selection, preset restoration and same-root refresh
  selection retention; new files are checked by default.
- Expanding a directory does not toggle its selection.
- Opening logs in a separate window does not shrink file trees.
- Bottom actions remain inside the window; settings can scroll to the last option.
- 100%, 125% and 150% Tk scaling layouts. This does not certify all Windows
  mixed-DPI multi-monitor combinations.

Manual visual review of actual captured windows completed for the LVGL,
FreeRTOS, settings and network pages at 980 x 650, 1180 x 800 and 150% scaling.
Checked = tick, unchecked = empty square, partial = horizontal dash. Screenshots
use a synthetic source listing, not an assertion that those paths are installed.

Full local regression after this change: all 12 `test_*.py` scripts passed,
including the five native GUI interaction cases. This is not a remote CI result.

Known scope: this refresh changes layout/selection interaction and DPI handling.
It does not convert the existing scan/download/migration routines to cancellable
background jobs. Long synchronous operations can still occupy the Tk main thread.
