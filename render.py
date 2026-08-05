import subprocess
import json
import os
import shutil
from pathlib import Path
import sys

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
            cmd_pad = [
                "ffmpeg", "-y", "-i", str(in_file),
                "-af", "apad", "-t", str(dur),
                str(out_file)
            ]
            subprocess.run(cmd_pad, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    subprocess.run(cmd_concat, check=True, cwd=str(tmp_audio_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  ✓ 成功合併主音軌：{master_audio}")
    
    # 3. 呼叫 Playwright 進行網頁無頭錄製
    embed_param = "true" if sub_mode == "embed" else "false"
    print(f"\n--- 3. 執行 Playwright 錄製網頁畫面 (字幕內嵌: {embed_param}) ---")
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
            # 優先嘗試在 temp_dir 執行 npm install
            subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=str(temp_dir), check=True, shell=use_shell)
            print("  ✓ Playwright 模組安裝成功！")
        except Exception as e:
            raise Exception(f"自動安裝 Playwright 失敗，請手動執行 'npm install'！錯誤資訊: {e}")

    # 檢查並下載 Chromium 瀏覽器
    try:
        # 測試是否可以成功載入並啟動 chromium
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

    print(f"  Node.js 將使用模組路徑：{temp_node_modules}")
    cmd_node = ["node", "record.cjs", f"--embed={embed_param}"]
    subprocess.run(cmd_node, check=True, env=env, shell=use_shell)
    
    # 4. 尋找剛剛生成的 webm 錄製檔
    print("\n--- 4. 尋找錄製的 WebM 影片檔 ---")
    webm_files = list(renders_dir.glob("*.webm"))
    if not webm_files:
        raise Exception("找不到 Playwright 生成的 .webm 錄製檔案！")
    
    latest_webm = max(webm_files, key=lambda f: f.stat().st_mtime)
    print(f"  ✓ 找到最新錄製檔：{latest_webm}")
    
    # Helper 產生 SRT 字幕內容
    def generate_srt_content():
        timings_dyn_path = Path("timings_dynamic.json")
        if not timings_dyn_path.exists():
            return ""
        try:
            with open(timings_dyn_path, "r", encoding="utf-8") as f_td:
                timings_dyn = json.load(f_td)
            
            def format_srt_time(seconds):
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                secs = int(seconds % 60)
                milliseconds = int(round((seconds - int(seconds)) * 1000))
                return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
            
            srt_content = ""
            subtitle_index = 1
            global_time_offset = 0.0
            
            for page in timings_dyn:
                page_dur = page.get("dur", 0)
                clauses = page.get("clauses", [])
                for clause in clauses:
                    c_text = clause.get("text", "").strip()
                    if not c_text:
                        continue
                    c_start = global_time_offset + clause.get("start", 0)
                    c_end = global_time_offset + clause.get("end", 0)
                    
                    spk_name = clause.get("speaker_name", "").strip()
                    full_text = f"[{spk_name}] {c_text}" if spk_name and clause.get("speaker") == "B" else c_text

                    srt_content += f"{subtitle_index}\n"
                    srt_content += f"{format_srt_time(c_start)} --> {format_srt_time(c_end)}\n"
                    srt_content += f"{full_text}\n\n"
                    subtitle_index += 1
                global_time_offset += page_dur
            return srt_content
        except Exception as e:
            print(f"  (解析 timings_dynamic.json 失敗: {e})")
            return ""

    # 5. 音畫 Mux 合成最終影片
    print("\n--- 5. 合成最終影片 (Mux) ---")
    final_output = "《企業朝廷生存指南》.mp4"
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

    # 確保輸出目錄存在
    output_dir = Path("MovieOutput")
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(final_output).stem
    ext = Path(final_output).suffix
    crf_val = os.environ.get("VIDEO_CRF", "27")

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
            "ffmpeg", "-y", "-i", str(latest_webm), "-i", str(master_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-crf", crf_val, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path_clean)
        ]
        
        print("  正在渲染無字幕版影片，請稍候...")
        subprocess.run(cmd_mux_clean, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✓ 成功產生無字幕版影片：{output_path_clean}")

        # 5.5. 產生獨立 SRT 字幕檔案
        print("\n--- 5.5. 產生獨立 SRT 字幕檔案 ---")
        srt_content = generate_srt_content()
        if srt_content:
            try:
                with open(output_path_srt, "w", encoding="utf-8") as f_srt:
                    f_srt.write(srt_content)
                print(f"  ✓ 成功產生字幕檔：{output_path_srt}")
            except Exception as e:
                print(f"  (產生 SRT 字幕檔失敗: {e})")

        # 5.6. 壓制字幕生成內嵌字幕版影片
        print("\n--- 5.6. 壓制字幕生成內嵌字幕影片 ---")
        if srt_content:
            try:
                temp_srt_relative = "temp_subtitles.srt"
                temp_srt_path = renders_dir / temp_srt_relative
                with open(temp_srt_path, "w", encoding="utf-8") as f_temp_srt:
                    f_temp_srt.write(srt_content)
                    
                # Load subtitle settings to customize burning style
                settings = {
                    "font_name": "Microsoft JhengHei",
                    "font_size": 24,
                    "font_color": "white",
                    "bg_style": "outline",
                    "bg_opacity": 0.75,
                    "bottom_pos": 40
                }
                if sub_settings_path.exists():
                    try:
                        with open(sub_settings_path, "r", encoding="utf-8") as f_ss:
                            settings.update(json.load(f_ss))
                    except Exception:
                        pass
                
                font_name = settings.get("font_name", "Microsoft JhengHei")
                font_size = settings.get("font_size", 24)
                font_color = settings.get("font_color", "white")
                bg_style = settings.get("bg_style", "outline")
                bg_opacity = settings.get("bg_opacity", 0.75)
                bottom_pos = settings.get("bottom_pos", 40)
                
                color_map = {
                    "white": "&H00FFFFFF",
                    "yellow": "&H0000FFFF",
                    "cyan": "&H00FFA940",
                    "green": "&H0011D9A0",
                    "gray": "&H00CCCCCC"
                }
                primary_colour = color_map.get(font_color, "&H00FFFFFF")
                
                if bg_style == "box":
                    border_style = 3
                    outline = 0
                    shadow = 0
                    alpha_hex = f"{int((1.0 - bg_opacity) * 255):02X}"
                    back_colour = f"&H{alpha_hex}000000"
                elif bg_style == "none":
                    border_style = 1
                    outline = 0
                    shadow = 0
                    back_colour = "&HFF000000"
                else: # outline (描邊 + 陰影)
                    border_style = 1
                    outline = 2
                    shadow = 1
                    back_colour = "&H00000000"
                
                force_style = f"Fontname={font_name},Fontsize={font_size},PrimaryColour={primary_colour},BackColour={back_colour},BorderStyle={border_style},Outline={outline},Shadow={shadow},MarginV={bottom_pos}"
                
                cmd_burn = [
                    "ffmpeg", "-y", "-i", str(output_path_clean.resolve()),
                    "-vf", f"subtitles={temp_srt_relative}:force_style='{force_style}'",
                    "-c:v", "libx264", "-crf", crf_val, "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    str(output_path_embed.resolve())
                ]
                print("  正在將字幕壓制到影片中，請稍候...")
                subprocess.run(cmd_burn, check=True, cwd=str(renders_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"  ✓ 成功產生內嵌字幕版影片：{output_path_embed}")
                
                try:
                    temp_srt_path.unlink()
                except Exception:
                    pass
            except Exception as e:
                print(f"  (壓制內嵌字幕影片失敗: {e})")

        print(f"\n🎉 恭喜！影片全部製作成功！")
        print(f"  輸出無字幕版：{os.path.abspath(output_path_clean)}")
        print(f"  輸出內嵌字幕版：{os.path.abspath(output_path_embed)}")
        print(f"  輸出字幕檔案：{os.path.abspath(output_path_srt)}")
        print(f"OUTPUT_FILENAME:{output_path_embed.name}")
        print(f"OUTPUT_FILENAME_CLEAN:{output_path_clean.name}")
        print(f"OUTPUT_SRT_FILENAME:{output_path_srt.name}")

    else:
        # 只輸出單一檔案 (embed 或 srt)
        output_path = output_dir / final_output
        srt_path = output_path.with_suffix(".srt")
        
        if sub_mode == "srt":
            counter = 1
            while output_path.exists() or srt_path.exists():
                new_name = f"{stem} ({counter}){ext}"
                output_path = output_dir / new_name
                srt_path = output_path.with_suffix(".srt")
                counter += 1
        else:
            counter = 1
            while output_path.exists():
                new_name = f"{stem} ({counter}){ext}"
                output_path = output_dir / new_name
                counter += 1

        cmd_mux = [
            "ffmpeg", "-y", "-i", str(latest_webm), "-i", str(master_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-crf", crf_val, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest",
            str(output_path)
        ]
        
        print("  正在渲染 Mux 影片，請稍候...")
        subprocess.run(cmd_mux, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  ✓ 成功產生影片：{output_path}")

        if sub_mode == "srt":
            print("\n--- 5.5. 產生獨立 SRT 字幕檔案 ---")
            srt_content = generate_srt_content()
            if srt_content:
                try:
                    with open(srt_path, "w", encoding="utf-8") as f_srt:
                        f_srt.write(srt_content)
                    print(f"  ✓ 成功產生字幕檔：{srt_path}")
                except Exception as e:
                    print(f"  (產生 SRT 字幕檔失敗: {e})")

        print(f"\n🎉 恭喜！影片製作成功！")
        print(f"  輸出路徑：{os.path.abspath(output_path)}")
        if sub_mode == "srt":
            print(f"  輸出字幕檔案：{os.path.abspath(srt_path)}")
        print(f"OUTPUT_FILENAME:{output_path.name}")
        if sub_mode == "srt":
            print(f"OUTPUT_SRT_FILENAME:{srt_path.name}")
        
    # 6. 清理暫存音訊檔
    try:
        shutil.rmtree(tmp_audio_dir)
        latest_webm.unlink()
        print("  ✓ 已清理暫存檔案。")
    except Exception as e:
        print(f"  (清理暫存檔時發生輕微錯誤: {e})")
    except Exception as e:
        print(f"  (清理暫存檔時發生輕微錯誤: {e})")

if __name__ == "__main__":
    run_render()
