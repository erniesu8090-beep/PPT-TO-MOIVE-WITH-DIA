import subprocess
import os
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def render_page_badge(image_path: Path, output_path: Path, page_num: int, title: str, bg_color: str = "#0e607c", font_size: int = 20):
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    scale = max(1.0, img.width / 1920.0)
    actual_font_size = max(14, int(font_size * scale))

    font_candidates = [
        "C:/Windows/Fonts/msjhbd.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "arial.ttf"
    ]
    font = None
    for fc in font_candidates:
        if os.path.exists(fc):
            try:
                font = ImageFont.truetype(fc, actual_font_size)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    clean_title = (title or "").strip()
    if clean_title.startswith("【") and clean_title.endswith("】"):
        clean_title = clean_title[1:-1].strip()

    # Split title if it contains leading page markers
    if "：" in clean_title or ":" in clean_title:
        parts = re.split(r'[:：]', clean_title, 1)
        if any(kw in parts[0] for kw in ["第", "頁", "Page", "P."]):
            clean_title = parts[1].strip()

    if clean_title:
        display_text = f"第 {page_num:02d} 頁 ｜ {clean_title}"
    else:
        display_text = f"第 {page_num:02d} 頁"

    bbox = draw.textbbox((0, 0), display_text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    mx, my = int(45 * scale), int(35 * scale)
    px, py = int(20 * scale), int(12 * scale)
    r = int(10 * scale)
    box_rect = [mx, my, mx + tw + px * 2, my + th + py * 2]

    # Draw rounded rect capsule
    draw.rounded_rectangle(box_rect, radius=r, fill=bg_color)
    draw.text((mx + px, my + py - int(2 * scale)), display_text, font=font, fill="#FFFFFF")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)
    return output_path

def build_video(
    slide_images,
    audio_files,
    output_mp4: Path,
    srt_file: Path = None,
    burn_subtitles: bool = False,
    pad_time: float = 0.5,
    show_badge: bool = True,
    badge_bg_color: str = "#0e607c",
    badge_font_size: int = 20,
    slide_titles: list = None,
    log_fn = None
):
    """
    slide_images: list of Path to page-*.png
    audio_files: list of Path to page-*.mp3 (or None for silence)
    output_mp4: destination Path
    srt_file: optional Path to subtitles.srt
    burn_subtitles: boolean
    pad_time: pause duration in seconds between slides (default 0.5s)
    log_fn: optional callable for streaming progress logs
    """
    def _log(msg):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass
        print(msg)

    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output_mp4.parent / "temp_build"
    work_dir.mkdir(parents=True, exist_ok=True)

    _log("--- [1/4] 正在分析各投影片畫面與對應語音長度... ---")
    # 1. Inspect durations for each slide
    durations = []
    total_audio_sec = 0.0
    for idx, af in enumerate(audio_files):
        p_num = idx + 1
        if af and Path(af).exists():
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(af)
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
                import json
                d = float(json.loads(res.stdout)["format"]["duration"])
                actual_d = max(d, 1.0)
                durations.append(actual_d)
                total_audio_sec += actual_d
                _log(f"  • 第 {p_num:02d} 頁: 音訊長度 {actual_d:.2f} 秒 (+ 轉場間隔 {pad_time:.1f} 秒)")
            except Exception:
                durations.append(5.0)
                total_audio_sec += 5.0
                _log(f"  • 第 {p_num:02d} 頁: 使用預設時長 5.0 秒")
        else:
            durations.append(5.0)
            total_audio_sec += 5.0
            _log(f"  • 第 {p_num:02d} 頁: 無音訊，使用預設時長 5.0 秒")

    # 2. Concat all audio files with optional pad_time silence
    _log(f"\n--- [2/4] 正在串接完整音軌 (共 {len(audio_files)} 頁，每頁間隔 {pad_time:.1f} 秒)... ---")
    combined_audio = work_dir / "combined_audio.mp3"
    audio_concat_txt = work_dir / "audio_concat.txt"
    silence_audio = work_dir / "silence.mp3"

    if pad_time > 0:
        cmd_silence = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(pad_time),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(silence_audio)
        ]
        subprocess.run(cmd_silence, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    with open(audio_concat_txt, "w", encoding="utf-8") as f:
        for af in audio_files:
            if af and Path(af).exists():
                f.write(f"file '{Path(af).resolve().as_posix()}'\n")
                if pad_time > 0 and silence_audio.exists():
                    f.write(f"file '{silence_audio.resolve().as_posix()}'\n")

    cmd_concat_audio = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(audio_concat_txt),
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(combined_audio)
    ]
    subprocess.run(cmd_concat_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    _log("  ✓ 音訊軌串接與淡出間隔處理完成！")

    # 3. Create slides concat demuxer file with (duration + pad_time)
    _log("\n--- [3/4] 正在建立簡報畫面精準對齊時間表... ---")
    badged_dir = work_dir / "badged_slides"
    actual_slide_images = []

    if show_badge:
        _log(f"  • [標籤壓印] 正在為各頁簡報繪製左上角標題膠囊框 (背景: {badge_bg_color}, 字級: {badge_font_size}px)...")
        badged_dir.mkdir(parents=True, exist_ok=True)
        for idx, img in enumerate(slide_images):
            p_num = idx + 1
            title = slide_titles[idx] if (slide_titles and idx < len(slide_titles)) else ""
            out_badged = badged_dir / f"page-{p_num:02d}.png"
            render_page_badge(
                image_path=Path(img),
                output_path=out_badged,
                page_num=p_num,
                title=title,
                bg_color=badge_bg_color,
                font_size=badge_font_size
            )
            actual_slide_images.append(out_badged)
        _log("  ✓ 簡報標題膠囊框繪製完畢！")
    else:
        actual_slide_images = slide_images

    slides_concat_txt = work_dir / "slides_concat.txt"
    with open(slides_concat_txt, "w", encoding="utf-8") as f:
        for img, dur in zip(actual_slide_images, durations):
            img_path = Path(img).resolve().as_posix()
            f.write(f"file '{img_path}'\n")
            f.write(f"duration {dur + pad_time}\n")
        # In FFmpeg concat demuxer, repeat the last image to display until the end
        if actual_slide_images:
            f.write(f"file '{Path(actual_slide_images[-1]).resolve().as_posix()}'\n")
    _log(f"  ✓ 畫面時序對齊完畢，預計影片總時長: 約 {int((total_audio_sec + len(slide_images)*pad_time)//60)} 分 {int((total_audio_sec + len(slide_images)*pad_time)%60)} 秒")

    # 4. Assemble video with FFmpeg
    mode_text = "含字幕硬燒錄" if (burn_subtitles and srt_file and Path(srt_file).exists()) else "無字幕純淨極速版"
    _log(f"\n--- [4/4] 正在調用 FFmpeg 進行影片編碼與壓制 ({mode_text})... ---")

    vf_filters = []
    if burn_subtitles and srt_file and Path(srt_file).exists():
        srt_clean_path = Path(srt_file).resolve().as_posix().replace(":", "\\:")
        # Styling rule from .agents/AGENTS.md: White text, Black outline, Black shadow, Bottom center
        style = "FontSize=22,FontName=Microsoft JhengHei,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1.5,Alignment=2,MarginV=35"
        vf_filters.append(f"subtitles='{srt_clean_path}':force_style='{style}'")

    cmd_build = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(slides_concat_txt),
        "-i", str(combined_audio),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", "30"
    ]

    if vf_filters:
        cmd_build.extend(["-vf", ",".join(vf_filters)])

    cmd_build.extend([
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_mp4)
    ])

    subprocess.run(cmd_build, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # Clean up temp
    try:
        combined_audio.unlink(missing_ok=True)
        audio_concat_txt.unlink(missing_ok=True)
        silence_audio.unlink(missing_ok=True)
        slides_concat_txt.unlink(missing_ok=True)
        if badged_dir.exists():
            import shutil
            shutil.rmtree(badged_dir, ignore_errors=True)
        work_dir.rmdir()
    except Exception:
        pass

    file_size_mb = output_mp4.stat().st_size / (1024 * 1024) if output_mp4.exists() else 0.0
    _log(f"\n🎉 恭喜！影片合成完全成功！")
    _log(f"  • 輸出檔案: {output_mp4.name} ({file_size_mb:.2f} MB)")
    _log(f"  • 畫面模式: {mode_text}")
    _log(f"  • 轉場間隔: {pad_time:.1f} 秒/頁")

    return output_mp4
