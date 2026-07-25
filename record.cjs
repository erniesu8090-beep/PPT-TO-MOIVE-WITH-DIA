// 用 Playwright 錄製 index.html，輸出 WebM 檔案（無音訊）
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

// 讀取 timings.json 動態計算總時長，若讀取失敗則使用預設值 412 秒
let recordDurationMs = 412 * 1000;
try {
  const timingsPath = path.join(__dirname, 'timings.json');
  if (fs.existsSync(timingsPath)) {
    const timings = JSON.parse(fs.readFileSync(timingsPath, 'utf8'));
    const totalDuration = timings.reduce((sum, item) => sum + (item.dur || 0), 0);
    if (totalDuration > 0) {
      recordDurationMs = totalDuration * 1000;
      console.log(`Successfully loaded timings.json. Dynamic duration: ${totalDuration}s`);
    }
  } else {
    console.log(`timings.json not found at ${timingsPath}, using default duration: 412s`);
  }
} catch (e) {
  console.warn('Warning: Could not read timings.json, using fallback duration 412s.', e);
}

const RECORD_DURATION_MS = recordDurationMs;
const EXTRA_BUFFER_MS = 1500; // 額外緩衝 1.5 秒

(async () => {
  const browser = await chromium.launch({
    args: ['--autoplay-policy=no-user-gesture-required', '--mute-audio'],
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    recordVideo: { 
      dir: path.join(__dirname, 'renders'), 
      size: { width: 1920, height: 1080 } 
    },
  });
  const page = await context.newPage();
  
  // 解析命令列參數 --embed=false
  let embedSubtitles = true;
  process.argv.forEach(val => {
    if (val === '--embed=false') {
      embedSubtitles = false;
    }
  });

  // 開啟具有 render=true 參數的本地 index.html
  const fileUrl = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/').replace(/ /g, '%20') + `?render=true&embed=${embedSubtitles}`;
  console.log('Loading page:', fileUrl);
  await page.goto(fileUrl);
  
  // 等待字型載入與初始化
  await page.waitForTimeout(1000);
  
  console.log(`Recording started. Duration: ${(RECORD_DURATION_MS + EXTRA_BUFFER_MS) / 1000}s...`);
  await page.waitForTimeout(RECORD_DURATION_MS + EXTRA_BUFFER_MS);
  
  await context.close();
  await browser.close();
  console.log('Recording completed.');
})();
