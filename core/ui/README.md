# 工作區介面資源

`studio.css` 提供預設的暖灰／深綠暗色介面，是本機 Web GUI 的樣式，透過 `app.py` 的 `HTML_TEMPLATE` 載入。影片畫格仍由 `build_index.py` 產生的 `index.html` 負責，介面樣式不會套用到輸出影片。

`studio-illustration.png` 是以 Codex 內建 ImageGen 生成的裝飾橫幅，儲存在專案內，執行時不需外部圖片服務。生成日期：2026-10-14。只用於編輯工作區，不加入使用者的影片。

生成提示詞：

> Use case: stylized-concept. Generate a premium cinematic 3D animated-film-style illustration for a local video creation app welcome banner, landscape 16:9. A tiny thoughtfully designed film editing and podcast studio still life: warm ivory sculpted tabletop, a tactile graphite movie camera, soft coral clapperboard with blank stripes and no lettering, a sage green microphone and headphones, small floating rounded frosted glass frames showing abstract sunrise landscapes with no writing. Subject grouping concentrated in right half, left half quiet soft warm cream negative space for HTML heading. Sophisticated restrained palette: off-white, charcoal, dusty sage, apricot coral. Soft afternoon sunlight, ambient occlusion, beautiful material detail, playful but refined proportions, high-end Pixar-like 3D rendering. Absolutely no text, no numbers, no logos, no watermark. No UI screenshot, no interface elements. This image will be used as a decorative 16:9 banner cropped horizontally in a web editor.


## 字幕預覽

`subtitles.js` 將本頁口白送到唯讀的 `/api/subtitle_preview`，使用和影片相同的切句、18 字折行及角色標籤規則。在主預覽、縮圖與放大視窗中，文字位置都以 1920 × 1080 座標等比例縮放。

「檢視 FFmpeg 實際輸出」呼叫 `/api/subtitle_frame`，以目前設定和選中的字幕句子渲染單張 PNG；ASS 樣式與 `render.py` 共用 `subtitle_styles.py`。畫格不含頁碼／角色卡等舞台裝飾，也不會儲存設定或重新製作配音。未生成配音時，介面會明確標示時間碼為估算。
