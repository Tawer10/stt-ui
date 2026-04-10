"""
Headless UI test:
1. Opens the overlay directly (no hotkey needed).
2. After 1.5 s simulates a completed utterance ("Hello from STT overlay test").
3. Lets the 2 s auto-paste timer fire → pastes into whichever window was focused.
4. Simulates a SECOND utterance 2 s later → verifies overlay stays alive.
5. Saves screenshots before paste, after first paste, after second paste.
"""
import sys, time, threading
sys.path.insert(0, ".")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    import pyautogui
    from main import STTOverlay, get_foreground_hwnd

    hwnd = get_foreground_hwnd()
    print(f"target hwnd: {hwnd}")

    overlay = STTOverlay(prev_hwnd=hwnd)

    def driver():
        time.sleep(1.0)
        img = pyautogui.screenshot()
        img.save("test_overlay_open.png")
        print("screenshot 1 saved  (overlay open)")

        time.sleep(0.5)
        overlay._on_sentence("First sentence.")
        print("sentence 1 injected")

        time.sleep(2.5)   # let paste timer fire
        img = pyautogui.screenshot()
        img.save("test_after_paste1.png")
        print("screenshot 2 saved  (after paste 1 — overlay should still be visible)")

        time.sleep(1.0)
        overlay._on_sentence("Second sentence.")
        print("sentence 2 injected")

        time.sleep(2.5)   # let paste timer fire again
        img = pyautogui.screenshot()
        img.save("test_after_paste2.png")
        print("screenshot 3 saved  (after paste 2)")

        time.sleep(1.0)
        print("closing overlay via Escape")
        overlay._cancel()

    threading.Thread(target=driver, daemon=True).start()
    overlay.run()
    print("overlay closed — test complete")
