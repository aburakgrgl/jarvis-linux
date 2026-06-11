#!/usr/bin/env python3
"""
Jarvis - CachyOS sesli asistan
Akış: "Hey Jarvis" (openWakeWord) -> kayıt -> faster-whisper (STT)
      -> Claude Agent SDK -> Piper TTS -> hoparlör

Mikrofon sürekli "dinler" ama hiçbir ses internete gitmez:
wake word tespiti tamamen lokaldir. Sadece tetiklendikten sonraki
komutun YAZIYA ÇEVRİLMİŞ hali Claude'a gönderilir.
"""

import asyncio
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import time

import numpy as np
import sounddevice as sd

import voice  # ElevenLabs İngilizce ses + Piper fallback (voice.py)

# ----------------------------- AYARLAR -----------------------------------
SAMPLE_RATE = 16000          # openWakeWord ve whisper için 16 kHz şart
FRAME_SAMPLES = 1280         # 80 ms'lik kareler (openWakeWord beklentisi)
WAKE_THRESHOLD = 0.5         # 0-1 arası; yanlış tetikleniyorsa yükselt (örn. 0.6)
SILENCE_SECONDS = 1.5        # konuşma bittikten sonra bu kadar sessizlik = kayıt biter
MIN_RECORD_SECONDS = 5       # kayıt en az bu kadar sürer (erken kesilmeyi önler)
WAIT_FOR_SPEECH_SECONDS = 6  # bip'ten sonra konuşmaya başlaman için tanınan süre
MAX_COMMAND_SECONDS = 15     # tek komut için maksimum kayıt süresi
ENERGY_THRESHOLD = 300       # sessizlik eşiği; ortam gürültülüyse yükselt
WHISPER_MODEL = "small"      # "small" hızlı, "medium" daha isabetli (TR için iyi)
WHISPER_LANG = "en"
PIPER_VOICE = os.path.expanduser("~/jarvis/voices/en_GB-alan-medium.onnx")
CLAUDE_CWD = os.path.expanduser("~")   # Claude'un dosya erişim kök dizini

# Mod seçimi:
#   "wake"   -> "Hey Jarvis" ile tetiklenir; kısayol tuşu (SIGUSR1) dinlemeyi
#               komple açıp kapatır (kapalıyken mikrofon tamamen kapalıdır)
#   "hotkey" -> wake word yok; her komut için kısayol tuşuna basılır
MODE = "wake"
TRIGGER_PORT = 5598          # kısayol tuşunun sinyal gönderdiği port

# 15 Haziran 2026'ya kadar True bırak: Pro aboneliğinle çalışan `claude -p`
# komutunu kullanır. 15 Haziran'dan sonra False yapıp Agent SDK kredine geçebilirsin.
USE_CLI = True

SYSTEM_PROMPT = voice.SYSTEM_PROMPT  # İngilizce JARVIS kişiliği (voice.py)
# --------------------------------------------------------------------------

audio_q: "queue.Queue[np.ndarray]" = queue.Queue()

# ----------------------------- HUD (gorsel arayuz) -----------------------------
HUD_ADDR = ("127.0.0.1", 5599)
HUD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud.py")
_hud_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_hud_proc = None


def hud_start():
    """Wake word algilaninca HUD penceresini ac."""
    global _hud_proc
    if not os.path.exists(HUD_PATH):
        return
    if _hud_proc is None or _hud_proc.poll() is not None:
        _hud_proc = subprocess.Popen(
            [sys.executable, HUD_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)  # pencerenin acilmasina firsat ver


def hud(state: str, level: float = 0.0):
    """HUD'a durum gonder (HUD kapaliysa sessizce gecilir)."""
    try:
        _hud_sock.sendto(
            json.dumps({"state": state, "level": round(float(level), 3)}).encode(),
            HUD_ADDR,
        )
    except OSError:
        pass



def audio_callback(indata, frames, t, status):
    if status:
        print(f"[ses] {status}", file=sys.stderr)
    audio_q.put(indata[:, 0].copy())


def beep(freq=880, dur=0.15):
    """Tetiklenince kısa bip sesi çal."""
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), False)
    tone = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sd.play(tone, SAMPLE_RATE)
    sd.wait()


def record_command():
    """Wake word sonrası komutu kaydet. Konuşma başlayana kadar bekler,
    konuşma bittikten SILENCE_SECONDS sonra kaydı kapatır."""
    print("🎙️  Dinliyorum...")
    chunks = []
    silent_frames = 0
    needed_silent = int(SILENCE_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
    max_frames = int(MAX_COMMAND_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
    wait_frames = int(WAIT_FOR_SPEECH_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
    min_frames = int(MIN_RECORD_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
    spoke = False
    waited = 0
    peak = 0.0

    for _ in range(max_frames):
        frame = audio_q.get()
        energy = np.abs(frame).mean() * 32768
        peak = max(peak, energy)
        hud("listening", min(1.0, energy / 2000.0))

        if not spoke:
            # Konuşma henüz başlamadı: başlamasını bekle
            if energy >= ENERGY_THRESHOLD:
                spoke = True
                chunks.append(frame)
            else:
                waited += 1
                if waited >= wait_frames:
                    print(f"(konuşma başlamadı; en yüksek seviye {peak:.0f}, "
                          f"eşik {ENERGY_THRESHOLD})")
                    return None
            continue

        # Konuşma başladı: sessizlik sayacı işlesin
        chunks.append(frame)
        if energy < ENERGY_THRESHOLD:
            silent_frames += 1
            # Kayıt, MIN_RECORD_SECONDS dolmadan asla kesilmez
            if silent_frames >= needed_silent and len(chunks) >= min_frames:
                break
        else:
            silent_frames = 0

    return np.concatenate(chunks) if chunks else None


HALLUCINATIONS = (
    "altyazı", "abone ol", "izlediğiniz için", "videoyu beğen",
    "kanalıma", "subtitles", "thank you for watching",
)


def transcribe(audio: np.ndarray, whisper) -> str:
    segments, _ = whisper.transcribe(
        audio.astype(np.float32),
        language=WHISPER_LANG,
        beam_size=1,
        vad_filter=True,  # konuşma olmayan kısımları at
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    if any(h in text.lower() for h in HALLUCINATIONS):
        return ""
    return text


def ask_claude_cli(prompt: str) -> str:
    """Pro aboneliğiyle çalışan `claude -p` üzerinden sor (15 Haziran öncesi yol)."""
    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
        "--permission-mode", "acceptEdits",
        "--append-system-prompt", SYSTEM_PROMPT,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=CLAUDE_CWD, timeout=180
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "claude CLI hata verdi")
    return proc.stdout.strip() or "İşlem tamam."


async def ask_claude(prompt: str) -> str:
    """Claude Agent SDK ile soru sor. Claude dosyalara erişip iş yapabilir."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        cwd=CLAUDE_CWD,
        permission_mode="acceptEdits",          # dosya düzenlemelerine izin ver
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        system_prompt=SYSTEM_PROMPT,
    )

    parts = []
    async for message in query(prompt=prompt, options=options):
        # Sadece metin bloklarını topla
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
    return " ".join(parts).strip() or "İşlem tamam ama söyleyecek bir şey bulamadım."


def speak_piper(text: str):
    """Piper ile lokal TTS (ElevenLabs çalışmazsa fallback)."""
    if not os.path.exists(PIPER_VOICE):
        print(f"[uyarı] Piper ses modeli yok: {PIPER_VOICE}\nCevap: {text}")
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    subprocess.run(
        ["piper", "-m", PIPER_VOICE, "-f", wav_path],
        input=text.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    subprocess.run(["aplay", "-q", wav_path], check=False)
    os.unlink(wav_path)


voice.set_piper_fallback(speak_piper)


def speak(text: str):
    """ElevenLabs ile İngilizce konuş; internet/API sorununda Piper'a düşer."""
    voice.speak(text, on_state=hud)


def process_command(audio, whisper):
    """Kayıt sonrası ortak akış: yazıya çevir -> Claude -> seslendir."""
    if audio is None:
        print("(sessizlik, komut algılanmadı)")
        hud("bye")
        return
    hud("thinking")
    text = transcribe(audio, whisper)
    if not text:
        hud("speaking")
        speak(voice.MSG_NOT_HEARD)
        hud("bye")
        return
    print(f"🗣️  Sen: {text}")
    try:
        if USE_CLI:
            answer = ask_claude_cli(text)
        else:
            answer = asyncio.run(ask_claude(text))
    except Exception as e:
        answer = "Claude bağlantısında bir sorun oldu."
        print(f"[hata] {e}", file=sys.stderr)
    print(f"🤖 Jarvis: {answer}")
    hud("speaking")
    speak(answer)
    hud("bye")


def capture_command():
    """Mikrofonu SADECE kayıt süresince açar, bitince kapatır."""
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=FRAME_SAMPLES,
        callback=audio_callback,
    ):
        while not audio_q.empty():
            audio_q.get_nowait()
        hud_start()
        hud("listening")
        beep()
        return record_command()
    # with bloğundan çıkınca mikrofon kapanır, gösterge söner


def main_hotkey(whisper):
    """Kısayol modu: mikrofon kapalı bekler, SIGUSR1 sinyaliyle tetiklenir."""
    import signal
    import threading

    trigger = threading.Event()
    signal.signal(signal.SIGUSR1, lambda s, f: trigger.set())

    print("✅ Jarvis hazır (kısayol modu, mikrofon kapalı).")
    print("   Tetikleme komutu: pkill -USR1 -f jarvis.py")
    while True:
        trigger.wait()
        trigger.clear()
        print("🔔 Kısayol tetiklendi!")
        audio = capture_command()   # mikrofon sadece burada açık
        process_command(audio, whisper)


def notify(msg: str):
    """Masaüstü bildirimi (notify-send yoksa sessizce geçer)."""
    try:
        subprocess.run(
            ["notify-send", "-a", "Jarvis", "-t", "2500", "Jarvis", msg],
            check=False,
        )
    except FileNotFoundError:
        pass


def main_wake(whisper):
    """Wake word modu: 'Hey Jarvis' ile tetiklenir.
    Kısayol tuşu (SIGUSR1) dinlemeyi açıp kapatır; kapalıyken mikrofon
    tamamen kapalıdır ve sistemdeki mikrofon göstergesi söner."""
    import signal
    import threading

    import openwakeword
    from openwakeword.model import Model as WakeModel

    openwakeword.utils.download_models()
    wake = WakeModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    toggle = threading.Event()
    signal.signal(signal.SIGUSR1, lambda s, f: toggle.set())

    listening = True
    print("✅ Jarvis hazır. 'Hey Jarvis' de. (Kısayol tuşu dinlemeyi açar/kapatır)")
    notify("Dinleme açık 🎙️ — 'Hey Jarvis' diyebilirsin")

    while True:
        if not listening:
            # Mikrofon KAPALI: sadece kısayol sinyalini bekle
            toggle.wait()
            toggle.clear()
            listening = True
            wake.reset()
            print("🎙️  Dinleme açıldı.")
            notify("Dinleme açık 🎙️ — 'Hey Jarvis' diyebilirsin")
            continue

        # Mikrofon AÇIK: wake word dinle
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            callback=audio_callback,
        ):
            while not audio_q.empty():
                audio_q.get_nowait()
            while True:
                if toggle.is_set():
                    toggle.clear()
                    listening = False
                    break
                try:
                    frame = audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                pcm16 = (frame * 32768).astype(np.int16)
                scores = wake.predict(pcm16)
                if scores.get("hey_jarvis", 0) > WAKE_THRESHOLD:
                    wake.reset()
                    hud_start()
                    hud("listening")
                    beep()
                    audio = record_command()
                    process_command(audio, whisper)
                    while not audio_q.empty():
                        audio_q.get_nowait()
        # with bloğundan çıkıldı -> mikrofon kapandı, gösterge söner
        if not listening:
            print("🔇 Dinleme kapatıldı (mikrofon kapalı).")
            notify("Dinleme kapalı 🔇 — mikrofon kapatıldı")


def main():
    print("Modeller yükleniyor (ilk açılışta indirme yapılır)...")

    from faster_whisper import WhisperModel

    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    if MODE == "wake":
        main_wake(whisper)
    else:
        main_hotkey(whisper)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nJarvis kapatıldı.")
