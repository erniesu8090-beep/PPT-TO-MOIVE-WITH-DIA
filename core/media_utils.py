import json
import math
from pathlib import Path
import subprocess


def get_audio_duration(file_path):
    """Read a real duration. Invalid audio must never silently become five seconds."""
    file_path = Path(file_path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        raise RuntimeError(f"音檔不存在或是空檔：{file_path.name}")
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'json', str(file_path)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    except FileNotFoundError as exc:
        raise RuntimeError('找不到 ffprobe，請安裝 FFmpeg 並加入 PATH。') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"讀取音檔時長逾時：{file_path.name}") from exc
    if result.returncode:
        raise RuntimeError(f"無法讀取音檔 {file_path.name}：{result.stderr.strip()[-500:]}")
    try:
        duration = float(json.loads(result.stdout)['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('duration must be positive and finite')
        return duration
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"音檔時長無效：{file_path.name}") from exc
