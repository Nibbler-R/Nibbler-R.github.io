import os
import json
import queue
import sounddevice as sd
import asyncio
import threading
import customtkinter as ctk
from tkinter import scrolledtext, Canvas
from vosk import Model, KaldiRecognizer
import edge_tts
import requests
import time
import uuid
from playsound import playsound
import math

# === CONFIGURATION ===
BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = r"C:\Users\ptrob\Documents\vosk-model-en-us-0.22"  # Update if needed
MEMORY_FILE = os.path.join(BASE, "memory.json")
SETTINGS_FILE = os.path.join(BASE, "settings.json")

# Default settings – can be overridden by settings.json
SETTINGS = {
    "ollama_model": "phi3:mini"
}

def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

# === SETTINGS HANDLING ===
def load_settings() -> None:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                SETTINGS.update(json.load(f))
        except Exception as e:
            log(f"[Settings Error] {e} – resetting settings.json")
            save_settings()
    else:
        save_settings()

def save_settings() -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(SETTINGS, f, indent=2)

load_settings()

# === EDGE-TTS SETTINGS ===
VOICE_NAME = "en-US-GuyNeural"
RATE = "+0%"

# === GUI SETUP ===
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

root = ctk.CTk()
root.title("Jarvis Assistant (Ollama + Serper Online Mode)")
root.geometry("980x720")

title = ctk.CTkLabel(
    root,
    text="🤖 Jarvis Assistant (Ollama + Serper Online Mode)",
    font=("Consolas", 24, "bold"),
    text_color="#00FFFF",
)
title.pack(pady=10)

frame = ctk.CTkFrame(root, fg_color="#0B1B23", corner_radius=16)
frame.pack(fill="both", expand=True, padx=10, pady=10)

console = scrolledtext.ScrolledText(
    frame, wrap="word", bg="#0b1b23", fg="#e6fff9", font=("Consolas", 10)
)
console.pack(fill="both", expand=True, padx=10, pady=10)
console.config(state="disabled")

status_bar = ctk.CTkLabel(
    root,
    text=f"🟢 Listening | Model: {SETTINGS.get('ollama_model', 'unknown')} | 🌐 Web Enabled (Serper.dev)",
    anchor="w",
    font=("Segoe UI", 12),
)
status_bar.pack(fill="x", side="bottom", pady=2, padx=6)

# --- Thread-safe GUI logging ---
def gui_log(msg: str) -> None:
    """Append text to the console from any thread."""
    timestamp = time.strftime("%H:%M:%S")

    def _append():
        console.config(state="normal")
        console.insert("end", f"[{timestamp}] {msg}\n")
        console.see("end")
        console.config(state="disabled")

    root.after(0, _append)

# === ORB VISUALIZER ===
canvas = Canvas(root, width=150, height=150, bg="#101a23", highlightthickness=0)
canvas.pack(pady=10)
circle = canvas.create_oval(40, 40, 110, 110, fill="#00FFFF", outline="")

breathing = True

def pulse(direction=1, size=0.0):
    if not breathing:
        return
    size += direction * 1.2
    if size >= 10:
        direction = -1
    elif size <= -10:
        direction = 1
    canvas.coords(circle, 40 - size, 40 - size, 110 + size, 110 + size)
    root.after(60, lambda: pulse(direction, size))

pulse()

def animate_speaking(duration: float = 2.5):
    """Temporarily stop breathing animation and animate speech."""
    global breathing
    breathing = False
    start_time = time.time()
    end_time = start_time + duration

    def pulse_talk():
        now = time.time()
        if now < end_time:
            scale = abs(math.sin((now - start_time) * 8))
            size = 20 * scale
            canvas.coords(circle, 40 - size, 40 - size, 110 + size, 110 + size)
            root.after(40, pulse_talk)
        else:
            breathing = True
            pulse()

    pulse_talk()

# === SPEECH HANDLING ===
speech_queue: "queue.Queue[str]" = queue.Queue()
speaking_active = False
listening = True

async def speak_async(text: str) -> None:
    """Generate speech via edge-tts and play it, with basic queueing."""
    global speaking_active, listening
    listening = False
    gui_log(f"Jarvis: {text}")
    animate_speaking(min(6, len(text) / 15))
    try:
        filename = f"voice_{uuid.uuid4().hex}.mp3"
        communicate = edge_tts.Communicate(text, VOICE_NAME, rate=RATE)
        with open(filename, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
        playsound(filename, block=True)
        os.remove(filename)
    except Exception as e:
        gui_log(f"[TTS Error] {e}")
    finally:
        speaking_active = False
        listening = True

        # Process next queued message, if any
        if not speech_queue.empty():
            next_text = speech_queue.get()
            speak(next_text)

def speak(text: str) -> None:
    """Public function to speak text; handles queueing."""
    global speaking_active
    if speaking_active:
        speech_queue.put(text)
        return
    speaking_active = True
    threading.Thread(
        target=lambda: asyncio.run(speak_async(text)),
        daemon=True
    ).start()

# === MEMORY ===
conversation_history = []

def load_memory() -> None:
    global conversation_history
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                conversation_history = json.load(f)
        except Exception as e:
            gui_log(f"[Memory Error] {e}")

def save_memory() -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(conversation_history, f, indent=2)
    except Exception as e:
        gui_log(f"[Memory Save Error] {e}")

# === VOSK MODEL ===
try:
    model = Model(MODEL_PATH)
    rec = KaldiRecognizer(model, 16000)
    log("VOSK model loaded successfully.")
except Exception as e:
    gui_log(f"[VOSK Error] {e}")
    rec = None

audio_q: "queue.Queue[bytes]" = queue.Queue()

def callback(indata, frames, time_info, status):
    """Sounddevice callback – push audio chunks to queue."""
    audio_q.put(bytes(indata))

# === WEB SEARCH (Serper.dev) ===
def web_search(query: str) -> str:
    """
    Search the web using Serper.dev (Google API).
    Requires SERPER_API_KEY environment variable.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return "[Web Search Error] SERPER_API_KEY environment variable is not set."

    try:
        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": 3}
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = data.get("organic", [])
        if not results:
            return "No relevant web results found."
        text = "\n".join(
            [f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in results[:3]]
        )
        return text
    except Exception as e:
        return f"[Web Search Error] {e}"

def should_use_web(prompt: str) -> bool:
    """
    Decide when to hit the web.
    We only trigger on explicit intents like:
    - 'search the web for ...'
    - 'look up ...'
    - 'google ...'
    """
    p = prompt.lower().strip()
    triggers = ["search the web for", "lookup ", "look up ", "google ", "search for "]
    return any(p.startswith(t) for t in triggers)

# === OLLAMA AI ===
def think(user_prompt: str) -> str:
    """Generate a reply using Ollama, optionally enriched with web results."""
    global conversation_history
    try:
        model_name = SETTINGS.get("ollama_model", "phi3:mini")
        prompt_for_model = user_prompt

        # 🌐 Conditionally fetch online info
        if should_use_web(user_prompt):
            gui_log("🌐 Searching the internet via Serper.dev...")
            web_info = web_search(user_prompt)
            prompt_for_model += f"\n\nHere are some web search results:\n{web_info}"

        gui_log(f"🤔 Thinking with Ollama ({model_name})...")

        recent_history = "\n".join(conversation_history[-6:]) if conversation_history else ""
        system_prompt = (
            "You are Jarvis, a calm and helpful AI assistant. "
            "Keep answers brief (1–3 sentences) and natural.\n"
        )

        final_prompt = (
            f"{system_prompt}"
            f"Recent context:\n{recent_history}\n\n"
            f"User: {prompt_for_model}\n"
            f"Jarvis:"
        )

        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": model_name, "prompt": final_prompt},
            stream=True,
            timeout=60,
        )

        result_text = ""
        for line in response.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line.decode("utf-8"))
                if "response" in data:
                    result_text += data["response"]
            except Exception:
                continue

        if not result_text.strip():
            result_text = "(No response from Ollama)"
    except Exception as e:
        result_text = f"[Ollama Error] {e}"

    # Save to memory
    conversation_history.append(f"You: {user_prompt}")
    conversation_history.append(f"Jarvis: {result_text.strip()}")
    save_memory()

    return result_text.strip()

# === COMMAND HANDLER ===
def parse_command(text: str):
    """
    Very simple command parser.
    You can extend this with more commands like:
    - 'what time is it'
    - 'open browser'
    """
    t = text.lower().strip()
    if any(w in t for w in ["exit", "quit", "goodbye", "stop listening"]):
        return ("exit", None)
    elif t.startswith("open "):
        return ("open_app", t.replace("open ", "").strip())
    else:
        return ("chat", t)

# === MAIN LOOP ===
def main_loop():
    global listening
    speak("Jarvis is online and ready.")
    if rec is None:
        gui_log("❌ Speech recognition is not available (VOSK failed to load).")
        return

    try:
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=callback
        ):
            while True:
                if not listening:
                    time.sleep(0.1)
                    continue
                try:
                    data = audio_q.get(timeout=0.5)
                except queue.Empty:
                    continue

                if rec and rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip().lower()

                    # Ignore empty or too-short phrases (to avoid random triggers)
                    if not text or len(text.split()) < 2:
                        continue

                    gui_log(f"You said: {text}")

                    cmd, arg = parse_command(text)
                    if cmd == "exit":
                        speak("Goodbye.")
                        save_memory()
                        root.quit()
                        break
                    elif cmd == "chat":
                        reply = think(arg)
                        speak(reply)
                    elif cmd == "open_app":
                        # Placeholder: implement open-app logic if desired
                        speak(f"I heard you want to open {arg}, but I can't do that yet.")
    except Exception as e:
        gui_log(f"[Error] {e}")
        speak("An error occurred in the main loop.")

# === STARTUP ===
if __name__ == "__main__":
    load_memory()
    threading.Thread(target=main_loop, daemon=True).start()
    root.mainloop()

