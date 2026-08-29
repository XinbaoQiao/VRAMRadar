from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = ROOT / "dist" / "VRAMRadar" / "VRAMRadar.exe"
WM_CLOSE = 0x0010
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020


class WindowRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WindowPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def wait_until(predicate, timeout_seconds: float, message: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise RuntimeError(message)


def find_window(process_id: int) -> int | None:
    user32 = ctypes.windll.user32
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(handle, _parameter):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        title_length = user32.GetWindowTextLengthW(handle)
        if owner.value == process_id and title_length > 0:
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(handle, title, len(title))
            if title.value == "显存雷达":
                handles.append(int(handle))
        return True

    user32.EnumWindows(callback, 0)
    return handles[0] if handles else None


def window_is_visible_on_a_monitor(window: int) -> bool:
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(window):
        return False
    rect = WindowRect()
    return bool(user32.GetWindowRect(window, ctypes.byref(rect))) and bool(
        user32.MonitorFromRect(ctypes.byref(rect), 0)
    )


def window_has_usable_size(window: int) -> bool:
    rect = WindowRect()
    return bool(ctypes.windll.user32.GetWindowRect(window, ctypes.byref(rect))) and (
        rect.right - rect.left >= 840 and rect.bottom - rect.top >= 600
    )


def window_content_is_painted(window: int) -> bool:
    """Reject the uniform WebView2 loading-color frame seen after bad restore.

    Pixels come from the composed desktop because WebView2 is rendered through
    a child composition surface that ``PrintWindow``/a parent DC may omit.  A
    sample is accepted only while ``WindowFromPoint`` resolves back to this
    exact top-level window, so another foreground or always-on-top window can
    never make the product surface look painted.
    """

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.WindowFromPoint.argtypes = [WindowPoint]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.GetPixel.restype = wintypes.DWORD
    native_window = wintypes.HWND(window)
    if not user32.IsWindowVisible(native_window) or user32.IsIconic(native_window):
        return False
    client = WindowRect()
    if not user32.GetClientRect(native_window, ctypes.byref(client)):
        return False
    width = client.right - client.left
    height = client.bottom - client.top
    if width < 800 or height < 520:
        return False
    origin = WindowPoint(0, 0)
    if not user32.ClientToScreen(native_window, ctypes.byref(origin)):
        return False
    user32.SetForegroundWindow(native_window)
    desktop = wintypes.HWND(0)
    device = user32.GetDC(desktop)
    if not device:
        return False
    colors: set[int] = set()
    loading_color = 0x00171007  # COLORREF for WebView background #071017
    loading_samples = 0
    total_samples = 0
    try:
        for column in range(1, 18):
            x = origin.x + (width * column // 18)
            for row in range(1, 12):
                y = origin.y + (height * row // 12)
                point_window = user32.WindowFromPoint(WindowPoint(x, y))
                if not point_window or int(user32.GetAncestor(point_window, 2) or 0) != window:
                    continue
                color = int(gdi32.GetPixel(device, x, y))
                if color == 0xFFFFFFFF:
                    continue
                colors.add(color)
                loading_samples += int(color == loading_color)
                total_samples += 1
    finally:
        user32.ReleaseDC(desktop, device)
    return (
        total_samples >= 100
        and len(colors) >= 4
        and loading_samples < total_samples * 0.8
    )


def frontend_dom_and_bridge_are_ready(endpoint: Path) -> bool:
    try:
        document = json.loads(endpoint.read_text(encoding="utf-8"))
        port = int(document["port"])
        nonce = str(document["nonce"])
        if not 1 <= port <= 65535 or len(nonce) < 16:
            return False
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            connection.sendall(f"{nonce} PROBE\n".encode("utf-8"))
            connection.settimeout(2)
            return connection.recv(32).strip() == b"READY"
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def run() -> None:
    if sys.platform != "win32":
        raise RuntimeError("the packaged notification-area validator must run on Windows")
    if not EXECUTABLE.is_file():
        raise RuntimeError(f"packaged executable is missing: {EXECUTABLE}")

    validation_root = ROOT / "work"
    validation_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vram-radar-tray-", dir=validation_root) as temporary:
        home = Path(temporary)
        arguments = [
            str(EXECUTABLE),
            "--home",
            str(home),
            "--profile",
            "tray-validation",
            "--no-auto-import",
        ]
        process = subprocess.Popen(arguments)
        try:
            endpoint = home / "runtime" / "tray-validation.activation.json"
            wait_until(endpoint.is_file, 20, "packaged activation endpoint did not start")
            window: int | None = None

            def window_started() -> bool:
                nonlocal window
                window = find_window(process.pid)
                return window is not None and window_is_visible_on_a_monitor(window)

            wait_until(window_started, 20, "packaged desktop window did not become visible")
            assert window is not None
            if not window_has_usable_size(window):
                raise RuntimeError("packaged desktop window started below the supported minimum size")
            wait_until(
                lambda: window_content_is_painted(window),
                20,
                "packaged WebView content did not paint before minimization",
            )
            wait_until(
                lambda: frontend_dom_and_bridge_are_ready(endpoint),
                20,
                "packaged frontend DOM or JavaScript bridge was not ready before minimization",
            )

            ctypes.windll.user32.PostMessageW(window, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            wait_until(
                lambda: (
                    process.poll() is None
                    and ctypes.windll.user32.IsWindowVisible(window)
                    and ctypes.windll.user32.IsIconic(window)
                ),
                8,
                "native minimize did not keep the packaged app visible in the taskbar",
            )

            subprocess.run(arguments, check=True, timeout=15)
            wait_until(
                lambda: (
                    window_is_visible_on_a_monitor(window)
                    and not ctypes.windll.user32.IsIconic(window)
                    and window_has_usable_size(window)
                ),
                8,
                "second-instance activation did not restore a usable window onto a monitor",
            )
            wait_until(
                lambda: window_content_is_painted(window),
                10,
                "minimize/restore left the packaged WebView surface blank",
            )
            wait_until(
                lambda: frontend_dom_and_bridge_are_ready(endpoint),
                10,
                "frontend DOM or JavaScript bridge did not survive minimize/restore",
            )

            ctypes.windll.user32.PostMessageW(window, WM_CLOSE, 0, 0)
            wait_until(
                lambda: process.poll() is None and not ctypes.windll.user32.IsWindowVisible(window),
                8,
                "window close did not keep the packaged app alive in the notification area",
            )

            subprocess.run(arguments, check=True, timeout=15)
            wait_until(
                lambda: window_is_visible_on_a_monitor(window) and window_has_usable_size(window),
                8,
                "activation after window close did not recover a usable window onto a monitor",
            )
            wait_until(
                lambda: window_content_is_painted(window),
                10,
                "close/activation left the packaged WebView surface blank",
            )
            wait_until(
                lambda: frontend_dom_and_bridge_are_ready(endpoint),
                10,
                "frontend DOM or JavaScript bridge did not survive close/activation",
            )

            subprocess.run(arguments + ["--quit-existing"], check=True, timeout=15)
            process.wait(timeout=15)
            log_path = home / "logs" / "app.log"
            log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
            if "failed to start the Windows notification icon" in log_text:
                raise RuntimeError("the packaged notification icon failed to start")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    run()
    print("Packaged Windows notification-area validation passed")
