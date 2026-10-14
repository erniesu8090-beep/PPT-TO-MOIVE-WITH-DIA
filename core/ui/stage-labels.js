(() => {
    const byId = id => document.getElementById(id);
    const frames = document.querySelectorAll('.preview-container, .subtitle-mini-frame, .subtitle-dialog-frame, .chapter-mini-frame');
    frames.forEach(frame => {
        const layer = document.createElement('div');
        layer.className = 'video-label-layer';
        layer.setAttribute('aria-hidden', 'true');
        layer.innerHTML = '<div class="video-chapter"><span class="video-page-num"></span><span class="video-page-title"></span></div>';
        frame.append(layer);
    });
    function paint() {
        const source = byId('slidePreview').getAttribute('src');
        const num = byId('slideNum').value;
        const title = byId('slideTitle').value;
        const color = byId('pageBgColor').value;
        const actualVisible = !byId('actualSubtitleFrame').hidden;
        const image = byId('chapterPreviewImage');
        image.hidden = !source;
        if (source && image.getAttribute('src') !== source) image.src = source;
        if (!source) image.removeAttribute('src');
        byId('chapterPreviewEmpty').hidden = !!source;
        byId('btnEnlargeChapter').disabled = !source;
        frames.forEach(frame => {
            const layer = frame.querySelector('.video-label-layer');
            // The FFmpeg-only frame deliberately excludes stage labels.
            layer.hidden = !source || !num || (frame.classList.contains('subtitle-dialog-frame') && actualVisible);
            layer.style.transform = `scale(${frame.clientWidth / 1920})`;
            layer.style.setProperty('--chapter-display', byId('showPageNumber').checked ? 'block' : 'none');
            layer.style.setProperty('--chapter-size', byId('pageFontSize').value + 'px');
            layer.style.setProperty('--chapter-color', color);
            layer.style.setProperty('--chapter-shadow', color + '66');
            layer.querySelector('.video-page-num').textContent = num.padStart(2, '0');
            layer.querySelector('.video-page-title').textContent = ` / ${slidesData.length} — ${title}`;
        });
    }
    window.refreshStageLabels = paint;
    ['showPageNumber', 'pageBgColor', 'pageFontSize', 'slideTitle'].forEach(id => {
        ['input', 'change'].forEach(event => byId(id).addEventListener(event, paint));
    });
    new MutationObserver(paint).observe(byId('slidePreview'), {attributes: true, attributeFilter: ['src', 'hidden']});
    new MutationObserver(paint).observe(byId('actualSubtitleFrame'), {attributes: true, attributeFilter: ['hidden']});
    const observer = new ResizeObserver(paint);
    frames.forEach(frame => observer.observe(frame));
    byId('btnEnlargeChapter').onclick = () => {
        // Reopen the combined preview, not a stale FFmpeg-only image.
        byId('actualSubtitleFrame').hidden = true;
        byId('subtitleActualStatus').textContent = '即時預覽包含頁碼、標題、字幕；設定需按儲存後才會用於製作影片。';
        byId('subtitlePreviewDialog').showModal();
        paint();
    };
    paint();
})();
