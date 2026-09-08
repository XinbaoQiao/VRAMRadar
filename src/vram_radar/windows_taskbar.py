"""Explicit Windows taskbar identity, separate from caption and tray icons."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import uuid

APP_ID = "VRAMRadar.Desktop.LightRadar"


class Guid(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    def __init__(self, value: str):
        super().__init__((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


class Key(ctypes.Structure):
    _fields_ = [("fmtid", Guid), ("pid", wintypes.DWORD)]


class Variant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3),
                ("value", ctypes.c_void_p), ("padding", ctypes.c_void_p)]


def _check(result: int) -> None:
    if result < 0:
        raise OSError(f"Taskbar COM operation failed: 0x{result & 0xffffffff:08x}")


def _method(pointer, slot, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[slot])


def _store(hwnd):
    store = ctypes.c_void_p()
    iid = Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")
    get_store = ctypes.windll.shell32.SHGetPropertyStoreForWindow
    get_store.argtypes = [wintypes.HWND, ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)]
    get_store.restype = ctypes.c_long
    _check(get_store(hwnd, ctypes.byref(iid), ctypes.byref(store)))
    return store


def window_property(hwnd: int, pid: int, value: str | None = None) -> str:
    """Read or write a string property; useful for exact packaged readback."""
    store = _store(hwnd)
    key = Key(Guid("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), pid)
    variant = Variant()
    try:
        if value is not None:
            text = ctypes.create_unicode_buffer(value)
            variant.vt = 31  # VT_LPWSTR; buffer remains live through SetValue.
            variant.value = ctypes.cast(text, ctypes.c_void_p).value
            _check(_method(store, 6, ctypes.c_long, ctypes.POINTER(Key), ctypes.POINTER(Variant))(
                store, ctypes.byref(key), ctypes.byref(variant)))
            _check(_method(store, 7, ctypes.c_long)(store))
            return value
        _check(_method(store, 5, ctypes.c_long, ctypes.POINTER(Key), ctypes.POINTER(Variant))(
            store, ctypes.byref(key), ctypes.byref(variant)))
        try:
            return ctypes.wstring_at(variant.value) if variant.vt == 31 and variant.value else ""
        finally:
            ctypes.windll.ole32.PropVariantClear(ctypes.byref(variant))
    finally:
        _method(store, 2, ctypes.c_ulong)(store)


def configure_identity(hwnd: int, icon: Path) -> None:
    if not icon.is_file():
        raise FileNotFoundError(icon)
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(sys.argv[0]).resolve()))
    command.extend(sys.argv[1:])
    window_property(hwnd, 2, subprocess.list2cmdline(command))
    window_property(hwnd, 4, "VRAM Radar")
    window_property(hwnd, 3, f"{icon.resolve()},0")
    # Set identity last: Windows refreshes the grouping using the properties above.
    window_property(hwnd, 5, APP_ID)


def refresh_button(hwnd: int) -> None:
    ole = ctypes.windll.ole32
    initialized = ole.CoInitializeEx(None, 2)
    pointer = ctypes.c_void_p()
    try:
        clsid = Guid("56fdf344-fd6d-11d0-958a-006097c9a090")
        iid = Guid("56fdf342-fd6d-11d0-958a-006097c9a090")
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)))
        _check(_method(pointer, 3, ctypes.c_long)(pointer))
        try:
            _check(_method(pointer, 5, ctypes.c_long, wintypes.HWND)(pointer, hwnd))
        finally:
            _check(_method(pointer, 4, ctypes.c_long, wintypes.HWND)(pointer, hwnd))
    finally:
        if pointer.value:
            _method(pointer, 2, ctypes.c_ulong)(pointer)
        if initialized in (0, 1):
            ole.CoUninitialize()
