"""Isolated regression runner; save failures/timeouts, never silently skip them.

Usage: python tools/run_tests.py NEW_OUTPUT_DIRECTORY [--timeout 240]
KPS_REAL_BUILD_PROJECT optionally enables the real Keil GUI build fixture.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('timeout must be positive')
    repo = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    results = []
    for test in sorted((repo / 'tests').glob('test_*.py')):
        start = time.monotonic()
        timed_out = False
        try:
            run = subprocess.run([sys.executable, str(test)], cwd=str(repo),
                                 capture_output=True, timeout=args.timeout)
            output, code = run.stdout + run.stderr, run.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b'') + (exc.stderr or b'')
            code, timed_out = 124, True
        # Python can exit 0 despite unsafe Tk finalization on a worker thread.
        unsafe_tk = bool(re.search(rb'Tcl interpreter is leaked|main thread is not in main loop|Exception ignored while calling deallocator', output))
        if unsafe_tk and not code:
            code = 1
        (out / (test.stem + '.log')).write_bytes(output)
        results.append({'test': test.name, 'code': code,
                        'seconds': time.monotonic() - start,
                        'timed_out': timed_out, 'unsafe_tk_finalization': unsafe_tk})
        (out / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print('%s: %s' % (test.name, 'PASS' if code == 0 else 'FAIL (%d)' % code), flush=True)
    return int(any(result['code'] for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
