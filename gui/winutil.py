"""Pencereyi öne getirme. Windows, arka plandaki uygulamanın odağı çalmasını engeller; bu yüzden ek yöntem denenir."""
from __future__ import annotations

import os


def bring_to_front(window) -> None:
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
    if os.name != "nt":
        return
    try:
        import ctypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = ctypes.c_void_p(int(window.winId()))
        user32.keybd_event(0x12, 0, 0, 0)       # Alt: Windows'un odak çalma engelini aşar
        user32.keybd_event(0x12, 0, 2, 0)
        foreground = user32.GetForegroundWindow()
        this_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None) if foreground else 0
        attached = bool(fg_thread and fg_thread != this_thread)
        if attached:
            user32.AttachThreadInput(fg_thread, this_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_thread, this_thread, False)
    except Exception:
        pass
