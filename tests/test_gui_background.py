"""Real Tk event-loop tests; no board/network/user files are touched."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location('background_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.g = object.__new__(m.KeilPortGUI)
        self.g.root = m.tk.Tk()
        self.g.root.withdraw()
        self.addCleanup(self.g.root.destroy)
        self.g.status_var = m.tk.StringVar(master=self.g.root, value='testing')
        self.g.progress_var = m.tk.IntVar(master=self.g.root, value=0)
        self.main = threading.get_ident()
        self.delivered = []
        self.g.append_log = lambda message: self.delivered.append(('log', threading.get_ident(), message))
        self.g.update_progress = lambda value, message: self.delivered.append(('progress', threading.get_ident(), value))
        self.errors = []
        self.g.root.report_callback_exception = lambda *args: self.errors.append(args)

    def test_worker_does_not_block_event_loop_or_call_tk_sinks(self):
        beats = []
        def heartbeat():
            beats.append(time.monotonic())
            if self.g._operation_busy:
                self.g.root.after(10, heartbeat)
        self.g.root.after(0, heartbeat)
        def backend():
            self.assertNotEqual(threading.get_ident(), self.main)
            for value in range(10):
                m.log('worker %d' % value)
                m.set_progress(value, 'testing')
                time.sleep(.03)
            return 42
        self.assertEqual(self.g._background_call(backend), 42)
        self.assertGreater(len(beats), 10)
        self.assertEqual(len(self.delivered), 20)
        self.assertTrue(all(item[1] == self.main for item in self.delivered))
        self.assertEqual(self.errors, [])
        self.assertFalse(self.g._operation_busy)

    def test_confirmation_marshaled_and_cancel_returned(self):
        def callback():
            self.assertEqual(threading.get_ident(), self.main)
            with self.assertRaises(m.ToolError):
                self.g._background_call(lambda: None)
            self.g._close()  # ignored while a worker is live
            self.assertTrue(self.g.root.winfo_exists())
            return False
        def backend():
            context = m._OPERATION_LOCAL.value
            return context.ui_call(callback)
        self.assertFalse(self.g._background_call(backend))
        self.assertEqual(self.errors, [])

    def test_errors_release_busy_and_context(self):
        def failed():
            raise m.ToolError('fixture failure')
        with self.assertRaisesRegex(m.ToolError, 'fixture failure'):
            self.g._background_call(failed)
        self.assertFalse(self.g._operation_busy)
        self.assertIsNone(getattr(m._OPERATION_LOCAL, 'value', None))
        self.assertEqual(self.g._background_call(lambda: 'next operation'), 'next operation')

    def test_ui_exception_reaches_worker_and_caller(self):
        def failed_ui():
            raise ValueError('preview failed')
        def backend():
            return m._OPERATION_LOCAL.value.ui_call(failed_ui)
        with self.assertRaisesRegex(ValueError, 'preview failed'):
            self.g._background_call(backend)
        self.assertFalse(self.g._operation_busy)


class ContextTests(unittest.TestCase):
    def test_nested_contexts_and_parallel_jobs_do_not_cross_logs(self):
        jobs = {name: [] for name in ('one', 'two')}
        gate = threading.Barrier(2)
        def run(name):
            with m.operation_context(log_sink=jobs[name].append):
                gate.wait(timeout=3)
                m.log(name)
                with m.operation_context(level='quiet'):
                    m.log('must not leak')
                m.log(name)
        threads = [threading.Thread(target=run, args=(name,)) for name in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(jobs, {'one': ['one', 'one'], 'two': ['two', 'two']})


if __name__ == '__main__':
    unittest.main()
