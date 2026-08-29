from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import threading
from typing import Any, Callable, Protocol


DEFAULT_WINDOW_WIDTH = 1180
DEFAULT_WINDOW_HEIGHT = 780
MIN_WINDOW_WIDTH = 840
MIN_WINDOW_HEIGHT = 600
MAX_WINDOW_WIDTH = 16_384
MAX_WINDOW_HEIGHT = 16_384


@dataclass(frozen=True)
class WindowGeometry:
    width: int = DEFAULT_WINDOW_WIDTH
    height: int = DEFAULT_WINDOW_HEIGHT

    @classmethod
    def validated(cls, width: object, height: object) -> WindowGeometry | None:
        if isinstance(width, bool) or isinstance(height, bool):
            return None
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            return None
        if not math.isfinite(width) or not math.isfinite(height):
            return None
        normalized_width = int(width)
        normalized_height = int(height)
        if (
            normalized_width < MIN_WINDOW_WIDTH
            or normalized_height < MIN_WINDOW_HEIGHT
            or normalized_width > MAX_WINDOW_WIDTH
            or normalized_height > MAX_WINDOW_HEIGHT
        ):
            return None
        return cls(normalized_width, normalized_height)


class WindowGeometryStore(Protocol):
    def load(self) -> WindowGeometry: ...

    def save(self, geometry: WindowGeometry) -> object: ...


class WindowStateController:
    """Persist only a debounced, valid, normal desktop-window size.

    Native backends emit transient resize events while minimizing, maximizing,
    restoring and moving a form through off-screen sentinel coordinates. Those
    events must never replace the last useful working size. The controller
    therefore suspends recording for minimized/maximized states, validates the
    dimensions, and coalesces resize bursts into one atomic store write.
    ``recheck_normal_state_on_commit`` is reserved for native backends whose
    window-state API is safe from the Timer worker; UI-thread-only backends can
    rely on the synchronous resize validation and lifecycle suspension events.
    """

    def __init__(
        self,
        store: WindowGeometryStore,
        *,
        debounce_seconds: float = 0.4,
        normal_state: Callable[[], bool] | None = None,
        recheck_normal_state_on_commit: bool = True,
    ) -> None:
        self.store = store
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.normal_state = normal_state
        self.recheck_normal_state_on_commit = bool(recheck_normal_state_on_commit)
        self._geometry = store.load()
        self._pending: WindowGeometry | None = None
        self._suspended = False
        self._closed = False
        self._generation = 0
        self._timer: threading.Timer | None = None
        self._window: Any | None = None
        self._lock = threading.Lock()
        # Store writes are serialized so an older debounce callback can never
        # finish after a newer geometry.  An RLock also keeps lifecycle calls
        # safe if a test double (or a future store observer) re-enters close()
        # from the small synchronous save boundary.
        self._write_lock = threading.RLock()

    @property
    def geometry(self) -> WindowGeometry:
        with self._lock:
            return self._pending or self._geometry

    def attach(self, window: Any) -> None:
        with self._lock:
            if self._window is window:
                return
            if self._window is not None:
                raise RuntimeError("window state controller is already attached")
            self._window = window
        window.events.resized += self.on_resized
        window.events.minimized += self.on_minimized
        window.events.maximized += self.on_maximized
        window.events.restored += self.on_restored

    def on_resized(self, width: object, height: object) -> None:
        geometry = WindowGeometry.validated(width, height)
        if geometry is None or not self._is_normal_state():
            return
        with self._lock:
            if self._closed or self._suspended:
                return
            self._pending = geometry
            self._generation += 1
            generation = self._generation
            self._cancel_timer_locked()
            timer = threading.Timer(
                self.debounce_seconds,
                self._commit_generation,
                args=(generation,),
            )
            timer.daemon = True
            self._timer = timer
            timer.start()

    def on_minimized(self) -> None:
        self._suspend()

    def on_maximized(self) -> None:
        self._suspend()

    def on_restored(self) -> None:
        with self._lock:
            if not self._closed:
                self._suspended = False

    def flush(self) -> None:
        with self._lock:
            self._cancel_timer_locked()
            geometry = None if self._suspended else self._pending
            self._pending = None
            if geometry is not None:
                self._generation += 1
            generation = self._generation
        if geometry is not None:
            self._save(geometry, generation)
        else:
            # A timer may already be inside the tiny atomic write after moving
            # its candidate out of ``_pending``. Join it before native teardown.
            with self._write_lock:
                pass

    def close(self) -> None:
        window: Any | None
        geometry: WindowGeometry | None
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_timer_locked()
            geometry = None if self._suspended else self._pending
            self._pending = None
            if geometry is not None:
                self._generation += 1
            generation = self._generation
            window = self._window
            self._window = None
        if window is not None:
            for event_name, handler in (
                ("resized", self.on_resized),
                ("minimized", self.on_minimized),
                ("maximized", self.on_maximized),
                ("restored", self.on_restored),
            ):
                try:
                    getattr(window.events, event_name).__isub__(handler)
                except (AttributeError, ValueError):
                    pass
        if geometry is not None:
            self._save(geometry, generation)
        else:
            # If the debounce callback already claimed the pending candidate,
            # let its atomic write finish before returning from close().
            with self._write_lock:
                pass

    def _suspend(self) -> None:
        geometry: WindowGeometry | None
        with self._lock:
            if self._closed:
                return
            self._suspended = True
            self._cancel_timer_locked()
            geometry = self._pending
            self._pending = None
            if geometry is not None:
                # A resize accepted while the native window was normal is the
                # user's last useful size.  Commit it before minimize/maximize
                # instead of losing it merely because the debounce interval
                # has not elapsed yet.
                self._generation += 1
            generation = self._generation
        if geometry is not None:
            self._save(geometry, generation)
        else:
            # A timer may already have claimed the candidate immediately
            # before the lifecycle event acquired ``_lock``.  Do not
            # invalidate that confirmed write; only wait for it to finish.
            with self._write_lock:
                pass

    def _cancel_timer_locked(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()

    def _commit_generation(self, generation: int) -> None:
        # Claim write ownership before removing ``_pending``.  Lifecycle
        # flushes can then distinguish a still-pending candidate from a timer
        # that is already guaranteed to finish (or be rejected) before they
        # acquire this same lock.  This closes the tiny process-exit race where
        # close() could otherwise overtake a daemon timer between those steps.
        with self._write_lock:
            with self._lock:
                if (
                    self._closed
                    or self._suspended
                    or generation != self._generation
                    or self._pending is None
                ):
                    return
                if self.recheck_normal_state_on_commit and not self._is_normal_state():
                    # The native form can change state before pywebview
                    # delivers its independently-threaded maximize/minimize
                    # event.  Once a debounce callback observes that state,
                    # permanently reject the candidate so the later lifecycle
                    # event cannot mistake a maximized/sentinel dimension for
                    # a normal working size.
                    self._pending = None
                    self._timer = None
                    self._generation += 1
                    return
                geometry = self._pending
                self._pending = None
                self._timer = None
            self._save(geometry, generation)

    def _save(self, geometry: WindowGeometry, generation: int) -> None:
        # Serialize atomic replacements and recheck the generation after
        # acquiring ownership. A delayed older timer can therefore never
        # overwrite a newer resize that already reached the controller.
        with self._write_lock:
            with self._lock:
                if generation != self._generation:
                    return
            try:
                self.store.save(geometry)
            except Exception:
                logging.getLogger("vram_radar").exception("failed to persist desktop window size")
                return
            with self._lock:
                if generation == self._generation:
                    self._geometry = geometry

    def _is_normal_state(self) -> bool:
        if self.normal_state is None:
            return True
        try:
            return bool(self.normal_state())
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read native desktop window state")
            return False
