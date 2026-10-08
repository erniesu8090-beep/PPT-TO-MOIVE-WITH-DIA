import os
import sys
import json
import re
import shutil
import asyncio
import argparse
import subprocess
from pathlib import Path

if sys.platform == "win32":
    if getattr(sys.flags, "utf8_mode", 0) == 0:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        res = subprocess.run([sys.executable, "-X", "utf8"] + sys.argv, env=env)
        sys.exit(res.returncode)
    sys.stdout.reconfigure(encoding="utf-8")

MODULE_DIR = Path(__file__).parent.resolve()
ROOT_DIR = MODULE_DIR.parent
TEMPLATES_DIR = MODULE_DIR / "templates"
DEFAULT_ASSETS_DIR = MODULE_DIR / "default_assets"
PROJECTS_DIR = MODULE_DIR / "projects"
MOVIE_OUTPUT_DIR = ROOT_DIR / "MovieOutput"
MOVIE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def load_chapters(proj_dir: Path):
    """讀取專案章節設定檔 chapters.json"""
    chapters_json = proj_dir / "chapters.json"
    if not chapters_json.exists():
        raise FileNotFoundError(f"找不到章節規劃檔案：{chapters_json}，請先由 AI Agent 或手動建立 chapters.json。")
    print(f"  ✓ 成功載入章節資料：{chapters_json.name}")
    return json.loads(chapters_json.read_text(encoding="utf-8"))

def prepare_subtitles(proj_dir: Path):
    """讀取 SRT 字幕並轉換為高對比雙色 ASS 字幕 (Alan 明黃色 #FFFF00 / Beth 水藍色 #40A9FF)"""
    ass_file = proj_dir / "subtitles.ass"
    srt_file = proj_dir / "subtitles.srt"

    if ass_file.exists():
        print(f"  ✓ 已存在雙色 ASS 字幕：{ass_file.name}")
        return

    if not srt_file.exists():
        raise FileNotFoundError(f"找不到字幕檔案：{srt_file}，請先由 AI Agent 或手動建立 subtitles.srt。")

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
    print(f"  ✓ 成功轉換為高對比雙色 ASS 字幕：{ass_file.name}")

async def render_scenes_async(proj_dir: Path):
    """使用 Playwright 載入 HTML 舞台，高速截取所有章節 1080p 畫格"""
    from playwright.async_api import async_playwright

    chapters_file = proj_dir / "chapters.json"
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    podcast_title = data.get("podcast_title", proj_dir.name)
    chapters = data["chapters"]

    scenes_dir = proj_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir = proj_dir / "concepts"

    stage_html = (TEMPLATES_DIR / "studio_stage.html").as_uri()

    print(f"  [Playwright] 正在高速截取 {len(chapters)} 個章節 1080p 舞台畫格...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})

        for ch in chapters:
            idx = ch["chapter_index"]
            out_frame = scenes_dir / f"scene_{idx:02d}.png"
            topic = ch["topic"]
            caption = ch["caption"]

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
    """調用 FFmpeg 串接畫格、封裝音軌並壓制雙色字幕"""
    chapters_file = proj_dir / "chapters.json"
    data = json.loads(chapters_file.read_text(encoding="utf-8"))
    chapters = data["chapters"]

    scenes_dir = proj_dir / "scenes"
    concat_txt = proj_dir / "concat_list.txt"

    with open(concat_txt, "w", encoding="utf-8") as f:
        for ch in chapters:
            idx = ch["chapter_index"]
            dur = round(ch["end_sec"] - ch["start_sec"], 2)
            frame_path = (scenes_dir / f"scene_{idx:02d}.png").resolve()
            f.write(f"file '{str(frame_path).replace(chr(92), '/')}'\n")
            f.write(f"duration {dur}\n")
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
    parser = argparse.ArgumentParser(description="雙人 Podcast 虛擬錄音室一鍵影片生成工作流 (100% 本機零 API 渲染)")
    parser.add_argument("audio", help="Podcast 音訊檔案路徑 (.m4a, .mp3 等)")
    parser.add_argument("--step", choices=["all", "subtitles", "scenes", "render"], default="all", help="執行特定步驟 (預設為 all)")
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
    print(f"🎙️ Podcast Studio 本機極簡渲染：{episode_name}")
    print(f"📁 專案工作目錄：{proj_dir}")
    print(f"==================================================")

    # 1. 載入章節
    load_chapters(proj_dir)

    step = args.step
    # 2. 準備雙色字幕
    if step in ["all", "subtitles"]:
        print("\n▶ 步驟：準備高對比雙色字幕...")
        prepare_subtitles(proj_dir)

    # 3. 截取 1080p 舞台畫格
    if step in ["all", "scenes"]:
        print("\n▶ 步驟：截取 1080p 舞台畫格...")
        render_scenes(proj_dir)

    # 4. FFmpeg 視訊合成壓制
    if step in ["all", "render"]:
        print("\n▶ 步驟：FFmpeg 視訊合成壓制...")
        output_file = MOVIE_OUTPUT_DIR / f"{episode_name}_雙人錄音室版.mp4"
        synthesize_video(audio_path, proj_dir, output_file)

    print("\n✅ 所有工作流程順利結束！")

if __name__ == "__main__":
    main()
