import os
import sys
sys.modules['aiodns'] = None  # Prevent broken aiodns on Windows

import http.server
import json
import urllib.parse
import subprocess
import pathlib
import asyncio
import edge_tts
import math
import re
import time
import base64
import fitz # PyMuPDF

PORT = 8000
WORKSPACE_DIR = pathlib.Path(__file__).parent.absolute()
OUT_DIR = WORKSPACE_DIR / "assets" / "narration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Helper to read durations using ffprobe
def get_audio_duration(file_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(file_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if res.returncode != 0 or not res.stdout or not res.stdout.strip():
            return 5.0
        data = json.loads(res.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 5.0

# Helper to get Git version and update time
def get_git_info():
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(WORKSPACE_DIR)
        ).strip()
        commit_time = subprocess.check_output(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=str(WORKSPACE_DIR)
        ).strip()
        return commit_hash, commit_time
    except Exception:
        return "Unknown", "Unknown"

# Helper to decode base64 Data URL to raw bytes
def decode_base64_file(data_url):
    if "," in data_url:
        header, base64_data = data_url.split(",", 1)
    else:
        base64_data = data_url
    return base64.b64decode(base64_data)

# Helper to render PDF slides into images
def render_uploaded_slides(pdf_bytes):
    temp_pdf = WORKSPACE_DIR / "assets" / "temp_slides.pdf"
    with open(temp_pdf, "wb") as f:
        f.write(pdf_bytes)
        
    doc = fitz.open(temp_pdf)
    num_slides = len(doc)
    
    # Delete old slide images
    images_dir = WORKSPACE_DIR / "assets" / "images"
    if images_dir.exists():
        for old_img in images_dir.glob("page-*.png"):
            try:
                old_img.unlink()
            except Exception:
                pass
    else:
        images_dir.mkdir(parents=True, exist_ok=True)
        
    for i, page in enumerate(doc):
        rect = page.rect
        width = rect.width
        zoom_factor = max(1920.0 / width, 2.0)
        
        mat = fitz.Matrix(zoom_factor, zoom_factor)
        pix = page.get_pixmap(matrix=mat)
        
        out_path = images_dir / f"page-{i+1:02d}.png"
        pix.save(str(out_path))
        
    doc.close()
    
    try:
        temp_pdf.unlink()
    except Exception:
        pass
        
    return num_slides

# Helper to extract text from PDF bytes using PyMuPDF
def extract_text_from_pdf_bytes(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text

def chinese_to_int(ch_str):
    mapping = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if ch_str.isdigit():
        return int(ch_str)
    if len(ch_str) == 1:
        return mapping.get(ch_str, 1)
    elif len(ch_str) == 2 and ch_str[0] == '十':
        return 10 + mapping.get(ch_str[1], 0)
    elif len(ch_str) == 2 and ch_str[1] == '十':
        return mapping.get(ch_str[0], 1) * 10
    elif len(ch_str) == 3 and ch_str[1] == '十':
        return mapping.get(ch_str[0], 1) * 10 + mapping.get(ch_str[2], 0)
    return 1

# Helper to parse text narration and save it
def parse_and_save_narration(text, num_slides):
    # CJK space cleaning
    general_cjk = r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef\u201c\u201d\u300c\u300d\u300e\u300f\u3010\u3011\u2026\uff5b\uff5d]'
    cjk_pattern = re.compile(rf'(?<={general_cjk})\s+(?={general_cjk})')
    
    cleaned_text = cjk_pattern.sub('', text)
    for _ in range(5):
        new_text = cjk_pattern.sub('', cleaned_text)
        if new_text == cleaned_text:
            break
        cleaned_text = new_text
        
    # Search for slide page headers like 【第 X 頁：Y】 or [第 X 頁：Y]
    # We require brackets to prevent page numbers mentioned in text from being parsed as headers.
    # Capture (page_num_str, title_str)
    header_pattern = re.compile(
        r'[【\[（(]第\s*([一二三四五六七八九十\d]+)\s*頁(?:\s*[:：]\s*([^】\]）)\n]+))?[】\]）)]'
    )
    
    matches = list(header_pattern.finditer(cleaned_text))
    slide_pages = []
    
    if matches:
        for index, match in enumerate(matches):
            page_num_str = match.group(1)
            page_title = match.group(2) if match.group(2) is not None else ""
            page_title = page_title.strip()
            
            page_num = chinese_to_int(page_num_str)
            
            # Slice narration text up to the next header or end of file
            start_pos = match.end()
            end_pos = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned_text)
            page_text = cleaned_text[start_pos:end_pos].strip()
            
            # Clean up leading punctuation and inner newlines
            if page_text.startswith("："):
                page_text = page_text[1:].strip()
            page_text = page_text.replace('\n', '').strip()
            
            slide_pages.append({
                'num': page_num,
                'title': page_title if page_title else f"第 {page_num} 頁",
                'narration': page_text
            })
    else:
        # Fallback to paragraph-based splitting
        paragraphs = [p.strip() for p in cleaned_text.split('\n\n') if p.strip()]
        for idx in range(num_slides):
            page_num = idx + 1
            if idx < len(paragraphs):
                p_text = paragraphs[idx].replace('\n', '')
                title = f"投影片第 {page_num} 頁"
            else:
                p_text = ""
                title = f"投影片第 {page_num} 頁"
                
            slide_pages.append({
                'num': page_num,
                'title': title,
                'narration': p_text
            })
            
    # Ensure slide_pages has exactly num_slides elements by padding empty slides
    existing_nums = {sp['num'] for sp in slide_pages}
    for idx in range(num_slides):
        page_num = idx + 1
        if page_num not in existing_nums:
            slide_pages.append({
                'num': page_num,
                'title': f"第 {page_num} 頁",
                'narration': ""
            })
    # Sort slides by page number
    slide_pages.sort(key=lambda x: x["num"])
            
    # Save JSON file
    cleaned_path = WORKSPACE_DIR / "口白_cleaned.json"
    with open(cleaned_path, "w", encoding="utf-8") as f:
        json.dump(slide_pages, f, ensure_ascii=False, indent=2)

# Helper to parse dialogue turns from narration text
def parse_dialogue_turns(text):
    if not text:
        return []

    pattern = re.compile(
        r'(?:^|\n|\s+|(?<=[。！？\n]))(?:'
        r'(?:\[?\b(A|B|甲|乙|角色A|角色B|角色1|角色2|主持人|專家|來賓|Host|Guest)\b\]?\s*[:：])'
        r'|'
        r'(?:\[(A|B|甲|乙|角色A|角色B|角色1|角色2|主持人|專家|來賓|Host|Guest)\])'
        r')',
        re.IGNORECASE
    )

    matches = list(pattern.finditer(text))
    if not matches:
        return [{'speaker': 'A', 'text': text.strip()}]

    turns = []
    if matches[0].start() > 0:
        prefix_text = text[:matches[0].start()].strip()
        if prefix_text:
            turns.append({'speaker': 'A', 'text': prefix_text})

    for i, m in enumerate(matches):
        raw_spk = m.group(1) or m.group(2)
        raw_spk_upper = raw_spk.upper() if raw_spk else 'A'
        
        if raw_spk_upper in ['B', '乙', '角色2', '角色B', '專家', '來賓', 'GUEST']:
            spk = 'B'
        else:
            spk = 'A'

        start_pos = m.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        turn_text = text[start_pos:end_pos].strip()
        
        if turn_text.startswith("："):
            turn_text = turn_text[1:].strip()
            
        if turn_text:
            turns.append({'speaker': spk, 'text': turn_text})

    return turns if turns else [{'speaker': 'A', 'text': text.strip()}]

# Helper to calculate dynamic timings
def calculate_dynamic_timings():
    cleaned_path = WORKSPACE_DIR / "口白_cleaned.json"
    if not cleaned_path.exists():
        return []
    
    pad_time = 1.5
    voice_path = WORKSPACE_DIR / "voice_settings.json"
    voice_settings = {}
    if voice_path.exists():
        try:
            with open(voice_path, "r", encoding="utf-8") as f_vs:
                voice_settings = json.load(f_vs)
                pad_time = float(voice_settings.get("pad_time", 1.5))
        except Exception:
            pass
            
    speaker_a_name = voice_settings.get("speaker_a", {}).get("name", "主持人 A")
    speaker_b_name = voice_settings.get("speaker_b", {}).get("name", "對談者 B")
    
    with open(cleaned_path, "r", encoding="utf-8") as f:
        slides = json.load(f)
        
    pages_timings = []
    
    for s in slides:
        num = s["num"]
        title = s.get("title", f"第 {num} 頁")
        text = s.get("narration", "")
        
        text_clean = text
        if text_clean.startswith("「") and text_clean.endswith("」"):
            text_clean = text_clean[1:-1]
            
        audio_file = OUT_DIR / f"page-{num:02d}.mp3"
        
        if audio_file.exists():
            try:
                audio_dur = get_audio_duration(audio_file)
            except Exception:
                audio_dur = len(text_clean) * 0.15
        else:
            audio_dur = len(text_clean) * 0.15

        meta_file = OUT_DIR / f"page-{num:02d}-meta.json"
        turn_durations = []
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f_meta:
                    meta = json.load(f_meta)
                    turn_durations = meta.get("turn_durations", [])
            except Exception:
                pass

        turns = parse_dialogue_turns(text_clean)
        
        all_clauses = []
        total_chars = sum(len(t["text"]) for t in turns) or 1

        current_time = 0.0
        for idx_turn, t in enumerate(turns):
            spk = t["speaker"]
            spk_name = speaker_a_name if spk == "A" else speaker_b_name
            turn_text = t["text"]
            
            if idx_turn < len(turn_durations) and turn_durations[idx_turn] > 0:
                turn_dur = turn_durations[idx_turn]
            else:
                turn_dur = audio_dur * (len(turn_text) / total_chars)

            clauses_text = re.findall(r'[^，。；：？！、\s]+[，。；：？！、\s]*', turn_text)
            clauses_text = [c.strip() for c in clauses_text if c.strip()]
            if not clauses_text:
                clauses_text = [turn_text]
                
            turn_chars = sum(len(c) for c in clauses_text) or 1

            for c in clauses_text:
                c_len = len(c)
                c_dur = turn_dur * (c_len / turn_chars)
                all_clauses.append({
                    "text": c,
                    "start": round(current_time, 2),
                    "end": round(current_time + c_dur, 2),
                    "speaker": spk,
                    "speaker_name": spk_name
                })
                current_time += c_dur

        dur = math.ceil(audio_dur + pad_time)
        
        pages_timings.append({
            "i": num,
            "title": title,
            "sub": text,
            "audio_dur": round(audio_dur, 2),
            "dur": dur,
            "clauses": all_clauses
        })
        
    # Save timings_dynamic.json
    with open(WORKSPACE_DIR / "timings_dynamic.json", "w", encoding="utf-8") as f_dyn:
        json.dump(pages_timings, f_dyn, ensure_ascii=False, indent=2)
        
    # Save timings.json
    simple_timings = []
    for p in pages_timings:
        simple_timings.append({
            "i": p["i"],
            "title": p["title"],
            "sub": p["sub"],
            "audio_dur": p["audio_dur"],
            "dur": p["dur"]
        })
    with open(WORKSPACE_DIR / "timings.json", "w", encoding="utf-8") as f_simp:
        json.dump(simple_timings, f_simp, ensure_ascii=False, indent=2)
        
    return pages_timings

# Async edge-tts generator supporting single & dual modes
async def generate_single_tts(num, text, voice, rate, pitch):
    # Compatibility wrapper for single speaker
    vs = {"mode": "single", "voice": voice, "rate": rate, "pitch": pitch}
    return await generate_page_tts(num, text, vs)

async def generate_page_tts(num, text, vs):
    out_file = OUT_DIR / f"page-{num:02d}.mp3"
    import aiohttp
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy") or None
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())

    mode = vs.get("mode", "single")
    speaker_a = vs.get("speaker_a", {
        "name": "主持人 A",
        "voice": vs.get("voice", "zh-TW-YunJheNeural"),
        "rate": vs.get("rate", "-5%"),
        "pitch": vs.get("pitch", "-2Hz")
    })
    speaker_b = vs.get("speaker_b", {
        "name": "對談者 B",
        "voice": "zh-TW-HsiaoChenNeural",
        "rate": vs.get("rate", "-5%"),
        "pitch": vs.get("pitch", "-2Hz")
    })

    turns = parse_dialogue_turns(text)

    def norm_param(val, default):
        v = str(val if val is not None else default).strip()
        if not v or v in ["0", "0%", "+0%", "-0%", "0Hz", "+0Hz", "-0Hz"]:
            return None
        if not v.startswith("+") and not v.startswith("-"):
            v = "+" + v
        return v

    if mode == "dual" or (len(turns) > 1 and any(t["speaker"] == "B" for t in turns)):
        turn_files = []
        for idx, turn in enumerate(turns):
            spk = turn["speaker"]
            spk_cfg = speaker_a if spk == "A" else speaker_b
            voice = spk_cfg.get("voice", "zh-TW-YunJheNeural" if spk == "A" else "zh-TW-HsiaoChenNeural")
            rate = norm_param(spk_cfg.get("rate"), "-5%")
            pitch = norm_param(spk_cfg.get("pitch"), "-2Hz")
            
            turn_file = OUT_DIR / f"page-{num:02d}-turn-{idx:02d}.mp3"
            for attempt in range(3):
                try:
                    kwargs = {}
                    if attempt < 2:
                        if rate: kwargs["rate"] = rate
                        if pitch: kwargs["pitch"] = pitch
                    if proxy:
                        kwargs["proxy"] = proxy
                    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
                    communicate = edge_tts.Communicate(turn["text"], voice, **kwargs, connector=connector)
                    await communicate.save(str(turn_file))
                    turn_files.append(turn_file)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(1.5)

        turn_durations = [get_audio_duration(tf) for tf in turn_files]
        meta_file = OUT_DIR / f"page-{num:02d}-meta.json"
        try:
            with open(meta_file, "w", encoding="utf-8") as f_m:
                json.dump({"turn_durations": turn_durations}, f_m, indent=2)
        except Exception:
            pass

        if len(turn_files) == 1:
            if out_file.exists():
                try: out_file.unlink()
                except Exception: pass
            shutil.move(str(turn_files[0]), str(out_file))
        elif len(turn_files) > 1:
            concat_txt = OUT_DIR / f"page-{num:02d}-concat.txt"
            with open(concat_txt, "w", encoding="utf-8") as f_c:
                for tf in turn_files:
                    f_c.write(f"file '{tf.name}'\n")
            
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c:a", "libmp3lame", str(out_file)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            for tf in turn_files:
                try: tf.unlink()
                except Exception: pass
            try: concat_txt.unlink()
            except Exception: pass

        return out_file
    else:
        voice = vs.get("voice", "zh-TW-YunJheNeural")
        rate = norm_param(vs.get("rate"), "-5%")
        pitch = norm_param(vs.get("pitch"), "-2Hz")
        for attempt in range(3):
            try:
                kwargs = {}
                if attempt < 2:
                    if rate: kwargs["rate"] = rate
                    if pitch: kwargs["pitch"] = pitch
                if proxy:
                    kwargs["proxy"] = proxy
                connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
                communicate = edge_tts.Communicate(text, voice, **kwargs, connector=connector)
                await communicate.save(str(out_file))
                return out_file
            except Exception as e:
                if attempt == 2:
                    raise e
                await asyncio.sleep(1.5)
                await asyncio.sleep(1)
        return out_file

class GUIHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORKSPACE_DIR), **kwargs)

    def end_headers(self):
        # Disable cache for development
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # Compatibility redirect/read for older video file URLs now inside MovieOutput
        if path.endswith(".mp4"):
            file_name = urllib.parse.unquote(path.lstrip("/"))
            # If requesting just the filename, check in MovieOutput
            alt_path = WORKSPACE_DIR / "MovieOutput" / file_name
            if alt_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(alt_path.stat().st_size))
                self.end_headers()
                with open(alt_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            commit_hash, commit_time = get_git_info()
            html = HTML_TEMPLATE.replace("__GIT_COMMIT__", commit_hash).replace("__GIT_DATE__", commit_time)
            self.wfile.write(html.encode('utf-8'))
            return
            
        elif path == "/api/slides":
            cleaned_path = WORKSPACE_DIR / "口白_cleaned.json"
            if cleaned_path.exists():
                with open(cleaned_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = []
                
            # Defensively check slide PNGs in assets/images and pad 口白_cleaned.json if mismatched
            images_dir = WORKSPACE_DIR / "assets" / "images"
            if images_dir.exists():
                png_files = list(images_dir.glob("page-*.png"))
                num_slides = 0
                for p in png_files:
                    match = re.search(r"page-(\d+)\.png", p.name)
                    if match:
                        num_slides = max(num_slides, int(match.group(1)))
                
                existing_nums = {sp['num'] for sp in data}
                mutated = False
                for idx in range(num_slides):
                    page_num = idx + 1
                    if page_num not in existing_nums:
                        data.append({
                            'num': page_num,
                            'title': f"第 {page_num} 頁",
                            'narration': ""
                        })
                        mutated = True
                
                if mutated:
                    data.sort(key=lambda x: x["num"])
                    with open(cleaned_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    
                    # Recalculate and rebuild index.html
                    calculate_dynamic_timings()
                    try:
                        subprocess.run([sys.executable, "build_index.py"], cwd=str(WORKSPACE_DIR), capture_output=True)
                    except Exception:
                        pass
            
            # Fetch voice settings
            voice_settings = {
                "mode": "single",
                "voice": "zh-TW-YunJheNeural",
                "rate": "-5%",
                "pitch": "-2Hz",
                "pad_time": 1.5,
                "speaker_a": {
                    "name": "主持人 A",
                    "voice": "zh-TW-YunJheNeural",
                    "rate": "-5%",
                    "pitch": "-2Hz",
                    "color": "#40a9ff"
                },
                "speaker_b": {
                    "name": "對談者 B",
                    "voice": "zh-TW-HsiaoChenNeural",
                    "rate": "-5%",
                    "pitch": "-2Hz",
                    "color": "#fadb14"
                }
            }
            voice_path = WORKSPACE_DIR / "voice_settings.json"
            if voice_path.exists():
                try:
                    with open(voice_path, "r", encoding="utf-8") as f:
                        voice_settings.update(json.load(f))
                except Exception:
                    pass
                    
            # Fetch subtitle settings
            subtitle_settings = {
                "font_size": 32,
                "bg_opacity": 0.75,
                "max_width": 85,
                "bottom_pos": 50,
                "style_preset": "cc",
                "font_name": "Microsoft JhengHei",
                "font_color": "white",
                "bg_style": "outline"
            }
            sub_path = WORKSPACE_DIR / "subtitle_settings.json"
            if sub_path.exists():
                try:
                    with open(sub_path, "r", encoding="utf-8") as f:
                        subtitle_settings = json.load(f)
                except Exception:
                    pass

            # Fetch chapter settings
            chapter_settings = {
                "show_page_number": True,
                "bg_color": "#0E7C7B",
                "font_size": 24,
                "show_version_badge": True,
                "version_text": "SLIDE EDITION v2.4.0 (2026/07/25)"
            }
            chap_path = WORKSPACE_DIR / "chapter_settings.json"
            if chap_path.exists():
                try:
                    with open(chap_path, "r", encoding="utf-8") as f:
                        chapter_settings = json.load(f)
                except Exception:
                    pass

            # Fetch project info
            project_info = {}
            project_info_path = WORKSPACE_DIR / "project_info.json"
            if project_info_path.exists():
                try:
                    with open(project_info_path, "r", encoding="utf-8") as f:
                        project_info = json.load(f)
                except Exception:
                    pass

            # Fetch video settings
            video_settings = {"crf": 27}
            video_path = WORKSPACE_DIR / "video_settings.json"
            if video_path.exists():
                try:
                    with open(video_path, "r", encoding="utf-8") as f:
                        video_settings = json.load(f)
                except Exception:
                    pass

            response = {
                "slides": data,
                "voice_settings": voice_settings,
                "subtitle_settings": subtitle_settings,
                "chapter_settings": chapter_settings,
                "video_settings": video_settings,
                "project_info": project_info
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            return
            
        elif path == "/api/render_video":
            # Server-Sent Events (SSE) for real-time logs
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            
            def log_sse(msg):
                self.wfile.write(f"data: {json.dumps({'log': msg}, ensure_ascii=False)}\n\n".encode('utf-8'))
                self.wfile.flush()

            try:
                # 1. Generate/regenerate TTS for all slides
                log_sse("--- [1/4] 開始檢查並產生所有分頁配音 (Edge-TTS 雙人/單人模式) ---")
                voice_settings = {
                    "mode": "single",
                    "voice": "zh-TW-YunJheNeural",
                    "rate": "-5%",
                    "pitch": "-2Hz",
                    "pad_time": 1.5,
                    "speaker_a": {"name": "主持人 A", "voice": "zh-TW-YunJheNeural", "rate": "-5%", "pitch": "-2Hz", "color": "#40a9ff"},
                    "speaker_b": {"name": "對談者 B", "voice": "zh-TW-HsiaoChenNeural", "rate": "-5%", "pitch": "-2Hz", "color": "#fadb14"}
                }
                voice_path = WORKSPACE_DIR / "voice_settings.json"
                if voice_path.exists():
                    try:
                        with open(voice_path, "r", encoding="utf-8") as f_vs:
                            voice_settings.update(json.load(f_vs))
                    except Exception:
                        pass

                cleaned_path = WORKSPACE_DIR / "口白_cleaned.json"
                if cleaned_path.exists():
                    with open(cleaned_path, "r", encoding="utf-8") as f_cl:
                        slides = json.load(f_cl)
                else:
                    slides = []
                    
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                for s in slides:
                    num = s["num"]
                    text = s.get("narration", "")
                    if text.startswith("「") and text.endswith("」"):
                        text = text[1:-1]
                    if text:
                        log_sse(f"  正在產生第 {num} 頁配音 (語音模式: {voice_settings.get('mode', 'single')})...")
                        loop.run_until_complete(generate_page_tts(num, text, voice_settings))
                loop.close()
                log_sse("✓ 所有投影片配音產生完畢！")

                log_sse("\n--- [2/4] 開始重新計算與儲存分頁時序 ---")
                calculate_dynamic_timings()
                log_sse("✓ 成功計算 timings.json 與 timings_dynamic.json")
                
                log_sse("\n--- [3/4] 開始編譯網頁動畫範本 (build_index.py) ---")
                build_proc = subprocess.Popen(
                    [sys.executable, "build_index.py"],
                    cwd=str(WORKSPACE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8'
                )
                for line in build_proc.stdout:
                    log_sse(f"  [編譯] {line.strip()}")
                build_proc.wait()
                if build_proc.returncode != 0:
                    log_sse("❌ 編譯 index.html 失敗！")
                    return
                log_sse("✓ index.html 編譯完成！")
                
                # Read CRF from video_settings.json
                crf_value = 27
                video_path = WORKSPACE_DIR / "video_settings.json"
                if video_path.exists():
                    try:
                        with open(video_path, "r", encoding="utf-8") as f:
                            crf_value = json.load(f).get("crf", 27)
                    except Exception:
                        pass

                log_sse(f"\n--- [4/4] 開始渲染與封裝影片 (render.py, 畫質 CRF: {crf_value}) ---")
                env = os.environ.copy()
                env["PYTHONUTF8"] = "1"
                env["VIDEO_CRF"] = str(crf_value)
                
                render_proc = subprocess.Popen(
                    [sys.executable, "render.py"],
                    cwd=str(WORKSPACE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    env=env
                )
                final_video_name = None
                clean_video_name = None
                srt_filename = None
                for line in render_proc.stdout:
                    stripped = line.strip()
                    if stripped.startswith("OUTPUT_FILENAME:"):
                        final_video_name = stripped.split("OUTPUT_FILENAME:", 1)[1].strip()
                    elif stripped.startswith("OUTPUT_FILENAME_CLEAN:"):
                        clean_video_name = stripped.split("OUTPUT_FILENAME_CLEAN:", 1)[1].strip()
                    elif stripped.startswith("OUTPUT_SRT_FILENAME:"):
                        srt_filename = stripped.split("OUTPUT_SRT_FILENAME:", 1)[1].strip()
                    else:
                        log_sse(f"  [渲染] {stripped}")
                render_proc.wait()
                
                if render_proc.returncode != 0:
                    log_sse("❌ 影片渲染壓制失敗！請檢查背景進程。")
                else:
                    if not final_video_name:
                        base_stem = "《企業朝廷生存指南》"
                        project_info_path = WORKSPACE_DIR / "project_info.json"
                        if project_info_path.exists():
                            try:
                                with open(project_info_path, "r", encoding="utf-8") as f_pi:
                                    info = json.load(f_pi)
                                    pdf_name = info.get("slide_pdf_name")
                                    if pdf_name:
                                        base_stem = pathlib.Path(pdf_name).stem
                            except Exception:
                                pass
                        final_video_name = f"{base_stem}_內嵌字幕.mp4"
                        clean_video_name = f"{base_stem}_無字幕.mp4"
                        srt_filename = f"{base_stem}.srt"
                            
                    log_sse("\n🎉 恭喜！影片全部製作成功！")
                    if final_video_name:
                        log_sse(f"  已輸出影片：{WORKSPACE_DIR / 'MovieOutput' / final_video_name}")
                    if clean_video_name:
                        log_sse(f"  已輸出無字幕版：{WORKSPACE_DIR / 'MovieOutput' / clean_video_name}")
                    if srt_filename:
                        log_sse(f"  已輸出字幕檔案：{WORKSPACE_DIR / 'MovieOutput' / srt_filename}")
                    
                    self.wfile.write(f"data: {json.dumps({'complete': True, 'video_name': final_video_name or '', 'clean_video_name': clean_video_name or '', 'srt_name': srt_filename or ''}, ensure_ascii=False)}\n\n".encode('utf-8'))
                    self.wfile.flush()
            except Exception as e:
                log_sse(f"錯誤: {str(e)}")
            return
            
        return super().do_GET()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        content_length = int(self.headers['Content-Length'])
        print(f"[DEBUG] 接收到 POST 請求，資料大小: {content_length} 位元組", flush=True)
        
        post_data = b""
        remaining = content_length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            post_data += chunk
            remaining -= len(chunk)
            if (content_length - remaining) % (1024 * 1024) < 65536:
                print(f"  [DEBUG] 讀取進度: {content_length - remaining} / {content_length} 位元組", flush=True)
                
        print(f"[DEBUG] 資料讀取完畢，實際讀取: {len(post_data)} 位元組。開始解析 JSON...", flush=True)
        payload = json.loads(post_data.decode('utf-8'))
        print("[DEBUG] JSON 解析成功，開始路由處理。", flush=True)
        
        if path == "/api/save_slide":
            num = int(payload["num"])
            title = payload["title"]
            narration = payload["narration"]
            
            cleaned_path = WORKSPACE_DIR / "口白_cleaned.json"
            if cleaned_path.exists():
                with open(cleaned_path, "r", encoding="utf-8") as f:
                    slides = json.load(f)
            else:
                slides = []
                
            updated = False
            for s in slides:
                if s["num"] == num:
                    s["title"] = title
                    s["narration"] = narration
                    updated = True
                    break
            if not updated:
                slides.append({"num": num, "title": title, "narration": narration})
                
            slides.sort(key=lambda x: x["num"])
            
            with open(cleaned_path, "w", encoding="utf-8") as f:
                json.dump(slides, f, ensure_ascii=False, indent=2)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return
            
        elif path == "/api/test_tts":
            num = int(payload["num"])
            text = payload["narration"]
            
            voice_path = WORKSPACE_DIR / "voice_settings.json"
            voice_settings = {
                "mode": "single",
                "voice": "zh-TW-YunJheNeural",
                "rate": "-5%",
                "pitch": "-2Hz",
                "pad_time": 1.5,
                "speaker_a": {"name": "主持人 A", "voice": "zh-TW-YunJheNeural", "rate": "-5%", "pitch": "-2Hz", "color": "#40a9ff"},
                "speaker_b": {"name": "對談者 B", "voice": "zh-TW-HsiaoChenNeural", "rate": "-5%", "pitch": "-2Hz", "color": "#fadb14"}
            }
            if voice_path.exists():
                try:
                    with open(voice_path, "r", encoding="utf-8") as f:
                        voice_settings.update(json.load(f))
                except Exception:
                    pass

            if "mode" in payload: voice_settings["mode"] = payload["mode"]
            if "voice" in payload: voice_settings["voice"] = payload["voice"]
            if "rate" in payload: voice_settings["rate"] = payload["rate"]
            if "pitch" in payload: voice_settings["pitch"] = payload["pitch"]
            if "pad_time" in payload: voice_settings["pad_time"] = float(payload["pad_time"])
            if "speaker_a" in payload: voice_settings["speaker_a"] = payload["speaker_a"]
            if "speaker_b" in payload: voice_settings["speaker_b"] = payload["speaker_b"]

            text_clean = text
            if text_clean.startswith("「") and text_clean.endswith("」"):
                text_clean = text_clean[1:-1]
                
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio_file = loop.run_until_complete(generate_page_tts(num, text_clean, voice_settings))
                loop.close()
                
                dur_seconds = get_audio_duration(audio_file)
                calculate_dynamic_timings()
                
                response = {
                    "success": True,
                    "duration": round(dur_seconds, 2),
                    "audio_url": f"/assets/narration/page-{num:02d}.mp3?t={int(time.time())}"
                }
            except Exception as e:
                response = {
                    "success": False,
                    "error": str(e)
                }
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            return

        elif path == "/api/save_voice_settings":
            mode = payload.get("mode", "single")
            voice = payload.get("voice", "zh-TW-YunJheNeural")
            rate = payload.get("rate", "-5%")
            pitch = payload.get("pitch", "-2Hz")
            pad_time = float(payload.get("pad_time", 1.5))
            
            speaker_a = payload.get("speaker_a", {
                "name": "主持人 A",
                "voice": "zh-TW-YunJheNeural",
                "rate": "-5%",
                "pitch": "-2Hz",
                "color": "#40a9ff"
            })
            speaker_b = payload.get("speaker_b", {
                "name": "對談者 B",
                "voice": "zh-TW-HsiaoChenNeural",
                "rate": "-5%",
                "pitch": "-2Hz",
                "color": "#fadb14"
            })

            vs_data = {
                "mode": mode,
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
                "pad_time": pad_time,
                "speaker_a": speaker_a,
                "speaker_b": speaker_b
            }

            voice_path = WORKSPACE_DIR / "voice_settings.json"
            with open(voice_path, "w", encoding="utf-8") as f:
                json.dump(vs_data, f, ensure_ascii=False, indent=2)
                
            calculate_dynamic_timings()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return
            
        elif path == "/api/save_video_settings":
            crf = int(payload.get("crf", 27))
            video_path = WORKSPACE_DIR / "video_settings.json"
            with open(video_path, "w", encoding="utf-8") as f:
                json.dump({"crf": crf}, f, ensure_ascii=False, indent=2)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return
            
        elif path == "/api/save_subtitle_settings":
            sub_path = WORKSPACE_DIR / "subtitle_settings.json"
            with open(sub_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return

        elif path == "/api/save_chapter_settings":
            chap_path = WORKSPACE_DIR / "chapter_settings.json"
            with open(chap_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return

        elif path == "/api/upload_project":
            slide_pdf_b64 = payload.get("slide_pdf")
            slide_pdf_name = payload.get("slide_pdf_name", "簡報.pdf")
            narration_type = payload.get("narration_type")
            narration_data = payload.get("narration_data")
            
            try:
                # Save project info
                project_info = {
                    "slide_pdf_name": slide_pdf_name
                }
                with open(WORKSPACE_DIR / "project_info.json", "w", encoding="utf-8") as f_pi:
                    json.dump(project_info, f_pi, ensure_ascii=False, indent=2)

                # 1. Decode & render slides
                slide_pdf_bytes = decode_base64_file(slide_pdf_b64)
                num_slides = render_uploaded_slides(slide_pdf_bytes)
                
                # 2. Extract and parse narration script
                if narration_type == "pdf":
                    narration_pdf_bytes = decode_base64_file(narration_data)
                    script_text = extract_text_from_pdf_bytes(narration_pdf_bytes)
                else:
                    script_text = narration_data
                    
                parse_and_save_narration(script_text, num_slides)
                
                # 3. Recalculate timings
                calculate_dynamic_timings()
                
                response = {
                    "success": True,
                    "num_slides": num_slides
                }
            except Exception as e:
                response = {
                    "success": False,
                    "error": str(e)
                }
                
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>簡報影片 AI 製作工坊 (Traditional Chinese GUI)</title>
    <style>
        :root {
            --bg-color: #0d0b18;
            --card-bg: rgba(22, 19, 43, 0.65);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-color: #f1f0f7;
            --text-muted: rgba(241, 240, 247, 0.6);
            --accent-color: #06b6d4; /* Neon Cyan */
            --accent-purple: #8b5cf6; /* Violet */
            --success-color: #10b981;
            --btn-hover: rgba(6, 182, 212, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-color);
            background-image: radial-gradient(circle at 20% 30%, rgba(139, 92, 246, 0.15) 0%, transparent 40%),
                              radial-gradient(circle at 80% 70%, rgba(6, 182, 212, 0.15) 0%, transparent 40%);
            color: var(--text-color);
            font-family: 'PingFang TC', 'Microsoft JhengHei', sans-serif;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        header {
            background: rgba(13, 11, 24, 0.85);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border-color);
            padding: 15px 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            z-index: 10;
        }

        header h1 {
            font-size: 22px;
            font-weight: 700;
            letter-spacing: 1px;
            background: linear-gradient(to right, #06b6d4, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .container {
            display: flex;
            flex: 1;
            overflow: hidden;
        }

        /* Left column: Slide selector */
        .sidebar {
            width: 320px;
            background: rgba(18, 15, 36, 0.45);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            overflow-y: auto;
        }

        .sidebar-header {
            padding: 15px 20px;
            font-size: 14px;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            letter-spacing: 0.5px;
        }

        .slide-list {
            list-style: none;
            padding: 10px;
        }

        .slide-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-bottom: 8px;
            border: 1px solid transparent;
        }

        .slide-item:hover {
            background: rgba(255, 255, 255, 0.04);
            border-color: rgba(255, 255, 255, 0.05);
        }

        .slide-item.active {
            background: rgba(6, 182, 212, 0.08);
            border-color: rgba(6, 182, 212, 0.3);
        }

        .slide-thumb {
            width: 70px;
            height: 40px;
            border-radius: 4px;
            background-size: cover;
            background-position: center;
            border: 1px solid rgba(255, 255, 255, 0.1);
            flex-shrink: 0;
            background-color: #121020;
        }

        .slide-info {
            overflow: hidden;
        }

        .slide-info .num {
            font-size: 11px;
            font-weight: 700;
            color: var(--accent-color);
            text-transform: uppercase;
        }

        .slide-info .title {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-color);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* Center column: Editor */
        .main-editor {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            padding: 30px;
            gap: 24px;
        }

        .card {
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
            flex-shrink: 0;
        }

        .card.collapsible-card {
            padding: 0;
            overflow: hidden;
            margin-bottom: 20px;
        }

        .collapsible-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            cursor: pointer;
            user-select: none;
            background: rgba(255, 255, 255, 0.02);
            border-bottom: 1px solid transparent;
            transition: background 0.2s ease, border-color 0.2s ease;
        }

        .collapsible-header:hover {
            background: rgba(255, 255, 255, 0.05);
        }

        .collapsible-header .title-text {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-color);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .collapsible-header .arrow-icon {
            font-size: 12px;
            color: var(--text-muted);
            transition: transform 0.3s ease;
        }

        .collapsible-content {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out, padding 0.3s ease;
            padding: 0 20px;
        }

        .collapsible-card.active .collapsible-header {
            border-color: var(--border-color);
        }

        .collapsible-card.active .collapsible-content {
            max-height: 600px;
            padding: 16px 20px 20px 20px;
            overflow-y: visible;
        }

        .collapsible-card.active .arrow-icon {
            transform: rotate(180deg);
        }

        .preview-container {
            width: 100%;
            aspect-ratio: 16/9;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
            position: relative;
            border: 1px solid var(--border-color);
        }

        .preview-img {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }

        .editor-form {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .form-row {
            display: flex;
            gap: 16px;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex: 1;
        }

        label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
        }

        input[type="text"], textarea, select {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 10px 12px;
            color: var(--text-color);
            font-size: 14px;
            transition: all 0.2s ease;
        }

        input[type="text"]:focus, textarea:focus, select:focus {
            outline: none;
            border-color: var(--accent-color);
            box-shadow: 0 0 0 2px rgba(6, 182, 212, 0.15);
        }

        textarea {
            resize: vertical;
            min-height: 100px;
            font-family: inherit;
            line-height: 1.5;
        }

        .button-group {
            display: flex;
            gap: 12px;
            justify-content: flex-end;
            margin-top: 8px;
        }

        button {
            padding: 10px 20px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            border: 1px solid transparent;
        }

        button.primary {
            background: linear-gradient(135deg, var(--accent-color), #0891b2);
            color: #0d0b18;
        }

        button.primary:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(6, 182, 212, 0.3);
        }

        button.secondary {
            background: rgba(255, 255, 255, 0.05);
            border-color: var(--border-color);
            color: var(--text-color);
        }

        button.secondary:hover {
            background: rgba(255, 255, 255, 0.08);
        }

        /* Right column: Settings & Compile Control */
        .right-panel {
            width: 380px;
            border-left: 1px solid var(--border-color);
            background: rgba(18, 15, 36, 0.45);
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            padding: 24px;
            gap: 24px;
        }

        .section-title {
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 16px;
            color: var(--text-color);
            border-left: 3px solid var(--accent-purple);
            padding-left: 10px;
        }

        .slider-group {
            margin-bottom: 12px;
        }

        .slider-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
        }

        input[type="range"] {
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
            outline: none;
            -webkit-appearance: none;
            accent-color: var(--accent-purple);
        }

        .audio-player-container {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            margin-top: 10px;
        }

        audio {
            width: 100%;
            height: 32px;
        }

        /* Compile logs screen */
        .compile-card {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .console-log {
            background: #05040a;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            height: 180px;
            padding: 12px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 11px;
            color: #10b981;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.4;
        }

        .progress-bar-container {
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            overflow: hidden;
            display: none;
        }

        .progress-bar {
            width: 0%;
            height: 100%;
            background: linear-gradient(to right, var(--accent-color), var(--accent-purple));
            transition: width 0.3s ease;
        }

        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: var(--success-color);
            color: #000;
            padding: 12px 24px;
            border-radius: 6px;
            font-weight: 700;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s ease;
            z-index: 1000;
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }

        .loading-spinner {
            border: 3px solid rgba(255, 255, 255, 0.1);
            border-radius: 50%;
            border-top: 3px solid var(--accent-color);
            width: 18px;
            height: 18px;
            animation: spin 1s linear infinite;
            display: inline-block;
            vertical-align: middle;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .play-video-btn {
            background: var(--success-color);
            color: #000;
            font-weight: 700;
            text-decoration: none;
            padding: 12px 20px;
            border-radius: 6px;
            text-align: center;
            display: none;
            transition: all 0.2s ease;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.25);
        }
        .play-video-btn:hover {
            transform: scale(1.02);
            filter: brightness(1.1);
        }

        /* 右上角版本標籤 (Capsule Version Badge) */
        .version-badge {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            background: rgba(18, 24, 33, 0.75);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 9999px;
            padding: 7px 20px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
        }

        .version-badge .version-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background-color: #4e937a;
            box-shadow: 0 0 8px rgba(78, 147, 122, 0.6);
            flex-shrink: 0;
        }

        .version-badge .version-text {
            font-family: 'GenSeki TW', system-ui, -apple-system, sans-serif;
            font-weight: 700;
            font-size: 13px;
            letter-spacing: 0.08em;
            color: #ffffff;
            text-transform: uppercase;
            white-space: nowrap;
        }
    </style>
</head>
<body>
    <header>
        <h1>🎬 簡報影片 AI 製作工坊</h1>
        <div class="version-badge">
            <span class="version-dot"></span>
            <span class="version-text">SLIDE EDITION v2.4.0 (2026/07/25)</span>
        </div>
    </header>

    <div class="container">
        <!-- Left: Slide List & Upload Form -->
        <div class="sidebar">
            <!-- Upload Panel -->
            <div class="card" style="margin: 10px; padding: 15px; background: rgba(30, 27, 57, 0.6); border: 1px solid rgba(6, 182, 212, 0.2);">
                <div style="font-size: 13px; font-weight: 700; color: var(--accent-color); margin-bottom: 8px;">📂 匯入全新簡報與口白</div>
                <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 12px; line-height: 1.3;">
                    請提供簡報 PDF（若是 PPTX 請另存為 PDF）與口白文字檔案：
                </div>
                <div style="display: flex; flex-direction: column; gap: 8px; font-size: 12px;">
                    <div>
                        <label style="font-size: 11px; margin-bottom: 2px; display: block; color: var(--text-color)">1. 簡報投影片 PDF</label>
                        <input type="file" id="slidePdfFile" accept=".pdf" style="font-size: 11px; width: 100%; color: var(--text-muted);">
                    </div>
                    <div>
                        <label style="font-size: 11px; margin-bottom: 2px; display: block; color: var(--text-color)">2. 口白檔案 (.pdf / .txt)</label>
                        <input type="file" id="narrationFile" accept=".pdf,.txt" style="font-size: 11px; width: 100%; color: var(--text-muted);">
                    </div>
                    <button class="primary" id="btnUploadProject" style="font-size: 11px; padding: 6px 12px; margin-top: 6px; width: 100%;">
                        🚀 匯入並初始化專案
                    </button>
                </div>
            </div>

            <div class="sidebar-header">投影片分頁清單</div>
            <ul class="slide-list" id="slideList">
                <!-- Slide items will be loaded dynamically -->
            </ul>
        </div>

        <!-- Center: Slide Editor -->
        <div class="main-editor">
            <div class="card" style="padding: 12px;">
                <div class="preview-container">
                    <img id="slidePreview" class="preview-img" src="" alt="Slide Preview">
                </div>
            </div>

            <div class="card">
                <div class="section-title">分頁口白編輯與配音</div>
                <div class="editor-form">
                    <div class="form-row">
                        <div class="form-group" style="flex: 0.2;">
                            <label for="slideNum">頁碼</label>
                            <input type="text" id="slideNum" readonly style="text-align: center; font-weight: bold;">
                        </div>
                        <div class="form-group" style="flex: 0.8;">
                            <label for="slideTitle">本頁標題</label>
                            <input type="text" id="slideTitle">
                        </div>
                    </div>

                    <div class="form-group">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <label for="slideNarration">旁白口白內容 (句子間用標點符號分隔，將自動切分子句時間碼)</label>
                            <div id="dialogueTagContainer" style="display: none; gap: 6px; align-items: center;">
                                <span style="font-size: 11px; color: var(--text-muted);">快速插入對話標點:</span>
                                <button type="button" class="secondary" onclick="insertTag('A: ')" style="padding: 2px 8px; font-size: 11px; background: rgba(6,182,212,0.15); color: #06b6d4; border-color: rgba(6,182,212,0.3);">+ A (主持人)</button>
                                <button type="button" class="secondary" onclick="insertTag('B: ')" style="padding: 2px 8px; font-size: 11px; background: rgba(139,92,246,0.15); color: #8b5cf6; border-color: rgba(139,92,246,0.3);">+ B (對談者)</button>
                            </div>
                        </div>
                        <textarea id="slideNarration" placeholder="請輸入口白段落... 支援雙人對話如 『A: 內容... B: 內容...』"></textarea>
                    </div>

                    <div class="button-group">
                        <button class="secondary" id="btnTestTts">🎵 試聽與生成本頁配音</button>
                        <button class="primary" id="btnSaveSlide">💾 儲存口白文字</button>
                    </div>

                    <div class="audio-player-container" id="audioContainer" style="display: none;">
                        <audio id="audioPlayer" controls></audio>
                        <div id="audioDuration" style="font-size: 12px; color: var(--accent-color); font-weight: bold;"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Right: Settings & Compiler -->
        <div class="right-panel">
            <!-- Voice & Dual Dialogue Settings -->
            <div class="card collapsible-card active" id="voiceCard">
                <div class="collapsible-header" onclick="toggleCollapsible('voiceCard')">
                    <span class="title-text">🎙️ 雙人對話與語音設定</span>
                    <span class="arrow-icon">▼</span>
                </div>
                <div class="collapsible-content">
                    <div class="form-group" style="margin-bottom: 14px;">
                        <label for="voiceMode">語音播報模式</label>
                        <select id="voiceMode" onchange="toggleVoiceModeUI()">
                            <option value="single">單人旁白模式 (單一主講)</option>
                            <option value="dual">雙人對話模式 (Host A & Guest B 互動對談)</option>
                        </select>
                    </div>

                    <!-- Single Voice Mode UI -->
                    <div id="singleVoicePanel">
                        <div class="form-group" style="margin-bottom: 10px;">
                            <label for="voiceSelect">單人語音配音員 (Edge-TTS)</label>
                            <select id="voiceSelect">
                                <option value="zh-TW-YunJheNeural">台灣雲哲 (男聲 - 推薦)</option>
                                <option value="zh-TW-HsiaoChenNeural">台灣曉臻 (女聲)</option>
                                <option value="zh-CN-YunxiNeural">國語雲希 (男聲)</option>
                                <option value="zh-CN-XiaoxiaoNeural">國語曉曉 (女聲)</option>
                            </select>
                        </div>
                        <div class="form-row" style="margin-bottom: 10px;">
                            <div class="form-group">
                                <label for="rateSelect">單人語速調整</label>
                                <select id="rateSelect">
                                    <option value="-50%">特慢 (-50%)</option>
                                    <option value="-30%">很慢 (-30%)</option>
                                    <option value="-20%">較慢 (-20%)</option>
                                    <option value="-10%">稍慢 (-10%)</option>
                                    <option value="-5%" selected>微慢 (-5%)</option>
                                    <option value="0%">正常 (0%)</option>
                                    <option value="+5%">微快 (+5%)</option>
                                    <option value="+10%">稍快 (+10%)</option>
                                    <option value="+20%">較快 (+20%)</option>
                                    <option value="+30%">很快 (+30%)</option>
                                    <option value="+50%">特快 (+50%)</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="pitchSelect">單人音高調整</label>
                                <select id="pitchSelect">
                                    <option value="-5Hz">低沈 (-5Hz)</option>
                                    <option value="-2Hz" selected>微低 (-2Hz)</option>
                                    <option value="0Hz">正常 (0Hz)</option>
                                    <option value="+2Hz">微高 (+2Hz)</option>
                                    <option value="+5Hz">高亢 (+5Hz)</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Dual Voice Mode UI -->
                    <div id="dualVoicePanel" style="display: none; background: rgba(0,0,0,0.25); padding: 14px; border-radius: 8px; border: 1px solid var(--border-color); margin-bottom: 14px;">
                        
                        <!-- 雙人高對比推薦配色預設面板 -->
                        <div style="background: rgba(255,255,255,0.05); padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 11px;">
                            <div style="color: #aaa; font-weight: 600; margin-bottom: 6px;">✨ 推薦黑底高對比雙人配色組合：</div>
                            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                                <button type="button" onclick="setDualColors('#40a9ff', '#fadb14')" style="padding: 3px 8px; font-size: 11px; background: rgba(64,169,255,0.15); color: #fff; border: 1px solid #40a9ff; border-radius: 4px; cursor: pointer;">
                                    <span style="color: #40a9ff;">■ A 亮藍</span> + <span style="color: #fadb14;">■ B 明黃</span> (推薦)
                                </button>
                                <button type="button" onclick="setDualColors('#ffffff', '#fadb14')" style="padding: 3px 8px; font-size: 11px; background: rgba(255,255,255,0.15); color: #fff; border: 1px solid #ffffff; border-radius: 4px; cursor: pointer;">
                                    <span style="color: #ffffff;">■ A 純白</span> + <span style="color: #fadb14;">■ B 明黃</span>
                                </button>
                                <button type="button" onclick="setDualColors('#40a9ff', '#a0d911')" style="padding: 3px 8px; font-size: 11px; background: rgba(160,217,17,0.15); color: #fff; border: 1px solid #a0d911; border-radius: 4px; cursor: pointer;">
                                    <span style="color: #40a9ff;">■ A 亮藍</span> + <span style="color: #a0d911;">■ B 淺綠</span>
                                </button>
                            </div>
                        </div>

                        <!-- Speaker A Box -->
                        <div style="margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px dashed var(--border-color);">
                            <div style="font-size: 12px; font-weight: bold; color: #40a9ff; margin-bottom: 8px;">🔷 角色 A (主講人 / Host A)</div>
                            <div class="form-row" style="margin-bottom: 8px;">
                                <div class="form-group">
                                    <label for="spkA_name">角色名稱</label>
                                    <input type="text" id="spkA_name" value="主持人 A" style="padding: 6px 10px; font-size: 12px;">
                                </div>
                                <div class="form-group" style="flex: 0.5;">
                                    <label for="spkA_color">代表色</label>
                                    <div style="display: flex; gap: 6px; align-items: center;">
                                        <input type="color" id="spkA_color" value="#40a9ff" style="height: 32px; border: none; background: transparent; cursor: pointer; width: 40px;">
                                        <div style="display: flex; gap: 3px;">
                                            <span onclick="document.getElementById('spkA_color').value='#40a9ff'" style="width: 18px; height: 18px; background: #40a9ff; border-radius: 3px; cursor: pointer; display: inline-block;" title="亮藍"></span>
                                            <span onclick="document.getElementById('spkA_color').value='#ffffff'" style="width: 18px; height: 18px; background: #ffffff; border-radius: 3px; cursor: pointer; display: inline-block;" title="純白"></span>
                                            <span onclick="document.getElementById('spkA_color').value='#fadb14'" style="width: 18px; height: 18px; background: #fadb14; border-radius: 3px; cursor: pointer; display: inline-block;" title="明黃"></span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="form-group" style="margin-bottom: 8px;">
                                <label for="spkA_voice">角色 A 語音</label>
                                <select id="spkA_voice" style="padding: 6px 10px; font-size: 12px;">
                                    <option value="zh-TW-YunJheNeural" selected>台灣雲哲 (男聲)</option>
                                    <option value="zh-TW-HsiaoChenNeural">台灣曉臻 (女聲)</option>
                                    <option value="zh-CN-YunjianNeural">國語雲健 (男聲)</option>
                                    <option value="zh-CN-XiaoxiaoNeural">國語曉曉 (女聲)</option>
                                </select>
                            </div>
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="spkA_rate">角色 A 語速</label>
                                    <select id="spkA_rate" style="padding: 6px 10px; font-size: 12px;">
                                        <option value="-30%">很慢 (-30%)</option>
                                        <option value="-20%">較慢 (-20%)</option>
                                        <option value="-10%">稍慢 (-10%)</option>
                                        <option value="-5%" selected>微慢 (-5%)</option>
                                        <option value="0%">正常 (0%)</option>
                                        <option value="+5%">微快 (+5%)</option>
                                        <option value="+10%">稍快 (+10%)</option>
                                        <option value="+20%">較快 (+20%)</option>
                                        <option value="+30%">很快 (+30%)</option>
                                    </select>
                                </div>
                                <div class="form-group">
                                    <label for="spkA_pitch">角色 A 音高</label>
                                    <select id="spkA_pitch" style="padding: 6px 10px; font-size: 12px;">
                                        <option value="-5Hz">低沈 (-5Hz)</option>
                                        <option value="-2Hz" selected>微低 (-2Hz)</option>
                                        <option value="0Hz">正常 (0Hz)</option>
                                        <option value="+2Hz">微高 (+2Hz)</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        <!-- Speaker B Box -->
                        <div>
                            <div style="font-size: 12px; font-weight: bold; color: #fadb14; margin-bottom: 8px;">💛 角色 B (對談者 / Guest B)</div>
                            <div class="form-row" style="margin-bottom: 8px;">
                                <div class="form-group">
                                    <label for="spkB_name">角色名稱</label>
                                    <input type="text" id="spkB_name" value="專家 B" style="padding: 6px 10px; font-size: 12px;">
                                </div>
                                <div class="form-group" style="flex: 0.5;">
                                    <label for="spkB_color">代表色</label>
                                    <div style="display: flex; gap: 6px; align-items: center;">
                                        <input type="color" id="spkB_color" value="#fadb14" style="height: 32px; border: none; background: transparent; cursor: pointer; width: 40px;">
                                        <div style="display: flex; gap: 3px;">
                                            <span onclick="document.getElementById('spkB_color').value='#fadb14'" style="width: 18px; height: 18px; background: #fadb14; border-radius: 3px; cursor: pointer; display: inline-block;" title="明黃"></span>
                                            <span onclick="document.getElementById('spkB_color').value='#a0d911'" style="width: 18px; height: 18px; background: #a0d911; border-radius: 3px; cursor: pointer; display: inline-block;" title="淺綠"></span>
                                            <span onclick="document.getElementById('spkB_color').value='#ff85c0'" style="width: 18px; height: 18px; background: #ff85c0; border-radius: 3px; cursor: pointer; display: inline-block;" title="粉紅"></span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="form-group" style="margin-bottom: 8px;">
                                <label for="spkB_voice">角色 B 語音</label>
                                <select id="spkB_voice" style="padding: 6px 10px; font-size: 12px;">
                                    <option value="zh-TW-HsiaoChenNeural" selected>台灣曉臻 (女聲)</option>
                                    <option value="zh-TW-YunJheNeural">台灣雲哲 (男聲)</option>
                                    <option value="zh-CN-XiaoxiaoNeural">國語曉曉 (女聲)</option>
                                    <option value="zh-CN-YunxiNeural">國語雲希 (男聲)</option>
                                </select>
                            </div>
                            <div class="form-row">
                                <div class="form-group">
                                    <label for="spkB_rate">角色 B 語速</label>
                                    <select id="spkB_rate" style="padding: 6px 10px; font-size: 12px;">
                                        <option value="-30%">很慢 (-30%)</option>
                                        <option value="-20%">較慢 (-20%)</option>
                                        <option value="-10%">稍慢 (-10%)</option>
                                        <option value="-5%" selected>微慢 (-5%)</option>
                                        <option value="0%">正常 (0%)</option>
                                        <option value="+5%">微快 (+5%)</option>
                                        <option value="+10%">稍快 (+10%)</option>
                                        <option value="+20%">較快 (+20%)</option>
                                        <option value="+30%">很快 (+30%)</option>
                                    </select>
                                </div>
                                <div class="form-group">
                                    <label for="spkB_pitch">角色 B 音高</label>
                                    <select id="spkB_pitch" style="padding: 6px 10px; font-size: 12px;">
                                        <option value="-5Hz">低沈 (-5Hz)</option>
                                        <option value="-2Hz" selected>微低 (-2Hz)</option>
                                        <option value="0Hz">正常 (0Hz)</option>
                                        <option value="+2Hz">微高 (+2Hz)</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="slider-group" style="margin-top: 10px; margin-bottom: 12px;">
                        <div class="slider-header">
                            <label for="padTime">每頁尾端停頓時間</label>
                            <span id="padTimeVal">1.5秒</span>
                        </div>
                        <input type="range" id="padTime" min="0" max="2" step="0.5" value="1.5" style="width: 100%;">
                    </div>

                    <button class="secondary" id="btnSaveVoiceSettings" style="width: 100%;">💾 儲存語音對話設定</button>
                    <button class="secondary" id="btnLoadDualSample" style="width: 100%; margin-top: 8px; background: rgba(139, 92, 246, 0.15); border-color: rgba(139, 92, 246, 0.4); color: #c084fc;">💡 載入雙人對話腳本範例</button>
                </div>
            </div>

            <div class="card collapsible-card" id="subtitleCard">
                <div class="collapsible-header" onclick="toggleCollapsible('subtitleCard')">
                    <span class="title-text">🎬 字幕樣式設定</span>
                    <span class="arrow-icon">▼</span>
                </div>
                <div class="collapsible-content">
                    <div class="form-group" style="margin-bottom: 16px;">
                        <label for="subMode">字幕輸出模式</label>
                        <select id="subMode">
                            <option value="embed">鑲嵌在影片中 (燒錄字幕)</option>
                            <option value="srt">獨立成 srt 檔 (不鑲嵌字幕)</option>
                            <option value="both">同時生成有字幕與無字幕影片 (並產生 srt 檔)</option>
                        </select>
                    </div>

                    <!-- Custom subtitle style controls (Stacked layout to fit sidebar width) -->
                    <div style="display: flex; flex-direction: column; gap: 15px; margin-bottom: 16px;">
                        <!-- Preview Box -->
                        <div style="display: flex; flex-direction: column; gap: 6px;">
                            <label>樣式即時預覽</label>
                            <div id="subtitlePreviewBox" style="background-image: linear-gradient(to bottom, rgba(0,0,0,0.1), rgba(0,0,0,0.35)), url('/assets/images/page-01.png'), linear-gradient(135deg, #1e3c72, #f1a7a1); background-size: cover; background-position: center; border: 1px solid var(--border-color); border-radius: 6px; display: flex; align-items: flex-end; justify-content: center; padding: 15px; min-height: 100px; position: relative; overflow: hidden;">
                                <div id="subtitlePreviewText" style="color: #fff; font-size: 14px; text-align: center; width: 100%; transition: all 0.2s; font-weight: bold; line-height: 1.4;">這是一段預覽字幕範例 (Subtitle Preview)</div>
                            </div>
                        </div>

                        <!-- Inputs -->
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div class="form-group">
                                <label for="fontName">字體名稱</label>
                                <select id="fontName">
                                    <option value="Microsoft JhengHei">微軟正黑體 (Microsoft JhengHei)</option>
                                    <option value="GenSeki TW">源石黑體 (GenSeki TW)</option>
                                    <option value="DFKai-SB">標楷體 (DFKai-SB)</option>
                                    <option value="PMingLiU">新細明體 (PMingLiU)</option>
                                    <option value="Noto Sans TC">Noto Sans TC</option>
                                    <option value="Arial">Arial</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="fontColor">字體顏色 <span style="font-size: 11px; color: #888;">(黑底建議高明度)</span></label>
                                <select id="fontColor">
                                    <option value="white">白色 (White - 最推薦)</option>
                                    <option value="yellow">明黃 (Yellow - 對比最高)</option>
                                    <option value="cyan">淺青 (Cyan - 高明度)</option>
                                    <option value="green">淺綠 (Light Green)</option>
                                    <option value="gray">淺灰 (Light Gray)</option>
                                </select>
                                <div style="font-size: 11px; color: #aaa; margin-top: 4px;">
                                    💡 提示：黑底請避免深藍、深紅或高飽和螢光色。
                                </div>
                            </div>
                            <div class="form-group">
                                <label for="bgStyle">背景樣式</label>
                                <select id="bgStyle">
                                    <option value="outline">描邊 + 陰影 (預設)</option>
                                    <option value="box">滿版/黑框背景</option>
                                    <option value="none">無背景</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <div class="form-group" style="margin-bottom: 16px; display: none;">
                        <label for="stylePreset">字幕背景預設樣式</label>
                        <select id="stylePreset">
                            <option value="cc">YouTube CC 簡約黑框 (只包覆文字)</option>
                            <option value="banner">底部滿版半透明條</option>
                            <option value="clean">無背景 (加強黑色投影字)</option>
                        </select>
                    </div>

                    <div class="slider-group">
                        <div class="slider-header">
                            <label for="fontSize">字體大小 (像素)</label>
                            <span id="fontSizeVal">32px</span>
                        </div>
                        <input type="range" id="fontSize" min="18" max="60" value="32">
                    </div>

                    <div class="slider-group" id="bgOpacityGroup">
                        <div class="slider-header">
                            <label for="bgOpacity">背景框透明度</label>
                            <span id="bgOpacityVal">0.75</span>
                        </div>
                        <input type="range" id="bgOpacity" min="0" max="1" step="0.05" value="0.75">
                    </div>

                    <div class="slider-group">
                        <div class="slider-header">
                            <label for="bottomPos">底部邊距 (Margin V)</label>
                            <span id="bottomPosVal">50px</span>
                        </div>
                        <input type="range" id="bottomPos" min="10" max="150" value="50">
                    </div>

                    <div class="slider-group">
                        <div class="slider-header">
                            <label for="maxWidth">最大寬度限制</label>
                            <span id="maxWidthVal">85%</span>
                        </div>
                        <input type="range" id="maxWidth" min="60" max="98" value="85">
                    </div>

                    <button class="secondary" id="btnSaveSubtitle" style="width: 100%; margin-top: 10px;">💾 套用並儲存字幕樣式</button>
                </div>
            </div>

            <div class="card collapsible-card" id="chapterCard">
                <div class="collapsible-header" onclick="toggleCollapsible('chapterCard')">
                    <span class="title-text">📌 頁面外觀與版本標籤設定</span>
                    <span class="arrow-icon">▼</span>
                </div>
                <div class="collapsible-content">
                    <div class="form-group" style="margin-bottom: 16px; display: flex; flex-direction: row; align-items: center; gap: 10px;">
                        <input type="checkbox" id="showPageNumber" checked style="width: 18px; height: 18px; cursor: pointer; flex-shrink: 0; margin-top: 2px;">
                        <label for="showPageNumber" style="cursor: pointer; margin-bottom: 0; user-select: none;">顯示左上角頁數與標題</label>
                    </div>

                    <div class="form-group" style="margin-bottom: 16px;">
                        <label for="pageBgColor">頁碼背景顏色</label>
                        <div style="display: flex; gap: 10px; align-items: center;">
                            <input type="color" id="pageBgColor" value="#0E7C7B" style="width: 50px; height: 36px; border: none; border-radius: 4px; cursor: pointer; background: transparent; padding: 0;">
                            <span id="pageBgColorText" style="font-family: monospace; font-size: 14px;">#0E7C7B</span>
                        </div>
                    </div>

                    <div class="slider-group" style="margin-bottom: 16px;">
                        <div class="slider-header">
                            <label for="pageFontSize">文字大小</label>
                            <span id="pageFontSizeVal">24px</span>
                        </div>
                        <input type="range" id="pageFontSize" min="14" max="40" value="24">
                    </div>

                    <div style="margin-top: 16px; padding-top: 14px; border-top: 1px dashed var(--border-color);">
                        <div class="form-group" style="margin-bottom: 12px; display: flex; flex-direction: row; align-items: center; gap: 10px;">
                            <input type="checkbox" id="showVersionBadge" checked style="width: 18px; height: 18px; cursor: pointer; flex-shrink: 0; margin-top: 2px;">
                            <label for="showVersionBadge" style="cursor: pointer; margin-bottom: 0; user-select: none;">顯示右上角版本標籤 (Version Badge)</label>
                        </div>
                        <div class="form-group" style="margin-bottom: 8px;">
                            <label for="versionText">版本標籤文字</label>
                            <input type="text" id="versionText" value="SLIDE EDITION v2.4.0 (2026/07/25)">
                        </div>
                    </div>

                    <button class="secondary" id="btnSaveChapter" style="width: 100%; margin-top: 10px;">💾 套用並儲存頁面與版本樣式</button>
                </div>
            </div>

            <div class="card compile-card">
                <div class="section-title">影片渲染控制</div>
                
                <div class="slider-group" style="margin-top: 10px; margin-bottom: 20px;">
                    <div class="slider-header">
                        <label for="videoCrf">影片畫質 (CRF)</label>
                        <span id="videoCrfVal">27</span>
                    </div>
                    <input type="range" id="videoCrf" min="20" max="27" step="1" value="27" style="width: 100%;">
                    <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                        <span>高品質 (20)</span>
                        <span>高壓縮 (27)</span>
                    </div>
                </div>

                <button class="primary" id="btnRenderVideo" style="width: 100%; padding: 14px; font-size: 15px; letter-spacing: 0.5px;">🎬 開始生成完整影片</button>
                
                <div class="progress-bar-container" id="progressBarContainer">
                    <div class="progress-bar" id="progressBar"></div>
                </div>

                <label>後台日誌控制台</label>
                <div class="console-log" id="logConsole">尚未開始渲染。</div>
                
                <div id="playButtonsContainer" style="display: flex; gap: 10px; margin-top: 15px;">
                    <a id="btnPlayVideo" class="play-video-btn" href="/《企業朝廷生存指南》_內嵌字幕.mp4" target="_blank" style="flex: 1; margin-top: 0;">▶ 播放內嵌字幕版</a>
                    <a id="btnPlayCleanVideo" class="play-video-btn" href="/《企業朝廷生存指南》_無字幕.mp4" target="_blank" style="flex: 1; margin-top: 0; background: #0E7C7B; color: #fff; box-shadow: 0 4px 12px rgba(14, 124, 123, 0.25);">▶ 播放無字幕版</a>
                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="toast">儲存成功！</div>

    <script>
        // Global error boundary to help user debug issues
        window.onerror = function(msg, url, line) {
            alert("網頁出錯啦！\\n錯誤訊息: " + msg + "\\n檔案位置: " + url + "\\n行號: " + line);
            return false;
        };

        function toggleCollapsible(id) {
            const card = document.getElementById(id);
            if (card) {
                card.classList.toggle('active');
            }
        }

        let slidesData = [];
        let currentSlideIdx = 0;

        // Load initialization data
        async function loadSlides() {
            try {
                const res = await fetch('/api/slides');
                const data = await res.json();
                slidesData = data.slides;
                
                if (data.voice_settings) {
                    const vs = data.voice_settings;
                    if (vs.mode) document.getElementById('voiceMode').value = vs.mode;
                    if (vs.voice) document.getElementById('voiceSelect').value = vs.voice;
                    if (vs.rate) document.getElementById('rateSelect').value = vs.rate;
                    if (vs.pitch) document.getElementById('pitchSelect').value = vs.pitch;
                    
                    if (vs.speaker_a) {
                        if (vs.speaker_a.name) document.getElementById('spkA_name').value = vs.speaker_a.name;
                        if (vs.speaker_a.color) document.getElementById('spkA_color').value = vs.speaker_a.color;
                        if (vs.speaker_a.voice) document.getElementById('spkA_voice').value = vs.speaker_a.voice;
                        if (vs.speaker_a.rate) document.getElementById('spkA_rate').value = vs.speaker_a.rate;
                        if (vs.speaker_a.pitch) document.getElementById('spkA_pitch').value = vs.speaker_a.pitch;
                    }
                    if (vs.speaker_b) {
                        if (vs.speaker_b.name) document.getElementById('spkB_name').value = vs.speaker_b.name;
                        if (vs.speaker_b.color) document.getElementById('spkB_color').value = vs.speaker_b.color;
                        if (vs.speaker_b.voice) document.getElementById('spkB_voice').value = vs.speaker_b.voice;
                        if (vs.speaker_b.rate) document.getElementById('spkB_rate').value = vs.speaker_b.rate;
                        if (vs.speaker_b.pitch) document.getElementById('spkB_pitch').value = vs.speaker_b.pitch;
                    }
                    if (typeof toggleVoiceModeUI === 'function') toggleVoiceModeUI();

                    if (vs.pad_time !== undefined) {
                        document.getElementById('padTime').value = vs.pad_time;
                        document.getElementById('padTimeVal').textContent = vs.pad_time + '秒';
                    } else {
                        document.getElementById('padTime').value = 1.5;
                        document.getElementById('padTimeVal').textContent = '1.5秒';
                    }
                }
                
                if (data.subtitle_settings) {
                    const sub = data.subtitle_settings;
                    document.getElementById('stylePreset').value = sub.style_preset || 'cc';
                    if (sub.mode !== undefined) {
                        document.getElementById('subMode').value = sub.mode;
                    } else {
                        document.getElementById('subMode').value = sub.embed !== false ? 'embed' : 'srt';
                    }
                    document.getElementById('fontSize').value = sub.font_size || 32;
                    document.getElementById('fontSizeVal').textContent = (sub.font_size || 32) + 'px';
                    document.getElementById('bgOpacity').value = sub.bg_opacity !== undefined ? sub.bg_opacity : 0.75;
                    document.getElementById('bgOpacityVal').textContent = sub.bg_opacity !== undefined ? sub.bg_opacity : 0.75;
                    document.getElementById('bottomPos').value = sub.bottom_pos || 50;
                    document.getElementById('bottomPosVal').textContent = (sub.bottom_pos || 50) + 'px';
                    document.getElementById('maxWidth').value = sub.max_width || 85;
                    document.getElementById('maxWidthVal').textContent = (sub.max_width || 85) + '%';
                    
                    // Populate new inputs
                    document.getElementById('fontName').value = sub.font_name || 'Microsoft JhengHei';
                    document.getElementById('fontColor').value = sub.font_color || 'white';
                    document.getElementById('bgStyle').value = sub.bg_style || 'outline';
                }

                if (data.chapter_settings) {
                    const chap = data.chapter_settings;
                    document.getElementById('showPageNumber').checked = chap.show_page_number !== false;
                    document.getElementById('pageBgColor').value = chap.bg_color || '#0E7C7B';
                    document.getElementById('pageBgColorText').textContent = chap.bg_color || '#0E7C7B';
                    document.getElementById('pageFontSize').value = chap.font_size || 24;
                    document.getElementById('pageFontSizeVal').textContent = (chap.font_size || 24) + 'px';
                    
                    document.getElementById('showVersionBadge').checked = chap.show_version_badge !== false;
                    document.getElementById('versionText').value = chap.version_text || 'SLIDE EDITION v2.4.0 (2026/07/25)';
                }

                if (data.video_settings) {
                    const video = data.video_settings;
                    if (video.crf !== undefined) {
                        document.getElementById('videoCrf').value = video.crf;
                        document.getElementById('videoCrfVal').textContent = video.crf;
                    }
                }

                // Update play button href dynamically based on project name
                const playBtn = document.getElementById('btnPlayVideo');
                const playCleanBtn = document.getElementById('btnPlayCleanVideo');
                let baseName = "《企業朝廷生存指南》";
                if (data.project_info && data.project_info.slide_pdf_name) {
                    baseName = data.project_info.slide_pdf_name.replace(/\.[^/.]+$/, "");
                }
                playBtn.href = `/MovieOutput/${encodeURIComponent(baseName)}_內嵌字幕.mp4`;
                playCleanBtn.href = `/MovieOutput/${encodeURIComponent(baseName)}_無字幕.mp4`;

                // Update live style preview on load
                if (typeof updateSubtitlePreview === 'function') {
                    updateSubtitlePreview();
                }
                renderSidebar();
                if (slidesData.length > 0) {
                    selectSlide(0);
                } else {
                    document.getElementById('slidePreview').src = '';
                    document.getElementById('slideNum').value = '';
                    document.getElementById('slideTitle').value = '';
                    document.getElementById('slideNarration').value = '';
                }
            } catch (err) {
                console.error("Failed to load slides:", err);
            }
        }

        // Render sidebar items
        function renderSidebar() {
            const list = document.getElementById('slideList');
            list.innerHTML = '';
            slidesData.forEach((s, idx) => {
                const li = document.createElement('li');
                li.className = `slide-item ${idx === currentSlideIdx ? 'active' : ''}`;
                const fileNum = String(s.num).padStart(2, '0');
                li.innerHTML = `
                    <div class="slide-thumb" style="background-image: url('/assets/images/page-${fileNum}.png?t=${new Date().getTime()}')"></div>
                    <div class="slide-info">
                        <div class="num">PAGE ${fileNum}</div>
                        <div class="title">${s.title}</div>
                    </div>
                `;
                li.onclick = () => selectSlide(idx);
                list.appendChild(li);
            });
        }

        // Select specific slide to edit
        function selectSlide(idx) {
            currentSlideIdx = idx;
            renderSidebar();
            const s = slidesData[idx];
            const fileNum = String(s.num).padStart(2, '0');
            
            document.getElementById('slidePreview').src = `/assets/images/page-${fileNum}.png?t=${new Date().getTime()}`;
            document.getElementById('slideNum').value = s.num;
            document.getElementById('slideTitle').value = s.title;
            document.getElementById('slideNarration').value = s.narration;

            document.getElementById('audioContainer').style.display = 'none';
            document.getElementById('audioPlayer').src = '';
        }

        // Project upload and initialization handler
        document.getElementById('btnUploadProject').onclick = async () => {
            const slideInput = document.getElementById('slidePdfFile');
            const narrationInput = document.getElementById('narrationFile');

            if (!slideInput.files[0] || !narrationInput.files[0]) {
                alert("請同時選擇簡報 PDF 檔案與口白檔案！");
                return;
            }

            const btn = document.getElementById('btnUploadProject');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<span class="loading-spinner"></span> 正在初始化專案...`;

            try {
                const slideFile = slideInput.files[0];
                const narrationFile = narrationInput.files[0];

                const slideB64 = await readFileAsBase64(slideFile);

                let narrationType = "txt";
                let narrationData = "";

                if (narrationFile.name.toLowerCase().endsWith(".pdf")) {
                    narrationType = "pdf";
                    narrationData = await readFileAsBase64(narrationFile);
                } else {
                    narrationType = "txt";
                    narrationData = await readFileAsText(narrationFile);
                }

                const payload = {
                    slide_pdf: slideB64,
                    slide_pdf_name: slideFile.name,
                    narration_type: narrationType,
                    narration_data: narrationData
                };

                const res = await fetch('/api/upload_project', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    showToast(`專案建立成功！共載入 ${data.num_slides} 頁簡報。`);
                    await loadSlides();
                } else {
                    alert("匯入專案失敗: " + data.error);
                }
            } catch (err) {
                alert("上傳與解析過程中發生錯誤: " + err);
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        };

        // Helper to read file as Base64
        function readFileAsBase64(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = (err) => reject(err);
                reader.readAsDataURL(file);
            });
        }

        // Helper to read file as Text
        function readFileAsText(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result);
                reader.onerror = (err) => reject(err);
                reader.readAsText(file, "UTF-8");
            });
        }

        // Subtitle Live Preview Logic
        function updateSubtitlePreview() {
            const fontName = document.getElementById('fontName').value;
            const fontColor = document.getElementById('fontColor').value;
            const bgStyle = document.getElementById('bgStyle').value;
            const fontSize = parseInt(document.getElementById('fontSize').value);
            const bgOpacity = parseFloat(document.getElementById('bgOpacity').value);
            const bottomPos = parseInt(document.getElementById('bottomPos').value);
            
            const previewText = document.getElementById('subtitlePreviewText');
            const previewBox = document.getElementById('subtitlePreviewBox');
            
            // Show/Hide background opacity group depending on style
            const bgOpacityGroup = document.getElementById('bgOpacityGroup');
            if (bgStyle === 'box') {
                bgOpacityGroup.style.display = 'block';
            } else {
                bgOpacityGroup.style.display = 'none';
            }
            
            // 1. Font Family
            previewText.style.fontFamily = `"${fontName}", 'PingFang TC', 'Microsoft JhengHei', sans-serif`;
            
            // 2. Font Color (避開刺眼螢光色，使用舒服的高明度對比色)
            const colorMap = {
                white: '#ffffff',
                yellow: '#ffff00',
                cyan: '#40a9ff',
                green: '#a0d911',
                gray: '#cccccc'
            };
            previewText.style.color = colorMap[fontColor] || '#ffffff';
            
            // 3. Font Size (scale it down slightly to fit the preview box)
            const scaledSize = Math.max(12, Math.min(28, fontSize * 0.5));
            previewText.style.fontSize = scaledSize + 'px';
            
            // 4. Background style & shadows
            if (bgStyle === 'outline') {
                previewText.style.textShadow = '-1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 2px 4px rgba(0,0,0,0.85)';
                previewText.style.background = 'transparent';
                previewText.style.padding = '0';
                previewText.style.borderRadius = '0';
            } else if (bgStyle === 'box') {
                previewText.style.textShadow = 'none';
                previewText.style.background = `rgba(8, 8, 8, ${bgOpacity})`;
                previewText.style.padding = '4px 12px';
                previewText.style.borderRadius = '6px';
            } else { // none
                previewText.style.textShadow = 'none';
                previewText.style.background = 'transparent';
                previewText.style.padding = '0';
                previewText.style.borderRadius = '0';
            }
            
            // 5. Margin V (bottomPos) - scale down by factor of 3 for the preview box
            const scaledMargin = Math.max(5, Math.min(50, bottomPos * 0.35));
            previewBox.style.paddingBottom = scaledMargin + 'px';
        }

        // Update range sliders text
        document.getElementById('fontSize').oninput = function() {
            document.getElementById('fontSizeVal').textContent = this.value + 'px';
            updateSubtitlePreview();
        };
        document.getElementById('bgOpacity').oninput = function() {
            document.getElementById('bgOpacityVal').textContent = this.value;
            updateSubtitlePreview();
        };
        document.getElementById('bottomPos').oninput = function() {
            document.getElementById('bottomPosVal').textContent = this.value + 'px';
            updateSubtitlePreview();
        };
        document.getElementById('maxWidth').oninput = function() {
            document.getElementById('maxWidthVal').textContent = this.value + '%';
        };

        // Wire up dropdowns for live preview
        document.getElementById('fontName').onchange = updateSubtitlePreview;
        document.getElementById('fontColor').onchange = updateSubtitlePreview;
        document.getElementById('bgStyle').onchange = updateSubtitlePreview;

        function setDualColors(colorA, colorB) {
            const elA = document.getElementById('spkA_color');
            const elB = document.getElementById('spkB_color');
            if (elA) elA.value = colorA;
            if (elB) elB.value = colorB;
            showToast("已更新雙人高對比配色！");
        }

        // Show toast feedback
        function showToast(msg = "儲存成功！") {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2000);
        }

        // Save Slide narration
        document.getElementById('btnSaveSlide').onclick = async () => {
            if (slidesData.length === 0) return;
            const s = slidesData[currentSlideIdx];
            const payload = {
                num: s.num,
                title: document.getElementById('slideTitle').value,
                narration: document.getElementById('slideNarration').value
            };

            try {
                const res = await fetch('/api/save_slide', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    s.title = payload.title;
                    s.narration = payload.narration;
                    renderSidebar();
                    showToast("口白存檔成功！");
                }
            } catch (err) {
                alert("儲存失敗: " + err);
            }
        };

        // Test and generate audio for page
        document.getElementById('btnTestTts').onclick = async () => {
            if (slidesData.length === 0) return;
            const btn = document.getElementById('btnTestTts');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<span class="loading-spinner"></span> 正在產生配音...`;

            const s = slidesData[currentSlideIdx];
            const payload = {
                num: s.num,
                narration: document.getElementById('slideNarration').value,
                mode: document.getElementById('voiceMode').value,
                voice: document.getElementById('voiceSelect').value,
                rate: document.getElementById('rateSelect').value,
                pitch: document.getElementById('pitchSelect').value,
                pad_time: parseFloat(document.getElementById('padTime').value),
                speaker_a: {
                    name: document.getElementById('spkA_name').value,
                    color: document.getElementById('spkA_color').value,
                    voice: document.getElementById('spkA_voice').value,
                    rate: document.getElementById('spkA_rate').value,
                    pitch: document.getElementById('spkA_pitch').value
                },
                speaker_b: {
                    name: document.getElementById('spkB_name').value,
                    color: document.getElementById('spkB_color').value,
                    voice: document.getElementById('spkB_voice').value,
                    rate: document.getElementById('spkB_rate').value,
                    pitch: document.getElementById('spkB_pitch').value
                }
            };

            try {
                const res = await fetch('/api/test_tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    const audio = document.getElementById('audioPlayer');
                    audio.src = data.audio_url;
                    document.getElementById('audioContainer').style.display = 'flex';
                    document.getElementById('audioDuration').textContent = `旁白長度: ${data.duration} 秒`;
                    audio.play();
                    showToast("配音檔產生成功！");
                } else {
                    alert("配音失敗: " + data.error);
                }
            } catch (err) {
                alert("配音生成錯誤: " + err);
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        };

        // Save subtitle style configurations
        document.getElementById('btnSaveSubtitle').onclick = async () => {
            const subModeVal = document.getElementById('subMode').value;
            const payload = {
                style_preset: document.getElementById('stylePreset').value,
                font_name: document.getElementById('fontName').value,
                font_color: document.getElementById('fontColor').value,
                bg_style: document.getElementById('bgStyle').value,
                font_size: parseInt(document.getElementById('fontSize').value),
                bg_opacity: parseFloat(document.getElementById('bgOpacity').value),
                bottom_pos: parseInt(document.getElementById('bottomPos').value),
                max_width: parseInt(document.getElementById('maxWidth').value),
                embed: subModeVal === 'embed',
                mode: subModeVal
            };

            try {
                const res = await fetch('/api/save_subtitle_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    showToast("字幕樣式儲存成功！");
                }
            } catch (err) {
                alert("樣式儲存失敗: " + err);
            }
        };

        // Update range slider text & values for chapter settings
        document.getElementById('pageBgColor').oninput = function() {
            document.getElementById('pageBgColorText').textContent = this.value.toUpperCase();
        };
        document.getElementById('pageFontSize').oninput = function() {
            document.getElementById('pageFontSizeVal').textContent = this.value + 'px';
        };

        // Save chapter style configurations
        document.getElementById('btnSaveChapter').onclick = async () => {
            const payload = {
                show_page_number: document.getElementById('showPageNumber').checked,
                bg_color: document.getElementById('pageBgColor').value,
                font_size: parseInt(document.getElementById('pageFontSize').value),
                show_version_badge: document.getElementById('showVersionBadge').checked,
                version_text: document.getElementById('versionText').value
            };

            try {
                const res = await fetch('/api/save_chapter_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    showToast("頁面與版本標籤儲存成功！");
                }
            } catch (err) {
                alert("樣式儲存失敗: " + err);
            }
        };

        // Stream render output logs
        document.getElementById('btnRenderVideo').onclick = () => {
            if (slidesData.length === 0) {
                alert("請先上傳並匯入專案檔案！");
                return;
            }
            const consoleLog = document.getElementById('logConsole');
            const btn = document.getElementById('btnRenderVideo');
            const progressContainer = document.getElementById('progressBarContainer');
            const progressBar = document.getElementById('progressBar');
            const playBtn = document.getElementById('btnPlayVideo');
            const playCleanBtn = document.getElementById('btnPlayCleanVideo');

            consoleLog.textContent = '準備渲染...\\n';
            btn.disabled = true;
            playBtn.style.display = 'none';
            playCleanBtn.style.display = 'none';
            progressContainer.style.display = 'block';
            progressBar.style.width = '10%';

            const es = new EventSource('/api/render_video');
            
            es.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.log) {
                    consoleLog.textContent += data.log + '\\n';
                    consoleLog.scrollTop = consoleLog.scrollHeight;
                    
                    if (data.log.includes('timings.json')) progressBar.style.width = '30%';
                    if (data.log.includes('build_index.py')) progressBar.style.width = '45%';
                    if (data.log.includes('Playwright')) progressBar.style.width = '60%';
                    if (data.log.includes('muxING')) progressBar.style.width = '85%';
                }
                if (data.complete) {
                    progressBar.style.width = '100%';
                    btn.disabled = false;
                    
                    if (data.video_name) {
                        playBtn.href = `/MovieOutput/${encodeURIComponent(data.video_name)}`;
                        playBtn.style.display = 'block';
                        if (data.clean_video_name) {
                            playBtn.innerHTML = '▶ 播放內嵌字幕版';
                        } else {
                            playBtn.innerHTML = '▶ 播放已生成影片';
                        }
                    }
                    
                    if (data.clean_video_name) {
                        playCleanBtn.href = `/MovieOutput/${encodeURIComponent(data.clean_video_name)}`;
                        playCleanBtn.style.display = 'block';
                    } else {
                        playCleanBtn.style.display = 'none';
                    }
                    
                    es.close();
                    showToast("影片生成成功！");
                }
            };

            es.onerror = (err) => {
                console.error("SSE Error:", err);
                consoleLog.textContent += "\\n[錯誤] 連線中斷或渲染程序異常終止。\\n";
                btn.disabled = false;
                es.close();
            };
        };

        function insertTag(tag) {
            const txt = document.getElementById('slideNarration');
            const start = txt.selectionStart || 0;
            const end = txt.selectionEnd || 0;
            const val = txt.value;
            txt.value = val.substring(0, start) + tag + val.substring(end);
            txt.focus();
            txt.selectionStart = txt.selectionEnd = start + tag.length;
        }

        function toggleVoiceModeUI() {
            const mode = document.getElementById('voiceMode').value;
            const singlePanel = document.getElementById('singleVoicePanel');
            const dualPanel = document.getElementById('dualVoicePanel');
            const tagContainer = document.getElementById('dialogueTagContainer');

            if (mode === 'dual') {
                singlePanel.style.display = 'none';
                dualPanel.style.display = 'block';
                if (tagContainer) tagContainer.style.display = 'flex';
            } else {
                singlePanel.style.display = 'block';
                dualPanel.style.display = 'none';
                if (tagContainer) tagContainer.style.display = 'none';
            }
        }

        document.getElementById('voiceMode').onchange = toggleVoiceModeUI;

        // Save voice and dual dialogue settings
        async function saveVoiceSettings() {
            const payload = {
                mode: document.getElementById('voiceMode').value,
                voice: document.getElementById('voiceSelect').value,
                rate: document.getElementById('rateSelect').value,
                pitch: document.getElementById('pitchSelect').value,
                pad_time: parseFloat(document.getElementById('padTime').value),
                speaker_a: {
                    name: document.getElementById('spkA_name').value,
                    color: document.getElementById('spkA_color').value,
                    voice: document.getElementById('spkA_voice').value,
                    rate: document.getElementById('spkA_rate').value,
                    pitch: document.getElementById('spkA_pitch').value
                },
                speaker_b: {
                    name: document.getElementById('spkB_name').value,
                    color: document.getElementById('spkB_color').value,
                    voice: document.getElementById('spkB_voice').value,
                    rate: document.getElementById('spkB_rate').value,
                    pitch: document.getElementById('spkB_pitch').value
                }
            };
            try {
                await fetch('/api/save_voice_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                showToast("語音對話設定儲存成功！");
            } catch (err) {
                console.error("儲存語音對話設定失敗:", err);
            }
        }

        // Update range slider text for padTime
        document.getElementById('padTime').oninput = function() {
            document.getElementById('padTimeVal').textContent = this.value + '秒';
        };

        // Attach auto-save to voice inputs
        document.getElementById('btnSaveVoiceSettings').onclick = saveVoiceSettings;

        document.getElementById('btnLoadDualSample').onclick = async () => {
            document.getElementById('voiceMode').value = 'dual';
            toggleVoiceModeUI();
            
            const sampleScript = [
                "A: 各位好，我是吉姆·羅恩！歡迎來到這檔講述「財富與快樂終極法則」的雙人對談節目。 B: 沒錯！今天我們要深入探討，為什麼「投資自己」是全世界回報率最高、也最穩健的商業模式！",
                "A: 這裡展示了著名的「收入物理學法則」。你花在自己身上的心力，必須要比花在工作上的更多。 B: 沒錯，財富具有強大的引力！如果你的個人價值沒有提升，意外獲得的財富也很難守住。",
                "A: 我們必須進行一場範式轉移，許多人盲目追逐金錢，但真正的關鍵是什麼？ B: 答案是：「成功不是追求來的，而是被你個人魅力與實力吸引來的！」專注於成為更好的自己吧。",
                "A: 這裡提供了一個清晰的「改變人生三步藍圖」：探索法則、付諸行動與堅守原則。 B: 切記不要尋求廉價捷徑，微小且持續的紀律，才能建立起面對大挑戰的堅實肌肉！"
            ];

            if (slidesData.length > 0) {
                for (let i = 0; i < slidesData.length; i++) {
                    const text = sampleScript[i % sampleScript.length];
                    slidesData[i].narration = text;
                    await fetch('/api/save_slide', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            num: slidesData[i].num,
                            title: slidesData[i].title,
                            narration: text
                        })
                    });
                }
                await saveVoiceSettings();
                selectSlide(currentSlideIdx);
                showToast("已成功載入雙人對話範例腳本！");
            }
        };

        // Update range slider text & auto-save for video crf
        document.getElementById('videoCrf').oninput = function() {
            document.getElementById('videoCrfVal').textContent = this.value;
        };
        document.getElementById('videoCrf').onchange = async function() {
            const payload = { crf: parseInt(this.value) };
            try {
                await fetch('/api/save_video_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
            } catch (err) {
                console.error("自動儲存畫質設定失敗:", err);
            }
        };

        window.onload = loadSlides;
    </script>
</body>
</html>
"""

def main():
    calculate_dynamic_timings()
    
    server_address = ('', PORT)
    # Use ThreadingHTTPServer for concurrent connections
    with http.server.ThreadingHTTPServer(server_address, GUIHTTPRequestHandler) as httpd:
        print(f"===========================================================")
        print(f"🎉 簡報影片 AI 製作工坊 (Local Web GUI) 已成功啟動！")
        print(f"請在瀏覽器中打開此網址： http://localhost:{PORT}")
        print(f"===========================================================")
        print("按 Ctrl + C 可以結束伺服器程序。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n正在關閉伺服器...")

if __name__ == "__main__":
    main()
