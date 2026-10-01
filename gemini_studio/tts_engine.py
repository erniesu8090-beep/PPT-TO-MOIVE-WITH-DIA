import os
import sys
import subprocess
import pathlib
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from google import genai
from google.genai import types

from usage_tracker import UsageTracker

def get_api_key():
    # Check current dir .env or parent dir .env
    candidates = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / ".env"
    ]
    for c in candidates:
        if c.exists():
            with open(c, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        val = line.strip().split("=", 1)[1].strip()
                        if val:
                            return val
    return os.environ.get("GEMINI_API_KEY", "")

def get_audio_duration(file_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(file_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if res.returncode != 0 or not res.stdout:
            return 5.0
        import json
        data = json.loads(res.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 5.0

class GeminiTTSEngine:
    def __init__(self, tracker: UsageTracker = None):
        self.tracker = tracker or UsageTracker()
        self.api_key = get_api_key()
        self.client = None
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                pass

    def reload_key(self, key=None):
        if key:
            self.api_key = key
        else:
            self.api_key = get_api_key()
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)

    def generate_speech(self, text: str, output_path: Path, voice_name: str = "Puck", model: str = "gemini-3.8-flash-tts", log_fn=None, style_prompt: str = None):
        def _log(msg):
            if log_fn:
                try:
                    log_fn(msg)
                except Exception:
                    pass
            try:
                print(msg)
            except Exception:
                pass

        if not self.api_key or not self.client:
            raise ValueError("GEMINI_API_KEY 未設定，請先填入金鑰！")

        if not self.tracker.can_request(1):
            raise RuntimeError(f"今日 Gemini API 免費呼叫額度已用完（每日上限 {self.tracker.daily_limit} 次）！")

        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("旁白文字為空，無法生成語音！")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_wav = output_path.with_suffix(".wav")

        used_model = model
        max_retries = 3
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                config_kwargs = {
                    "speech_config": types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_name
                            )
                        )
                    )
                }
                if style_prompt and style_prompt.strip():
                    config_kwargs["system_instruction"] = style_prompt.strip()

                response = self.client.models.generate_content(
                    model=used_model,
                    contents=cleaned_text,
                    config=types.GenerateContentConfig(**config_kwargs)
                )

                audio_bytes = None
                if response and response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if part.inline_data and part.inline_data.data:
                            audio_bytes = part.inline_data.data
                            break

                if not audio_bytes:
                    raise RuntimeError("Gemini TTS API 未回傳音訊資料！")

                with open(temp_wav, "wb") as f:
                    f.write(audio_bytes)

                # Convert to standard 192k MP3 via ffmpeg
                cmd = [
                    "ffmpeg", "-y", "-i", str(temp_wav),
                    "-c:a", "libmp3lame", "-b:a", "192k",
                    str(output_path)
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                if temp_wav.exists():
                    temp_wav.unlink(missing_ok=True)

                duration = get_audio_duration(output_path)
                if duration < 0.2:
                    raise RuntimeError("生成的音訊時長異常 (<0.2秒)，判定為生成不完全！")

                # Record usage
                self.tracker.record_request(
                    count=1,
                    chars=len(cleaned_text),
                    seconds=duration,
                    note=f"{used_model} ({voice_name})"
                )

                return {
                    "file": str(output_path),
                    "duration": round(duration, 2),
                    "chars": len(cleaned_text),
                    "voice": voice_name,
                    "model": used_model
                }

            except Exception as e:
                last_error = e
                err_str = str(e)
                if ("429" in err_str or "quota" in err_str.lower() or "exhausted" in err_str.lower()) and used_model != "gemini-3.8-flash-lite-tts":
                    _log(f"  ⚠️ {used_model} 免費額度耗盡或頻率超限 (429)，自動無縫切換為高吞吐備援模型: gemini-3.8-flash-lite-tts...")
                    used_model = "gemini-3.8-flash-lite-tts"
                    import time
                    time.sleep(1.0)
                    continue
                
                if attempt < max_retries:
                    wait_time = attempt * 2.0
                    _log(f"  ⚠️ 語音請求異常（第 {attempt}/{max_retries} 次嘗試失敗: {err_str[:60]}...），等待 {wait_time:.1f} 秒後自動重試...")
                    import time
                    time.sleep(wait_time)
                else:
                    _log(f"  ❌ 歷經 {max_retries} 次重試後仍無法完成語音生成: {err_str}")
                    raise last_error

