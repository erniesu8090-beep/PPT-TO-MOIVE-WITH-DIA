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
import shutil
import hashlib
import tempfile
import threading

from media_utils import get_audio_duration
from subtitle_styles import ass_header, style_values
from subtitle_utils import split_timed_text, wrap_ass_lines, ass_text

PORT = 8000
WORKSPACE_DIR = pathlib.Path(__file__).parent.absolute()
ROOT_DIR = WORKSPACE_DIR.parent
MOVIE_OUTPUT_DIR = ROOT_DIR / "MovieOutput"
MOVIE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR = WORKSPACE_DIR / "assets" / "narration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# All HTTP operations that write project files share this lock.
PROJECT_LOCK = threading.Lock()


def clean_narration(text):
    text = text.strip()
    return text[1:-1].strip() if text.startswith("「") and text.endswith("」") else text


def silent_page_duration(settings):
    value = float(settings.get("silent_page_duration", 5))
    if not math.isfinite(value) or not 1 <= value <= 120:
        raise ValueError("空白頁停留時間必須介於 1 到 120 秒。")
    return value


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
        
    # Delete old narration audio files
    narration_dir = WORKSPACE_DIR / "assets" / "narration"
    if narration_dir.exists():
        for old_audio in narration_dir.glob("page-*.*"):
            try:
                old_audio.unlink()
            except Exception:
                pass
    else:
        narration_dir.mkdir(parents=True, exist_ok=True)
        
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
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        turn_text = text[m.end():end].strip()
        if turn_text:
            turns.append({'speaker': spk, 'text': turn_text})
    return turns


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
        
        text_clean = clean_narration(text)
        audio_file = OUT_DIR / f"page-{num:02d}.mp3"
        meta_file = OUT_DIR / f"page-{num:02d}-meta.json"
        turn_durations, exact_clauses = [], []
        generated = audio_matches_cache(num, text_clean, voice_settings)
        if not parse_dialogue_turns(text_clean):
            audio_dur = silent_page_duration(voice_settings)
            audio_status = "silent"
        elif generated:
            audio_dur = get_audio_duration(audio_file)
            audio_status = "generated"
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            turn_durations = meta.get("turn_durations", [])
            exact_clauses = meta.get("exact_clauses", [])
        else:
            # Estimates are only for editing before synthesis, never for rendering.
            audio_dur = max(1.0, len(text_clean) * 0.15)
            audio_status = "estimated"
        for clause in exact_clauses:
            clause["speaker_name"] = speaker_b_name if clause.get("speaker") == "B" else speaker_a_name

        if exact_clauses:
            all_clauses = exact_clauses
        else:
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

                clause_weights = []
                for c in clauses_text:
                    w = float(len(c))
                    if any(p in c for p in ["。", "！", "？", "!", "?"]):
                        w += 3.5
                    elif any(p in c for p in ["，", "；", "：", ",", ";"]):
                        w += 1.8
                    clause_weights.append(max(w, 0.5))

                total_weight = sum(clause_weights) or 1.0

                for c, w in zip(clauses_text, clause_weights):
                    c_dur = turn_dur * (w / total_weight)
                    all_clauses.append({
                        "text": c,
                        "start": round(current_time, 2),
                        "end": round(current_time + c_dur, 2),
                        "speaker": spk,
                        "speaker_name": spk_name
                    })
                    current_time += c_dur

        if audio_status == "silent":
            all_clauses = []
        else:
            normalized = []
            previous_speaker = None
            for clause in all_clauses:
                speaker = clause.get("speaker", "A")
                # Label only the beginning of a speaker's turn.
                show_label = previous_speaker != speaker
                for cue in split_timed_text(clause["text"], clause["start"], clause["end"]):
                    cue.update({"speaker": speaker, "speaker_name": clause.get("speaker_name", ""),
                                "show_speaker_label": show_label})
                    show_label = False
                    normalized.append(cue)
                previous_speaker = speaker
            all_clauses = normalized
        dur = math.ceil(audio_dur if audio_status == "silent" else audio_dur + pad_time)
        
        pages_timings.append({
            "i": num,
            "title": title,
            "sub": text,
            "audio_dur": round(audio_dur, 2),
            "audio_status": audio_status,
            "silent": audio_status == "silent",
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
            "audio_status": p["audio_status"],
            "silent": p["silent"],
            "dur": p["dur"]
        })
    with open(WORKSPACE_DIR / "timings.json", "w", encoding="utf-8") as f_simple:
        json.dump(simple_timings, f_simple, ensure_ascii=False, indent=2)
        
    return pages_timings

# Generate one page at a time while keeping per-page files and subtitle offsets.
TTS_CONCURRENCY = 1
TTS_REQUEST_TIMEOUT = 180
TTS_RETRY_DELAYS = [1.5, 3.0]

# Dual endpoints: Bing ReadAloud (legacy) + Edge 155 Cognitive Services (new)
TTS_LEGACY_WSS_URL = (
    f"wss://{edge_tts.constants.BASE_URL}/edge/v1?TrustedClientToken={edge_tts.constants.TRUSTED_CLIENT_TOKEN}"
)
TTS_NEW_WSS_URL = (
    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
    f"?Ocp-Apim-Subscription-Key={edge_tts.constants.TRUSTED_CLIENT_TOKEN}"
)
TTS_ENDPOINTS = [TTS_LEGACY_WSS_URL, TTS_NEW_WSS_URL]
_current_tts_endpoint_idx = 0


def get_tts_endpoints():
    global _current_tts_endpoint_idx
    primary = TTS_ENDPOINTS[_current_tts_endpoint_idx]
    fallback = [ep for i, ep in enumerate(TTS_ENDPOINTS) if i != _current_tts_endpoint_idx]
    return [primary] + fallback


def mark_tts_endpoint_success(endpoint):
    global _current_tts_endpoint_idx
    if endpoint in TTS_ENDPOINTS:
        _current_tts_endpoint_idx = TTS_ENDPOINTS.index(endpoint)


async def collect_tts_stream(text, voice, kwargs):
    import aiohttp

    class StreamConnector(aiohttp.TCPConnector):
        async def close(self):
            # edge-tts creates an owning ClientSession for each text chunk and
            # 403 retry. Keep this connector alive until the whole stream ends.
            pass

        async def close_stream(self):
            await super().close()

    connector = StreamConnector(resolver=aiohttp.ThreadedResolver())

    async def collect():
        communicate = edge_tts.Communicate(text, voice, **kwargs, connector=connector)
        audio_bytes = bytearray()
        sentence_boundaries = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes.extend(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                sentence_boundaries.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 10000000.0,
                    "end": (chunk["offset"] + chunk["duration"]) / 10000000.0
                })
        return audio_bytes, sentence_boundaries

    try:
        return await asyncio.wait_for(collect(), timeout=TTS_REQUEST_TIMEOUT)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Edge-TTS 請求超過 {TTS_REQUEST_TIMEOUT} 秒") from exc
    except edge_tts.exceptions.NoAudioReceived as exc:
        raise RuntimeError(
            f"Edge-TTS 未回傳音訊（音色 {voice}）。"
            "請稍後重試，或在語音設定中切換其他音色後再生成。"
        ) from exc
    except aiohttp.WSServerHandshakeError as exc:
        if exc.status == 403:
            raise RuntimeError(
                f"Edge-TTS 服務拒絕連線（HTTP 403，目前版本 {edge_tts.__version__}）。"
                "請確認已安裝 edge-tts 7.3.1 以上並重新啟動工坊；"
                "若已更新，請檢查系統時間、代理伺服器或稍後重試。"
            ) from exc
        if exc.status == 429:
            raise RuntimeError(
                "Edge-TTS 請求過於頻繁（HTTP 429 頻率限制），系統將自動切換端點或退避重試。"
            ) from exc
        if exc.status in (500, 502, 503, 504):
            raise RuntimeError(
                f"Edge-TTS 服務伺服器忙碌（HTTP {exc.status}），微軟伺服器負載過高，系統將自動重試。"
            ) from exc
        raise
    finally:
        await connector.close_stream()


async def generate_batch_tts(slides, voice_settings, log, *, force=False):
    semaphore = asyncio.Semaphore(TTS_CONCURRENCY)

    async def generate(slide, text):
        num = slide["num"]
        async with semaphore:
            try:
                cached = not force and await asyncio.to_thread(audio_matches_cache, num, text, voice_settings)
                log(f"  第 {num} 頁：" + ("沿用配音快取" if cached else "產生配音" if tts_plan(text, voice_settings)["mode"] != "silent" else "產生靜音頁"))
                if force:
                    await generate_page_tts(num, text, voice_settings, force=True)
                else:
                    await generate_page_tts(num, text, voice_settings)
            except Exception as exc:
                log(f"  ❌ 第 {num} 頁配音失敗：{exc}")
                return num
            log(f"  ✓ 第 {num} 頁配音完成")
            return None

    jobs = []
    for slide in slides:
        text = clean_narration(slide.get("narration", ""))
        jobs.append(generate(slide, text))
    results = await asyncio.gather(*jobs)
    failed = [str(num) for num in results if num is not None]
    if failed:
        raise RuntimeError("配音失敗頁碼：" + "、".join(failed) + "；其餘頁面已完成，請重試失敗頁面。")


# Compatibility wrapper for single speaker
async def generate_tts(num, text, voice="zh-TW-YunJheNeural", rate="-5%", pitch="-2Hz"):
    vs = {"mode": "single", "voice": voice, "rate": rate, "pitch": pitch}
    return await generate_page_tts(num, text, vs)

def tts_plan(text, settings):
    text = clean_narration(text)
    turns = parse_dialogue_turns(text)
    if not turns:
        return {"mode": "silent", "duration": silent_page_duration(settings), "turns": []}
    dual = settings.get("mode") == "dual" or any(t["speaker"] == "B" for t in turns)

    def config(source, default_voice):
        def normalized(key, default):
            value = str(source.get(key, default)).strip()
            if value in ("", "0", "0%", "+0%", "-0%", "0Hz", "+0Hz", "-0Hz"):
                return None
            return value if value.startswith(("+", "-")) else "+" + value
        return {"voice": source.get("voice", default_voice),
                "rate": normalized("rate", "-5%"), "pitch": normalized("pitch", "-2Hz")}

    if dual:
        speaker_a = settings.get("speaker_a", settings)
        speaker_b = settings.get("speaker_b", {"rate": settings.get("rate", "-5%"), "pitch": settings.get("pitch", "-2Hz")})
        planned = [{**turn, **config(speaker_b if turn["speaker"] == "B" else speaker_a,
                    "zh-TW-HsiaoChenNeural" if turn["speaker"] == "B" else "zh-TW-YunJheNeural")} for turn in turns]
    else:
        planned = [{"speaker": "A", "text": " ".join(t["text"] for t in turns), **config(settings, "zh-TW-YunJheNeural")}]
    return {"mode": "dual" if dual else "single", "turns": planned}


def tts_cache_key(text, settings):
    payload = {"version": 1, "edge_tts": edge_tts.__version__, "plan": tts_plan(text, settings)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def audio_matches_cache(num, text, settings):
    audio = OUT_DIR / f"page-{num:02d}.mp3"
    meta_path = OUT_DIR / f"page-{num:02d}-meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        return (meta.get('cache_key') == tts_cache_key(text, settings)
                and audio.is_file() and audio.stat().st_size > 0
                and meta.get('audio_sha256') == hashlib.sha256(audio.read_bytes()).hexdigest())
    except (OSError, ValueError, TypeError):
        return False


async def generate_page_tts(num, text, vs, *, force=False):
    text = clean_narration(text)
    plan = tts_plan(text, vs)
    out_file = OUT_DIR / f"page-{num:02d}.mp3"
    meta_file = OUT_DIR / f"page-{num:02d}-meta.json"
    if not force and await asyncio.to_thread(audio_matches_cache, num, text, vs):
        await asyncio.to_thread(get_audio_duration, out_file)
        return out_file
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Work in isolation. Failed requests never overwrite a previous good page.
    with tempfile.TemporaryDirectory(prefix=f"page-{num:02d}-", dir=OUT_DIR) as tmp:
        staging = pathlib.Path(tmp)
        staged_audio = staging / out_file.name
        meta = {"mode": plan["mode"], "turn_durations": [], "exact_clauses": []}
        if plan["mode"] == "silent":
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                   "-t", str(plan["duration"]), "-c:a", "libmp3lame", str(staged_audio)]
            await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True, timeout=60)
        else:
            turn_files, offset = [], 0.0
            for idx, turn in enumerate(plan["turns"]):
                kwargs = {k: turn[k] for k in ("rate", "pitch") if turn[k] is not None}
                if proxy:
                    kwargs["proxy"] = proxy
                audio, boundaries = None, None
                last_exc = None
                max_attempts = len(TTS_RETRY_DELAYS) + 1
                for attempt in range(max_attempts):
                    endpoints = get_tts_endpoints()
                    for ep in endpoints:
                        orig_url = getattr(edge_tts.communicate, "WSS_URL", None)
                        try:
                            edge_tts.communicate.WSS_URL = ep
                            audio, boundaries = await collect_tts_stream(turn["text"], turn["voice"], kwargs)
                            if audio:
                                mark_tts_endpoint_success(ep)
                                last_exc = None
                                break
                        except Exception as exc:
                            last_exc = exc
                            continue
                        finally:
                            if orig_url:
                                edge_tts.communicate.WSS_URL = orig_url

                    if audio:
                        break

                    if attempt < max_attempts - 1:
                        await asyncio.sleep(TTS_RETRY_DELAYS[attempt])

                if not audio:
                    if last_exc:
                        raise last_exc
                    raise RuntimeError("Edge-TTS 未回傳音訊。")
                turn_file = staging / f"turn-{idx:02d}.mp3"
                turn_file.write_bytes(audio)
                duration = await asyncio.to_thread(get_audio_duration, turn_file)
                turn_files.append(turn_file)
                meta["turn_durations"].append(duration)
                # No boundary metadata: retain per-turn timing as an explicit estimate.
                for boundary in boundaries or [{"text": turn["text"], "start": 0, "end": duration}]:
                    for cue in split_timed_text(boundary["text"], offset + boundary["start"],
                                                offset + min(boundary["end"], duration)):
                        cue.update({"speaker": turn["speaker"]})
                        meta["exact_clauses"].append(cue)
                offset += duration
            if len(turn_files) == 1:
                os.replace(turn_files[0], staged_audio)
            else:
                concat_file = staging / "concat.txt"
                concat_file.write_text(''.join(f"file '{f.name}'\n" for f in turn_files), encoding='utf-8')
                await asyncio.to_thread(subprocess.run,
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                     "-c:a", "libmp3lame", str(staged_audio)], check=True, capture_output=True, timeout=120)
        duration = await asyncio.to_thread(get_audio_duration, staged_audio)
        meta.update({"cache_key": tts_cache_key(text, vs), "audio_duration": duration,
                     "audio_sha256": hashlib.sha256(staged_audio.read_bytes()).hexdigest()})
        staged_meta = staging / meta_file.name
        staged_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staged_audio, out_file)
        os.replace(staged_meta, meta_file)
    return out_file


def subtitle_preview(payload, *, allow_cache=True):
    text = clean_narration(payload.get("narration", ""))
    settings = payload.get("voice_settings", {})
    plan = tts_plan(text, settings)
    cues, cursor = [], 0.0
    num = int(payload.get("num", 0))
    cached = allow_cache and audio_matches_cache(num, text, settings)
    if cached:
        raw = json.loads((OUT_DIR / f"page-{num:02d}-meta.json").read_text(encoding='utf-8')).get("exact_clauses", [])
    else:
        raw = []
        for turn in plan["turns"]:
            duration = max(1.0, len(turn["text"]) * .15)
            raw.append({"text": turn["text"], "speaker": turn["speaker"], "start": cursor, "end": cursor + duration})
            cursor += duration
    previous_speaker = None
    for clause in raw:
        speaker = clause.get("speaker", "A")
        name = settings.get("speaker_b" if speaker == "B" else "speaker_a", {}).get("name", "對談者 B" if speaker == "B" else "主持人 A")
        prefix = f"[{name}]" if plan["mode"] == "dual" and previous_speaker != speaker else ""
        for cue in split_timed_text(clause["text"], clause["start"], clause["end"], prefix=prefix):
            cue["text"] = wrap_ass_lines(ass_text(cue["text"])).replace(r"\N", "\n")
            cue["speaker"] = speaker
            cues.append(cue)
        previous_speaker = speaker
    return {"cues": cues, "estimated": not cached, "silent": plan["mode"] == "silent", "dual": plan["mode"] == "dual",
            "style": style_values(payload.get("subtitle_settings", {}))}


def actual_subtitle_frame(payload):
    preview = subtitle_preview(payload)
    image = WORKSPACE_DIR / "assets" / "images" / f"page-{int(payload.get('num', 0)):02d}.png"
    if not image.is_file():
        raise RuntimeError("找不到目前投影片圖片，請先匯入專案。")
    cues = preview["cues"]
    index = max(0, min(int(payload.get("cue_index", 0)), len(cues) - 1))
    events = ""
    if cues:
        cue = cues[index]
        style = ("SpeakerB" if cue["speaker"] == "B" else "SpeakerA") if preview["dual"] else "Default"
        text = cue["text"].replace("\n", r"\N")
        events = f"Dialogue: 0,0:00:00.00,0:00:02.00,{style},,0,0,0,,{text}\n"
    with tempfile.TemporaryDirectory(prefix="subtitle-preview-") as tmp:
        temp = pathlib.Path(tmp)
        (temp / "preview.ass").write_text(ass_header(payload.get("subtitle_settings", {}), payload.get("voice_settings", {})) + events, encoding='utf-8')
        frame = temp / "frame.png"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", str(image), "-vf",
                "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,ass=preview.ass",
                "-frames:v", "1", str(frame)], cwd=temp, capture_output=True, check=True, timeout=30)
        except FileNotFoundError as exc:
            raise RuntimeError("找不到 FFmpeg，請安裝並加入 PATH。") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b'').decode('utf-8', errors='replace')[-800:]
            raise RuntimeError("字幕實際預覽失敗：" + detail) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("字幕實際預覽逾時，請稍後重試。") from exc
        return {"image": "data:image/png;base64," + base64.b64encode(frame.read_bytes()).decode('ascii')}



class GUIHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORKSPACE_DIR), **kwargs)

    def end_headers(self):
        # Disable cache for development
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def send_json(self, payload, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        self.close_connection = True

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/render_video", "/api/slides"):
            return self._do_GET()
        if not PROJECT_LOCK.acquire(blocking=False):
            message = "目前正在處理專案，請等作業完成後再操作。"
            if path == "/api/render_video":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps({'error': message}, ensure_ascii=False)}\n\n".encode('utf-8'))
                self.close_connection = True
            else:
                self.send_json({"success": False, "error": message}, 409)
            return
        try:
            self._do_GET()
        except (RuntimeError, ValueError) as exc:
            self.send_json({"success": False, "error": str(exc)}, 400)
        finally:
            PROJECT_LOCK.release()

    def _do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        # Compatibility redirect/read for video/subtitle file URLs inside MovieOutput
        if path.startswith("/MovieOutput/") or path.endswith(".mp4") or path.endswith(".srt"):
            if path.startswith("/MovieOutput/"):
                file_name = urllib.parse.unquote(path[len("/MovieOutput/"):])
            else:
                file_name = urllib.parse.unquote(path.lstrip("/"))
            alt_path = MOVIE_OUTPUT_DIR / file_name
            if alt_path.exists() and alt_path.is_file():
                self.send_response(200)
                if alt_path.suffix.lower() == ".mp4":
                    self.send_header("Content-Type", "video/mp4")
                elif alt_path.suffix.lower() == ".srt":
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                else:
                    self.send_header("Content-Type", "application/octet-stream")
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
                "version_text": "SLIDE EDITION v3.0.0 (2026/10/05)"
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
            self.send_header("Connection", "close")
            self.end_headers()
            
            connected = True
            def emit_sse(payload):
                nonlocal connected
                if connected:
                    try:
                        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode('utf-8'))
                        self.wfile.flush()
                    except OSError:
                        connected = False

            def log_sse(msg):
                emit_sse({"log": msg})

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
                    
                log_sse(f"  批次生成配音，最多同時處理 {TTS_CONCURRENCY} 頁")
                force = urllib.parse.parse_qs(parsed_url.query).get("force", ["0"])[0] == "1"
                if not slides:
                    raise RuntimeError("沒有投影片，請先匯入專案。")
                if force:
                    asyncio.run(generate_batch_tts(slides, voice_settings, log_sse, force=True))
                else:
                    asyncio.run(generate_batch_tts(slides, voice_settings, log_sse))
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
                    raise RuntimeError("編譯 index.html 失敗，已停止影片製作。")
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
                    raise RuntimeError("影片渲染壓制失敗，請查看上方日誌。")
                else:
                    if not final_video_name:
                        raise RuntimeError("渲染程式未回報影片檔名，無法確認輸出完成。")
                    for output_name in (final_video_name, clean_video_name, srt_filename):
                        if output_name and not (MOVIE_OUTPUT_DIR / output_name).is_file():
                            raise RuntimeError(f"找不到預期的輸出檔案：{output_name}")

                    log_sse("\n🎉 恭喜！影片全部製作成功！")
                    if final_video_name:
                        log_sse(f"  已輸出影片：{MOVIE_OUTPUT_DIR / final_video_name}")
                    if clean_video_name:
                        log_sse(f"  已輸出無字幕版：{MOVIE_OUTPUT_DIR / clean_video_name}")
                    if srt_filename:
                        log_sse(f"  已輸出字幕檔案：{MOVIE_OUTPUT_DIR / srt_filename}")
                    
                    emit_sse({'complete': True, 'video_name': final_video_name or '', 'clean_video_name': clean_video_name or '', 'srt_name': srt_filename or ''})
            except Exception as e:
                log_sse(f"錯誤: {str(e)}")
                emit_sse({'error': str(e)})
            finally:
                self.close_connection = True
            return
            
        return super().do_GET()

    def do_POST(self):
        preview_only = urllib.parse.urlparse(self.path).path == "/api/subtitle_preview"
        locked = not preview_only
        if locked and not PROJECT_LOCK.acquire(blocking=False):
            self.send_json({"success": False, "error": "目前正在處理專案，請等作業完成後再操作。"}, 409)
            return
        try:
            self._do_POST()
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            self.send_json({"success": False, "error": str(exc)}, 400)
        finally:
            if locked:
                PROJECT_LOCK.release()

    def _do_POST(self):
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
        
        if path == "/api/subtitle_frame":
            self.send_json(actual_subtitle_frame(payload))
            return

        if path == "/api/subtitle_preview":
            preview_locked = PROJECT_LOCK.acquire(blocking=False)
            try:
                # Rendering may publish audio files; avoid reading them mid-replace.
                self.send_json(subtitle_preview(payload, allow_cache=preview_locked))
            finally:
                if preview_locked:
                    PROJECT_LOCK.release()
            return

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
            if "silent_page_duration" in payload: voice_settings["silent_page_duration"] = silent_page_duration(payload)
            if "speaker_a" in payload: voice_settings["speaker_a"] = payload["speaker_a"]
            if "speaker_b" in payload: voice_settings["speaker_b"] = payload["speaker_b"]

            text_clean = text
            if text_clean.startswith("「") and text_clean.endswith("」"):
                text_clean = text_clean[1:-1]
                
            try:
                audio_file = asyncio.run(generate_page_tts(num, text_clean, voice_settings, force=bool(payload.get("force", False))))
                
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
            silent_duration = silent_page_duration(payload)
            
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
                "silent_page_duration": silent_duration,
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
    <title>影片製作工坊 · Video Workshop</title>
    <link rel="stylesheet" href="/ui/studio.css">
</head>
<body>
    <header>
        <div class="brand">
            <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="7" width="18" height="14" rx="3"/><path d="M3 12h18M7 7l3 5m4-5l3 5M3 7l17-4"/></svg></span>
            <div><h1>影片製作工坊</h1><p class="brand-subtitle">VIDEO WORKSHOP / CREATIVE STUDIO</p></div>
        </div>
        <div class="header-actions">
            <div class="version-badge"><span class="version-dot"></span><span>本機創作工作區</span></div>
            <button class="secondary" onclick="document.getElementById('compileCard').scrollIntoView({behavior: 'smooth', block: 'start'}); document.getElementById('btnRenderVideo').focus({preventScroll: true})">輸出影片 ↗</button>
        </div>
    </header>

    <div class="container">
        <!-- Left: Slide List & Upload Form -->
        <aside class="sidebar" aria-label="專案與投影片">
            <p class="panel-eyebrow">YOUR PROJECT</p>
            <!-- Upload Panel -->
            <div class="card import-card">
                <div class="import-title">開始一個新故事</div>
                <p class="import-help">匯入簡報 PDF 與口白，<br>把想法變成有聲有色的影片。</p>
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
                        ＋ 匯入專案
                    </button>
                </div>
            </div>

            <div class="sidebar-header"><span>投影片清單</span><span class="count-badge" id="slideCount">0 頁</span></div>
            <ul class="slide-list" id="slideList">
                <!-- Slide items will be loaded dynamically -->
            </ul>
        </aside>

        <!-- Center: Slide Editor -->
        <main class="main-editor" aria-label="影片編輯工作區">
            <section class="welcome-banner" aria-labelledby="welcomeTitle">
                <div class="welcome-copy">
                    <p class="panel-eyebrow">A LITTLE IDEA. A GREAT STORY.</p>
                    <h2 id="welcomeTitle">讓每一頁，都有故事。</h2>
                    <p>從簡報、口白到影片，在這裡完成你的創作。</p>
                    <div class="project-pill"><span aria-hidden="true">◈</span><span id="projectName">尚未匯入專案</span></div>
                </div>
            </section>
            <div class="workspace-heading">
                <div><h2>創作工作區</h2><p>選擇投影片，編輯口白並試聽配音。</p></div>
                <span class="stage-label">16:9 畫面預覽</span>
            </div>
            <div class="card preview-card">
                <div class="preview-toolbar"><span id="previewPageLabel">投影片預覽</span><label class="preview-status"><input type="checkbox" id="showSubtitleOverlay" checked> 顯示字幕預覽</label></div>
                <div class="preview-container">
                    <img id="slidePreview" class="preview-img" alt="目前投影片預覽" hidden>
                    <div class="subtitle-canvas" id="mainSubtitleCanvas"><div class="subtitle-rendered-text"></div></div>
                    <div class="preview-empty" id="previewEmpty">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 22h8m-4-4v4m-2-14l5 3-5 3z"/></svg>
                        <strong>你的下一部作品，從這裡開始</strong>
                        <p>匯入簡報與口白，即可開始編輯。</p>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="section-title"><span class="section-number">01</span> 本頁口白與配音</div>
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
                            <label for="slideNarration">旁白內容</label>
                            <div id="dialogueTagContainer" style="display: none; gap: 6px; align-items: center;">
                                <span style="font-size: 11px; color: var(--text-muted);">快速插入對話標點:</span>
                                <button type="button" class="secondary" onclick="insertTag('A: ')" style="padding: 2px 8px; font-size: 11px; background: rgba(6,182,212,0.15); color: #06b6d4; border-color: rgba(6,182,212,0.3);">+ A (主持人)</button>
                                <button type="button" class="secondary" onclick="insertTag('B: ')" style="padding: 2px 8px; font-size: 11px; background: rgba(139,92,246,0.15); color: #8b5cf6; border-color: rgba(139,92,246,0.3);">+ B (對談者)</button>
                            </div>
                        </div>
                        <textarea id="slideNarration" placeholder="寫下這一頁想說的話… 雙人對談可使用 A: 與 B: 標記。"></textarea>
                        <div class="editor-footnote"><span>依標點自動切分字幕時間碼</span><span id="narrationCount">0 字</span></div>
                    </div>

                    <div class="button-group">
                        <button class="secondary" id="btnTestTts" disabled>▷ 生成配音並試聽</button>
                        <button class="primary" id="btnSaveSlide" disabled>儲存口白</button>
                    </div>

                    <div class="audio-player-container" id="audioContainer" style="display: none;">
                        <audio id="audioPlayer" controls></audio>
                        <div id="audioDuration" style="font-size: 12px; color: var(--accent-color); font-weight: bold;"></div>
                    </div>
                </div>
            </div>
        </main>

        <!-- Right: Settings & Compiler -->
        <aside class="right-panel" aria-label="製作與輸出設定">
            <div class="settings-heading"><h2>製作設定</h2><span>細節，讓作品更出色</span></div>
            <!-- Voice & Dual Dialogue Settings -->
            <div class="card collapsible-card active" id="voiceCard">
                <button type="button" class="collapsible-header" aria-expanded="false" aria-controls="voiceCardContent" onclick="toggleCollapsible('voiceCard')">
                    <span class="title-text"><span class="section-number">02</span> 聲音與對話</span>
                    <span class="arrow-icon" aria-hidden="true">⌄</span>
                </button>
                <div class="collapsible-content" id="voiceCardContent">
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
                    <div id="dualVoicePanel" style="display: none; background: #27352f; padding: 14px; border-radius: 8px; border: 1px solid var(--border-color); margin-bottom: 14px;">
                        
                        <!-- 雙人高對比推薦配色預設面板 -->
                        <div style="background: #28332f; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 11px;">
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
                            <div style="font-size: 12px; font-weight: bold; color: #9dc5af; margin-bottom: 8px;">🔷 角色 A (主講人 / Host A)</div>
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
                            <div style="font-size: 12px; font-weight: bold; color: #d5b77e; margin-bottom: 8px;">💛 角色 B (對談者 / Guest B)</div>
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

                    <div class="form-group" style="margin-bottom: 14px;">
                        <label for="silentPageDuration">無口白頁停留秒數（不含配音）</label>
                        <input type="number" id="silentPageDuration" min="1" max="120" step="1" value="5">
                    </div>
                    <button class="secondary" id="btnSaveVoiceSettings" style="width: 100%;">💾 儲存語音對話設定</button>
                    <button class="secondary" id="btnLoadDualSample" disabled style="width: 100%; margin-top: 8px; background: #38352f; border-color: #665742; color: #d8ba98;">填入本頁對話範例</button>
                </div>
            </div>

            <div class="card collapsible-card" id="subtitleCard">
                <button type="button" class="collapsible-header" aria-expanded="false" aria-controls="subtitleCardContent" onclick="toggleCollapsible('subtitleCard')">
                    <span class="title-text"><span class="section-number">03</span> 字幕樣式</span>
                    <span class="arrow-icon" aria-hidden="true">⌄</span>
                </button>
                <div class="collapsible-content" id="subtitleCardContent">
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
                            <label>字幕預覽 · 目前投影片</label>
                            <div class="subtitle-mini-frame" id="subtitlePreviewBox">
                                <img class="subtitle-slide-image" alt="目前投影片" hidden>
                                <div class="subtitle-canvas"><div class="subtitle-rendered-text"></div></div>
                            </div>
                            <div class="subtitle-cue-controls">
                                <button type="button" class="secondary" data-preview-step="-1" aria-label="上一句字幕">‹</button>
                                <span id="subtitleCuePosition">尚無字幕</span>
                                <button type="button" class="secondary" data-preview-step="1" aria-label="下一句字幕">›</button>
                            </div>
                            <p id="subtitlePreviewText" class="subtitle-cue-copy">編輯本頁口白後即可預覽。</p>
                            <p class="preview-note" id="subtitleTimingNote"></p>
                            <button type="button" class="secondary" id="btnEnlargeSubtitle">放大預覽 ↗</button>
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
                            <label for="maxWidth">字幕安全區寬度</label>
                            <span id="maxWidthVal">56%</span>
                        </div>
                        <input type="range" id="maxWidth" min="30" max="56" value="56">
                    </div>

                    <button class="secondary" id="btnSaveSubtitle" style="width: 100%; margin-top: 10px;">💾 套用並儲存字幕樣式</button>
                </div>
            </div>

            <div class="card collapsible-card" id="chapterCard">
                <button type="button" class="collapsible-header" aria-expanded="false" aria-controls="chapterCardContent" onclick="toggleCollapsible('chapterCard')">
                    <span class="title-text"><span class="section-number">04</span> 畫面與標籤</span>
                    <span class="arrow-icon" aria-hidden="true">⌄</span>
                </button>
                <div class="collapsible-content" id="chapterCardContent">
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
                            <input type="text" id="versionText" value="SLIDE EDITION v3.0.0 (2026/10/05)">
                        </div>
                    </div>

                    <button class="secondary" id="btnSaveChapter" style="width: 100%; margin-top: 10px;">💾 套用並儲存頁面與版本樣式</button>
                </div>
            </div>

            <div class="card compile-card" id="compileCard">
                <div class="section-title"><span class="section-number">05</span> 輸出你的作品</div>
                
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

                <label class="force-tts"><input type="checkbox" id="forceTts"> 重新生成全部配音（忽略快取）</label>
                <button class="primary" id="btnRenderVideo" disabled style="width: 100%; padding: 14px; font-size: 15px; letter-spacing: 0.5px;">開始製作影片 ↗</button>
                
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
        </aside>
    </div>

    <dialog id="subtitlePreviewDialog" class="subtitle-dialog" aria-labelledby="subtitleDialogTitle">
        <div class="subtitle-dialog-header"><div><h2 id="subtitleDialogTitle">字幕輸出預覽</h2><p>目前投影片與本頁口白 · 1920 × 1080 等比例</p></div><button type="button" class="secondary" id="btnCloseSubtitlePreview" aria-label="關閉字幕預覽">✕</button></div>
        <div class="subtitle-dialog-frame">
            <img class="subtitle-slide-image" alt="目前投影片" hidden>
            <div class="subtitle-canvas"><div class="subtitle-rendered-text"></div></div>
            <img id="actualSubtitleFrame" alt="由 FFmpeg 渲染的實際字幕畫格" hidden>
        </div>
        <div class="subtitle-dialog-footer">
            <div class="subtitle-cue-controls"><button type="button" class="secondary" data-preview-step="-1" aria-label="上一句字幕">‹</button><span id="subtitleDialogPosition">尚無字幕</span><button type="button" class="secondary" data-preview-step="1" aria-label="下一句字幕">›</button></div>
            <button type="button" class="primary" id="btnActualSubtitleFrame">檢視 FFmpeg 實際輸出</button>
        </div>
        <p class="preview-note" id="subtitleActualStatus">等比例排版預覽；實際輸出可確認字型與描邊。此預覽不會儲存設定。</p>
    </dialog>
    <div class="toast" role="status" id="toast">儲存成功！</div>

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
                card.querySelector('.collapsible-header').setAttribute('aria-expanded', card.classList.contains('active'));
            }
        }

        let slidesData = [];
        let currentSlideIdx = 0;

        // Load initialization data
        async function loadSlides() {
            try {
                const res = await fetch('/api/slides');
                const data = await res.json();
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
                slidesData = data.slides;
                ['btnSaveSlide', 'btnTestTts', 'btnLoadDualSample', 'btnRenderVideo'].forEach(id => {
                    document.getElementById(id).disabled = slidesData.length === 0;
                });
                document.getElementById('projectName').textContent = data.project_info?.slide_pdf_name || '尚未匯入專案';
                
                if (data.voice_settings) {
                    const vs = data.voice_settings;
                    document.getElementById('silentPageDuration').value = vs.silent_page_duration || 5;
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
                    document.getElementById('maxWidth').value = Math.min(56, Math.max(30, sub.max_width || 56));
                    document.getElementById('maxWidthVal').textContent = document.getElementById('maxWidth').value + '%';
                    
                    // Populate new inputs
                    document.getElementById('fontName').value = sub.font_name || 'Microsoft JhengHei';
                    document.getElementById('fontColor').value = sub.font_color || 'white';
                    document.getElementById('bgStyle').value = sub.bg_style === 'box' ? 'box' : 'outline';
                }

                if (data.chapter_settings) {
                    const chap = data.chapter_settings;
                    document.getElementById('showPageNumber').checked = chap.show_page_number !== false;
                    document.getElementById('pageBgColor').value = chap.bg_color || '#0E7C7B';
                    document.getElementById('pageBgColorText').textContent = chap.bg_color || '#0E7C7B';
                    document.getElementById('pageFontSize').value = chap.font_size || 24;
                    document.getElementById('pageFontSizeVal').textContent = (chap.font_size || 24) + 'px';
                    
                    document.getElementById('showVersionBadge').checked = chap.show_version_badge !== false;
                    document.getElementById('versionText').value = chap.version_text || 'SLIDE EDITION v3.0.0 (2026/10/05)';
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
                    baseName = data.project_info.slide_pdf_name.replace(/\\.[^/.]+$/, "");
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
                    document.getElementById('slidePreview').removeAttribute('src');
                    document.getElementById('slidePreview').hidden = true;
                    document.getElementById('previewEmpty').hidden = false;
                    document.getElementById('slideNum').value = '';
                    document.getElementById('slideTitle').value = '';
                    document.getElementById('slideNarration').value = '';
                }
            } catch (err) {
                console.error("Failed to load slides:", err);
                document.getElementById('projectName').textContent = '暫時無法載入，請稍後重新整理';
                showToast(err.message || '專案載入失敗');
            }
        }

        // Render sidebar items
        function renderSidebar() {
            const list = document.getElementById('slideList');
            list.innerHTML = '';
            document.getElementById('slideCount').textContent = `${slidesData.length} 頁`;
            if (!slidesData.length) {
                const empty = document.createElement('li');
                empty.className = 'sidebar-empty';
                empty.textContent = '還沒有投影片。匯入專案後，你的故事會出現在這裡。';
                list.appendChild(empty);
            }
            slidesData.forEach((s, idx) => {
                const li = document.createElement('li');
                li.className = `slide-item ${idx === currentSlideIdx ? 'active' : ''}`;
                const fileNum = String(s.num).padStart(2, '0');
                li.innerHTML = `
                    <div class="slide-thumb" style="background-image: url('/assets/images/page-${fileNum}.png?t=${new Date().getTime()}')"></div>
                    <div class="slide-info">
                        <div class="num">PAGE ${fileNum}</div>
                        <div class="title"></div>
                    </div>
                `;
                li.querySelector('.title').textContent = s.title;
                li.tabIndex = 0;
                li.setAttribute('role', 'button');
                li.setAttribute('aria-label', `第 ${s.num} 頁：${s.title}`);
                li.setAttribute('aria-current', idx === currentSlideIdx ? 'true' : 'false');
                li.onclick = () => selectSlide(idx);
                li.onkeydown = (event) => {
                    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSlide(idx); }
                };
                list.appendChild(li);
            });
        }

        // Select specific slide to edit
        function selectSlide(idx) {
            currentSlideIdx = idx;
            renderSidebar();
            const s = slidesData[idx];
            const fileNum = String(s.num).padStart(2, '0');
            
            document.getElementById('slidePreview').hidden = false;
            document.getElementById('previewEmpty').hidden = true;
            document.getElementById('previewPageLabel').textContent = `投影片 ${fileNum} / ${slidesData.length} 頁`;
            document.getElementById('slidePreview').src = `/assets/images/page-${fileNum}.png?t=${new Date().getTime()}`;
            document.getElementById('slideNum').value = s.num;
            document.getElementById('slideTitle').value = s.title;
            document.getElementById('slideNarration').value = s.narration;
            updateNarrationCount();

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
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
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
            document.getElementById('bgOpacityGroup').style.display = document.getElementById('bgStyle').value === 'box' ? 'block' : 'none';
            window.refreshSubtitlePreview?.();
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
            updateSubtitlePreview();
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
            updateSubtitlePreview();
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
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
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
                silent_page_duration: parseFloat(document.getElementById('silentPageDuration').value),
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
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
                if (data.success) {
                    const audio = document.getElementById('audioPlayer');
                    audio.src = data.audio_url;
                    document.getElementById('audioContainer').style.display = 'flex';
                    document.getElementById('audioDuration').textContent = `旁白長度: ${data.duration} 秒`;
                    audio.play();
                    showToast("配音檔產生成功！");
                    window.refreshSubtitlePreview?.();
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
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
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
                if (!res.ok || data.success === false) throw new Error(data.error || '操作失敗');
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

            const es = new EventSource('/api/render_video' + (document.getElementById('forceTts').checked ? '?force=1' : ''));
            
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
                if (data.error) {
                    consoleLog.textContent += `[失敗] ${data.error}\\n`;
                    consoleLog.scrollTop = consoleLog.scrollHeight;
                    btn.disabled = false;
                    progressContainer.style.display = 'none';
                    es.close();
                    showToast("製作失敗，請查看日誌。");
                    return;
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
            updateNarrationCount();
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
                silent_page_duration: parseFloat(document.getElementById('silentPageDuration').value),
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
                const res = await fetch('/api/save_voice_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || data.success === false) throw new Error(data.error || '設定儲存失敗');
                showToast("語音對話設定儲存成功！");
            } catch (err) {
                console.error("儲存語音對話設定失敗:", err);
                showToast("儲存失敗：" + err.message);
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
                document.getElementById('slideNarration').value = sampleScript[currentSlideIdx % sampleScript.length];
                updateNarrationCount();
                showToast("已填入本頁範例，確認後請儲存口白與語音設定。");
            }

        };

        // Update range slider text & auto-save for video crf
        document.getElementById('videoCrf').oninput = function() {
            document.getElementById('videoCrfVal').textContent = this.value;
        };
        document.getElementById('videoCrf').onchange = async function() {
            const payload = { crf: parseInt(this.value) };
            try {
                const res = await fetch('/api/save_video_settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || data.success === false) throw new Error(data.error || '畫質設定儲存失敗');
            } catch (err) {
                console.error("自動儲存畫質設定失敗:", err);
            }
        };

        function updateNarrationCount() {
            document.getElementById('narrationCount').textContent = `${Array.from(document.getElementById('slideNarration').value).length} 字`;
            window.refreshSubtitlePreview?.();
        }
        document.getElementById('slideNarration').addEventListener('input', updateNarrationCount);
        document.querySelectorAll('.collapsible-card').forEach(card => {
            card.querySelector('.collapsible-header').setAttribute('aria-expanded', card.classList.contains('active'));
        });
        window.onload = loadSlides;
    </script>
    <script src="/ui/subtitles.js"></script>
</body>
</html>
"""

def main():
    # The editor can open before audio exists; render computes validated timings.
    
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
