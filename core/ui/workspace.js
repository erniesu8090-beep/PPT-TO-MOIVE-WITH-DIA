/* Editor drafts and job controls. Drafts are scoped to the imported project. */
const Studio = {
    selected: null, drafts: {}, key: '', saving: false, storageOK: true,
    job: {active: false}, timer: null, terminal: false,
    init(data) {
        this.selected = null;
        this.key = 'video-workshop:drafts:' + data.project_info.project_id;
        try { this.drafts = JSON.parse(localStorage.getItem(this.key) || '{}') || {}; }
        catch (_) { this.drafts = {}; this.storageOK = false; }
        for (const [num, draft] of Object.entries(this.drafts)) {
            if (!draft || typeof draft.title !== 'string' || typeof draft.narration !== 'string') delete this.drafts[num];
        }
        document.body.classList.toggle('has-project', data.slides.length > 0);
    },
    draft(slide) { return this.drafts[slide.num] || slide; },
    persist() {
        try { localStorage.setItem(this.key, JSON.stringify(this.drafts)); this.storageOK = true; }
        catch (_) { this.storageOK = false; }
    },
    capture() {
        if (this.selected === null || !slidesData[this.selected]) return;
        const slide = slidesData[this.selected];
        const draft = {title: document.getElementById('slideTitle').value,
            narration: document.getElementById('slideNarration').value};
        if (draft.title === slide.title && draft.narration === slide.narration) delete this.drafts[slide.num];
        else this.drafts[slide.num] = draft;
        this.persist();
        this.indicator();
        this.paintStates();
    },
    indicator() {
        const count = Object.keys(this.drafts).length;
        document.getElementById('draftStatus').textContent = count
            ? `${count} 頁未儲存 · ${this.storageOK ? '草稿已保留在此瀏覽器' : '無法保留草稿，請立即儲存'}`
            : '已儲存 · Ctrl+S 儲存本頁';
    },
    status(slide) {
        if (this.drafts[slide.num]) return '● 未儲存草稿';
        return {silent: '○ 無口白', ready: '✓ 配音可沿用', pending: '◷ 待配音'}[slide.audio_status] || '◷ 待配音';
    },
    paintStates() {
        document.querySelectorAll('.slide-state').forEach((el, i) => {
            if (slidesData[i]) el.textContent = this.status(slidesData[i]);
        });
    },
    async save(slide) {
        const draft = this.drafts[slide.num];
        if (!draft) return;
        const payload = {num: slide.num, title: draft.title, narration: draft.narration};
        const response = await fetch('/api/save_slide', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        const result = await response.json();
        if (!response.ok || result.success === false) throw new Error(result.error || '儲存失敗');
        if (slide.narration !== payload.narration) slide.audio_status = 'pending';
        slide.title = payload.title;
        slide.narration = payload.narration;
        // Keep edits made while the request was running.
        const current = this.drafts[slide.num];
        if (current?.title === payload.title && current?.narration === payload.narration) delete this.drafts[slide.num];
        this.persist();
    },
    async saveCurrent() {
        this.capture();
        if (this.saving || this.selected === null) return false;
        this.saving = true;
        try {
            await this.save(slidesData[this.selected]);
            renderSidebar(); this.indicator();
            showToast('口白已儲存');
            await this.refreshStates();
            return true;
        } catch (error) { showToast(error.message); return false; }
        finally { this.saving = false; }
    },
    async saveAll() {
        this.capture();
        if (this.saving) { showToast('正在儲存，請稍候'); return false; }
        this.saving = true;
        try {
            for (const slide of slidesData) await this.save(slide);
            renderSidebar(); this.indicator();
            if (Object.keys(this.drafts).length) throw new Error('儲存期間有新的修改，請再按一次以儲存最新內容。');
            return true;
        } catch (error) { showToast(error.message); return false; }
        finally { this.saving = false; }
    },
    async refreshStates() {
        try {
            const response = await fetch('/api/slides');
            if (!response.ok) return;
            const data = await response.json();
            if (this.key !== 'video-workshop:drafts:' + data.project_info.project_id) return;
            data.slides.forEach(fresh => {
                const slide = slidesData.find(s => s.num === fresh.num);
                if (slide && slide.narration === fresh.narration) slide.audio_status = fresh.audio_status;
            });
            this.paintStates();
        } catch (_) { /* Keep current states when offline. */ }
    },
    renderEvent(data) {
        if (data.stage) document.getElementById('jobStatus').textContent = data.stage;
        if (data.log_url) {
            const link = document.getElementById('jobLog');
            link.href = data.log_url; link.hidden = false;
        }
        if (data.error || data.complete) {
            this.terminal = true;
            document.getElementById('jobStatus').textContent = data.complete ? '影片製作完成' : '製作失敗，請查看完整日誌';
            document.getElementById('btnPauseRender').hidden = true;
            if (data.video_name) {
                const link = document.getElementById('btnPlayVideo');
                link.href = '/MovieOutput/' + encodeURIComponent(data.video_name);
                link.style.display = 'block';
                link.textContent = data.clean_video_name ? '▶ 播放內嵌字幕版' : '▶ 播放已生成影片';
            }
            if (data.clean_video_name) {
                const link = document.getElementById('btnPlayCleanVideo');
                link.href = '/MovieOutput/' + encodeURIComponent(data.clean_video_name);
                link.style.display = 'block';
            }
        }
    },
    monitor() {
        this.terminal = false;
        this.job = {active: true};
        document.getElementById('jobStatus').textContent = '準備製作…';
        if (!this.timer) this.timer = setInterval(() => this.poll(), 1000);
    },
    async poll() {
        try {
            const response = await fetch('/api/render_status');
            if (!response.ok) throw new Error('無法讀取製作狀態');
            this.job = await response.json();
            if (this.job.log_url) this.renderEvent({log_url: this.job.log_url});
            if (!this.job.active && this.job.result) this.renderEvent(this.job.result);
            document.getElementById('progressBarContainer').classList.toggle('paused', !!this.job.paused);
            const button = document.getElementById('btnPauseRender');
            button.hidden = !this.job.active;
            button.textContent = this.job.requested ? '繼續製作' : '暫停製作';
            button.setAttribute('aria-pressed', String(!!this.job.requested));
            document.getElementById('btnRenderVideo').disabled = this.job.active || !slidesData.length;
            if (this.job.active) {
                if (!this.timer) this.timer = setInterval(() => this.poll(), 1000);
                document.getElementById('jobStatus').textContent = this.job.paused ? '已暫停 · 成果已保留，可繼續製作'
                    : this.job.requested ? '等待目前步驟完成後暫停 · ' + this.job.stage : this.job.stage;
            } else if (this.timer) {
                clearInterval(this.timer); this.timer = null;
                if (!this.terminal) document.getElementById('jobStatus').textContent = '製作已結束，請查看日誌或輸出資料夾';
                document.getElementById('progressBarContainer').classList.remove('indeterminate');
                await this.refreshStates();
                if (!slidesData.length) await loadSlides();
            }
        } catch (_) {
            document.getElementById('jobStatus').textContent = '無法連接製作服務，正在重試…';
            if (!this.timer) this.timer = setInterval(() => this.poll(), 1000);
        }
    },
    async control() {
        const button = document.getElementById('btnPauseRender');
        button.disabled = true;
        try {
            const response = await fetch('/api/render_control', {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: this.job.requested ? 'resume' : 'pause'})});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || '製作控制失敗');
            this.job = data;
            await this.poll();
        } catch (error) { showToast(error.message); }
        finally { button.disabled = false; }
    },
    disconnected() { this.monitor(); this.poll(); },
};

const editorTools = document.createElement('div');
editorTools.className = 'workspace-tools';
editorTools.innerHTML = '<button class="secondary" id="toggleSlides" aria-pressed="false">收合投影片</button><button class="secondary" id="toggleSettings" aria-pressed="false">收合設定</button><button class="secondary" id="focusEditor" aria-pressed="false">專注編輯</button>';
document.querySelector('.main-editor').prepend(editorTools);
const bannerToggle = document.createElement('button');
bannerToggle.className = 'secondary';
bannerToggle.id = 'toggleBanner';
bannerToggle.textContent = '收合美圖';
bannerToggle.setAttribute('aria-expanded', 'true');
document.querySelector('.welcome-banner').id = 'welcomeBanner';
bannerToggle.setAttribute('aria-controls', 'welcomeBanner');
bannerToggle.onclick = () => {
    const collapsed = document.body.classList.toggle('banner-collapsed');
    bannerToggle.textContent = collapsed ? '展開美圖' : '收合美圖';
    bannerToggle.setAttribute('aria-expanded', String(!collapsed));
};
editorTools.append(bannerToggle);
[['toggleSlides', 'hide-slides', '投影片'], ['toggleSettings', 'hide-settings', '設定']].forEach(([id, cls, label]) => {
    document.getElementById(id).onclick = event => {
        const hidden = document.body.classList.toggle(cls);
        event.currentTarget.textContent = `${hidden ? '展開' : '收合'}${label}`;
        event.currentTarget.setAttribute('aria-pressed', hidden);
    };
});
document.getElementById('focusEditor').onclick = event => {
    const focused = document.body.classList.toggle('focus-editor');
    event.currentTarget.textContent = focused ? '離開專注' : '專注編輯';
    event.currentTarget.setAttribute('aria-pressed', focused);
};
const draftStatus = document.createElement('p');
draftStatus.id = 'draftStatus'; draftStatus.className = 'draft-status'; draftStatus.setAttribute('role', 'status');
document.querySelector('.editor-footnote').after(draftStatus);
['slideTitle', 'slideNarration'].forEach(id => document.getElementById(id).addEventListener('input', () => Studio.capture()));
const actionBar = document.createElement('footer');
actionBar.className = 'production-bar';
actionBar.innerHTML = '<div class="production-status"><strong id="jobStatus" role="status">準備好，就開始製作</strong><span>暫停會在目前配音或合成步驟完成後生效</span></div><a id="jobLog" target="_blank" hidden>完整日誌 ↗</a><button id="btnPauseRender" class="secondary" hidden>暫停製作</button>';
document.querySelector('.container').after(actionBar);
actionBar.append(document.getElementById('btnRenderVideo'));
actionBar.append(document.getElementById('progressBarContainer'));
document.getElementById('btnPauseRender').onclick = () => Studio.control();
document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault(); Studio.saveCurrent();
    }
    if (event.key === 'Escape') {
        document.body.classList.remove('focus-editor');
        document.getElementById('focusEditor').textContent = '專注編輯';
        document.getElementById('focusEditor').setAttribute('aria-pressed', false);
    }
});
window.addEventListener('beforeunload', event => {
    Studio.capture();
    if (Object.keys(Studio.drafts).length) { event.preventDefault(); event.returnValue = ''; }
});
Studio.poll();
