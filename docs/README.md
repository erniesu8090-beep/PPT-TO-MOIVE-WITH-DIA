# 🎬 簡報影片 AI 製作工坊 (Slide to Video Generator)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/Node.js-18+-green.svg)](https://nodejs.org/)

**簡報影片 AI 製作工坊** 是一款輕量、高效且功能齊全的自動化簡報影片生成工具。只需輸入 PDF 簡報與語音腳本，即可快速合成包含高清簡報畫面、高品質語音旁白（支援雙人對話）、動態字幕的高質感影片。

---

## 🌟 核心特色 (Key Features)

- 📄 **PDF 簡報自動轉圖**：採用 PyMuPDF 自動將上傳的 PDF 投影片轉換為高清 1080p/2K 圖像。
- 🎙️ **Edge-TTS 強大配音**：整合免費、無次數限制且發音自然的 Microsoft Edge 語音合成引擎，支援語速與音高微調。
- 🗣️ **雙人對話式簡報 (Host & Guest)**：支援 `A:` (主持人) 與 `B:` (專家) 發言標籤，一鍵輕鬆打造熱絡對談式的簡報影片。
- ⏱️ **自動字幕切分與時間軸對齊**：依據標點符號自動分割句構，精確計算語音時長並高亮顯示動態字幕。
- 🖥️ **直覺式 Web GUI 介面**：提供極致流暢的網頁介面，支援即時語音試聽、對白編輯與預覽。
- 🎥 **高清影片錄製與合成**：基於 Playwright 自動化瀏覽器畫格擷取與 FFmpeg 音視訊高畫質合成（1080p / 60fps）。

---

## 🛠️ 系統需求 (Prerequisites)

在開始安裝前，請確保您的系統已安裝以下軟體環境：

1. **Python 3.9+** (建議 Python 3.10 以上)
2. **Node.js 18+** (供 Playwright 視訊錄製組件使用)
3. **FFmpeg & FFprobe** (需設定至系統環境變數 PATH 中)
   - *Windows 建議可使用 `winget install Gyan.FFmpeg` 或由 [FFmpeg 官網](https://ffmpeg.org/) 下載。*

---

## 🚀 快速開始 (Quick Start)

### 1. 複製專案庫 (Clone Repository)

```bash
git clone https://github.com/your-username/slide-to-video-generator.git
cd slide-to-video-generator
```

### 2. 安裝 Python 依賴包

```bash
pip install -r requirements.txt
```

### 3. 安裝 Node.js 依賴與 Playwright 瀏覽器

```bash
npm install
npx playwright install chromium
```

### 4. 啟動應用程式

- **Windows 使用者**：可直接雙擊執行目錄下的 `啟動影片製作工坊.bat`
- **通用命令列**：
  ```bash
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

詳情請參閱 [雙人對話腳本格式指南.md](file:///c:/Users/ernie/Documents/%E5%BD%B1%E7%89%87%E8%A3%BD%E4%BD%9C%E5%B7%A5%E5%9D%8A_%E7%B2%BE%E7%B0%A1%E7%89%88/%E9%9B%99%E4%BA%BA%E5%B0%8D%E8%A9%B1%E8%85%B3%E6%9C%AC%E6%A0%BC%E5%BC%8F%E6%8C%87%E5%8D%97.md)。

---

## 📁 專案目錄架構 (Project Directory)

```text
.
├── app.py                     # 後端 HTTP 伺服器 & API 邏輯 (TTS, PDF 轉圖, 設定儲存)
├── index.html                 # 現代化前端 Web GUI 編輯器
├── render.py                  # 影片繪製與 FFmpeg 音視訊極速合成處理器
├── snapshot_slides.cjs        # Playwright 高速投影片畫格擷取腳本
├── package.json               # Node.js 專案設定檔 (Playwright 依賴)
├── requirements.txt           # Python 依賴包 (PyMuPDF, edge-tts)
├── 啟動影片製作工坊.bat          # Windows 一鍵啟動批次檔
├── 雙人對話腳本格式指南.md        # 雙人對話腳本撰寫詳細說明
├── assets/                    # 靜態資源、生成之圖片與配音音訊檔
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
