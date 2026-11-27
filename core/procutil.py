"""
Alt süreç (subprocess) yardımcıları: gizli pencere, UTF-8 ve süreç AĞACINI güvenle öldürme.
yt-dlp ve ffmpeg gibi dış programlar için kullanılır.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Sequence


def python_command(*args: str) -> list[str]:
    """Bu uygulamayla aynı Python'u (venv dahil) UTF-8 kipinde çalıştıran komut."""
    return [sys.executable, "-X", "utf8", *args]


def popen_kwargs() -> dict:
    """Alt süreç için ortak ayarlar: UTF-8 çıktı, Windows'ta konsol penceresi açma, Linux'ta ayrı süreç grubu."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"          # ilerleme satırları anında gelsin
    kwargs: dict = {"env": env, "stdin": subprocess.DEVNULL}   # konsolsuz (paketli) uygulamada geçersiz tutamaç hatasını önler
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _kill_tree_blocking(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, creationflags=0x08000000, timeout=10,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError, ProcessLookupError):
        pass


def kill_tree(proc: subprocess.Popen) -> None:
    """
    Süreci ve ürettiği tüm alt süreçleri (ör. ffmpeg) öldürür. Çağıranı BEKLETMEZ:
    asıl öldürme ayrı bir thread'de yapılır; süreç hemen terminate edilir.
    """
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        pass
    threading.Thread(target=_kill_tree_blocking, args=(proc.pid,), daemon=True).start()


class ProcessCancelled(Exception):
    """run_capture_cancellable çalışırken iptal istendi."""


def run_capture_cancellable(cmd: Sequence[str], timeout: float, cancel_event: threading.Event) -> subprocess.CompletedProcess:
    """
    run_capture gibi çıktıyı toplar ama cancel_event ayarlanınca süreci (ve alt süreçlerini) hemen öldürür ve
    ProcessCancelled fırlatır. Zaman aşımında subprocess.TimeoutExpired fırlatır.
    """
    if cancel_event.is_set():
        raise ProcessCancelled()
    proc = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", **popen_kwargs(),
    )
    box: dict = {}
    finished = threading.Event()

    def reader() -> None:
        try:
            box["out"] = proc.communicate()
        except Exception as e:  # noqa: BLE001 - çağırana taşınır
            box["error"] = e
        finally:
            finished.set()

    threading.Thread(target=reader, daemon=True, name="proc-reader").start()
    deadline = time.monotonic() + timeout
    while not finished.wait(0.1):
        if cancel_event.is_set():
            kill_tree(proc)
            raise ProcessCancelled()
        if time.monotonic() > deadline:
            kill_tree(proc)
            raise subprocess.TimeoutExpired(list(cmd), timeout)
    if "error" in box:
        raise box["error"]
    out, err = box["out"]
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


def run_capture(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, **popen_kwargs(),
    )