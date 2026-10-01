import os
import sys
import json
import time
import re

import shutil
import pathlib
import subprocess
import urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import fitz # PyMuPDF
from usage_tracker import UsageTracker
from tts_engine import GeminiTTSEngine, get_audio_duration
from srt_generator import generate_srt
from video_builder import build_video

PORT = 8001
STUDIO_DIR = Path(__file__).parent.absolute()
ASSETS_DIR = STUDIO_DIR / "assets"
IMAGES_DIR = ASSETS_DIR / "images"
AUDIO_DIR = ASSETS_DIR / "audio"
OUTPUT_DIR = STUDIO_DIR / "output"

for d in [ASSETS_DIR, IMAGES_DIR, AUDIO_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

tracker = UsageTracker(data_file=STUDIO_DIR / "usage.json")
engine = GeminiTTSEngine(tracker=tracker)

PROJECT_FILE = STUDIO_DIR / "project_data.json"

def load_project_data():
    if PROJECT_FILE.exists():
        try:
            with open(PROJECT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "slides": [],
        "voice": "Puck",
        "model": "gemini-3.8-flash-tts",
        "voice_style_prompt": "",
        "burn_subtitles": False,
        "pad_time": 0.5
    }


def save_project_data(data):
    with open(PROJECT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class StudioHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html_file = STUDIO_DIR / "index.html"
            if html_file.exists():
                with open(html_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "index.html not found")
            return

        elif path == "/api/status":
            proj = load_project_data()
            usage = tracker.get_status()
            
            # Check slide images
            existing_imgs = sorted(list(IMAGES_DIR.glob("page-*.png")))
            num_slides = len(existing_imgs)
            
            # Synchronize slides list length
            current_slides = proj.get("slides", [])
            if len(current_slides) != num_slides:
                new_slides = []
                for idx in range(1, num_slides + 1):
                    existing = next((s for s in current_slides if s.get("page") == idx), None)
                    if existing:
                        new_slides.append(existing)
                    else:
                        new_slides.append({
                            "page": idx,
                            "text": "",
                            "duration": 0.0,
                            "audio_ready": False
                        })
                proj["slides"] = new_slides
                save_project_data(proj)

            # Check audio ready status
            for s in proj["slides"]:
                p_num = s["page"]
                a_file = AUDIO_DIR / f"page-{p_num:02d}.mp3"
                if a_file.exists():
                    s["audio_ready"] = True
                    s["duration"] = round(get_audio_duration(a_file), 2)
                else:
                    s["audio_ready"] = False

            srt_ready = (OUTPUT_DIR / "subtitles.srt").exists()
            video_ready = (OUTPUT_DIR / "output_video.mp4").exists()

            self.send_json({
                "has_api_key": bool(engine.api_key),
                "usage": usage,
                "project": proj,
                "num_slides": num_slides,
                "srt_ready": srt_ready,
                "video_ready": video_ready
            })
            return

        elif path.startswith("/api/images/"):
            img_name = path.replace("/api/images/", "")
            img_path = IMAGES_DIR / img_name
            if img_path.exists():
                with open(img_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404, "Image not found")
            return

        elif path.startswith("/api/audio/"):
            audio_name = path.replace("/api/audio/", "")
            audio_path = AUDIO_DIR / audio_name
            if audio_path.exists():
                with open(audio_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404, "Audio not found")
            return

        elif path == "/api/download_srt":
            srt_path = OUTPUT_DIR / "subtitles.srt"
            if srt_path.exists():
                with open(srt_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="subtitles.srt"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404, "SRT not found")
            return

        elif path == "/api/download_video":
            vid_path = OUTPUT_DIR / "output_video.mp4"
            if vid_path.exists():
                with open(vid_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Disposition", 'attachment; filename="output_video.mp4"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404, "Video not found")
            return

        elif path == "/api/generate_audio_stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def log_sse(msg, pct=None, complete=False, extra=None):
                payload = {"log": msg, "complete": complete}
                if pct is not None:
                    payload["progress"] = pct
                if extra:
                    payload.update(extra)
                try:
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode('utf-8'))
                    self.wfile.flush()
                except Exception:
                    pass

            query = urllib.parse.parse_qs(parsed.query)
            target_page_str = query.get("page", [None])[0]
            target_page = int(target_page_str) if target_page_str is not None else None

            if not engine.api_key:
                log_sse("❌ 錯誤：GEMINI_API_KEY 尚未設定，請先點擊右上角「🔑 設定金鑰」填入 API Key！", complete=True)
                return

            proj = load_project_data()
            voice = proj.get("voice", "Puck")
            model = proj.get("model", "gemini-3.8-flash-tts")
            style_prompt = proj.get("voice_style_prompt", "")
            slides = proj.get("slides", [])

            targets = [s for s in slides if target_page is None or s["page"] == target_page]
            valid_targets = [s for s in targets if s.get("text", "").strip()]
            needed_requests = len(valid_targets)

            if needed_requests == 0:
                log_sse("ℹ️ 未找到包含口白文字的投影片！請先輸入台詞或載入劇本。", complete=True)
                return

            if not tracker.can_request(needed_requests):
                status = tracker.get_status()
                log_sse(f"❌ 免費呼叫額度不足！本次需要 {needed_requests} 次，今日剩餘 {status['remaining']} 次。", complete=True)
                return

            action_desc = f"第 {target_page} 頁" if target_page else f"全部 {len(valid_targets)} 頁"
            style_info = f", 風格提示: {style_prompt[:30]}..." if style_prompt else ""
            log_sse(f"--- [語音生成] 開始處理 {action_desc} 口白 (預設模型: {model}, 角色: {voice}{style_info}) ---", pct=5)

            total_valid = len(valid_targets)
            for idx_t, s in enumerate(valid_targets):
                p_idx = s["page"]
                text = s["text"].strip()

                calc_pct = int(5 + (idx_t / total_valid) * 75)
                log_sse(f"🎙️ [{idx_t+1}/{total_valid}] 正在生成第 {p_idx} 頁語音 (字數: {len(text)} 字)...", pct=calc_pct)

                out_mp3 = AUDIO_DIR / f"page-{p_idx:02d}.mp3"
                try:
                    res = engine.generate_speech(
                        text=text,
                        output_path=out_mp3,
                        voice_name=voice,
                        model=model,
                        style_prompt=style_prompt,
                        log_fn=log_sse
                    )
                    s["audio_ready"] = True
                    s["duration"] = res["duration"]
                    log_sse(f"  ✓ 第 {p_idx} 頁語音生成成功！時長: {res['duration']} 秒 (實際模型: {res.get('model', model)})")
                    save_project_data(proj)
                except Exception as e:
                    log_sse(f"  ❌ 第 {p_idx} 頁語音生成失敗: {str(e)}")

            # Verification & Auto-repair Pass (確保每一頁確實生成)
            log_sse("\n🔍 正在進行全投影片音訊完整性核對校驗...", pct=82)
            unready = []
            for s in valid_targets:
                p_idx = s["page"]
                mp3 = AUDIO_DIR / f"page-{p_idx:02d}.mp3"
                if not mp3.exists() or mp3.stat().st_size < 500 or not s.get("audio_ready"):
                    unready.append(s)

            if unready:
                log_sse(f"⚠️ 偵測到 {len(unready)} 頁未完整就緒，啟動自動補全機制 (自動使用 gemini-3.8-flash-lite-tts 備援模型進行容錯補齊)...")
                for us in unready:
                    p_idx = us["page"]
                    out_mp3 = AUDIO_DIR / f"page-{p_idx:02d}.mp3"
                    try:
                        log_sse(f"  🛠️ 正在重新補齊第 {p_idx} 頁語音...")
                        res = engine.generate_speech(
                            text=us["text"].strip(),
                            output_path=out_mp3,
                            voice_name=voice,
                            model="gemini-3.8-flash-lite-tts",
                            style_prompt=style_prompt,
                            log_fn=log_sse
                        )
                        us["audio_ready"] = True
                        us["duration"] = res["duration"]
                        log_sse(f"  ✓ 第 {p_idx} 頁補齊成功！時長: {res['duration']} 秒")
                    except Exception as err:
                        log_sse(f"  ❌ 第 {p_idx} 頁補齊失敗: {str(err)}")
            else:
                log_sse(f"  ✓ 核驗完成！全部 {total_valid} 頁語音檔案均已 100% 完整生成！", pct=88)

            save_project_data(proj)

            # Rebuild SRT
            log_sse("\n📝 正在自動計算時間軸並生成 subtitles.srt 外掛字幕檔...", pct=92)
            pad_time = float(proj.get("pad_time", 0.5))
            page_data = [{"page": s["page"], "text": s["text"], "duration": s["duration"]} for s in proj.get("slides", []) if s.get("audio_ready")]
            srt_content = generate_srt(page_data, pad_time=pad_time)
            with open(OUTPUT_DIR / "subtitles.srt", "w", encoding="utf-8") as f:
                f.write(srt_content)
            log_sse(f"  ✓ subtitles.srt 生成完成！共 {len(page_data)} 頁字幕對齊完畢 (轉場間隔: {pad_time} 秒)。", pct=98)

            ready_count = len([s for s in proj.get("slides", []) if s.get("audio_ready")])
            log_sse(f"\n🎉 任務圓滿完成！全部 {ready_count} 頁語音已準備就緒，您可以點選「🎬 合成完整影片」！", pct=100, complete=True, extra={
                "usage": tracker.get_status(),
                "num_ready": ready_count
            })
            return

        elif path == "/api/build_video_stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def log_sse(msg, pct=None, complete=False, extra=None):
                payload = {"log": msg, "complete": complete}
                if pct is not None:
                    payload["progress"] = pct
                if extra:
                    payload.update(extra)
                try:
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode('utf-8'))
                    self.wfile.flush()
                except Exception:
                    pass

            query = urllib.parse.parse_qs(parsed.query)
            burn = query.get("burn_subtitles", ["false"])[0].lower() in ["true", "1", "yes"]
            pad_param = query.get("pad_time", [None])[0]

            proj = load_project_data()
            proj["burn_subtitles"] = burn
            if pad_param is not None:
                try:
                    proj["pad_time"] = max(0.0, float(pad_param))
                except Exception:
                    pass

            show_badge = query.get("show_badge", [str(proj.get("show_badge", True))])[0].lower() in ["true", "1", "yes"]
            badge_bg_color = query.get("badge_bg_color", [proj.get("badge_bg_color", "#0e607c")])[0]
            try:
                badge_font_size = int(query.get("badge_font_size", [proj.get("badge_font_size", 20)])[0])
            except Exception:
                badge_font_size = 20

            proj["show_badge"] = show_badge
            proj["badge_bg_color"] = badge_bg_color
            proj["badge_font_size"] = badge_font_size
            save_project_data(proj)

            pad_time = float(proj.get("pad_time", 0.5))
            slides = proj.get("slides", [])
            slide_images = []
            audio_files = []
            slide_titles = []

            for s in slides:
                p_idx = s["page"]
                img_path = IMAGES_DIR / f"page-{p_idx:02d}.png"
                aud_path = AUDIO_DIR / f"page-{p_idx:02d}.mp3"
                if img_path.exists() and aud_path.exists():
                    slide_images.append(img_path)
                    audio_files.append(aud_path)
                    slide_titles.append(s.get("title", f"第 {p_idx} 頁"))

            if not slide_images or not audio_files:
                log_sse("❌ 錯誤：尚未生成足夠的投影片畫面或對應語音！請先上傳 PDF 並生成語音。", complete=True)
                return

            badge_desc = f"含左上角章節標籤 ({badge_bg_color})" if show_badge else "無標籤"
            log_sse(f"🎬 開始合成完整影片 (畫面數: {len(slide_images)}, 字幕: {'硬字幕' if burn else '純淨版'}, 標籤: {badge_desc}, 轉場間隔: {pad_time}秒)...", pct=10)

            out_mp4 = OUTPUT_DIR / "output_video.mp4"
            srt_file = OUTPUT_DIR / "subtitles.srt"

            try:
                build_video(
                    slide_images=slide_images,
                    audio_files=audio_files,
                    output_mp4=out_mp4,
                    srt_file=srt_file,
                    burn_subtitles=burn,
                    pad_time=pad_time,
                    show_badge=show_badge,
                    badge_bg_color=badge_bg_color,
                    badge_font_size=badge_font_size,
                    slide_titles=slide_titles,
                    log_fn=log_sse
                )
                log_sse("\n✨ 影片合成任務圓滿完成！您可切換至「🎥 成果預覽與下載」分頁進行試看或下載影片。", pct=100, complete=True, extra={
                    "video_url": "/api/download_video",
                    "video_ready": True
                })
            except Exception as e:
                log_sse(f"\n❌ 合成影片過程中發生錯誤: {str(e)}", complete=True)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b""
        
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            body = {}

        if path == "/api/upload_pdf":
            data_url = body.get("pdf_base64", "")
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            import base64
            pdf_bytes = base64.b64decode(data_url)
            
            # Clear old images and audios
            for f in IMAGES_DIR.glob("page-*.png"):
                try: f.unlink()
                except Exception: pass
            for f in AUDIO_DIR.glob("page-*.mp3"):
                try: f.unlink()
                except Exception: pass

            temp_pdf = ASSETS_DIR / "uploaded.pdf"
            with open(temp_pdf, "wb") as f:
                f.write(pdf_bytes)

            doc = fitz.open(temp_pdf)
            num_pages = len(doc)
            for i, page in enumerate(doc):
                rect = page.rect
                zoom_factor = max(1920.0 / rect.width, 2.0)
                mat = fitz.Matrix(zoom_factor, zoom_factor)
                pix = page.get_pixmap(matrix=mat)
                out_path = IMAGES_DIR / f"page-{i+1:02d}.png"
                pix.save(str(out_path))
            doc.close()
            temp_pdf.unlink(missing_ok=True)

            # Re-init project slides while preserving existing parsed texts/titles
            proj = load_project_data()
            old_slides = proj.get("slides", [])
            new_slides = []
            for i in range(num_pages):
                p_num = i + 1
                existing = next((s for s in old_slides if s.get("page") == p_num), None)
                if existing:
                    new_slides.append({
                        "page": p_num,
                        "title": existing.get("title", f"第 {p_num} 頁"),
                        "text": existing.get("text", ""),
                        "duration": 0.0,
                        "audio_ready": False
                    })
                else:
                    new_slides.append({
                        "page": p_num,
                        "title": f"第 {p_num} 頁",
                        "text": "",
                        "duration": 0.0,
                        "audio_ready": False
                    })
            proj["slides"] = new_slides
            save_project_data(proj)

            self.send_json({"success": True, "num_slides": num_pages})
            return

        elif path == "/api/save_project":
            proj = load_project_data()
            if "slides" in body:
                proj["slides"] = body["slides"]
            if "voice" in body:
                proj["voice"] = body["voice"]
            if "voice_style_prompt" in body:
                proj["voice_style_prompt"] = str(body["voice_style_prompt"])
            if "model" in body:
                proj["model"] = body["model"]
            if "burn_subtitles" in body:
                proj["burn_subtitles"] = body["burn_subtitles"]
            if "pad_time" in body:
                try:
                    proj["pad_time"] = max(0.0, float(body["pad_time"]))
                except Exception:
                    pass
            if "show_badge" in body:
                proj["show_badge"] = bool(body["show_badge"])
            if "badge_bg_color" in body:
                proj["badge_bg_color"] = str(body["badge_bg_color"])
            if "badge_font_size" in body:
                try:
                    proj["badge_font_size"] = int(body["badge_font_size"])
                except Exception:
                    pass
            save_project_data(proj)

            self.send_json({"success": True})
            return

        elif path == "/api/parse_batch_script":
            script_text = body.get("script", "")
            
            # CJK space cleaning
            general_cjk = r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef\u201c\u201d\u300c\u300d\u300e\u300f\u3010\u3011\u2026\uff5b\uff5d]'
            cjk_pattern = re.compile(rf'(?<={general_cjk})\s+(?={general_cjk})')
            cleaned_text = cjk_pattern.sub('', script_text)
            for _ in range(5):
                new_text = cjk_pattern.sub('', cleaned_text)
                if new_text == cleaned_text:
                    break
                cleaned_text = new_text

            # Chinese numbers mapping
            cn_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            def chinese_to_int(ch_str):
                ch_str = ch_str.strip()
                if ch_str.isdigit():
                    return int(ch_str)
                if len(ch_str) == 1:
                    return cn_map.get(ch_str, 1)
                elif len(ch_str) == 2 and ch_str[0] == '十':
                    return 10 + cn_map.get(ch_str[1], 0)
                elif len(ch_str) == 2 and ch_str[1] == '十':
                    return cn_map.get(ch_str[0], 1) * 10
                elif len(ch_str) == 3 and ch_str[1] == '十':
                    return cn_map.get(ch_str[0], 1) * 10 + cn_map.get(ch_str[2], 0)
                return 1

            # Header regex: matches 【第 1 頁：標題】 or [第 1 頁：標題] or 第 1 頁：標題
            header_pattern = re.compile(
                r'[【\[（(]?第\s*([一二三四五六七八九十\d]+)\s*頁(?:\s*[:：]\s*([^】\]）)\n\r]+))?[】\]）)]?'
            )
            matches = list(header_pattern.finditer(cleaned_text))

            proj = load_project_data()
            slides = proj.get("slides", [])

            if matches:
                for index, match in enumerate(matches):
                    page_num_str = match.group(1)
                    page_title = match.group(2) if match.group(2) is not None else ""
                    page_title = page_title.strip()
                    page_num = chinese_to_int(page_num_str)

                    start_pos = match.end()
                    end_pos = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned_text)
                    page_text = cleaned_text[start_pos:end_pos].strip()

                    # Clean leading colons or linebreaks
                    if page_text.startswith("：") or page_text.startswith(":"):
                        page_text = page_text[1:].strip()

                    # Find matching slide or append
                    target = next((s for s in slides if s.get("page") == page_num), None)
                    if target:
                        target["title"] = page_title if page_title else f"第 {page_num} 頁"
                        target["text"] = page_text
                    else:
                        slides.append({
                            "page": page_num,
                            "title": page_title if page_title else f"第 {page_num} 頁",
                            "text": page_text,
                            "duration": 0.0,
                            "audio_ready": False
                        })
                # Sort slides by page number
                slides.sort(key=lambda x: x["page"])
            else:
                # Fallback: split by double newlines into paragraphs
                paragraphs = [p.strip() for p in cleaned_text.split('\n\n') if p.strip()]
                for idx, para in enumerate(paragraphs):
                    p_num = idx + 1
                    target = next((s for s in slides if s.get("page") == p_num), None)
                    if target:
                        target["text"] = para
                        if not target.get("title"):
                            target["title"] = f"第 {p_num} 頁"
                    else:
                        slides.append({
                            "page": p_num,
                            "title": f"第 {p_num} 頁",
                            "text": para,
                            "duration": 0.0,
                            "audio_ready": False
                        })

            proj["slides"] = slides
            save_project_data(proj)
            self.send_json({"success": True, "slides": slides, "parsed_count": len(matches) or len(paragraphs)})
            return

        elif path == "/api/generate_audio":
            page_num = body.get("page") # if None, generate for all
            proj = load_project_data()
            voice = proj.get("voice", "Puck")
            model = proj.get("model", "gemini-3.8-flash-tts")
            style_prompt = proj.get("voice_style_prompt", "")
            slides = proj.get("slides", [])

            targets = [s for s in slides if page_num is None or s["page"] == page_num]
            needed_requests = len([s for s in targets if s["text"].strip()])

            if not tracker.can_request(needed_requests):
                self.send_json({
                    "success": False,
                    "error": f"額度不足！需要 {needed_requests} 次呼叫，但今日剩餘 {tracker.get_status()['remaining']} 次。"
                }, status=429)
                return

            results = []
            for idx_t, s in enumerate(targets):
                p_idx = s["page"]
                text = s["text"].strip()
                if not text:
                    continue

                if idx_t > 0:
                    time.sleep(1.0)

                out_mp3 = AUDIO_DIR / f"page-{p_idx:02d}.mp3"

                try:
                    res = engine.generate_speech(
                        text=text,
                        output_path=out_mp3,
                        voice_name=voice,
                        model=model,
                        style_prompt=style_prompt
                    )
                    s["audio_ready"] = True
                    s["duration"] = res["duration"]
                    results.append({"page": p_idx, "success": True, "duration": res["duration"]})
                except Exception as e:
                    results.append({"page": p_idx, "success": False, "error": str(e)})

            save_project_data(proj)
            
            # Automatically rebuild SRT after audio generation
            pad_time = float(proj.get("pad_time", 0.5))
            page_data = [{"page": s["page"], "text": s["text"], "duration": s["duration"]} for s in slides if s.get("audio_ready")]
            srt_content = generate_srt(page_data, pad_time=pad_time)
            with open(OUTPUT_DIR / "subtitles.srt", "w", encoding="utf-8") as f:
                f.write(srt_content)

            self.send_json({
                "success": True,
                "results": results,
                "usage": tracker.get_status()
            })
            return

        elif path == "/api/generate_srt":
            proj = load_project_data()
            pad_time = float(proj.get("pad_time", 0.5))
            slides = proj.get("slides", [])
            page_data = [{"page": s["page"], "text": s["text"], "duration": s.get("duration", 5.0)} for s in slides if s.get("audio_ready")]
            srt_content = generate_srt(page_data, pad_time=pad_time)
            with open(OUTPUT_DIR / "subtitles.srt", "w", encoding="utf-8") as f:
                f.write(srt_content)
            self.send_json({"success": True, "srt": srt_content})
            return

        elif path == "/api/build_video":
            burn = body.get("burn_subtitles", False)
            proj = load_project_data()
            proj["burn_subtitles"] = burn
            if "pad_time" in body:
                try:
                    proj["pad_time"] = max(0.0, float(body["pad_time"]))
                except Exception:
                    pass
            save_project_data(proj)

            show_badge = body.get("show_badge", proj.get("show_badge", True))
            badge_bg_color = body.get("badge_bg_color", proj.get("badge_bg_color", "#0e607c"))
            badge_font_size = int(body.get("badge_font_size", proj.get("badge_font_size", 20)))

            slide_titles = []
            for s in slides:
                p_idx = s["page"]
                img_path = IMAGES_DIR / f"page-{p_idx:02d}.png"
                aud_path = AUDIO_DIR / f"page-{p_idx:02d}.mp3"
                if img_path.exists() and aud_path.exists():
                    slide_images.append(img_path)
                    audio_files.append(aud_path)
                    slide_titles.append(s.get("title", f"第 {p_idx} 頁"))

            if not slide_images or not audio_files:
                self.send_json({"success": False, "error": "尚未生成足夠的投影片或語音！"}, status=400)
                return

            out_mp4 = OUTPUT_DIR / "output_video.mp4"
            srt_file = OUTPUT_DIR / "subtitles.srt"
            pad_time = float(proj.get("pad_time", 0.5))

            try:
                build_video(
                    slide_images=slide_images,
                    audio_files=audio_files,
                    output_mp4=out_mp4,
                    srt_file=srt_file,
                    burn_subtitles=burn,
                    pad_time=pad_time,
                    show_badge=show_badge,
                    badge_bg_color=badge_bg_color,
                    badge_font_size=badge_font_size,
                    slide_titles=slide_titles
                )
                self.send_json({"success": True, "video_url": "/api/download_video"})
            except Exception as e:
                self.send_json({"success": False, "error": f"合成失敗：{str(e)}"}, status=500)
            return


        elif path == "/api/save_api_key":
            new_key = body.get("api_key", "").strip()
            if new_key:
                env_file = STUDIO_DIR.parent / ".env"
                with open(env_file, "w", encoding="utf-8") as f:
                    f.write(f"GEMINI_API_KEY={new_key}\n")
                engine.reload_key(new_key)
                self.send_json({"success": True, "usage": tracker.get_status()})
            else:
                self.send_json({"success": False, "error": "金鑰不能為空"}, status=400)
            return

        self.send_error(404)

def run():
    server_address = ("", PORT)
    httpd = ThreadingHTTPServer(server_address, StudioHandler)
    print(f"🚀 Gemini 3.8 影音工作坊 (獨立版) 伺服器已啟動！")
    print(f"🔗 請在瀏覽器訪問: http://localhost:{PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已安全停止。")

if __name__ == "__main__":
    run()
