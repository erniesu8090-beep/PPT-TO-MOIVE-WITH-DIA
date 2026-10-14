"""Cooperative pause at safe boundaries; an active encoder is allowed to finish."""
import threading
import time
from pathlib import Path


class JobControl:
    def __init__(self):
        self.condition = threading.Condition()
        self.active = False
        self.requested = False
        self.paused = False
        self.marker = None
        self.stage = "尚未開始製作"
        self.result = None
        self.log_url = None

    def snapshot(self):
        with self.condition:
            return dict(active=self.active, requested=self.requested,
                        paused=self.paused, stage=self.stage, result=self.result, log_url=self.log_url)

    def start(self, marker):
        with self.condition:
            self.marker = Path(marker)
            self.marker.unlink(missing_ok=True)
            self.active, self.requested, self.paused = True, False, False
            self.stage = "準備製作"
            self.result = None
            self.log_url = "/renders/logs/" + self.marker.with_suffix('.log').name

    def request(self, pause):
        with self.condition:
            if not self.active:
                raise ValueError("目前沒有正在製作的影片。")
            if pause:
                self.marker.touch()
            else:
                self.marker.unlink(missing_ok=True)
            self.requested = pause
            if not pause:
                self.paused = False
            self.condition.notify_all()
            return self.snapshot()

    def checkpoint(self):
        with self.condition:
            while self.requested:
                self.paused = True
                self.condition.wait(timeout=0.5)
            self.paused = False

    def finish(self):
        with self.condition:
            if self.marker:
                self.marker.unlink(missing_ok=True)
            self.active = self.requested = self.paused = False
            self.condition.notify_all()


def child_checkpoint(marker):
    if marker and Path(marker).exists():
        print("CONTROL:paused", flush=True)
        while Path(marker).exists():
            time.sleep(0.25)
        print("CONTROL:running", flush=True)
