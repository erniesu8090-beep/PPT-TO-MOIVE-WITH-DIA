import os
import sys
import json
import re
import shutil
import asyncio
import argparse
import subprocess
from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

MODULE_DIR = Path(__file__).parent.resolve()
ROOT_DIR = MODULE_DIR.parent
TEMPLATES_DIR = MODULE_DIR / "templates"
DEFAULT_ASSETS_DIR = MODULE_DIR / "default_assets"
PROJECTS_DIR = MODULE_DIR / "projects"
MOVIE_OUTPUT_DIR = ROOT_DIR / "MovieOutput"
MOVIE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def upload_audio_safely(client, audio_path: Path):
    """上傳音訊檔案至 Gemini Files API，自動使用 ASCII 名稱避免 httpx 標頭編碼錯誤"""
    temp_ascii_path = audio_path.parent / f"_temp_upload_{abs(hash(str(audio_path))) % 1000000}.m4a"
    shutil.copy(audio_path, temp_ascii_path)
    try:
        print(f"  [上傳] 正在上傳音訊至 Gemini Files API...")
        file_obj = client.files.upload(file=str(temp_ascii_path))
        print(f"  [上傳] 成功！伺服器檔案 ID: {file_obj.name}")
        return file_obj
    finally:
        if temp_ascii_path.exists():
            temp_ascii_path.unlink()

def analyze_chapters(audio_path: Path, proj_dir: Path):
    """步驟 1：呼叫 Gemini 多模態語音解析全片章節與核心概念插圖提示詞"""
    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    chapters_json = proj_dir / "chapters.json"
    if chapters_json.exists():
        print(f"  ✓ 偵測到現有章節檔：{chapters_json.name}，略過生成。")
        return json.loads(chapters_json.read_text(encoding="utf-8"))

    audio_file = upload_audio_safely(client, audio_path)

    prompt = """
這是一段繁體中文雙人 Podcast 音訊（來自 Google NotebookLM）。
請聆聽這段音訊，將整段內容結構化切分為 10 到 12 個「核心概念視覺章節 (Visual Chapters)」：
要求：
1. 包含 Chapter 0「開場導讀封面」（0.0 至 18.0 秒），作為節目視覺導讀，避免首幕劇透。
2. 接下來 Chapter 1 至 11（從 18.0 秒起至結尾），每個章節約 1.5 ~ 2.5 分鐘，覆蓋完整音訊。
3. 提煉出每個章節中最核心生動的隱喻、故事或心理情境。
4. 中文標籤 (caption) 與主題 (topic) 必須為「純繁體中文」，嚴格禁止出現任何英文單字或英文字詞括號（例如嚴禁出現 (Shadow) 或 (Persona) 等英文）。
5. 概念插畫提示詞 (image_prompt)：若畫面中包含任何看板、文字、字牌、圖表或標籤，必須明確指定為 Traditional Chinese text only (純繁體中文，禁止英文或簡體)；或設計為無文字純視覺隱喻畫面。
請以純 JSON 格式輸出：
{
  "podcast_title": "榮格心理學的情緒戰略",
  "total_duration": 1136.9,
  "chapters": [
    {
      "chapter_index": 0,
      "start_sec": 0.0,
      "end_sec": 18.0,
      "time_range": "00:00 - 00:18",
      "topic": "【本集導讀】榮格心理學的情緒戰略",
      "caption": "概念導讀：探索潛意識與情緒整合之道",
      "key_insight": "深入解析榮格心理學核心模型，將壓抑與投射轉化為內在覺察成長契機。",
      "image_prompt": "Podcast episode intro cover card with title and theme highlights"
    },
    {
      "chapter_index": 1,
      "start_sec": 18.0,
      "end_sec": 120.0,
      "time_range": "00:18 - 02:00",
      "topic": "主題簡述 (純繁體中文)",
      "caption": "概念可視化：...(純繁體中文，無英文括號)",
      "key_insight": "核心觀點 (純繁體中文)",
      "image_prompt": "Editorial 3D Pixar concept illustration of ..., vibrant warm cinematic lighting, high quality 3D art, no text, 16:9 aspect ratio"
    }
  ]
}
"""
    print("  [Gemini] 正在進行全篇語意章節切分與視覺概念規劃...")
    resp = client.models.generate_content(model="gemini-3.5-flash", contents=[audio_file, prompt])
    text = resp.text.strip()
    if text.startswith("```json"): text = text[7:]
    if text.startswith("```"): text = text[3:]
    if text.endswith("```"): text = text[:-3]
    text = text.strip()

    data = json.loads(text)
    chapters = data.get("chapters", [])
    if chapters and chapters[0]["chapter_index"] != 0:
        intro_ch = {
            "chapter_index": 0,
            "start_sec": 0.0,
            "end_sec": 18.0,
            "time_range": "00:00 - 00:18",
            "topic": f"【本集導讀】{data.get('podcast_title', '節目精華')}",
            "caption": "概念導讀：探索潛意識與情緒整合之道",
            "key_insight": "深入解析核心心理學模型，將壓抑與投射轉化為內在覺察成長契機。",
            "image_prompt": "Podcast episode intro cover card with title and theme highlights"
        }
        if chapters[0]["start_sec"] < 18.0:
            chapters[0]["start_sec"] = 18.0
            chapters[0]["time_range"] = f"00:18 - {int(chapters[0]['end_sec']//60):02d}:{int(chapters[0]['end_sec']%60):02d}"
        chapters.insert(0, intro_ch)
        data["chapters"] = chapters
        text = json.dumps(data, ensure_ascii=False, indent=2)

    chapters_json.write_text(text, encoding="utf-8")
    print(f"  ✓ 成功儲存章節資料：{chapters_json}")
    return data

def generate_subtitles(audio_path: Path, proj_dir: Path):
    """步驟 2：呼叫 Gemini 生成全篇雙人 SRT 字幕並轉換為高對比雙色 ASS 字幕"""
    from google import genai
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    srt_file = proj_dir / "subtitles.srt"
    ass_file = proj_dir / "subtitles.ass"

    if not srt_file.exists():
        audio_file = upload_audio_safely(client, audio_path)
        prompt = """
這是一段繁體中文雙人 Podcast 音訊。
請將整段音訊從開頭到結尾，完整逐字轉錄輸出為標準的繁體中文 SRT 字幕格式。
要求：
1. 每條字幕開頭標註發言者名稱，男主持標註為「Alan (主持): 」，女嘉賓標註為「Beth (嘉賓): 」。
2. 時間碼必須精確標準，例如 00:01:23,450 --> 00:01:26,800。
3. 繁體中文，斷句自然。請直接輸出純 SRT。
"""
        print("  [Gemini] 正在轉錄全篇逐字稿 SRT 字幕...")
        resp = client.models.generate_content(model="gemini-3.5-flash", contents=[audio_file, prompt])
        text = resp.text.strip()
        if text.startswith("```srt"): text = text[6:]
        if text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        srt_file.write_text(text.strip(), encoding="utf-8")
        print(f"  ✓ 成功儲存 SRT 字幕：{srt_file}")

    # 轉換 SRT 為規範雙色 ASS (主持人 Alan 明黃色 #FFFF00 / 嘉賓 Beth 水藍色 #40A9FF)
    srt_content = srt_file.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", srt_content.strip())

    ass_header = """[Script Info]
Title: Podcast Studio Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: HostA,Microsoft JhengHei,44,&H0000FFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.5,2,2,420,420,45,1
Style: HostB,Microsoft JhengHei,44,&H00FFA940,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,3.5,2,2,420,420,45,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    for b in blocks:
        lines = [l.strip() for l in b.strip().split("\n") if l.strip()]
        if len(lines) < 3: continue
        m = re.match(r"(\d+:\d+:\d+)[,\.](\d+)\s*-->\s*(\d+:\d+:\d+)[,\.](\d+)", lines[1])
        if not m: continue
        start_time = f"{int(m.group(1).split(':')[0])}:{m.group(1).split(':')[1]}:{m.group(1).split(':')[2]}.{m.group(2)[:2]}"
        end_time = f"{int(m.group(3).split(':')[0])}:{m.group(3).split(':')[1]}:{m.group(3).split(':')[2]}.{m.group(4)[:2]}"
        text = " ".join(lines[2:])
        style = "HostB" if ("Beth" in text or "嘉賓" in text or "來賓" in text or "B:" in text) else "HostA"
        dialogues.append(f"Dialogue: 0,{start_time},{end_time},{style},,0,0,0,,{text}")

    ass_file.write_text(ass_header + "\n".join(dialogues) + "\n", encoding="utf-8")
    print(f"  ✓ 成功轉換為高對比雙色 ASS 字幕：{ass_file}")

def generate_concepts(proj_dir: Path):
    """步驟 3：調用繪圖模型生成各章節 16:9 3D Pixar 概念插圖"""
    import urllib.request
    import urllib.parse
    import time

    chapters_file = proj_dir / "chapters.json"
    if not chapters_file.exists():
        print("  ❌ 找不到 chapters.json，請先執行 analyze")
        return
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    concepts_dir = proj_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    chapters = data.get("chapters", [])
    print(f"  [生圖] 正在檢查並繪製 {len(chapters)} 個章節 3D 概念插圖...")

    for ch in chapters:
        idx = ch["chapter_index"]
        if idx == 0:
            continue
        out_path = concepts_dir / f"concept_{idx:02d}.jpg"
        if out_path.exists() and out_path.stat().st_size > 5000:
            print(f"    ✓ 概念圖 {idx:02d} 已存在，略過。")
            continue

        raw_prompt = ch.get("image_prompt", "")
        prompt = f"Editorial 3D Pixar concept illustration of {raw_prompt}, vibrant warm volumetric lighting, high quality 3D art, octane render 8k, no text, clean composition"
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=576&seed={idx * 137 + 42}&model=sana&nologo=true"

        print(f"    🎨 [生圖中] 章節 {idx:02d}：{ch.get('topic', '')}...")
        success = False
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                img_data = urllib.request.urlopen(req, timeout=35).read()
                if len(img_data) > 5000:
                    out_path.write_bytes(img_data)
                    print(f"      ✓ 成功產出：{out_path.name} ({len(img_data)//1024} KB)")
                    success = True
                    break
            except Exception as e:
                print(f"      ⚠️ 嘗試 {attempt+1} 失敗：{e}，重試中...")
                time.sleep(2)

        if not success:
            try:
                url_flux = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=576&seed={idx * 137 + 42}&model=flux&nologo=true"
                req = urllib.request.Request(url_flux, headers={"User-Agent": "Mozilla/5.0"})
                img_data = urllib.request.urlopen(req, timeout=40).read()
                out_path.write_bytes(img_data)
                print(f"      ✓ (Flux 備援成功)：{out_path.name}")
            except Exception as e:
                print(f"      ❌ 章節 {idx:02d} 生圖失敗：{e}")

    print("  ✓ 全章節概念插圖準備完畢！")

async def render_scenes_async(proj_dir: Path):
    """步驟 4：使用 Playwright 載入 HTML 舞台，高速截取所有章節 1080p 畫格"""
    from playwright.async_api import async_playwright

    chapters_file = proj_dir / "chapters.json"
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    podcast_title = data.get("podcast_title", proj_dir.name)
    chapters = data["chapters"]

    scenes_dir = proj_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir = proj_dir / "concepts"

    stage_html = (TEMPLATES_DIR / "studio_stage.html").as_uri()
    intro_html = (TEMPLATES_DIR / "intro_cover.html").as_uri()

    print(f"  [Playwright] 正在高速截取 {len(chapters)} 個章節 1080p 舞台畫格...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})

        for ch in chapters:
            idx = ch["chapter_index"]
            out_frame = scenes_dir / f"scene_{idx:02d}.png"
            topic = ch["topic"]
            caption = ch["caption"]

            if idx == 0 and (TEMPLATES_DIR / "intro_cover.html").exists():
                await page.goto(intro_html)
                h1_title = chapters[1].get("topic", "心理陰影與投射") if len(chapters) > 1 else "核心議題一"
                h1_desc = chapters[1].get("key_insight", "") if len(chapters) > 1 else ""
                h2_title = chapters[min(4, len(chapters)-1)].get("topic", "面具與情結機制") if len(chapters) > 4 else "核心議題二"
                h2_desc = chapters[min(4, len(chapters)-1)].get("key_insight", "") if len(chapters) > 4 else ""
                h3_title = chapters[min(7, len(chapters)-1)].get("topic", "自性化整合之道") if len(chapters) > 7 else "核心議題三"
                h3_desc = chapters[min(7, len(chapters)-1)].get("key_insight", "") if len(chapters) > 7 else ""

                await page.evaluate(f"""() => {{
                    const titleEl = document.getElementById('cover-title');
                    if (titleEl) titleEl.textContent = '{podcast_title}';
                    const subEl = document.getElementById('cover-subtitle');
                    if (subEl) subEl.textContent = '{caption}';
                    const hCards = document.querySelectorAll('.highlight-card');
                    if (hCards.length >= 3) {{
                        hCards[0].querySelector('.highlight-title').innerHTML = '<span>🎭</span> {h1_title}';
                        hCards[0].querySelector('.highlight-desc').textContent = '{h1_desc}';
                        hCards[1].querySelector('.highlight-title').innerHTML = '<span>🪞</span> {h2_title}';
                        hCards[1].querySelector('.highlight-desc').textContent = '{h2_desc}';
                        hCards[2].querySelector('.highlight-title').innerHTML = '<span>🧭</span> {h3_title}';
                        hCards[2].querySelector('.highlight-desc').textContent = '{h3_desc}';
                    }}
                }}""")
                await page.wait_for_timeout(200)
                await page.screenshot(path=str(out_frame))
                out_concept = concepts_dir / f"concept_{idx:02d}.jpg"
                shutil.copy(out_frame, out_concept)
                print(f"    ✓ 封面畫格 {idx:02d}: {topic} -> {out_frame.name}")
            else:
                await page.goto(stage_html)
                concept_img = concepts_dir / f"concept_{idx:02d}.jpg"
                if not concept_img.exists():
                    concept_img = concepts_dir / "concept_01.jpg"

                img_uri = concept_img.as_uri()

                await page.evaluate(f"""() => {{
                    const titleEl = document.getElementById('podcast-title');
                    if (titleEl) titleEl.textContent = '{podcast_title}';
                    document.getElementById('concept-display').src = '{img_uri}';
                    document.getElementById('topic-text').textContent = '{topic}';
                    document.getElementById('concept-caption').textContent = '{caption}';
                }}""")
                await page.wait_for_timeout(150)
                await page.screenshot(path=str(out_frame))
                print(f"    ✓ 舞台畫格 {idx:02d}: {topic} -> {out_frame.name}")

        await browser.close()
    print("  ✓ 全章節畫格截取完畢！")

def render_scenes(proj_dir: Path):
    asyncio.run(render_scenes_async(proj_dir))

def synthesize_video(audio_path: Path, proj_dir: Path, output_file: Path):
    """步驟 5：調用 FFmpeg 串接畫格、封裝音軌並壓制雙色字幕"""
    chapters_file = proj_dir / "chapters.json"
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    chapters = data["chapters"]

    concat_txt = proj_dir / "video_concat.txt"
    scenes_dir = proj_dir / "scenes"

    with open(concat_txt, "w", encoding="utf-8") as f:
        for ch in chapters:
            idx = ch["chapter_index"]
            dur = round(ch["end_sec"] - ch["start_sec"], 2)
            frame_path = (scenes_dir / f"scene_{idx:02d}.png").resolve()
            f.write(f"file '{str(frame_path).replace(chr(92), '/')}'\n")
            f.write(f"duration {dur}\n")
        # 結尾重複最後一幀
        last_idx = chapters[-1]["chapter_index"]
        last_frame = (scenes_dir / f"scene_{last_idx:02d}.png").resolve()
        f.write(f"file '{str(last_frame).replace(chr(92), '/')}'\n")

    ass_file = proj_dir / "subtitles.ass"
    ass_filter_path = str(ass_file).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-i", str(audio_path),
        "-vf", f"fps=25,subtitles='{ass_filter_path}'",
        "-r", "25",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_file)
    ]

    print("  [FFmpeg] 正在進行多核視訊串接與字幕壓制...")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        print("FFmpeg 錯誤:", res.stderr)
        raise RuntimeError("FFmpeg 渲染失敗！")

    print(f"🎉 影片合成完成！輸出路徑：{output_file}")

def main():
    parser = argparse.ArgumentParser(description="雙人 Podcast 虛擬錄音室一鍵影片生成工作流")
    parser.add_argument("audio", help="Podcast 音訊檔案路徑 (.m4a, .mp3 等)")
    parser.add_argument("--step", choices=["all", "analyze", "subtitles", "concepts", "scenes", "render"], default="all", help="執行特定步驟 (預設為 all)")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        for cand in [ROOT_DIR / "sound_source" / args.audio, ROOT_DIR / "sound source" / args.audio, ROOT_DIR / args.audio]:
            if cand.exists():
                audio_path = cand
                break
    audio_path = audio_path.resolve()
    if not audio_path.exists():
        print(f"❌ 找不到指定的音訊檔案：{args.audio}")
        sys.exit(1)

    episode_name = audio_path.stem
    proj_dir = PROJECTS_DIR / episode_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "concepts").mkdir(exist_ok=True)
    (proj_dir / "scenes").mkdir(exist_ok=True)

    print(f"==================================================")
    print(f"🎙️ Podcast Studio 管線啟動：{episode_name}")
    print(f"📁 專案工作目錄：{proj_dir}")
    print(f"==================================================")

    step = args.step
    if step in ["all", "analyze"]:
        print("\n▶ 步驟 1：分析章節與概念視覺...")
        analyze_chapters(audio_path, proj_dir)

    if step in ["all", "subtitles"]:
        print("\n▶ 步驟 2：生成逐字稿與高對比字幕...")
        generate_subtitles(audio_path, proj_dir)

    if step in ["all", "concepts"]:
        print("\n▶ 步驟 3：繪製核心概念 3D 插圖...")
        generate_concepts(proj_dir)

    if step in ["all", "scenes"]:
        print("\n▶ 步驟 4：截取 1080p 舞台畫格...")
        render_scenes(proj_dir)

    if step in ["all", "render"]:
        print("\n▶ 步驟 5：FFmpeg 視訊合成壓制...")
        output_file = MOVIE_OUTPUT_DIR / f"{episode_name}_雙人錄音室版.mp4"
        synthesize_video(audio_path, proj_dir, output_file)

    print("\n✅ 所有工作流程順利結束！")

if __name__ == "__main__":
    main()
