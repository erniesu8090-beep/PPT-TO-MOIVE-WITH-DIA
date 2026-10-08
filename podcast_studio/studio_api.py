import os
import sys
import json
import re
import urllib.parse
import subprocess
import shutil
import base64
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MODULE_DIR = Path(__file__).parent.resolve()
ROOT_DIR = MODULE_DIR.parent
PROJECTS_DIR = MODULE_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_ASSETS_DIR = MODULE_DIR / "default_assets"
MOVIE_OUTPUT_DIR = ROOT_DIR / "MovieOutput"
MOVIE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = MODULE_DIR / "templates"

def get_gemini_key_status():
    env_file = ROOT_DIR / ".env"
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                os.environ["GEMINI_API_KEY"] = key
                break
    has_key = bool(key)
    masked_key = ""
    if has_key:
        if len(key) > 10:
            masked_key = f"{key[:6]}...{key[-4:]}"
        else:
            masked_key = "********"
    return has_key, masked_key

def save_gemini_key(new_key: str):
    new_key = new_key.strip()
    os.environ["GEMINI_API_KEY"] = new_key
    env_file = ROOT_DIR / ".env"
    lines = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                lines.append(f"GEMINI_API_KEY={new_key}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"GEMINI_API_KEY={new_key}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

def scan_audio_files():
    audios = []
    # Scan root dir
    for ext in ["*.m4a", "*.mp3", "*.wav", "*.aac"]:
        for f in ROOT_DIR.glob(ext):
            audios.append({
                "name": f.name,
                "path": str(f.resolve()),
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1)
            })
    return audios

def scan_projects():
    projects = []
    if PROJECTS_DIR.exists():
        for d in PROJECTS_DIR.iterdir():
            if d.is_dir():
                has_chapters = (d / "chapters.json").exists()
                has_subtitles = (d / "subtitles.srt").exists()
                projects.append({
                    "name": d.name,
                    "has_chapters": has_chapters,
                    "has_subtitles": has_subtitles
                })
    return projects

def parse_srt_preview(srt_file: Path, max_lines: int = 40):
    if not srt_file.exists():
        return []
    content = srt_file.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip())
    dialogues = []
    for b in blocks[:max_lines]:
        lines = [l.strip() for l in b.strip().split("\n") if l.strip()]
        if len(lines) >= 3:
            time_str = lines[1]
            text = " ".join(lines[2:])
            speaker = "主持人 Alan"
            if "Beth" in text or "嘉賓" in text or "來賓" in text or "B:" in text:
                speaker = "嘉賓 Beth"
            dialogues.append({
                "time": time_str,
                "speaker": speaker,
                "text": text
            })
    return dialogues

async def render_single_scene_async(proj_dir: Path, chapter_index: int):
    from playwright.async_api import async_playwright
    chapters_file = proj_dir / "chapters.json"
    if not chapters_file.exists():
        return False
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    chapter = next((c for c in data.get("chapters", []) if c["chapter_index"] == chapter_index), None)
    if not chapter:
        return False

    scenes_dir = proj_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir = proj_dir / "concepts"

    html_path = (TEMPLATES_DIR / "studio_stage.html").as_uri()

    concept_img = concepts_dir / f"concept_{chapter_index:02d}.jpg"
    if not concept_img.exists():
        concept_img = concepts_dir / "concept_01.jpg"

    topic = chapter.get("topic", "")
    caption = chapter.get("caption", "")
    img_uri = concept_img.as_uri()

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(html_path)
        await page.evaluate(f"""() => {{
            document.getElementById('concept-display').src = '{img_uri}';
            document.getElementById('topic-text').textContent = '{topic}';
            document.getElementById('concept-caption').textContent = '{caption}';
        }}""")
        await page.wait_for_timeout(150)
        out_frame = scenes_dir / f"scene_{chapter_index:02d}.png"
        await page.screenshot(path=str(out_frame))
        await browser.close()
    return True

def serve_podcast_static(handler, path: str):
    """處理 /podcast/default_assets/ 與 /podcast/projects/ 的靜態圖片與字幕串流"""
    if path.startswith("/podcast/default_assets/"):
        file_name = urllib.parse.unquote(path[len("/podcast/default_assets/"):])
        target_path = DEFAULT_ASSETS_DIR / file_name
    elif path.startswith("/podcast/projects/"):
        subpath = urllib.parse.unquote(path[len("/podcast/projects/"):])
        target_path = PROJECTS_DIR / subpath
    else:
        return False

    if target_path.exists() and target_path.is_file():
        handler.send_response(200)
        suffix = target_path.suffix.lower()
        if suffix in [".jpg", ".jpeg"]:
            handler.send_header("Content-Type", "image/jpeg")
        elif suffix == ".png":
            handler.send_header("Content-Type", "image/png")
        elif suffix == ".srt":
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
        elif suffix == ".ass":
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
        elif suffix == ".json":
            handler.send_header("Content-Type", "application/json; charset=utf-8")
        else:
            handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(target_path.stat().st_size))
        handler.end_headers()
        with open(target_path, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
        return True
    return False

def handle_podcast_get(handler, path: str, parsed_url):
    query = urllib.parse.parse_qs(parsed_url.query)

    if path == "/api/podcast/info":
        has_key, masked_key = get_gemini_key_status()
        projects = scan_projects()
        audios = scan_audio_files()
        res = {
            "has_gemini_key": has_key,
            "masked_key": masked_key,
            "projects": projects,
            "audios": audios
        }
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
        return True

    elif path == "/api/podcast/project_details":
        proj_name = query.get("project", [""])[0]
        if not proj_name:
            handler.send_response(400)
            handler.end_headers()
            return True

        proj_dir = PROJECTS_DIR / proj_name
        if not proj_dir.exists():
            handler.send_response(404)
            handler.end_headers()
            return True

        chapters = []
        chapters_file = proj_dir / "chapters.json"
        if chapters_file.exists():
            try:
                data = json.loads(chapters_file.read_text(encoding="utf-8"))
                chapters = data.get("chapters", [])
            except Exception:
                chapters = []

        subtitles_sample = parse_srt_preview(proj_dir / "subtitles.srt", max_lines=40)

        # 檢查 MovieOutput 下對應的影片
        video_url = None
        video_name = None
        for mp4 in MOVIE_OUTPUT_DIR.glob(f"*{proj_name}*.mp4"):
            video_name = mp4.name
            video_url = f"/MovieOutput/{urllib.parse.quote(mp4.name)}"
            break

        res = {
            "success": True,
            "project_name": proj_name,
            "has_chapters": bool(chapters),
            "chapters": chapters,
            "subtitles_sample": subtitles_sample,
            "has_subtitles": (proj_dir / "subtitles.srt").exists(),
            "srt_url": f"/podcast/projects/{urllib.parse.quote(proj_name)}/subtitles.srt" if (proj_dir / "subtitles.srt").exists() else None,
            "ass_url": f"/podcast/projects/{urllib.parse.quote(proj_name)}/subtitles.ass" if (proj_dir / "subtitles.ass").exists() else None,
            "video_url": video_url,
            "video_name": video_name
        }
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
        return True

    elif path == "/api/podcast/run_pipeline":
        # SSE stream for running pipeline steps
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        def log_sse(msg):
            try:
                handler.wfile.write(f"data: {json.dumps({'log': msg}, ensure_ascii=False)}\n\n".encode('utf-8'))
                handler.wfile.flush()
            except Exception:
                pass

        audio_input = query.get("audio", [""])[0]
        step = query.get("step", ["all"])[0]

        if not audio_input:
            log_sse("❌ 錯誤：未指定音訊檔案！")
            return True

        audio_path = Path(audio_input)
        if not audio_path.exists():
            # Check in ROOT_DIR
            audio_path = ROOT_DIR / audio_input

        if not audio_path.exists():
            log_sse(f"❌ 錯誤：找不到音訊檔案：{audio_input}")
            return True

        log_sse(f"🎙️ 正在啟動 Podcast Studio 管線 (步驟: {step})：{audio_path.name}")

        pipeline_script = MODULE_DIR / "pipeline.py"
        cmd = [sys.executable, "-u", str(pipeline_script), str(audio_path), "--step", step]

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(MODULE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                env=env
            )
            for line in proc.stdout:
                log_sse(line.strip())
            proc.wait()

            if proc.returncode == 0:
                proj_name = audio_path.stem
                output_video = f"{proj_name}_雙人錄音室版.mp4"
                log_sse("\n🎉 管線執行成功完成！")
                handler.wfile.write(f"data: {json.dumps({'complete': True, 'project_name': proj_name, 'video_name': output_video}, ensure_ascii=False)}\n\n".encode('utf-8'))
                handler.wfile.flush()
            else:
                log_sse(f"\n❌ 管線執行出錯，結束代碼: {proc.returncode}")
        except Exception as e:
            log_sse(f"❌ 發生異常：{str(e)}")

        return True

    return False

def handle_podcast_post(handler, path: str, payload: dict):
    if path == "/api/podcast/save_key":
        key = payload.get("key", "").strip()
        save_gemini_key(key)
        has_key, masked_key = get_gemini_key_status()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps({"success": True, "masked_key": masked_key}, ensure_ascii=False).encode("utf-8"))
        return True

    elif path == "/api/podcast/update_chapter":
        proj_name = payload.get("project")
        ch_idx = int(payload.get("chapter_index", 1))
        topic = payload.get("topic", "")
        caption = payload.get("caption", "")
        key_insight = payload.get("key_insight", "")

        proj_dir = PROJECTS_DIR / proj_name
        chapters_file = proj_dir / "chapters.json"
        if chapters_file.exists():
            data = json.loads(chapters_file.read_text(encoding="utf-8"))
            for ch in data.get("chapters", []):
                if ch["chapter_index"] == ch_idx:
                    if topic: ch["topic"] = topic
                    if caption: ch["caption"] = caption
                    if key_insight: ch["key_insight"] = key_insight
                    break
            chapters_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            # 重新截圖該畫格
            try:
                asyncio.run(render_single_scene_async(proj_dir, ch_idx))
            except Exception as e:
                print(f"[Warning] Failed to re-render scene {ch_idx}: {e}")

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps({"success": True}).encode("utf-8"))
        return True

    elif path == "/api/podcast/replace_image":
        proj_name = payload.get("project")
        ch_idx = int(payload.get("chapter_index", 1))
        img_b64 = payload.get("image_base64", "")

        proj_dir = PROJECTS_DIR / proj_name
        concepts_dir = proj_dir / "concepts"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        if "," in img_b64:
            img_b64 = img_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(img_b64)

        target_file = concepts_dir / f"concept_{ch_idx:02d}.jpg"
        target_file.write_bytes(img_bytes)

        # 立即重新截取該畫格
        try:
            asyncio.run(render_single_scene_async(proj_dir, ch_idx))
        except Exception as e:
            print(f"[Warning] Failed to re-render scene {ch_idx}: {e}")

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps({"success": True}).encode("utf-8"))
        return True

    elif path == "/api/podcast/open_folder":
        target = payload.get("target", "movie_output")
        proj_name = payload.get("project", "")
        if target == "movie_output":
            p = MOVIE_OUTPUT_DIR
        elif target == "project" and proj_name:
            p = PROJECTS_DIR / proj_name
        else:
            p = ROOT_DIR

        if p.exists() and sys.platform == "win32":
            os.startfile(str(p))

        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(json.dumps({"success": True}).encode("utf-8"))
        return True

    return False
