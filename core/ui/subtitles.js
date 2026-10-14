(() => {
    const byId = id => document.getElementById(id);
    const dialog = byId('subtitlePreviewDialog');
    let data = { cues: [], style: {}, estimated: true, dual: false };
    let index = 0;
    let timer, controller;
    let revision = 0;
    let frameRevision = 0;
    let actualPending = false;
    let narrationKey = null;

    function payload() {
        const speaker = role => ({
            name: byId(`spk${role}_name`).value,
            color: byId(`spk${role}_color`).value,
            voice: byId(`spk${role}_voice`).value,
            rate: byId(`spk${role}_rate`).value,
            pitch: byId(`spk${role}_pitch`).value,
        });
        return {
            num: Number(byId('slideNum').value),
            narration: byId('slideNarration').value,
            cue_index: index,
            voice_settings: {
                mode: byId('voiceMode').value, voice: byId('voiceSelect').value,
                rate: byId('rateSelect').value, pitch: byId('pitchSelect').value,
                speaker_a: speaker('A'), speaker_b: speaker('B'),
                silent_page_duration: Number(byId('silentPageDuration').value),
            },
            subtitle_settings: {
                font_name: byId('fontName').value, font_color: byId('fontColor').value,
                font_size: Number(byId('fontSize').value), bg_style: byId('bgStyle').value,
                bg_opacity: Number(byId('bgOpacity').value), bottom_pos: Number(byId('bottomPos').value),
                max_width: Number(byId('maxWidth').value),
            },
        };
    }

    function resize() {
        document.querySelectorAll('.subtitle-canvas').forEach(canvas => {
            canvas.style.transform = `scale(${canvas.parentElement.clientWidth / 1920})`;
        });
    }

    function clearActualFrame() {
        frameRevision++;
        byId('actualSubtitleFrame').hidden = true;
        byId('actualSubtitleFrame').removeAttribute('src');
        byId('subtitleActualStatus').textContent = '即時預覽包含頁碼、標題與字幕。FFmpeg 字幕畫格只檢查字幕，不含頁碼與標題；預覽不會儲存設定。';
    }

    function paint() {
        index = Math.max(0, Math.min(index, data.cues.length - 1));
        const cue = data.cues[index];
        const values = data.style;
        const current = payload();
        const color = cue && data.dual
            ? current.voice_settings[cue.speaker === 'B' ? 'speaker_b' : 'speaker_a'].color
            : values.font_color;
        document.querySelectorAll('.subtitle-rendered-text').forEach(text => {
            text.textContent = cue?.text || '';
            text.style.fontFamily = `"${values.font_name || 'Microsoft JhengHei'}", sans-serif`;
            text.style.fontSize = `${values.font_size || 32}px`;
            text.style.color = color || '#fff';
            text.style.bottom = `${values.bottom_pos || 50}px`;
            text.style.maxWidth = `${1920 - 2 * (values.safe_margin || 420)}px`;
            const box = values.bg_style === 'box';
            text.style.background = box ? `rgba(0,0,0,${values.bg_opacity ?? .75})` : 'transparent';
            text.style.padding = box ? '3px' : '0';
            text.style.webkitTextStroke = box ? '0' : '2px #000';
            text.style.paintOrder = 'stroke fill';
            text.style.textShadow = box ? 'none' : '2px 2px 2px #000';
        });
        const source = byId('slidePreview').getAttribute('src');
        document.querySelectorAll('.subtitle-slide-image').forEach(image => {
            image.hidden = !source;
            if (source && image.getAttribute('src') !== source) image.src = source;
            if (!source) image.removeAttribute('src');
        });
        byId('mainSubtitleCanvas').hidden = !byId('showSubtitleOverlay').checked || !cue;
        const position = cue ? `${index + 1} / ${data.cues.length} 句` : '本頁無字幕';
        byId('subtitleCuePosition').textContent = position;
        byId('subtitleDialogPosition').textContent = position;
        byId('subtitlePreviewText').textContent = cue?.text || '無口白頁只顯示投影片，不顯示字幕。';
        byId('subtitleTimingNote').textContent = cue
            ? `${cue.start.toFixed(1)}–${cue.end.toFixed(1)} 秒 · ${data.estimated ? '時間為估算，生成配音後更新' : '依配音時間碼'}`
            : '輸入本頁口白後，即可檢視字幕。';
        document.querySelectorAll('[data-preview-step]').forEach(button => {
            button.disabled = !cue || (button.dataset.previewStep === '-1' ? index === 0 : index === data.cues.length - 1);
        });
        byId('btnEnlargeSubtitle').disabled = !source;
        byId('btnActualSubtitleFrame').disabled = !source || actualPending;
        resize();
    }

    async function refresh() {
        const currentRevision = ++revision;
        controller?.abort();
        controller = new AbortController();
        clearActualFrame();
        if (!byId('slideNum').value) {
            data = { cues: [], style: {}, estimated: true, dual: false };
            paint();
            return;
        }
        try {
            const response = await fetch('/api/subtitle_preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload()), signal: controller.signal,
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '字幕預覽失敗');
            if (currentRevision !== revision) return;
            data = result;
            paint();
        } catch (error) {
            if (error.name !== 'AbortError' && currentRevision === revision) {
                byId('subtitleTimingNote').textContent = error.message;
            }
        }
    }

    window.refreshSubtitlePreview = () => {
        const key = `${byId('slideNum').value}:${byId('slideNarration').value}`;
        if (narrationKey !== key) index = 0;
        narrationKey = key;
        clearTimeout(timer);
        clearActualFrame();
        controller?.abort();
        revision++;
        timer = setTimeout(refresh, 180);
    };
    const observer = new ResizeObserver(resize);
    document.querySelectorAll('.subtitle-canvas').forEach(canvas => observer.observe(canvas.parentElement));
    document.querySelectorAll('[data-preview-step]').forEach(button => button.addEventListener('click', () => {
        index += Number(button.dataset.previewStep);
        clearActualFrame();
        paint();
    }));
    byId('showSubtitleOverlay').addEventListener('change', paint);
    byId('btnEnlargeSubtitle').addEventListener('click', () => {
        dialog.showModal();
        resize();
    });
    byId('btnCloseSubtitlePreview').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    byId('btnActualSubtitleFrame').addEventListener('click', async () => {
        const button = byId('btnActualSubtitleFrame');
        clearActualFrame();
        const requestRevision = frameRevision;
        actualPending = true;
        button.disabled = true;
        button.textContent = '正在渲染字幕畫格…';
        try {
            const response = await fetch('/api/subtitle_frame', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '字幕實際預覽失敗');
            if (requestRevision !== frameRevision) return;
            byId('actualSubtitleFrame').src = result.image;
            byId('actualSubtitleFrame').hidden = false;
            byId('subtitleActualStatus').textContent = '這張畫格由 FFmpeg 渲染，使用與影片輸出相同的字型、描邊與換行規則。不含頁碼與角色卡。';
        } catch (error) {
            byId('subtitleActualStatus').textContent = error.message;
        } finally {
            actualPending = false;
            button.textContent = '檢視 FFmpeg 字幕畫格';
            paint();
        }
    });
    ['voiceMode', 'voiceSelect', 'rateSelect', 'pitchSelect', 'silentPageDuration',
     'spkA_name', 'spkB_name', 'spkA_color', 'spkB_color', 'spkA_voice', 'spkB_voice',
     'spkA_rate', 'spkB_rate', 'spkA_pitch', 'spkB_pitch'].forEach(id => {
        byId(id).addEventListener('input', window.refreshSubtitlePreview);
        byId(id).addEventListener('change', window.refreshSubtitlePreview);
    });
    byId('slidePreview').addEventListener('load', window.refreshSubtitlePreview);
    byId('btnTestTts').addEventListener('click', () => { index = 0; });
    paint();
})();
