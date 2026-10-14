# 🎬 簡報影片 AI 製作工坊 (Slide to Video Generator)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-18+-green.svg)](https://nodejs.org/)

**簡報影片 AI 製作工坊** 是一款本機影片製作工具。輸入 PDF 簡報與語音腳本後，可合成簡報畫面、語音旁白（支援雙人對話）與字幕。另提供 Podcast 錄音室影片合成流程。

---

## 🌟 核心特色 (Key Features)

- 📄 **PDF 簡報自動轉圖**：採用 PyMuPDF 自動將上傳的 PDF 投影片轉換為高清 1080p/2K 圖像。
- 🎙️ **Edge-TTS 強大配音**：整合免費、無次數限制且發音自然的 Microsoft Edge 語音合成引擎，支援語速與音高微調。
- 🗣️ **雙人對話式簡報 (Host & Guest)**：支援 `A:` (主持人) 與 `B:` (專家) 發言標籤，一鍵輕鬆打造熱絡對談式的簡報影片。
- ⏱️ **自動字幕切分與時間軸對齊**：依據標點符號自動分割句構，精確計算語音時長並高亮顯示動態字幕。
- 🖥️ **直覺式 Web GUI 介面**：提供極致流暢的網頁介面，支援即時語音試聽、對白編輯與預覽。
- 🎥 **高清影片錄製與合成**：使用 Playwright 擷取畫格，再由 FFmpeg 合成影片；簡報流程輸出 30fps，Podcast 流程輸出 25fps。

---

## 🛠️ 系統需求 (Prerequisites)

在開始安裝前，請確保您的系統已安裝以下軟體環境：

1. **Python 3.9+** (建議 Python 3.10 以上)
2. **Node.js 18+** (供簡報流程的 Playwright 投影片擷取使用)
3. **FFmpeg & FFprobe** (需設定至系統環境變數 PATH 中)
   - *Windows 建議可使用 `winget install Gyan.FFmpeg` 或由 [FFmpeg 官網](https://ffmpeg.org/) 下載。*

---

## 🚀 快速開始 (Quick Start)

### 1. 安裝 Python 依賴包

```bash
python -m pip install -r core/requirements.txt
```

若配音出現 HTTP 403，請將 Edge-TTS 更新至 7.3.1 以上（支援新版 v2 驗證），更新後關閉並重新啟動工坊。Windows 使用專案虛擬環境時，在專案根目錄執行：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -r core/requirements.txt
```

### 2. 安裝簡報流程的 Node.js 依賴與 Chromium

```bash
cd core
npm install
npx playwright install chromium
cd ..
```

Python 依賴清單包含 Podcast 流程所需的 Playwright。首次使用該流程前，還需執行 `python -m playwright install chromium` 安裝其 Chromium 瀏覽器。

### 3. 啟動應用程式

- **Windows 使用者**：可直接雙擊執行目錄下的 `啟動影片製作工坊.bat`
- **通用命令列**：
  ```bash
  cd core
  python app.py
  ```

啟動後，請開啟瀏覽器訪問：**`http://localhost:8000`**

---

## 📖 使用指南 (Usage Guide)

1. **上傳簡報 (Upload PDF)**：在 Web 介面頂部點擊上傳您的 PDF 簡報檔。
2. **撰寫與編輯對白 (Edit Narration)**：
   - 針對每一頁投影片輸入旁白文字。
   - 欲製作雙人對話，可在文字開頭加入角色標籤（例如 `A: 歡迎大家` 與 `B: 大家好`），或點擊編輯器上方的 `+ A` / `+ B` 按鈕。
3. **設定語音與音色 (Voice Settings)**：
   - 選擇角色 A 與角色 B 的配音員（支援多種中文、英文音色）、調整語速與音高。
4. **生成語音與預覽 (Generate & Preview)**：
   - 點擊「合成語音」，系統將自動呼叫 Edge-TTS 產生音訊檔案並對齊時間軸。可在網頁上進行即時撥放試聽。
5. **匯出影片 (Render Video)**：
   - 點擊「生成影片」，系統將啟動背景錄製與 FFmpeg 合成，完成後影片將儲存於 `MovieOutput/` 目錄。

---

## 🗣️ 雙人對話腳本格式規範

寫腳本時可靈活運用多種發言標籤：

| 角色類型 | 支援標籤語法 | 預設說明 |
| :--- | :--- | :--- |
| **角色 A** | `A:`, `A：`, `[A]`, `主持人:`, `Host:`, `角色A:` | 主講人 / 主持人 |
| **角色 B** | `B:`, `B：`, `[B]`, `專家:`, `Guest:`, `角色B:` | 對談者 / 專家來賓 |

詳情請參閱 [雙人對話腳本格式指南.md](雙人對話腳本格式指南.md)。

---

## 🎙️ NotebookLM / 雙人 Podcast 轉虛擬錄音室影片

本專案可將 Google NotebookLM 或 Gemini 生成的雙人 Podcast 音訊檔，配合預先準備的章節、字幕和概念圖，合成包含**「兩側主持人偶向內注視 + 中央概念插圖 + 高對比雙色字幕」**的高清節目影片。音訊分析、逐字稿與概念圖目前需由 AI Agent 或人工先行準備。

- **音檔放置**：將音檔放入 `sound_source/` 資料夾。
- **影片合成流程**：轉換字幕、擷取舞台畫格，再以 FFmpeg 合成 MP4。

詳情請參閱 [雙人Podcast虛擬錄音室影片製作指南.md](雙人Podcast虛擬錄音室影片製作指南.md)。


---

## 📁 專案目錄架構 (Project Directory)

```text
.
├── core/
│   ├── app.py                 # Web GUI 後端 (TTS、PDF 轉圖、設定與輸出)
│   ├── build_index.py         # 產生投影片影片預覽頁
│   ├── render.py              # FFmpeg 音訊、畫面與字幕合成
│   ├── snapshot_slides.cjs    # Node.js Playwright 投影片擷取
│   ├── package.json           # 簡報流程的 Node.js 依賴
│   └── requirements.txt       # Python 依賴
├── podcast_studio/
│   └── pipeline.py            # Podcast 舞台畫格與影片合成
├── 啟動影片製作工坊.bat          # Windows 一鍵啟動批次檔
├── docs/                      # 操作、腳本與字幕指南
├── core/assets/               # PDF 投影片圖像與生成的配音音訊
├── sound_source/              # Podcast 原始音訊
└── MovieOutput/               # 最終導出的簡報影片成果目錄
```

---

## ❓ 常見問題 (FAQ)

<details>
<summary><b>1. 執行時提示 <code>ffprobe / ffmpeg is not recognized</code>？</b></summary>
<p>這代表您的系統尚未安裝 FFmpeg，或是未將其執行檔路徑加入系統的 PATH 環境變數中。請下載 FFmpeg 並配置環境變數後重新開啟終端機。</p>
</details>

<details>
<summary><b>2. 配音合成失敗或提示網路連線問題？</b></summary>
<p>本專案使用免費的 Edge-TTS 服務，需連線至微軟語音伺服器。請確保您的電腦已連接網際網路，且未被防火牆阻擋連線。</p>
</details>

---

## 📄 開源授權 (License)

本專案採用 [MIT License](LICENSE) 授權釋出，歡迎自由修改與商業/個人使用。

## 配音快取、靜音頁與字幕預覽

- 製作影片時，未改動口白和聲音設定的頁面會沿用配音；勾選「重新生成全部配音（忽略快取）」可強制重製。
- 無口白頁會顯示靜音投影片，預設停留 5 秒，可在語音設定調整。
- 字幕設定中的預覽使用目前投影片與本頁口白，可放大、切換句子，並用「檢視 FFmpeg 實際輸出」產生單張真實字幕畫格，不必先製作完整影片。
- 詳細修改與驗證範圍見 [程式檢查與改善建議](程式檢查與改善建議.md)。

## 工作區與製作控制（2026-10-14）

- 口白與標題會保留為此瀏覽器的專案草稿；切頁、重新整理後可繼續編輯。草稿尚未寫入專案時，會顯示「未儲存」。按「儲存口白」或 Ctrl+S（Mac 為 Command+S）儲存本頁；開始製作及匯入新專案前會先儲存所有頁面草稿。瀏覽器禁止儲存時會明確提示。
- 預設保留生成的美圖橫幅，可用「收合美圖／展開美圖」切換精簡專案列。可收合投影片、收合設定，或切換「專注編輯」；Esc 離開專注模式。介面說明與按鈕以 14px 為主，標籤 15px、編輯輸入 16px。
- 頁面清單顯示未儲存草稿、無口白、待配音或配音可沿用。切頁沿用既有縮圖與圖片快取。
- 底部固定操作列提供製作與暫停控制。按「暫停製作」後，正在進行的配音或 FFmpeg 步驟會先完成，接著在下一個安全步驟前暫停。等待期間按「繼續製作」可撤回暫停要求；若已是最後一步，影片可能直接完成。
- 暫停期間保留作業鎖與已生成成果，不能同時匯入或儲存專案設定。重新整理頁面後仍可繼續正在執行的作業；這不是關閉伺服器或重新開機後的斷點續作。
- 進度顯示實際製作階段與已完成配音頁數，不再以日誌關鍵字推算百分比。完整日誌保存在 `core/renders/logs/`，可從操作列開啟；畫面控制台只保留最近一段，避免長時間製作時拖慢介面。
- 匯入會先在暫存目錄完成圖片、口白及時序驗證。一般匯入／替換錯誤會復原原專案；成功替換前的檔案保留在 `renders/project-backups/<時間與識別碼>/`。備份含圖片、配音、口白、時序與專案資訊；復原時需關閉工坊，再將這些項目複製回 `core/`。尚未提供一鍵復原或斷電交易保證。

## 畫面與標籤即時預覽

「畫面與標籤」現在提供小預覽與「放大預覽」。頁碼／標題開關、頁碼底色與字級，會立即同步至主畫面和放大視窗；編輯本頁標題、切換投影片也會同步更新。放大視窗可同時檢查字幕。

預覽不會自動儲存這些設定，需按「套用並儲存畫面與標籤」後才套用於製作。標籤使用與影片畫格共用的 `core/ui/stage-labels.css`，以 1920 × 1080 等比例顯示。「檢視 FFmpeg 字幕畫格」仍是字幕專用的實際畫格檢查，不包含頁碼／版本標籤，切到該畫格時會隱藏預覽標籤，避免混淆。

程式版本僅顯示於工坊右上角，從 `core/package.json` 讀取。已移除影片版本標籤設定，新的影片輸出不再附加版本標籤。
