"""Thread-local operation callbacks, independent of Tk and global settings."""
import contextlib
import threading
from types import SimpleNamespace

_OPERATION_LOCAL = threading.local()


@contextlib.contextmanager
def operation_context(log_sink=None, progress_sink=None, ui_call=None, level='normal'):
    """Per-operation callbacks; background jobs never call a Tk sink directly."""
    previous = getattr(_OPERATION_LOCAL, 'value', None)
    current = SimpleNamespace(log_sink=log_sink, progress_sink=progress_sink,
                              ui_call=ui_call, level=level)
    _OPERATION_LOCAL.value = current
    try:
        yield current
    finally:
        _OPERATION_LOCAL.value = previous
