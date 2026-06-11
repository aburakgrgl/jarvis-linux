"""
voice.py — Jarvis English voice (ElevenLabs) with Piper fallback.

Bu modul ~/jarvis/voice.py olarak kaydedilir ve jarvis.py icinden import edilir.
ElevenLabs ile dusuk gecikmeli, akici Ingilizce konusma saglar; internet/API
sorununda otomatik olarak Piper'a duser.

Kullanim (jarvis.py icinde):

    import voice
    voice.set_piper_fallback(speak_piper)   # eski speak() fonksiyonun
    voice.speak("Hello sir.", on_state=hud)  # on_state istege bagli (HUD)

API key ortam degiskeni: ELEVENLABS_API_KEY
"""

import os
import subprocess

try:
    import requests
except ImportError:
    requests = None


# ---------------------------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------------------------
ELEVEN_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "")

# Jarvis'e yakin sesler (ElevenLabs varsayilan kutuphane ID'leri):
#   George  -> JBFqnCBsd6RMkjVDRZzb  (raspy British, onerilen)
#   Daniel  -> onwK4e9ZLuTAKqWW03F9  (derin Ingiliz haber spikeri)
# Kendi hesabindan baska bir ses sec: elevenlabs.io -> Voices -> ses "ID"si
ELEVEN_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"

# Model: eleven_flash_v2_5 = en dusuk gecikme (akici).
# Daha yuksek kalite icin: eleven_turbo_v2_5
ELEVEN_MODEL    = "eleven_flash_v2_5"

# Ses karakteri. stability dusuk = daha ifadeli/insansi; cok dusurme tutarsizlasir.
VOICE_SETTINGS = {
    "stability":        0.45,
    "similarity_boost": 0.85,
    "style":            0.0,
    "use_speaker_boost": True,
}

# PCM cikis hizi (aplay ile dogrudan calar, mp3 decode yok = dusuk gecikme)
SAMPLE_RATE = 24000


# ---------------------------------------------------------------------------
# Piper fallback (jarvis.py senin eski speak fonksiyonunu buraya kaydeder)
# ---------------------------------------------------------------------------
_piper_fallback = None

def set_piper_fallback(fn):
    """jarvis.py: voice.set_piper_fallback(speak_piper) ile eski sesini kaydet."""
    global _piper_fallback
    _piper_fallback = fn


# ---------------------------------------------------------------------------
# Ana konusma fonksiyonu
# ---------------------------------------------------------------------------
def speak(text, on_state=None):
    """
    Metni ElevenLabs ile seslendirir. Hata olursa Piper'a duser.
    on_state: istege bagli, "speaking" durumu gondermek icin (HUD).
    """
    if not text:
        return

    if on_state:
        try:
            on_state("speaking")
        except Exception:
            pass

    if not ELEVEN_API_KEY or requests is None:
        return _fallback(text)

    try:
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/"
               f"{ELEVEN_VOICE_ID}/stream?output_format=pcm_{SAMPLE_RATE}")
        headers = {
            "xi-api-key":   ELEVEN_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "text":          text,
            "model_id":      ELEVEN_MODEL,
            "voice_settings": VOICE_SETTINGS,
        }
        with requests.post(url, headers=headers, json=payload,
                           stream=True, timeout=30) as r:
            r.raise_for_status()
            player = subprocess.Popen(
                ["aplay", "-q", "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", "1"],
                stdin=subprocess.PIPE,
            )
            for chunk in r.iter_content(chunk_size=4096):
                if chunk:
                    player.stdin.write(chunk)
            player.stdin.close()
            player.wait()
    except Exception as e:
        print("[voice] ElevenLabs hata, Piper'a dusuluyor:", e)
        return _fallback(text)


def _fallback(text):
    if _piper_fallback:
        _piper_fallback(text)
    else:
        print("[voice] Piper fallback ayarli degil, ses yok:", text)


# ---------------------------------------------------------------------------
# Ingilizce sabit metinler ve system prompt
# (jarvis.py'deki Turkce karsiliklarini bunlarla degistir)
# ---------------------------------------------------------------------------
MSG_NOT_HEARD = "I didn't catch that, sir."
MSG_BYE       = "Goodbye, sir."
MSG_LISTENING = "I'm listening."
MSG_ERROR     = "Something went wrong, sir."

SYSTEM_PROMPT = (
    "You are JARVIS, a calm, witty British AI assistant in the style of "
    "Iron Man's JARVIS. Address the user as 'sir'. Keep replies short and "
    "spoken-friendly: one or two sentences, no markdown, no lists, no emojis. "
    "Be precise, dry-humored, and efficient."
)

# faster-whisper dil ayari (jarvis.py'de WHISPER_LANG = "en" yap)
WHISPER_LANG = "en"


# Hizli test:  python voice.py "Hello sir, all systems are online."
if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "Hello sir, all systems online."
    print("Test:", msg)
    speak(msg)
