import json
from pathlib import Path

def hex_to_rgb(hex_str):
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 3:
        hex_str = ''.join([c*2 for c in hex_str])
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def build():
    # Load timings
    with open("timings_dynamic.json", "r", encoding="utf-8") as f:
        timings = json.load(f)
        
    # Load voice settings
    voice_settings = {
        "mode": "single",
        "speaker_a": {"name": "主持人 A", "color": "#06b6d4"},
        "speaker_b": {"name": "對談者 B", "color": "#8b5cf6"}
    }
    voice_path = Path("voice_settings.json")
    if voice_path.exists():
        try:
            with open(voice_path, "r", encoding="utf-8") as f_vs:
                voice_settings.update(json.load(f_vs))
        except Exception as e:
            print(f"Warning: Failed to load voice_settings.json: {e}")

    # Load subtitle settings if exists, else use defaults
    subtitle_settings = {
        "font_size": 32,
        "bg_opacity": 0.75,
        "max_width": 85,
        "bottom_pos": 50,
        "style_preset": "cc"
    }
    settings_path = Path("subtitle_settings.json")
    if settings_path.exists():
        try:
            with open(settings_path, "r", encoding="utf-8") as f_sub:
                subtitle_settings.update(json.load(f_sub))
        except Exception as e:
            print(f"Warning: Failed to load subtitle_settings.json: {e}")

    # Load chapter settings
    chapter_settings = {
        "show_page_number": True,
        "bg_color": "#0E7C7B",
        "font_size": 24,
        "show_version_badge": True,
        "version_text": "SLIDE EDITION v2.4.0 (2026/07/25)"
    }
    chap_settings_path = Path("chapter_settings.json")
    if chap_settings_path.exists():
        try:
            with open(chap_settings_path, "r", encoding="utf-8") as f_chap:
                chapter_settings.update(json.load(f_chap))
        except Exception as e:
            print(f"Warning: Failed to load chapter_settings.json: {e}")

    show_page_number = chapter_settings.get("show_page_number", True)
    chap_bg_color = chapter_settings.get("bg_color", "#0E7C7B")
    chap_font_size = chapter_settings.get("font_size", 24)
    show_version_badge = chapter_settings.get("show_version_badge", True)
    version_text = chapter_settings.get("version_text", "SLIDE EDITION v2.4.0 (2026/07/25)")
    r, g, b = hex_to_rgb(chap_bg_color)

    if not show_page_number:
        chapter_css = "  .chapter { display: none !important; }"
    else:
        chapter_css = f"""  .chapter {{
    position: absolute; top: 28px; left: 48px;
    font-family: 'GenSeki TW', sans-serif; font-weight: 700;
    font-size: {chap_font_size}px; letter-spacing: 0.15em; color: #fff;
    text-shadow: 0 2px 8px rgba(0,0,0,0.6);
    z-index: 10;
  }}
  .chapter .num {{
    color: #fff; background: {chap_bg_color};
    padding: 4px 14px; border-radius: 6px; font-weight: 900;
    margin-right: 12px;
    box-shadow: 0 2px 10px rgba({r},{g},{b},0.4);
  }}"""

    if not show_version_badge:
        version_css = "  .version-badge { display: none !important; }"
    else:
        version_css = """  .version-badge {
    position: absolute; top: 28px; right: 48px; z-index: 10;
    display: inline-flex; align-items: center; gap: 10px;
    background: rgba(18, 24, 33, 0.75);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 9999px;
    padding: 8px 22px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  }
  .version-badge .version-dot {
    width: 9px; height: 9px; border-radius: 50%;
    background-color: #4e937a;
    box-shadow: 0 0 8px rgba(78, 147, 122, 0.6);
    flex-shrink: 0;
  }
  .version-badge .version-text {
    font-family: 'GenSeki TW', system-ui, -apple-system, sans-serif;
    font-weight: 700; font-size: 14px; letter-spacing: 0.08em;
    color: #ffffff; text-transform: uppercase; white-space: nowrap;
  }"""

    # Build subtitle style CSS based on settings
    font_size = subtitle_settings.get("font_size", 32)
    bg_opacity = subtitle_settings.get("bg_opacity", 0.75)
    max_width = subtitle_settings.get("max_width", 85)
    bottom_pos = subtitle_settings.get("bottom_pos", 50)
    style_preset = subtitle_settings.get("style_preset", "cc")
    embed_subtitles = subtitle_settings.get("embed", True)
    
    font_name = subtitle_settings.get("font_name", "Microsoft JhengHei")
    font_color = subtitle_settings.get("font_color", "white")
    bg_style = subtitle_settings.get("bg_style", "outline")

    color_map = {
        "white": "#ffffff",
        "yellow": "#ffff00",
        "cyan": "#00ffff",
        "green": "#00ff00",
        "gray": "#cccccc"
    }
    color_val = color_map.get(font_color, "#ffffff")

    if bg_style == "box":
        bg_css = f"background: rgba(8, 8, 8, {bg_opacity}); border-radius: 6px; text-shadow: 0 1px 3px rgba(0,0,0,0.85);"
        padding_css = "padding: 10px 20px;"
    elif bg_style == "none":
        bg_css = "background: transparent; text-shadow: none;"
        padding_css = "padding: 10px 0;"
    else: # "outline" (描邊 + 陰影)
        bg_css = "background: transparent; text-shadow: -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 2px 4px rgba(0,0,0,0.85);"
        padding_css = "padding: 10px 20px;"

    if style_preset == "banner":
        sub_css = f"""    position: absolute; left: 0; bottom: 0; width: 100%;
    padding: 15px 40px; border-radius: 0;
    background: rgba(8, 8, 8, {bg_opacity}); color: {color_val};
    font-family: '{font_name}', 'GenSeki TW', sans-serif; font-weight: 700;
    font-size: {font_size}px; line-height: 1.4; text-align: center;
    text-shadow: 0 1px 3px rgba(0,0,0,0.85);
    opacity: 0; transition: opacity 0.2s ease;
    z-index: 100;
    display: block;
    white-space: pre-wrap;"""
    else:
        sub_css = f"""    position: absolute; left: 50%; bottom: {bottom_pos}px; transform: translateX(-50%);
    {padding_css}
    {bg_css} color: {color_val};
    font-family: '{font_name}', 'GenSeki TW', sans-serif; font-weight: 700;
    font-size: {font_size}px; line-height: 1.4; text-align: center;
    opacity: 0; transition: opacity 0.2s ease;
    z-index: 100;
    display: inline-block;
    width: auto;
    max-width: {max_width}%;
    white-space: pre-wrap;"""

    # Extract speaker configurations
    spk_a = voice_settings.get("speaker_a", {})
    spk_b = voice_settings.get("speaker_b", {})
    spk_a_name = spk_a.get("name", "主持人 A")
    spk_a_color = spk_a.get("color", "#06b6d4")
    spk_b_name = spk_b.get("name", "對談者 B")
    spk_b_color = spk_b.get("color", "#8b5cf6")

    # Serialize timings to JS string
    pages_js = []
    for p in timings:
        clauses_list = []
        for c in p['clauses']:
            escaped_text = c['text'].replace('"', '\\"')
            spk = c.get('speaker', 'A')
            spk_name = c.get('speaker_name', spk_a_name if spk == 'A' else spk_b_name).replace('"', '\\"')
            clauses_list.append(f'      {{ text: "{escaped_text}", start: {c["start"]}, end: {c["end"]}, speaker: "{spk}", speaker_name: "{spk_name}" }}')
            
        clauses_str = ",\n".join(clauses_list)
        pages_js.append(f"""  {{
    i: {p['i']},
    dur: {p['dur']},
    title: "{p['title']}",
    audio_dur: {p['audio_dur']},
    clauses: [
{clauses_str}
    ]
  }}""")
        
    js_pages_array = "const PAGES = [\n" + ",\n".join(pages_js) + "\n];"
    js_dual_config = f"const DUAL_CONFIG = {json.dumps(voice_settings, ensure_ascii=False)};"

    # HTML template
    html_content = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<title>雙人對話式動態簡報影片播放器</title>
<style>
  @font-face {{
    font-family: 'GenSeki TW';
    src: url('assets/fonts/GenSekiGothic2TW-M.otf') format('opentype');
    font-weight: 500;
    font-display: block;
  }}
  @font-face {{
    font-family: 'GenSeki TW';
    src: url('assets/fonts/GenSekiGothic2TW-B.otf') format('opentype');
    font-weight: 700;
    font-display: block;
  }}
  @font-face {{
    font-family: 'GenSeki TW';
    src: url('assets/fonts/GenSekiGothic2TW-H.otf') format('opentype');
    font-weight: 900;
    font-display: block;
  }}

  :root {{
    --ink: #1A1A1A;
    --paper: #FAF7EE;
    --teal: #0E7C7B;
    --gold: #C8941F;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 100%; height: 100%; background: var(--ink); overflow: hidden;
    font-family: 'GenSeki TW', sans-serif; color: #fff;
  }}

  .stage {{
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
  }}

  .slide {{
    position: absolute; inset: 0; opacity: 0;
    transition: opacity 0.8s ease;
    display: flex; align-items: center; justify-content: center;
  }}
  .slide.active {{ opacity: 1; }}

  /* Ken Burns 緩慢放大效果 */
  .slide .img {{
    position: absolute; inset: 0; background-size: cover; background-position: center;
    transform: scale(1.0);
    transition: transform var(--dur, 30s) linear;
  }}
  .slide.active .img {{ transform: scale(1.06); }}

  .slide .vignette {{
    position: absolute; inset: 0;
    background: radial-gradient(ellipse at center, rgba(0,0,0,0) 60%, rgba(0,0,0,0.3) 100%);
    pointer-events: none;
  }}

{chapter_css}
{version_css}

  /* 旁白字幕條 */
  .subtitle {{
{sub_css}
  }}
  .subtitle.show {{ opacity: 1; }}

  /* 雙人對話名牌區 */
  .dialogue-stage {{
    position: fixed; bottom: 105px; left: 48px; right: 48px;
    display: flex; justify-content: space-between; align-items: center;
    pointer-events: none; z-index: 90;
    transition: opacity 0.3s ease;
  }}
  .dialogue-stage.hidden {{ display: none !important; }}

  .speaker-card {{
    display: flex; align-items: center; gap: 12px;
    background: rgba(13, 11, 24, 0.75); backdrop-filter: blur(12px);
    border: 2px solid rgba(255, 255, 255, 0.12); border-radius: 40px;
    padding: 8px 20px 8px 10px;
    transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
    opacity: 0.45; transform: scale(0.92);
  }}
  .speaker-card.speaking {{
    opacity: 1; transform: scale(1.06);
    background: rgba(22, 19, 43, 0.92);
  }}
  .speaker-card.card-a.speaking {{
    border-color: {spk_a_color};
    box-shadow: 0 0 25px {spk_a_color}aa, inset 0 0 10px {spk_a_color}44;
  }}
  .speaker-card.card-b.speaking {{
    border-color: {spk_b_color};
    box-shadow: 0 0 25px {spk_b_color}aa, inset 0 0 10px {spk_b_color}44;
  }}

  .speaker-card .avatar {{
    width: 42px; height: 42px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-weight: 900; font-size: 16px; color: #fff;
    flex-shrink: 0; position: relative;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }}
  .speaker-card.speaking .avatar::after {{
    content: ''; position: absolute; inset: -4px; border-radius: 50%;
    border: 2px solid currentColor; animation: pulse-ring 1.5s infinite;
  }}
  @keyframes pulse-ring {{
    0% {{ transform: scale(1); opacity: 0.8; }}
    100% {{ transform: scale(1.35); opacity: 0; }}
  }}

  .speaker-info {{ display: flex; flex-direction: column; }}
  .speaker-role {{ font-size: 10px; font-weight: 800; color: rgba(255,255,255,0.5); letter-spacing: 1px; }}
  .speaker-name {{ font-size: 15px; font-weight: 700; color: #fff; }}

  /* 開場畫面 */
  .start-screen {{
    position: fixed; inset: 0; background: var(--ink); color: var(--paper);
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    z-index: 999; cursor: pointer; gap: 40px;
  }}
  .start-screen .title {{
    font-family: 'GenSeki TW', sans-serif; font-weight: 900;
    font-size: 100px; letter-spacing: 0.08em; line-height: 1.2;
    text-align: center;
    color: var(--paper);
  }}
  .start-screen .sub {{
    font-size: 26px; color: rgba(250,247,238,0.65); font-weight: 500;
    letter-spacing: 0.15em;
  }}
  .start-screen .play {{
    width: 96px; height: 96px; border: 3px solid var(--gold);
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    color: var(--gold); transition: all 0.3s ease;
  }}
  .start-screen:hover .play {{ background: var(--gold); color: var(--ink); transform: scale(1.05); }}
  .start-screen.hidden {{ display: none; }}

  /* 進度條 */
  .progress {{
    position: absolute; left: 0; bottom: 0; height: 6px; background: var(--teal);
    width: 0%; transition: width 0.2s linear;
    z-index: 200;
  }}
  body.render-no-embed .subtitle {{ display: none !important; }}
</style>
</head>
<body>

<div class="start-screen" id="startScreen">
  <div class="title">雙人對話簡報影音工坊</div>
  <div class="play">
    <svg width="28" height="32" viewBox="0 0 24 28"><path d="M2 2 L22 14 L2 26 Z" fill="currentColor"/></svg>
  </div>
  <div class="sub">雙人互動語音簡報 · 點擊開始</div>
</div>

<div class="stage" id="stage"></div>

<!-- 雙人對話 Avatar 名牌區 -->
<div class="dialogue-stage {"hidden" if voice_settings.get("mode") != "dual" else ""}" id="dialogueStage">
  <div class="speaker-card card-a" id="speakerCardA">
    <div class="avatar" style="background: linear-gradient(135deg, {spk_a_color}, #0284c7);">A</div>
    <div class="speaker-info">
      <div class="speaker-role">HOST</div>
      <div class="speaker-name">{spk_a_name}</div>
    </div>
  </div>
  <div class="speaker-card card-b" id="speakerCardB">
    <div class="avatar" style="background: linear-gradient(135deg, {spk_b_color}, #c084fc);">B</div>
    <div class="speaker-info">
      <div class="speaker-role">GUEST</div>
      <div class="speaker-name">{spk_b_name}</div>
    </div>
  </div>
</div>

<div class="progress" id="progress"></div>
<audio id="audio" preload="auto"></audio>

<script>
{js_pages_array}
{js_dual_config}
const EMBED_SUBTITLES = {str(embed_subtitles).lower()};

const TOTAL_DUR = PAGES.reduce((sum, p) => sum + p.dur, 0);
const stage = document.getElementById('stage');

// 動態生成 Slide HTML
PAGES.forEach(p => {{
  const slideEl = document.createElement('div');
  slideEl.className = 'slide';
  slideEl.dataset.page = p.i;
  slideEl.style.setProperty('--dur', p.dur + 's');
  slideEl.innerHTML = `
    <div class="img" style="background-image:url('assets/images/page-${{String(p.i).padStart(2, '0')}}.png')"></div>
    <div class="vignette"></div>
    <div class="chapter"><span class="num">${{String(p.i).padStart(2, '0')}}</span> / ${{PAGES.length}} — ${{p.title}}</div>
    <div class="version-badge"><span class="version-dot"></span><span class="version-text">{version_text}</span></div>
  `;
  stage.appendChild(slideEl);
}});

// 建立共用字幕條
const subtitleEl = document.createElement('div');
subtitleEl.className = 'subtitle';
subtitleEl.id = 'subtitle';
stage.appendChild(subtitleEl);

const slideEls = [...document.querySelectorAll('.slide')];
const audio = document.getElementById('audio');
const progress = document.getElementById('progress');
const startScreen = document.getElementById('startScreen');
const sub = document.getElementById('subtitle');
const dialogueStage = document.getElementById('dialogueStage');
const speakerCardA = document.getElementById('speakerCardA');
const speakerCardB = document.getElementById('speakerCardB');

let currentSlide = 0;
let totalElapsed = 0;
let slideStartTime = 0;
let rafId = null;

const urlParams = new URLSearchParams(window.location.search);
const isRenderMode = urlParams.get('render') === 'true';

function showSlide(i) {{
  slideEls.forEach((el, idx) => el.classList.toggle('active', idx === i));
  const p = PAGES[i];
  
  sub.innerHTML = "";
  sub.classList.remove('show');
  if (speakerCardA && speakerCardB) {{
    speakerCardA.classList.remove('speaking');
    speakerCardB.classList.remove('speaking');
  }}

  if (!isRenderMode) {{
    audio.src = `assets/narration/page-${{String(p.i).padStart(2, '0')}}.mp3`;
    audio.play().catch(()=>{{}});
  }}

  slideStartTime = performance.now();
}}

function tick() {{
  const now = performance.now();
  const elapsed = (now - slideStartTime) / 1000;
  const p = PAGES[currentSlide];
  const slideDur = p.dur;

  const totalProgress = (totalElapsed + elapsed) / TOTAL_DUR;
  progress.style.width = (Math.min(totalProgress, 1) * 100) + '%';

  const activeClause = p.clauses.find(c => elapsed >= c.start && elapsed < c.end);
  if (activeClause) {{
    if (sub.dataset.text !== activeClause.text) {{
      sub.dataset.text = activeClause.text;
      let spkPrefix = "";
      
      if (DUAL_CONFIG.mode === "dual" || (activeClause.speaker && activeClause.speaker !== 'A')) {{
        const isB = activeClause.speaker === "B";
        const spkName = activeClause.speaker_name || (isB ? (DUAL_CONFIG.speaker_b ? DUAL_CONFIG.speaker_b.name : '專家 B') : (DUAL_CONFIG.speaker_a ? DUAL_CONFIG.speaker_a.name : '主持人 A'));
        const spkColor = isB ? (DUAL_CONFIG.speaker_b ? DUAL_CONFIG.speaker_b.color : '#8b5cf6') : (DUAL_CONFIG.speaker_a ? DUAL_CONFIG.speaker_a.color : '#06b6d4');
        
        spkPrefix = `<span style="background:${{spkColor}}; color:#000; font-weight:900; padding:2px 8px; border-radius:4px; margin-right:8px; display:inline-block; vertical-align:middle; font-size:0.85em; box-shadow: 0 2px 6px rgba(0,0,0,0.4);">${{spkName}}</span>`;
        
        if (dialogueStage && speakerCardA && speakerCardB) {{
          dialogueStage.classList.remove('hidden');
          if (isB) {{
            speakerCardB.classList.add('speaking');
            speakerCardA.classList.remove('speaking');
          }} else {{
            speakerCardA.classList.add('speaking');
            speakerCardB.classList.remove('speaking');
          }}
        }}
        sub.innerHTML = `${{spkPrefix}}<span style="color:${{spkColor}}; text-shadow: -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 2px 8px rgba(0,0,0,0.9);">${{activeClause.text}}</span>`;
      }} else {{
        sub.innerHTML = activeClause.text;
      }}
    sub.classList.add('show');
  }} else {{
    sub.innerHTML = "";
    sub.classList.remove('show');
    sub.dataset.text = "";
    if (speakerCardA && speakerCardB) {{
      speakerCardA.classList.remove('speaking');
      speakerCardB.classList.remove('speaking');
    }}
  }}

  if (elapsed >= slideDur) {{
    totalElapsed += slideDur;
    currentSlide++;
    if (currentSlide >= PAGES.length) {{
      cancelAnimationFrame(rafId);
      sub.classList.remove('show');
      console.log('Playback complete');
      return;
    }}
    showSlide(currentSlide);
  }}
  rafId = requestAnimationFrame(tick);
}}

startScreen.addEventListener('click', () => {{
  startScreen.classList.add('hidden');
  showSlide(0);
  rafId = requestAnimationFrame(tick);
}});

if (isRenderMode) {{
  if (!EMBED_SUBTITLES) {{
    document.body.classList.add('render-no-embed');
  }}
  startScreen.classList.add('hidden');
  setTimeout(() => {{
    showSlide(0);
    rafId = requestAnimationFrame(tick);
  }}, 500);
}}
</script>

</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("index.html written successfully with dynamic timings and dual speaker support!")

if __name__ == "__main__":
    build()
