"""Stage an entire import before publishing; retain replaced files for recovery."""
import json
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_PATHS = ("assets/images", "assets/narration", "口白_cleaned.json",
                 "timings.json", "timings_dynamic.json", "project_info.json")


def import_project(workspace, payload, render_slides, parse_narration, timings, decode, extract):
    workspace = Path(workspace)
    backup = workspace.parent / "renders" / "project-backups" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    with tempfile.TemporaryDirectory(prefix="import-", dir=workspace) as temp:
        stage = Path(temp)
        (stage / "assets").mkdir()
        voice = workspace / "voice_settings.json"
        if voice.exists():
            shutil.copy2(voice, stage / voice.name)
        count = render_slides(decode(payload["slide_pdf"]), workspace=stage)
        if not count:
            raise ValueError("簡報沒有可匯入的頁面。")
        narration = payload.get("narration_data") or ""
        if payload.get("narration_type") == "pdf":
            narration = extract(decode(narration))
        parse_narration(narration, count, workspace=stage)
        timings(workspace=stage)
        (stage / "project_info.json").write_text(json.dumps({
            "slide_pdf_name": payload.get("slide_pdf_name", "簡報.pdf"),
            "project_id": uuid.uuid4().hex,
        }, ensure_ascii=False), encoding="utf-8")
        backup.mkdir(parents=True)
        moved, published = [], []
        try:
            for relative in PROJECT_PATHS:
                source, previous = workspace / relative, backup / relative
                if source.exists():
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(previous)
                    moved.append(relative)
                source.parent.mkdir(parents=True, exist_ok=True)
                (stage / relative).replace(source)
                published.append(relative)
        except Exception:
            # Move new files back into staging; restore all old files in reverse order.
            for relative in reversed(published):
                (stage / relative).parent.mkdir(parents=True, exist_ok=True)
                (workspace / relative).replace(stage / relative)
            for relative in reversed(moved):
                (backup / relative).replace(workspace / relative)
            raise
    return count, str(backup)
