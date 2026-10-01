import os
import json
import time
from datetime import datetime
from pathlib import Path

class UsageTracker:
    def __init__(self, data_file=None, daily_limit=100):
        if data_file is None:
            data_file = Path(__file__).parent / "usage.json"
        self.data_file = Path(data_file)
        self.daily_limit = daily_limit
        self._load()

    def _today_str(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _load(self):
        today = self._today_str()
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        self.used = data.get("used", 0)
                        self.total_seconds = data.get("total_seconds", 0.0)
                        self.total_chars = data.get("total_chars", 0)
                        self.history = data.get("history", [])
                        return
            except Exception:
                pass
        
        # New day or first run
        self.used = 0
        self.total_seconds = 0.0
        self.total_chars = 0
        self.history = []
        self._save()

    def _save(self):
        data = {
            "date": self._today_str(),
            "limit": self.daily_limit,
            "used": self.used,
            "remaining": max(0, self.daily_limit - self.used),
            "total_seconds": round(self.total_seconds, 2),
            "total_chars": self.total_chars,
            "history": self.history[-30:] # keep last 30 entries
        }
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_status(self):
        self._load()
        remaining = max(0, self.daily_limit - self.used)
        pct = round((self.used / self.daily_limit) * 100, 1) if self.daily_limit > 0 else 0
        return {
            "date": self._today_str(),
            "limit": self.daily_limit,
            "used": self.used,
            "remaining": remaining,
            "usage_pct": pct,
            "total_seconds": round(self.total_seconds, 1),
            "total_chars": self.total_chars
        }

    def can_request(self, count=1):
        self._load()
        return (self.used + count) <= self.daily_limit

    def record_request(self, count=1, chars=0, seconds=0.0, note=""):
        self._load()
        self.used += count
        self.total_chars += chars
        self.total_seconds += seconds
        self.history.append({
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "count": count,
            "chars": chars,
            "seconds": round(seconds, 2),
            "note": note
        })
        self._save()
        return self.get_status()
