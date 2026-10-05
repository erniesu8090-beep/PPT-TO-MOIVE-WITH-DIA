// snapshot_slides.cjs: 高速擷取所有投影片高清畫格 (用於 FFmpeg 直接合成)
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

(async () => {
  const rendersDir = path.join(__dirname, 'renders');
  const framesDir = path.join(rendersDir, 'frames');
  if (!fs.existsSync(framesDir)) {
    fs.mkdirSync(framesDir, { recursive: true });
  }

  const browser = await chromium.launch({
    args: ['--autoplay-policy=no-user-gesture-required', '--mute-audio'],
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  // 載入具有 render=true&embed=false 的 index.html (含章節名牌、高畫質投影片、暗角)
  const fileUrl = 'file:///' + path.join(__dirname, 'index.html').replace(/\\/g, '/').replace(/ /g, '%20') + '?render=true&embed=false';
  console.log('Loading page for fast snapshots:', fileUrl);
  await page.goto(fileUrl);
  await page.waitForTimeout(1000);

  // 徹底停止動畫迴圈並移除字幕節點，確保畫格截圖 100% 純淨
  await page.evaluate(() => {
    if (typeof cancelAnimationFrame !== 'undefined' && typeof rafId !== 'undefined') {
      cancelAnimationFrame(rafId);
    }
    const sub = document.getElementById('subtitle');
    if (sub) sub.remove();
  });

  const count = await page.evaluate(() => typeof PAGES !== 'undefined' ? PAGES.length : 0);
  console.log(`Taking high-speed snapshots for ${count} slides...`);

  for (let i = 0; i < count; i++) {
    await page.evaluate((idx) => {
      document.querySelectorAll('.slide').forEach((el, j) => el.classList.toggle('active', j === idx));
    }, i);
    await page.waitForTimeout(50);
    const framePath = path.join(framesDir, `slide_${String(i).padStart(3, '0')}.png`);
    await page.screenshot({ path: framePath });
    console.log(`  ✓ 擷取第 ${i + 1}/${count} 頁畫格`);
  }

  await browser.close();
  console.log('All slide snapshots captured successfully!');
})();
