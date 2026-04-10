#!/usr/bin/env python3
"""
STT Overlay  —  speak → paste.

Hotkey : Ctrl+Shift+Space
  1. Press hotkey while any text field is focused.
  2. Small overlay appears at bottom-center. Recording starts immediately.
  3. Speak. Waveform reacts. Partial text shown in status strip.
  4. Stop talking for 2 s → transcribed text is pasted, overlay closes.
  5. Escape / ✕ → cancel without pasting.

Debug  : python main.py --debug
"""

import os
# Suppress ctranslate2 C-level warnings before anything loads the library
os.environ["CT2_VERBOSE"] = "0"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import sys
# Force UTF-8 on Windows console so non-ASCII transcriptions don't crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import math
import time
import queue
import struct
import logging
import threading
import tkinter as tk

import pyperclip
import pyautogui
import keyboard

# Suppress ctranslate2 Python-level warnings
logging.getLogger("ctranslate2").setLevel(logging.ERROR)
logging.getLogger("faster_whisper").setLevel(logging.ERROR)
logging.getLogger("RealtimeSTT").setLevel(logging.ERROR)

try:
    import ctranslate2 as _ct2
    _ct2.set_log_level(logging.ERROR)
except Exception:
    pass

try:
    import win32gui, win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    import pyaudio as _pyaudio
    HAS_PA = True
except ImportError:
    HAS_PA = False

from RealtimeSTT import AudioToTextRecorder

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str, end: str = "\n"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", end=end, flush=True)

# ── Config ────────────────────────────────────────────────────────────────────
HOTKEY            = "ctrl+shift+space"
MODEL             = "large-v3-turbo"
LANGUAGE          = "en"   # set to "" for auto-detect (multilingual but slower)
SILENCE_AUTOPASTE = 2.0
DEBUG             = "--debug" in sys.argv

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#0a0a0a"
CARD   = "#111111"
BORDER = "#1e1e1e"
ACCENT = "#b5f23d"
TEXT   = "#ffffff"
SUB    = "#555555"
DIM    = "#222222"
RED    = "#ff4545"

# ── OS helpers ────────────────────────────────────────────────────────────────

def get_foreground_hwnd():
    if HAS_WIN32:
        return win32gui.GetForegroundWindow()
    return None

def focus_and_paste(hwnd, text: str):
    pyperclip.copy(text)
    if HAS_WIN32 and hwnd:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    time.sleep(0.4)
    pyautogui.hotkey("ctrl", "v")

def _lerp_hex(a: str, b: str, t: float) -> str:
    def p(h):
        h = h.lstrip("#")
        return int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    ra,ga,ba = p(a); rb,gb,bb = p(b)
    return "#{:02x}{:02x}{:02x}".format(
        int(ra+(rb-ra)*t), int(ga+(gb-ga)*t), int(ba+(bb-ba)*t))

# ── Overlay ───────────────────────────────────────────────────────────────────

class STTOverlay:
    BAR_N   = 28
    BAR_GAP = 2
    FPS     = 30

    def __init__(self, prev_hwnd):
        self.prev_hwnd    = prev_hwnd
        self.recorder     = None
        self.is_recording = False
        self._closed      = False
        self._pasting     = False
        self._level       = 0.0       # 0-1 audio amplitude
        self._paste_timer = None
        self._anim_id     = None
        self._text        = ""

        self._build_ui()
        self.root.after(100, self._start_recorder)
        self._animate()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.configure(bg=BG)
        self.root.title("")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        self.root.resizable(False, False)
        self.root.bind("<Escape>", lambda _: self._cancel())

        W, H = 460, 64
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{sh-H-56}")

        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>",     self._drag_motion)

        border = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        border.pack(fill=tk.BOTH, expand=True)
        card = tk.Frame(border, bg=CARD)
        card.pack(fill=tk.BOTH, expand=True)

        # top row: dot + waveform + ✕
        top = tk.Frame(card, bg=CARD, height=44)
        top.pack(fill=tk.X, padx=10, pady=(6,0))
        top.pack_propagate(False)

        self.dot_canvas = tk.Canvas(top, width=24, height=44,
                                    bg=CARD, highlightthickness=0)
        self.dot_canvas.pack(side=tk.LEFT)

        self.wave_canvas = tk.Canvas(top, height=44,
                                     bg=CARD, highlightthickness=0)
        self.wave_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8,6))

        tk.Button(top, text="✕",
                  bg=CARD, fg=SUB, activebackground=RED, activeforeground=TEXT,
                  font=("Segoe UI",11), relief=tk.FLAT, bd=0,
                  padx=6, pady=0, cursor="hand2",
                  command=self._cancel).pack(side=tk.RIGHT)

        # status strip
        strip = tk.Frame(card, bg=BG, height=18)
        strip.pack(fill=tk.X, side=tk.BOTTOM)
        strip.pack_propagate(False)

        self.status_var = tk.StringVar(value="initializing…")
        tk.Label(strip, textvariable=self.status_var,
                 bg=BG, fg=SUB, font=("Segoe UI",7), anchor="w"
                 ).pack(fill=tk.X, padx=10, pady=1)

    def _drag_start(self, e): self._dx, self._dy = e.x, e.y
    def _drag_motion(self, e):
        self.root.geometry(
            f"+{self.root.winfo_x()+e.x-self._dx}"
            f"+{self.root.winfo_y()+e.y-self._dy}")

    # ── animation ─────────────────────────────────────────────────────────

    def _animate(self):
        if self._closed:
            self._anim_id = None
            return
        self._draw_dot()
        self._draw_wave()
        self._anim_id = self.root.after(1000 // self.FPS, self._animate)

    def _draw_dot(self):
        c = self.dot_canvas
        c.delete("all")
        cx, cy = 12, 22
        if self.is_recording:
            pulse = 0.5 + 0.5 * math.sin(time.time() * 5)
            r = 5 + int(pulse * 3)
            c.create_oval(cx-r-4, cy-r-4, cx+r+4, cy+r+4,
                          fill=_lerp_hex(RED, CARD, 0.55+0.3*pulse), outline="")
            c.create_oval(cx-r, cy-r, cx+r, cy+r, fill=RED, outline="")
        else:
            c.create_oval(cx-6, cy-6, cx+6, cy+6, fill=DIM, outline="")

    def _draw_wave(self):
        c = self.wave_canvas
        c.delete("all")
        W = c.winfo_width()
        if W < 2:
            return
        H  = 44
        t  = time.time()
        bw = max(2, (W - self.BAR_GAP*(self.BAR_N-1)) / self.BAR_N)
        bs = bw + self.BAR_GAP
        active = self.is_recording and self._level > 0.015

        for i in range(self.BAR_N):
            if active:
                wave = (0.22*math.sin(t*5.5+i*0.85)
                      + 0.18*math.sin(t*3.2+i*1.60)
                      + 0.12*math.sin(t*9.1+i*0.35)
                      + 0.08*math.sin(t*13.0+i*2.10))
                height = max(3, min(abs((0.15+self._level*0.60+wave)*(H-8)), H-8))
                color  = ACCENT
            else:
                height = 3
                color  = DIM
            x0 = i * bs
            c.create_rectangle(x0, (H-height)/2, x0+bw, (H-height)/2+height,
                                fill=color, outline="")

    # ── recorder ──────────────────────────────────────────────────────────

    def _start_recorder(self):
        self.is_recording = True
        self._set_status("listening…")

        def run():
            def _on_chunk(data: bytes):
                count = len(data) // 2
                if count == 0:
                    return
                shorts = struct.unpack(f"{count}h", data)
                rms = math.sqrt(sum(s * s for s in shorts) / count) / 32768.0
                self._level = min(1.0, rms * 35)
                bars = int(self._level * 30)
                tag  = "MIC" if self._level > 0.015 else " . "
                ts   = time.strftime("%H:%M:%S")
                print(f"\r[{ts}] [{tag}] {'|'*bars:<30} {self._level:.3f}  ", end="", flush=True)

            # Wait for mic
            if HAS_PA:
                while not self._closed:
                    try:
                        pa = _pyaudio.PyAudio()
                        pa.get_default_input_device_info()
                        pa.terminate()
                        break
                    except Exception:
                        pa.terminate()
                    self.root.after(0, lambda: self._set_status("connecting…"))
                    time.sleep(1.5)

            if self._closed:
                return

            self.root.after(0, lambda: self._set_status("listening…"))

            try:
                # Redirect fd 2 (stderr) to devnull at OS level BEFORE spawning
                # subprocesses so they inherit the silenced fd and Silero's
                # torchaudio DLL error never reaches the terminal.
                null_fd  = os.open(os.devnull, os.O_WRONLY)
                saved_fd = os.dup(2)
                os.dup2(null_fd, 2)
                os.close(null_fd)
                try:
                    self.recorder = AudioToTextRecorder(
                        model=MODEL,
                        language=LANGUAGE,
                        compute_type="int8",
                        device="cpu",
                        enable_realtime_transcription=True,
                        realtime_processing_pause=0.8,
                        on_realtime_transcription_update=self._on_partial,
                        on_recorded_chunk=_on_chunk,
                        on_vad_detect_stop=self._on_vad_stop,
                        beam_size=1,
                        beam_size_realtime=1,
                        spinner=False,
                        silero_sensitivity=0.01,
                        webrtc_sensitivity=2,
                        post_speech_silence_duration=0.6,
                        min_length_of_recording=0.3,
                        min_gap_between_recordings=0.1,
                        pre_recording_buffer_duration=0.2,
                    )
                finally:
                    # Restore our own stderr — subprocesses keep the devnull copy
                    os.dup2(saved_fd, 2)
                    os.close(saved_fd)

                log("[stt] recorder ready — speak now")

                while self.is_recording and not self._closed:
                    try:
                        text = self.recorder.text()
                    except (BrokenPipeError, EOFError, OSError):
                        break
                    if self._closed:
                        break
                    if text and text.strip():
                        self._on_sentence(text.strip())

            except (BrokenPipeError, EOFError, OSError):
                pass
            except Exception as exc:
                if not self._closed:
                    msg = str(exc)
                    log(f"[stt] recorder error: {msg}")
                    self.root.after(0, lambda m=msg: self._set_status(f"error: {m}"))

        threading.Thread(target=run, daemon=True).start()

    def _on_vad_stop(self):
        """VAD detected end of speech — start paste countdown immediately,
        before the (slow) transcription even returns."""
        if self._closed or self._pasting:
            return
        try:
            self.root.after(0, self._reset_paste_timer)
        except Exception:
            pass

    def _on_partial(self, text):
        if self._closed or not text:
            return
        log(f"[stt] partial  : {text[:80]:<80}", end="\r")
        try:
            self.root.after(0, lambda t=text: self._set_status(t[:72]))
        except Exception:
            pass

    def _on_sentence(self, text):
        if self._closed:
            return
        log(f"[stt] sentence : {text}")
        self._text = (self._text + " " + text).strip() if self._text else text
        log(f"[stt] buffer   : {self._text}")

        def _update():
            if self._closed:
                return
            preview = ("…" + self._text[-58:]) if len(self._text) > 58 else self._text
            self._set_status(preview)
            # Only reset timer if not already running — VAD may have started it
            # before transcription returned. If the timer is alive, leave it alone
            # so paste fires at the right time relative to when speech ended.
            timer_running = (self._paste_timer is not None
                             and self._paste_timer.is_alive())
            if not timer_running:
                self._reset_paste_timer()
        try:
            self.root.after(0, _update)
        except Exception:
            pass

    # ── paste timer ───────────────────────────────────────────────────────

    def _reset_paste_timer(self):
        if self._paste_timer:
            self._paste_timer.cancel()
        if self._closed or self._pasting:
            return
        self._paste_timer = threading.Timer(
            SILENCE_AUTOPASTE,
            lambda: self.root.after(0, self._do_paste))
        self._paste_timer.daemon = True
        self._paste_timer.start()

    # ── paste / close ─────────────────────────────────────────────────────

    def _do_paste(self):
        if self._closed or self._pasting:
            return
        self._pasting = True
        if self._paste_timer:
            self._paste_timer.cancel()
        text = self._text.strip()
        if not text:
            self._pasting = False
            return
        self._set_status("pasting…")
        self.root.withdraw()
        self.root.after(300, lambda: self._finish_paste(text))

    def _finish_paste(self, text):
        # Run paste in a thread so the tkinter mainloop stays unblocked.
        def _do():
            # Let the OS process the overlay withdrawal and restore focus
            # to whichever real app the user was working in.
            time.sleep(0.15)
            target = self.prev_hwnd
            if HAS_WIN32:
                fg = win32gui.GetForegroundWindow()
                if fg and fg != self._own_hwnd():
                    target = fg
                    self.prev_hwnd = fg   # keep up-to-date for next paste
            log(f"[stt] pasting into hwnd={target}: {text!r}")
            focus_and_paste(target, text)
            # Wait so the paste lands before the overlay reappears
            time.sleep(0.35)
            if not self._closed:
                try:
                    self.root.after(0, self._post_paste)
                except Exception:
                    pass
        threading.Thread(target=_do, daemon=True).start()

    def _own_hwnd(self):
        """Return this overlay's own Windows handle (so we never paste into ourselves)."""
        try:
            return win32gui.FindWindow(None, self.root.title()) if HAS_WIN32 else None
        except Exception:
            return None

    def _post_paste(self):
        if self._closed:
            return
        self._text    = ""
        self._pasting = False
        self.root.deiconify()
        self._set_status("listening…")

    def _shutdown_recorder(self):
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        def _do():
            try:
                rec.shutdown()
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def _cancel(self):
        if self._paste_timer:
            self._paste_timer.cancel()
        self._close()

    def _close(self):
        if self._closed:
            return
        self._closed      = True
        self.is_recording = False
        if self._paste_timer:
            self._paste_timer.cancel()
        if self._anim_id is not None:
            try:
                self.root.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        self._shutdown_recorder()
        # Unbind status_var before destroy to prevent GC from wrong thread
        try:
            self.status_var.set("")
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _set_status(self, msg: str):
        if self._closed:
            return
        try:
            self.status_var.set(msg)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

_active: "STTOverlay | None" = None


def show_window():
    global _active
    if _active is not None:
        try:
            # Overlay already open — update paste target to currently focused app
            hwnd = get_foreground_hwnd()
            if hwnd:
                _active.prev_hwnd = hwnd
            _active.root.lift()
            _active.root.focus_force()
            return
        except Exception:
            _active = None
    hwnd = get_foreground_hwnd()
    time.sleep(0.05)
    _active = STTOverlay(prev_hwnd=hwnd)
    _active.run()
    _active = None


def _ensure_model():
    """Pre-download the Whisper model so the first hotkey press is instant."""
    try:
        from faster_whisper import WhisperModel
        log(f"Checking model '{MODEL}'… ", end="")
        WhisperModel(MODEL, device="cpu", compute_type="int8", num_workers=4)
        print("ready.")
    except Exception as e:
        log(f"warning: {e}")


def main():
    import signal

    log(f"STT Overlay  •  {HOTKEY}  •  {MODEL}")
    log(f"win32={'yes' if HAS_WIN32 else 'no'}  "
        f"pyaudio={'yes' if HAS_PA else 'no'}  "
        f"debug={'on' if DEBUG else 'off'}")
    log("Ctrl+C to quit.")

    _ensure_model()

    def _shutdown(sig, frame):
        global _active
        log("Bye.")
        if _active is not None:
            try: _active._close()
            except Exception: pass
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, 2)
            os.close(fd)
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    q: queue.Queue[int] = queue.Queue()
    keyboard.add_hotkey(HOTKEY, lambda: q.put(1))

    while True:
        try:
            q.get(timeout=0.1)
            show_window()
        except queue.Empty:
            pass


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
