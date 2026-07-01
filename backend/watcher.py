#!/usr/bin/env python3
"""Dev watcher: restarts main.py when any .py file changes.
Uses PollingObserver — works on Windows Docker volumes (no inotify needed)."""
import subprocess, sys, time
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

class _Handler(FileSystemEventHandler):
    def __init__(self): self.pending = False
    def on_any_event(self, e):
        if not e.is_directory and str(e.src_path).endswith('.py'):
            self.pending = True

handler = _Handler()
obs = PollingObserver(timeout=2)
obs.schedule(handler, '/app', recursive=True)
obs.start()

proc = subprocess.Popen([sys.executable, '-u', 'main.py'], cwd='/app')

try:
    while True:
        time.sleep(2)
        if handler.pending:
            handler.pending = False
            print('[watcher] .py changed — restarting', flush=True)
            proc.terminate(); proc.wait()
            proc = subprocess.Popen([sys.executable, '-u', 'main.py'], cwd='/app')
        elif proc.poll() is not None:
            print(f'[watcher] exited ({proc.returncode}) — restarting', flush=True)
            proc = subprocess.Popen([sys.executable, '-u', 'main.py'], cwd='/app')
finally:
    obs.stop(); obs.join(); proc.terminate()
