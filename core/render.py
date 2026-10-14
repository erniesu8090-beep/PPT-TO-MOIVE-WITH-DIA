import subprocess
import json
import os
import shutil
from pathlib import Path
import sys
from job_control import child_checkpoint


def run_media_step(cmd, **kwargs):
    child_checkpoint(os.environ.get("RENDER_PAUSE_FILE"))
    label = "擷取投影片畫面" if str(cmd[0]).lower() == "node" else "合成音訊與影片"
    if "ass=temp_subtitles.ass" in cmd:
        label = "壓制影片字幕"
    print("STAGE:" + label, flush=True)
    kwargs["stderr"] = None  # Inherit parent's pipe; retain full FFmpeg diagnostics.
    return subprocess.run(cmd, **kwargs)

from subtitle_styles import ass_header as make_ass_header
from subtitle_utils import split_timed_text, wrap_ass_lines, ass_text, format_srt_time, format_ass_time

def generate_subtitles(settings):
    timings_dyn_path = Path("timings_dynamic.json")
    if not timings_dyn_path.exists():
        return "", ""
    try:
        with open(timings_dyn_path, "r", encoding="utf-8") as f_td:
            timings_dyn = json.load(f_td)
    except Exception as e:
        print(f"  (解析 timings_dynamic.json 失敗: {e})")
        return "", ""

    # 讀取雙人角色色彩
    voice_settings = {}
    voice_path = Path("voice_settings.json")
    if voice_path.exists():
        try:
            with open(voice_path, "r", encoding="utf-8") as f_vs:
                voice_settings = json.load(f_vs)
        except Exception:
            pass

    is_dual = voice_settings.get("mode") == "dual"
    ass_header = make_ass_header(settings, voice_settings)

    srt_parts, ass_events = [], []
    subtitle_index, global_time_offset = 1, 0.0
    for page in timings_dyn:
        page_dual = is_dual or any(c.get("speaker") == "B" for c in page.get("clauses", []))
        previous_speaker = None
        for clause in page.get("clauses", []):
            text = clause.get("text", "").strip()
            if not text:
                continue
            speaker = clause.get("speaker", "A")
            dual = page_dual
            style_name = ("SpeakerB" if speaker == "B" else "SpeakerA") if dual else "Default"
            name = clause.get("speaker_name", "").strip()
            show_label = clause.get("show_speaker_label", previous_speaker != speaker)
            prefix = f"[{name}]" if dual and name and show_label else ""
            for cue in split_timed_text(text, global_time_offset + clause.get("start", 0),
                                       global_time_offset + clause.get("end", 0), prefix=prefix):
                plain = wrap_ass_lines(ass_text(cue["text"]))
                srt_text = plain.replace(r"\N", "\n")
                srt_parts.append(f"{subtitle_index}\n{format_srt_time(cue['start'])} --> {format_srt_time(cue['end'])}\n{srt_text}\n\n")
                ass_events.append(f"Dialogue: 0,{format_ass_time(cue['start'])},{format_ass_time(cue['end'])},{style_name},,0,0,0,,{plain}")
                subtitle_index += 1
            previous_speaker = speaker
        global_time_offset += page.get("dur", 0)
    srt_content = ''.join(srt_parts)

    ass_content = ass_header + "\n".join(ass_events) + "\n"
    return srt_content, ass_content


def run_render():
    sys.stdout.reconfigure(encoding='utf-8')
    
    # 0. 載入字幕設定以決定渲染模式 (embed, srt, both)
    sub_mode = "both"  # 預設為 both
    sub_settings_path = Path("subtitle_settings.json")
    if sub_settings_path.exists():
        try:
            with open(sub_settings_path, "r", encoding="utf-8") as f_ss:
                settings = json.load(f_ss)
                sub_mode = settings.get("mode")
                if not sub_mode:
                    embed_bool = settings.get("embed", True)
                    sub_mode = "embed" if embed_bool else "srt"
        except Exception:
            pass
            
    # 1. 載入時序資料
    with open("timings.json", "r", encoding="utf-8") as f:
        timings = json.load(f)
        
    renders_dir = Path("renders")
    renders_dir.mkdir(parents=True, exist_ok=True)
    
    # 建立音訊暫存目錄
    tmp_audio_dir = renders_dir / "audio_tmp"
    tmp_audio_dir.mkdir(parents=True, exist_ok=True)
    
    print("--- 1. 開始對齊旁白音量與長度 ---")
    concat_list_path = tmp_audio_dir / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f_list:
        for p in timings:
            i = p['i']
            dur = p['dur']
            in_file = Path(f"assets/narration/page-{i:02d}.mp3")
            out_file = tmp_audio_dir / f"padded-{i:02d}.mp3"
            
            # 使用 ffmpeg 將每段旁白尾端填補靜音，使其剛好達到指定的單頁秒數 dur
            if p.get("silent", False):
                cmd_pad = ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(dur), str(out_file)]
            else:
                if p.get("audio_status") == "estimated":
                    raise RuntimeError(f"第 {i} 頁配音尚未完成，請先生成配音。")
                cmd_pad = ["ffmpeg", "-y", "-i", str(in_file), "-af", "apad", "-t", str(dur), str(out_file)]
            run_media_step(cmd_pad, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"  ✓ 填補 page-{i:02d}.mp3 -> {dur} 秒")
            
            f_list.write(f"file 'padded-{i:02d}.mp3'\n")
            
    # 2. 合併為 master 主音軌
    print("\n--- 2. 合併為 master_audio.mp3 ---")
    master_audio = renders_dir / "master_audio.mp3"
    
    cmd_concat = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", "concat_list.txt", "-c", "copy",
        "../master_audio.mp3"
    ]
    run_media_step(cmd_concat, check=True, cwd=str(tmp_audio_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  ✓ 成功合併主音軌：{master_audio}")
    
    # 3. 呼叫 Playwright 進行極速網頁畫面擷取 (Snapshot)
    print("\n--- 3. 執行 Playwright 高速擷取投影片畫格 (約 5~15 秒) ---")
    env = os.environ.copy()
    temp_dir = Path(env.get("TEMP", "C:\\Temp")) / "cvs-render"
    temp_node_modules = temp_dir / "node_modules"
    env["NODE_PATH"] = str(temp_node_modules)
    
    # 檢查 Node.js 是否已安裝
    if not shutil.which("node"):
        raise Exception("系統中未偵測到 Node.js，無法錄製影片！請先下載並安裝 Node.js： https://nodejs.org/")

    use_shell = os.name == "nt"

    # 檢查與安裝 Playwright Node 模組
    try:
        subprocess.run(["node", "-e", "require('playwright')"], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
        print("  ✓ 偵測到 Playwright 模組已安裝")
    except subprocess.CalledProcessError:
        print("  ⚠️ 未在暫存目錄偵測到 Playwright，正在開始自動安裝 (可能需要幾分鐘)...")
        temp_dir.mkdir(parents=True, exist_ok=True)
        pkg_json = temp_dir / "package.json"
        if not pkg_json.exists():
            with open(pkg_json, "w", encoding="utf-8") as f:
                f.write('{"private": true, "dependencies": {"playwright": "^1.40.0"}}')
        try:
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=str(temp_dir), check=True, shell=use_shell)
            print("  ✓ Playwright 模組安裝成功！")
        except Exception as e:
            raise Exception(f"自動安裝 Playwright 失敗，請手動執行 'npm install'！錯誤資訊: {e}")

    # 檢查並下載 Chromium 瀏覽器
    try:
        test_script = "const { chromium } = require('playwright'); (async () => { const b = await chromium.launch(); await b.close(); })().catch(e => { process.exit(1); })"
        subprocess.run(["node", "-e", test_script], env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=use_shell)
        print("  ✓ 偵測到 Playwright Chromium 瀏覽器已下載")
    except subprocess.CalledProcessError:
        print("  ⚠️ 未偵測到 Playwright 瀏覽器核心，正在下載 Chromium 瀏覽器...")
        try:
            subprocess.run(["npx", "playwright", "install", "chromium"], env=env, cwd=str(temp_dir), check=True, shell=use_shell)
            print("  ✓ Chromium 瀏覽器下載成功！")
        except Exception as e:
            raise Exception(f"自動下載 Chromium 瀏覽器失敗，請嘗試手動執行 'npx playwright install chromium'。錯誤資訊: {e}")

    # 清理並重建 frames 目錄
    frames_dir = renders_dir / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    cmd_snapshot = ["node", "snapshot_slides.cjs"]
    run_media_step(cmd_snapshot, check=True, env=env, shell=use_shell)
    print("  ✓ 投影片高畫質畫格擷取完成！")

    # 4. 生成 FFmpeg 投影片時間軸 Concat 清單
    print("\n--- 4. 建立視訊時序串接清單 ---")
    slides_concat_path = renders_dir / "slides_concat.txt"
    with open(slides_concat_path, "w", encoding="utf-8") as f_sc:
        for i, p in enumerate(timings):
            frame_rel = f"frames/slide_{i:03d}.png"
            dur = p.get('dur', 5)
            f_sc.write(f"file '{frame_rel}'\n")
            f_sc.write(f"duration {dur}\n")
        # 結尾必須重複最後一幀以滿足 ffmpeg concat demuxer 規格
        last_frame = f"frames/slide_{len(timings)-1:03d}.png"
        f_sc.write(f"file '{last_frame}'\n")
    print(f"  ✓ 成功建立時序串接清單 (共 {len(timings)} 頁)")

    # 載入字幕設定
    sub_settings = {
        "font_name": "Microsoft JhengHei",
        "font_size": 42,
        "font_color": "white",
        "bg_style": "outline",
        "bg_opacity": 0.75,
        "bottom_pos": 40
    }
    if sub_settings_path.exists():
        try:
            with open(sub_settings_path, "r", encoding="utf-8") as f_ss:
                sub_settings.update(json.load(f_ss))
        except Exception:
            pass

    # Helper: 產生標準 SRT 與 1080p 等比例 ASS 格式字幕
    # 5. 音畫直接合成最終影片 (Direct FFmpeg Synthesis)
    print("\n--- 5. 直接由 FFmpeg 極速合成影片 ---")
    final_output = "簡報影片.mp4"
    project_info_path = Path("project_info.json")
    if project_info_path.exists():
        try:
            with open(project_info_path, "r", encoding="utf-8") as f_pi:
                info = json.load(f_pi)
                pdf_name = info.get("slide_pdf_name")
                if pdf_name:
                    final_output = f"{Path(pdf_name).stem}.mp4"
        except Exception as e:
            print(f"  (讀取 project_info.json 失敗，使用預設檔名: {e})")

    output_dir = Path(__file__).resolve().parent.parent / "MovieOutput"
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(final_output).stem
    ext = Path(final_output).suffix
    crf_val = os.environ.get("VIDEO_CRF", "23")

    srt_content, ass_content = generate_subtitles(sub_settings)
    silent_project = bool(timings) and all(p.get("silent", False) for p in timings)
    if not ass_content or (not srt_content and not silent_project):
        raise RuntimeError("缺少可用字幕，請先確認口白與字幕時序資料。")
    temp_ass_path = renders_dir / "temp_subtitles.ass"
    if ass_content:
        with open(temp_ass_path, "w", encoding="utf-8") as f_ass:
            f_ass.write(ass_content)

    if sub_mode == "both":
        # 同時輸出無字幕版、內嵌字幕版與 SRT
        clean_name = f"{stem}_無字幕{ext}"
        embed_name = f"{stem}_內嵌字幕{ext}"
        srt_name = f"{stem}.srt"
        
        output_path_clean = output_dir / clean_name
        output_path_embed = output_dir / embed_name
        output_path_srt = output_dir / srt_name
        
        counter = 1
        while output_path_clean.exists() or output_path_embed.exists() or output_path_srt.exists():
            new_stem = f"{stem} ({counter})"
            clean_name = f"{new_stem}_無字幕{ext}"
            embed_name = f"{new_stem}_內嵌字幕{ext}"
            srt_name = f"{new_stem}.srt"
            output_path_clean = output_dir / clean_name
            output_path_embed = output_dir / embed_name
            output_path_srt = output_dir / srt_name
            counter += 1

        cmd_mux_clean = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "slides_concat.txt",
            "-i", "master_audio.mp3",
            "-vf", "fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path_clean.resolve())
        ]
        
        print("  正在極速合成無字幕版影片，請稍候...")
        run_media_step(cmd_mux_clean, check=True, cwd=str(renders_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✓ 成功產生無字幕版影片：{output_path_clean}")

        # 5.5 產生獨立 SRT 字幕檔案
        print("\n--- 5.5. 產生獨立 SRT 字幕檔案 ---")
        if srt_content or silent_project:
            try:
                with open(output_path_srt, "w", encoding="utf-8") as f_srt:
                    f_srt.write(srt_content)
                print(f"  ✓ 成功產生字幕檔：{output_path_srt}")
            except Exception as e:
                raise RuntimeError(f"產生 SRT 字幕檔失敗: {e}") from e

        # 5.6 壓制字幕生成內嵌字幕版影片 (使用 1080p 精確等比例 ASS)
        print("\n--- 5.6. 壓制字幕生成內嵌字幕影片 ---")
        if ass_content:
            try:
                cmd_burn = [
                    "ffmpeg", "-y", "-i", str(output_path_clean.resolve()),
                    "-vf", "ass=temp_subtitles.ass",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val, "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    str(output_path_embed.resolve())
                ]
                print("  正在將等比例字幕壓制到影片中，請稍候...")
                run_media_step(cmd_burn, check=True, cwd=str(renders_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"  ✓ 成功產生內嵌字幕版影片：{output_path_embed}")
            except Exception as e:
                raise RuntimeError(f"壓制內嵌字幕影片失敗: {e}") from e

        print(f"\n🎉 恭喜！影片全部製作成功！")
        print(f"  輸出無字幕版：{os.path.abspath(output_path_clean)}")
        print(f"  輸出內嵌字幕版：{os.path.abspath(output_path_embed)}")
        print(f"  輸出字幕檔案：{os.path.abspath(output_path_srt)}")
        print(f"OUTPUT_FILENAME:{output_path_embed.name}")
        print(f"OUTPUT_FILENAME_CLEAN:{output_path_clean.name}")
        print(f"OUTPUT_SRT_FILENAME:{output_path_srt.name}")

    elif sub_mode == "srt":
        # 僅輸出無字幕影片 + 獨立 SRT
        output_path = output_dir / final_output
        srt_path = output_path.with_suffix(".srt")
        
        counter = 1
        while output_path.exists() or srt_path.exists():
            new_name = f"{stem} ({counter}){ext}"
            output_path = output_dir / new_name
            srt_path = output_path.with_suffix(".srt")
            counter += 1

        cmd_mux = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "slides_concat.txt",
            "-i", "master_audio.mp3",
            "-vf", "fps=30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path.resolve())
        ]
        print("  正在極速合成影片，請稍候...")
        run_media_step(cmd_mux, check=True, cwd=str(renders_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✓ 成功產生影片：{output_path}")

        print("\n--- 5.5. 產生獨立 SRT 字幕檔案 ---")
        if srt_content or silent_project:
            try:
                with open(srt_path, "w", encoding="utf-8") as f_srt:
                    f_srt.write(srt_content)
                print(f"  ✓ 成功產生字幕檔：{srt_path}")
            except Exception as e:
                raise RuntimeError(f"產生 SRT 字幕檔失敗: {e}") from e

        print(f"\n🎉 恭喜！影片製作成功！")
        print(f"  輸出路徑：{os.path.abspath(output_path)}")
        print(f"  輸出字幕檔案：{os.path.abspath(srt_path)}")
        print(f"OUTPUT_FILENAME:{output_path.name}")
        print(f"OUTPUT_SRT_FILENAME:{srt_path.name}")

    else:
        # 模式為 embed (單一內嵌字幕影片)
        output_path = output_dir / final_output
        counter = 1
        while output_path.exists():
            new_name = f"{stem} ({counter}){ext}"
            output_path = output_dir / new_name
            counter += 1

        vf_filter = "fps=30,ass=temp_subtitles.ass" if ass_content else "fps=30"
        cmd_mux = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", "slides_concat.txt",
            "-i", "master_audio.mp3",
            "-vf", vf_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path.resolve())
        ]
        print("  正在極速壓制等比例內嵌字幕影片，請稍候...")
        run_media_step(cmd_mux, check=True, cwd=str(renders_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✓ 成功產生內嵌字幕影片：{output_path}")

        print(f"\n🎉 恭喜！影片製作成功！")
        print(f"  輸出路徑：{os.path.abspath(output_path)}")
        print(f"OUTPUT_FILENAME:{output_path.name}")

    # 6. 清理暫存檔案
    try:
        shutil.rmtree(tmp_audio_dir, ignore_errors=True)
        shutil.rmtree(frames_dir, ignore_errors=True)
        slides_concat_path.unlink(missing_ok=True)
        temp_ass_path.unlink(missing_ok=True)
        print("  ✓ 已清理暫存檔案。")
    except Exception as e:
        print(f"  (清理暫存檔時發生輕微錯誤: {e})")

if __name__ == "__main__":
    run_render()
