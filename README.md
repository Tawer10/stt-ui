# STT UI — Speech-to-Text Overlay

A minimal floating overlay that transcribes your voice and pastes the text into any focused field — no buttons, no typing.

## How it works

1. Focus any text field in any app
2. Press `Ctrl+Shift+Space`
3. Speak
4. Pause for 2 seconds → transcribed text is automatically pasted
5. Overlay stays open for the next utterance — press `Escape` or `✕` when done

## Features

- **Zero friction** — hotkey opens overlay, recording starts immediately
- **Live waveform** — bars animate in real time as you speak
- **Auto-paste** — 2s silence triggers paste into whatever app was focused
- **Persistent** — stays open between pastes, no need to re-press hotkey
- **Smart target** — paste target updates dynamically to the currently focused window
- **Multilingual** — set `LANGUAGE = ""` for auto-detect, or lock to any language code
- **Timestamps** — all console output includes time for easy debugging

## Setup

```bash
pip install -r requirements.txt
python main.py
```

On first run the Whisper model downloads automatically (~1.5 GB for `large-v3-turbo`).

## Configuration

Edit the constants at the top of `main.py`:

| Variable | Default | Description |
|---|---|---|
| `MODEL` | `large-v3-turbo` | Whisper model — `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo` |
| `LANGUAGE` | `en` | Language code (`"en"`, `"ru"`, `"de"`, …) or `""` for auto-detect |
| `HOTKEY` | `ctrl+shift+space` | Global hotkey to open the overlay |
| `SILENCE_AUTOPASTE` | `2.0` | Seconds of silence before auto-paste fires |

## Requirements

- Windows 10/11
- Python 3.9+
- Microphone

## Dependencies

```
RealtimeSTT   — Whisper-based real-time transcription
keyboard      — global hotkey
pyautogui     — keyboard simulation for paste
pyperclip     — clipboard
pywin32       — Windows window focus management
sounddevice   — audio
numpy         — audio processing
```
