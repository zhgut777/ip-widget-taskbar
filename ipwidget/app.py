"""Приложение: окно-плашка на панели задач, таймеры, меню, подсказка, обновление данных."""

from __future__ import annotations

import ctypes
import threading
import time
import traceback
from typing import Optional

from . import __version__, i18n, netinfo, render, state, system, taskbar
from . import winapi as w
from .config import INTERVAL_CHOICES, LANGUAGES, PLACEMENTS, Config, load_config, save_config
from .i18n import t
from .layout import find_slot
from .logs import log
from .netwatch import NetworkWatcher
from .paths import APP_DIR, MUTEX_NAME, TIP_CLASS, WINDOW_CLASS
from .state import Status, View

# --- сообщения окну из других потоков --------------------------------------
APP_FETCH_DONE = w.WM_APP
APP_TOPFIX = w.WM_APP + 1
APP_NETCHANGE = w.WM_APP + 2
APP_LAYOUT = w.WM_APP + 3

# --- таймеры ----------------------------------------------------------------
(T_REFRESH, T_WATCH, T_RETRY, T_RAISE, T_FOCUS, T_NETEVENT, T_TIP, T_CHANGED, T_TRIM,
 T_RECHECK) = range(1, 11)

WATCH_MS = 1000            # слежение за панелью задач
TRIM_DELAY_MS = 6000       # через сколько после работы отдать неиспользуемую память системе
TIP_DELAY_MS = 450         # через сколько после наведения показать подсказку
NET_DEBOUNCE_MS = 1200     # ждём, пока сеть «успокоится», прежде чем спрашивать IP
RESUME_DELAY_MS = 6000     # после выхода из сна даём сети подняться
MIN_EVENT_REFRESH_S = 2    # не чаще, чем раз в N секунд по событиям сети
RECHECK_DELAY_MS = 3000    # после смены сети IP мог ещё не смениться — проверим ещё раз
RECHECK_COUNT = 2
RENDER_CACHE_MAX = 12

# --- идентификаторы пунктов меню ---------------------------------------------
(ID_REFRESH, ID_COPY_IP, ID_COPY_ALL, ID_ALERT_CHANGES, ID_SET_EXPECTED, ID_CLEAR_EXPECTED,
 ID_BOTH_FAMILIES, ID_AUTOSTART, ID_OPEN_LOG, ID_ABOUT) = range(1, 11)
ID_EXIT = 99
ID_INTERVAL, ID_PLACEMENT, ID_LANGUAGE = 100, 200, 300


def _error_text(kind: str) -> str:
    key = f"err_{kind}"
    text = t(key)
    return text if text != key else t("err_unknown")


class Widget:
    """Плашка «флаг · страна · IP» — прозрачное окно поверх панели задач."""

    def __init__(self) -> None:
        self._mutex = w.CreateMutexW(None, False, MUTEX_NAME)
        if ctypes.get_last_error() == w.ERROR_ALREADY_EXISTS:
            log.info("виджет уже запущен — второй экземпляр завершается")
            raise SystemExit(0)

        self.cfg: Config = load_config()
        i18n.set_language(self.cfg.language)

        # данные
        self.info: Optional[netinfo.IpInfo] = None
        self.loading = True
        self.stale = False
        self.error_text = ""
        self.fail_count = 0
        self._busy = False
        self._again = False
        self._pending = None
        self._lock = threading.Lock()
        self._fetcher = netinfo.Fetcher()
        self._changed_until = 0.0
        self._last_event_fetch = 0.0
        self._rechecks_left = 0

        # окно и отрисовка
        self.hwnd = None
        self.tip_hwnd = None
        self.scale = 1.0
        self.dark = True
        self.hover = False
        self._tracking_leave = False
        self._pressed = False
        self._visible = False
        self._tip_visible = False
        self._menu_open = False
        self._tb: Optional[taskbar.TaskbarInfo] = None
        self._pos: Optional[tuple[int, int]] = None
        self._win_w = self._win_h = 0
        self._render_cache: dict = {}
        self._owner = 0
        self._theme = taskbar.ThemeTracker()
        self._theme_sig = system.theme_signature()
        self._theme_recheck = 0
        self._ticks = 0
        self._last_app_fg = None
        self._pending_focus = None
        self._quit = False

        # обратные вызовы Win32 должны жить столько же, сколько окно
        self._wndproc = w.WNDPROC(self._on_message)
        self._tip_proc = w.WNDPROC(self._on_tip_message)
        self._winevent_cb = w.WINEVENTPROC(self._on_winevent)
        self._event_hook = None

        self._scanner = taskbar.LayoutScanner(lambda: self._post(APP_LAYOUT))
        self._netwatch = NetworkWatcher(lambda: self._post(APP_NETCHANGE))

    # ------------------------------------------------------------------
    #  Запуск
    # ------------------------------------------------------------------
    def run(self) -> None:
        hinst = w.GetModuleHandleW(None)
        self._register_class(WINDOW_CLASS, self._wndproc, hinst)
        self._register_class(TIP_CLASS, self._tip_proc, hinst)
        self._scanner.start()
        self._scanner.wait_first(1.0)       # к первой отрисовке уже знаем, где свободно
        if not self._create_windows(hinst):
            return
        self._event_hook = w.SetWinEventHook(
            w.EVENT_SYSTEM_FOREGROUND, w.EVENT_SYSTEM_FOREGROUND, None, self._winevent_cb,
            0, 0, w.WINEVENT_OUTOFCONTEXT)
        self._netwatch.start()
        self.refresh()

        msg = w.MSG()
        while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w.TranslateMessage(ctypes.byref(msg))
            w.DispatchMessageW(ctypes.byref(msg))
            if self.hwnd is None and not self._quit:
                # Окно, принадлежащее панели задач, умирает вместе с ней (перезапуск
                # explorer.exe). Ждём новую панель и создаём плашку заново.
                if not self._recreate(hinst):
                    break
        self._shutdown()

    @staticmethod
    def _register_class(name: str, proc, hinst) -> None:
        wc = w.WNDCLASSW()
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hCursor = w.LoadCursorW(None, ctypes.c_void_p(w.IDC_ARROW))
        wc.lpszClassName = name
        w.RegisterClassW(ctypes.byref(wc))        # повторная регистрация безвредна

    def _create_windows(self, hinst) -> bool:
        base = w.WS_EX_LAYERED | w.WS_EX_TOPMOST | w.WS_EX_TOOLWINDOW | w.WS_EX_NOACTIVATE
        self.hwnd = w.CreateWindowExW(base, WINDOW_CLASS, "IP-виджет", w.WS_POPUP,
                                      -32000, -32000, 10, 10, None, None, hinst, None)
        if not self.hwnd:
            log.error("CreateWindowEx не удался: %s", ctypes.get_last_error())
            return False
        # подсказка пропускает мышь сквозь себя (WS_EX_TRANSPARENT) и не крадёт фокус
        self.tip_hwnd = w.CreateWindowExW(base | w.WS_EX_TRANSPARENT, TIP_CLASS, "", w.WS_POPUP,
                                          -32000, -32000, 10, 10, None, None, hinst, None)
        self._owner = 0
        w.SetTimer(self.hwnd, T_WATCH, WATCH_MS, None)
        self._arm_interval_timer()
        self.dark = self._theme.update(taskbar.read_taskbar())
        system.apply_menu_theme(system.apps_use_light())
        self._watch()
        self._render_and_show()
        self._schedule_trim()
        return True

    def _recreate(self, hinst) -> bool:
        log.info("окно плашки закрыто системой (перезапуск проводника?) — создаю заново")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if taskbar.tray_hwnd():
                self._visible = False
                self._tip_visible = False
                self._render_cache.clear()
                return self._create_windows(hinst)
            time.sleep(0.5)
        log.error("панель задач не вернулась за 3 минуты — выход")
        return False

    def _shutdown(self) -> None:
        if self._event_hook:
            w.UnhookWinEvent(self._event_hook)
            self._event_hook = None
        self._netwatch.stop()
        self._scanner.stop()

    def _post(self, msg: int) -> None:
        """Потокобезопасно будит окно."""
        hwnd = self.hwnd
        if hwnd:
            w.PostMessageW(hwnd, msg, 0, 0)

    # ------------------------------------------------------------------
    #  Данные
    # ------------------------------------------------------------------
    def refresh(self, manual: bool = False) -> None:
        """Запрашивает IP. manual=True (клик, меню) — с надписью «Определяю…»."""
        if manual:
            self.loading = True
            self._render_and_show()
        if self._busy:
            self._again = True            # сеть могла смениться во время запроса — повторим
            return
        self._busy = True
        self._again = False
        threading.Thread(target=self._worker, name="ip-fetch", daemon=True).start()

    def _worker(self) -> None:
        try:
            info = self._fetcher.fetch(want_other_family=self.cfg.show_both_families)
            try:
                netinfo.ensure_flag(info.cc)
            except Exception as exc:  # noqa: BLE001 — без флага тоже работаем
                log.info("флаг %s не скачан: %s", info.cc, exc)
            result = ("ok", info)
        except Exception as exc:  # noqa: BLE001
            result = ("error", netinfo.classify_error(exc))
        with self._lock:
            self._pending = result
        self._post(APP_FETCH_DONE)

    def _apply_pending(self) -> None:
        with self._lock:
            result, self._pending = self._pending, None
        self._busy = False
        self.loading = False
        if result is None:
            return
        kind, payload = result
        if kind == "ok":
            changed = state.changed(self.info, payload)
            if self._rechecks_left > 0:
                if changed:
                    self._rechecks_left = 0       # новый IP уже получен — повторы не нужны
                else:
                    # Сразу после смены сети маршрут мог ещё не переключиться: IP тот же.
                    self._rechecks_left -= 1
                    w.SetTimer(self.hwnd, T_RECHECK, RECHECK_DELAY_MS, None)
            if changed:
                self._changed_until = time.monotonic() + state.CHANGE_HIGHLIGHT_S
                w.SetTimer(self.hwnd, T_CHANGED, state.CHANGE_HIGHLIGHT_S * 1000 + 200, None)
                log.info("IP или страна изменились: %s -> %s", self.info and self.info.cc, payload.cc)
            self.info = payload
            self.fail_count = 0
            self.stale = False
            self.error_text = ""
            w.KillTimer(self.hwnd, T_RETRY)
            render.clear_caches()           # мог скачаться флаг
        else:
            self.fail_count += 1
            self.error_text = _error_text(payload.kind)
            self.stale = self.info is not None      # прежние данные оставляем, но помечаем
            delay = state.retry_delay(self.fail_count)
            w.SetTimer(self.hwnd, T_RETRY, delay * 1000, None)
            log.warning("обновление не удалось (%s), повтор через %d с: %s",
                        payload.kind, delay, payload.detail)
        self._render_and_show()
        self._refresh_tip()
        self._schedule_trim()
        if self._again:
            self._again = False
            self.refresh()

    def _schedule_trim(self) -> None:
        """После всплеска работы (запуск, ответ сети) отдаём лишнюю память системе."""
        if self.hwnd:
            w.SetTimer(self.hwnd, T_TRIM, TRIM_DELAY_MS, None)   # повтор сбрасывает отсчёт

    def _arm_interval_timer(self) -> None:
        w.KillTimer(self.hwnd, T_REFRESH)
        if self.cfg.interval_min > 0:
            w.SetTimer(self.hwnd, T_REFRESH, self.cfg.interval_min * 60_000, None)

    def _network_event(self) -> None:
        """Сеть сменилась (VPN, Wi-Fi) или компьютер проснулся: спросим IP, когда всё утихнет."""
        w.SetTimer(self.hwnd, T_NETEVENT, NET_DEBOUNCE_MS, None)

    def _network_settled(self) -> None:
        w.KillTimer(self.hwnd, T_NETEVENT)
        wait = MIN_EVENT_REFRESH_S - (time.monotonic() - self._last_event_fetch)
        if wait > 0:
            w.SetTimer(self.hwnd, T_NETEVENT, int(wait * 1000) + 100, None)
            return
        self._last_event_fetch = time.monotonic()
        log.info("сеть изменилась — обновляю данные")
        self._rechecks_left = RECHECK_COUNT
        self.refresh()

    # ------------------------------------------------------------------
    #  Вид
    # ------------------------------------------------------------------
    def _view(self) -> View:
        if self.loading:
            return View(Status.LOADING)
        info = self.info
        if info is None:
            return View(Status.ERROR)
        recent = time.monotonic() < self._changed_until
        return View(Status.OK, info.cc, i18n.country_name(info.cc, info.country_en), info.ip,
                    self.stale,
                    state.alert_for(info, self.cfg.expected_cc, self.cfg.alert_on_change, recent))

    def _render_and_show(self) -> None:
        if not self.hwnd:
            return
        view = self._view()
        key = (view, self.hover, self.dark, round(self.scale, 3), i18n.current_language())
        cached = self._render_cache.get(key)
        if cached is None:
            img = render.render_plate(view, self.scale, self.hover, self.dark)
            cached = (img, render.premultiplied_bgra(img))
            if len(self._render_cache) >= RENDER_CACHE_MAX:
                self._render_cache.pop(next(iter(self._render_cache)))
            self._render_cache[key] = cached
        img, data = cached
        self._win_w, self._win_h = img.size
        self._pos = self._compute_pos()
        self._present(self.hwnd, img, data, self._pos)

    def _compute_pos(self) -> tuple[int, int]:
        tb = self._tb
        if tb is None:
            return self._pos or (-32000, -32000)
        s = tb.scale
        margin = int(round(10 * s))
        left, top, right, bottom = tb.rect
        if tb.horizontal and (bottom - top) >= int(round(24 * s)):
            occupied, _rect = self._scanner.snapshot()
            prefer = "right" if self.cfg.placement == "right" else "left"
            x = find_slot(left, right, occupied, self._win_w, margin, prefer)
            if x is not None:
                return x, top + max(0, (bottom - top - self._win_h) // 2)
        # Места нет (или панель вертикальная): плашка «паркуется» над/под краем панели.
        mon_top, mon_bottom = tb.monitor[1], tb.monitor[3]
        y = top - self._win_h - margin if top - mon_top > (mon_bottom - mon_top) // 2 \
            else bottom + margin
        return left + margin, y

    @staticmethod
    def _present(hwnd, img, data: bytes, pos: tuple[int, int]) -> None:
        """Показывает картинку с попиксельной прозрачностью через UpdateLayeredWindow."""
        width, height = img.size
        screen_dc = w.GetDC(None)
        mem_dc = w.CreateCompatibleDC(screen_dc)
        try:
            bmi = w.BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(w.BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height          # сверху вниз
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            bitmap = w.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not bitmap:
                return
            old = w.SelectObject(mem_dc, bitmap)
            ctypes.memmove(bits, data, len(data))
            w.GdiFlush()
            blend = w.BLENDFUNCTION(w.AC_SRC_OVER, 0, 255, w.AC_SRC_ALPHA)
            w.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(w.POINT(pos[0], pos[1])),
                                  ctypes.byref(w.SIZE(width, height)), mem_dc,
                                  ctypes.byref(w.POINT(0, 0)), 0, ctypes.byref(blend), w.ULW_ALPHA)
            w.SelectObject(mem_dc, old)
            w.DeleteObject(bitmap)
        finally:
            w.DeleteDC(mem_dc)
            w.ReleaseDC(None, screen_dc)

    def _move_to(self, x: int, y: int) -> None:
        self._pos = (x, y)
        w.SetWindowPos(self.hwnd, None, x, y, 0, 0, w.SWP_NOSIZE | w.SWP_NOZORDER | w.SWP_NOACTIVATE)

    def _set_visible(self, visible: bool) -> None:
        if visible == self._visible or not self.hwnd:
            return
        self._visible = visible
        log.debug("плашка: %s", "показана" if visible else "скрыта")
        if visible:
            w.ShowWindow(self.hwnd, w.SW_SHOWNOACTIVATE)
            w.SetWindowPos(self.hwnd, w.HWND_TOPMOST, 0, 0, 0, 0,
                           w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE)
        else:
            self._hide_tip()
            w.ShowWindow(self.hwnd, w.SW_HIDE)

    # ------------------------------------------------------------------
    #  Слежение за панелью задач (раз в секунду, дёшево)
    # ------------------------------------------------------------------
    def _watch(self) -> None:
        if not self.hwnd:
            return
        tb = taskbar.read_taskbar()
        if tb is None or taskbar.fullscreen_app_running():
            self._tb = None
            self._set_visible(False)       # панель скрыта или игра/видео на весь экран
            return
        self._tb = tb
        self._update_owner(tb.hwnd)

        redraw = False
        if abs(tb.scale - self.scale) > 0.01:      # другой монитор или масштаб
            self.scale = tb.scale
            self._render_cache.clear()
            render.clear_caches()
            redraw = True

        self._ticks += 1
        signature = system.theme_signature()
        if signature != self._theme_sig:
            self._theme_sig = signature
            self._theme_recheck = 4
        if self._theme_recheck > 0 or self._ticks % 30 == 0:
            # панель перекрашивается с небольшой задержкой, поэтому проверяем несколько тиков
            self._theme_recheck = max(0, self._theme_recheck - 1)
            dark = self._theme.update(tb)
            if dark != self.dark:
                self.dark = dark
                self._render_cache.clear()
                redraw = True

        pos = self._compute_pos()
        if redraw:
            self._render_and_show()
        elif pos != self._pos:
            self._move_to(*pos)
        self._set_visible(True)
        self._enforce_top()

    def _update_owner(self, tray: int) -> None:
        """Владелец окна — панель задач: владелец всегда ниже своего окна."""
        if tray and tray != self._owner and taskbar.set_owner(self.hwnd, tray):
            self._owner = tray

    def _enforce_top(self) -> None:
        """Возвращает плашку над панелью задач, если клик по панели поднял её выше."""
        if not self._visible or not self.hwnd or taskbar.shell_popup_open():
            return
        tray = taskbar.tray_hwnd()
        if not taskbar.window_above(self.hwnd, tray):
            return
        w.SetWindowPos(self.hwnd, w.HWND_TOPMOST, 0, 0, 0, 0,
                       w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE)
        # активация панели заканчивается чуть позже — проверим ещё раз одним выстрелом
        w.SetTimer(self.hwnd, T_RAISE, 25, None)

    def _on_theme_changed(self) -> None:
        self._theme_sig = system.theme_signature()
        self._theme_recheck = 5
        self._render_cache.clear()
        system.apply_menu_theme(system.apps_use_light())
        self.dark = self._theme.update(self._tb)
        self._render_and_show()

    # ------------------------------------------------------------------
    #  Подсказка
    # ------------------------------------------------------------------
    def _tip_image(self):
        expected = i18n.country_name(self.cfg.expected_cc) if self.cfg.expected_cc else ""
        data = render.build_tip(self.info, self._view(), self.error_text, expected,
                                self.cfg.show_both_families)
        return render.render_tip(data, self.scale, self.dark)

    def _show_tip(self) -> None:
        if self._tip_visible or self._menu_open or not self.hover or not self.tip_hwnd \
                or not self._visible or not self._pos:
            return
        img = self._tip_image()
        mon = self._tb.monitor if self._tb else (0, 0, 1920, 1080)
        gap = int(round(8 * self.scale))
        x = max(mon[0] + gap, min(self._pos[0], mon[2] - img.width - gap))
        y = self._pos[1] - img.height - gap
        if y < mon[1]:                                   # панель сверху — показываем под ней
            y = self._pos[1] + self._win_h + gap
        log.debug("подсказка: показываю %dx%d в (%d, %d)", img.width, img.height, x, y)
        self._present(self.tip_hwnd, img, render.premultiplied_bgra(img), (x, y))
        w.ShowWindow(self.tip_hwnd, w.SW_SHOWNOACTIVATE)
        w.SetWindowPos(self.tip_hwnd, w.HWND_TOPMOST, 0, 0, 0, 0,
                       w.SWP_NOMOVE | w.SWP_NOSIZE | w.SWP_NOACTIVATE)
        self._tip_visible = True

    def _hide_tip(self) -> None:
        if self.hwnd:
            w.KillTimer(self.hwnd, T_TIP)
        if self._tip_visible and self.tip_hwnd:
            log.debug("подсказка: скрываю")
            w.ShowWindow(self.tip_hwnd, w.SW_HIDE)
        self._tip_visible = False

    def _refresh_tip(self) -> None:
        """Данные обновились, пока подсказка открыта, — перерисовываем её."""
        if self._tip_visible:
            self._tip_visible = False
            self._show_tip()

    # ------------------------------------------------------------------
    #  Меню
    # ------------------------------------------------------------------
    def _focusable_target(self, hwnd) -> bool:
        """Можно ли вернуть фокус этому окну (панель задач и всплывашки — нет)."""
        if not hwnd or hwnd in (self.hwnd, self.tip_hwnd) or not w.IsWindow(hwnd):
            return False
        if hwnd == taskbar.tray_hwnd():
            return False
        name = taskbar.window_class(hwnd)
        return bool(name) and name not in taskbar.SHELL_POPUP_CLASSES

    def _force_foreground(self, previous) -> None:
        """Активирует окно, чтобы контекстное меню не закрывалось сразу."""
        if w.SetForegroundWindow(self.hwnd):
            return
        other = w.GetWindowThreadProcessId(previous, None) if previous else 0
        mine = w.GetCurrentThreadId()
        if other and other != mine:
            w.AttachThreadInput(mine, other, True)
            w.SetForegroundWindow(self.hwnd)
            w.AttachThreadInput(mine, other, False)

    def _restore_foreground(self, previous) -> None:
        """Возвращает фокус окну, которое было активным до меню."""
        if not previous or previous == self.hwnd:
            return
        mine = w.GetCurrentThreadId()
        for _ in range(3):
            current = w.GetForegroundWindow()
            if current == previous:
                return
            threads = {w.GetWindowThreadProcessId(h, None) for h in (current, previous) if h}
            attached = [th for th in threads if th and th != mine]
            for th in attached:
                w.AttachThreadInput(mine, th, True)
            w.SetForegroundWindow(previous)
            for th in attached:
                w.AttachThreadInput(mine, th, False)
            time.sleep(0.02)

    def _menu_anchor(self) -> tuple[int, int, int]:
        """Меню над плашкой, а если сверху нет места — под ней."""
        if not self._pos:
            cursor = w.POINT()
            w.GetCursorPos(ctypes.byref(cursor))
            return cursor.x, cursor.y, 0
        s = self.scale
        left, top = self._pos
        above = top - int(round(6 * s))
        if above > int(round(24 * s)):
            return left + int(round(3 * s)), above, w.TPM_BOTTOMALIGN
        return left + int(round(3 * s)), top + self._win_h + int(round(6 * s)), 0

    @staticmethod
    def _add(menu, label: str, item_id: int, checked: bool = False, disabled: bool = False) -> None:
        flags = w.MF_STRING | (w.MF_CHECKED if checked else 0) | (w.MF_GRAYED if disabled else 0)
        w.AppendMenuW(menu, flags, item_id, label)

    @staticmethod
    def _add_submenu(menu, label: str) -> ctypes.c_void_p:
        sub = w.CreatePopupMenu()
        w.AppendMenuW(menu, w.MF_POPUP | w.MF_STRING, sub, label)
        return sub

    def _build_menu(self):
        cfg, info = self.cfg, self.info
        menu = w.CreatePopupMenu()
        self._add(menu, t("menu_refresh"), ID_REFRESH)

        sub = self._add_submenu(menu, t("menu_interval"))
        for index, minutes in enumerate(INTERVAL_CHOICES):
            self._add(sub, t(f"interval_{minutes}"), ID_INTERVAL + index, minutes == cfg.interval_min)

        self._add(menu, t("menu_copy_ip"), ID_COPY_IP, disabled=info is None)
        self._add(menu, t("menu_copy_all"), ID_COPY_ALL, disabled=info is None)
        w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)

        sub = self._add_submenu(menu, t("menu_alerts"))
        self._add(sub, t("alert_on_change"), ID_ALERT_CHANGES, cfg.alert_on_change)
        if cfg.expected_cc:
            self._add(sub, t("alert_clear_expected", name=i18n.country_name(cfg.expected_cc)),
                      ID_CLEAR_EXPECTED)
        if info is not None and info.cc and info.cc != cfg.expected_cc:
            self._add(sub, t("alert_set_expected", name=i18n.country_name(info.cc, info.country_en)),
                      ID_SET_EXPECTED)

        sub = self._add_submenu(menu, t("menu_placement"))
        for index, value in enumerate(PLACEMENTS):
            self._add(sub, t(f"placement_{value}"), ID_PLACEMENT + index, value == cfg.placement)
        sub = self._add_submenu(menu, t("menu_language"))
        for index, value in enumerate(LANGUAGES):
            label = t("language_auto") if value == "auto" else {"ru": "Русский", "en": "English"}[value]
            self._add(sub, label, ID_LANGUAGE + index, value == cfg.language)
        w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)

        self._add(menu, t("menu_both_families"), ID_BOTH_FAMILIES, cfg.show_both_families)
        self._add(menu, t("menu_autostart"), ID_AUTOSTART, system.autostart_enabled())
        self._add(menu, t("menu_open_log"), ID_OPEN_LOG)
        w.AppendMenuW(menu, w.MF_SEPARATOR, 0, None)
        self._add(menu, t("menu_about", version=__version__), ID_ABOUT, disabled=True)
        self._add(menu, t("menu_exit"), ID_EXIT)
        return menu

    def _show_menu(self) -> None:
        self._hide_tip()
        current = w.GetForegroundWindow()
        previous = current if self._focusable_target(current) else self._last_app_fg
        x, y, align = self._menu_anchor()

        # Меню Windows требует активное окно-владелец, а плашка живёт как NOACTIVATE:
        # на время меню разрешаем активацию и потом возвращаем фокус прежнему окну.
        style = w.GetWindowLongW(self.hwnd, w.GWL_EXSTYLE)
        w.SetWindowLongW(self.hwnd, w.GWL_EXSTYLE, style & ~w.WS_EX_NOACTIVATE)
        cmd = 0
        self._menu_open = True
        try:
            self._force_foreground(previous)
            system.apply_menu_theme(system.apps_use_light())
            menu = self._build_menu()
            try:
                cmd = w.TrackPopupMenu(menu, w.TPM_RETURNCMD | w.TPM_RIGHTBUTTON | align,
                                       x, y, 0, self.hwnd, None)
                w.PostMessageW(self.hwnd, 0, 0, 0)        # закрывает меню без «залипания»
            finally:
                w.DestroyMenu(menu)                       # вместе с подменю
        finally:
            self._menu_open = False
            self._restore_foreground(previous)
            w.SetWindowLongW(self.hwnd, w.GWL_EXSTYLE, style)
            self._restore_foreground(previous)
            # окно, «принадлежащее» панели, может отдать ей фокус позже — проверим ещё раз
            self._pending_focus = previous
            w.SetTimer(self.hwnd, T_FOCUS, 150, None)
        if cmd:
            self._menu_action(cmd)

    def _save(self) -> None:
        save_config(self.cfg)

    def _menu_action(self, cmd: int) -> None:
        cfg, info = self.cfg, self.info
        try:
            if cmd == ID_REFRESH:
                self.refresh(manual=True)
            elif ID_INTERVAL <= cmd < ID_INTERVAL + len(INTERVAL_CHOICES):
                cfg.interval_min = INTERVAL_CHOICES[cmd - ID_INTERVAL]
                self._save()
                self._arm_interval_timer()
            elif ID_PLACEMENT <= cmd < ID_PLACEMENT + len(PLACEMENTS):
                cfg.placement = PLACEMENTS[cmd - ID_PLACEMENT]
                self._save()
                self._render_and_show()
            elif ID_LANGUAGE <= cmd < ID_LANGUAGE + len(LANGUAGES):
                cfg.language = LANGUAGES[cmd - ID_LANGUAGE]
                i18n.set_language(cfg.language)
                self._save()
                self._render_cache.clear()
                self._render_and_show()
            elif cmd == ID_COPY_IP and info:
                system.copy_text(info.ip)
            elif cmd == ID_COPY_ALL and info:
                system.copy_text(f"{i18n.country_name(info.cc, info.country_en)} — {info.ip}")
            elif cmd == ID_ALERT_CHANGES:
                cfg.alert_on_change = not cfg.alert_on_change
                self._save()
                self._render_and_show()
            elif cmd == ID_SET_EXPECTED and info:
                cfg.expected_cc = info.cc
                self._save()
                self._render_and_show()
            elif cmd == ID_CLEAR_EXPECTED:
                cfg.expected_cc = ""
                self._save()
                self._render_and_show()
            elif cmd == ID_BOTH_FAMILIES:
                cfg.show_both_families = not cfg.show_both_families
                self._save()
                if cfg.show_both_families:
                    self.refresh()
            elif cmd == ID_AUTOSTART:
                system.set_autostart(not system.autostart_enabled())
            elif cmd == ID_OPEN_LOG:
                system.open_folder(APP_DIR)
            elif cmd == ID_EXIT:
                self._quit = True
                w.DestroyWindow(self.hwnd)
        except Exception:  # noqa: BLE001
            log.error("ошибка в пункте меню %s:\n%s", cmd, traceback.format_exc())

    # ------------------------------------------------------------------
    #  Оконные процедуры
    # ------------------------------------------------------------------
    def _on_winevent(self, _hook, event, hwnd, _obj, _child, _thread, _when) -> None:
        """Сменилось активное окно: клик по панели поднимает её над плашкой, а
        запуск/закрытие программ двигает значки — перепроверяем сразу."""
        try:
            if event == w.EVENT_SYSTEM_FOREGROUND and hwnd:
                if self._focusable_target(hwnd):
                    self._last_app_fg = hwnd
                self._post(APP_TOPFIX)
                self._scanner.poke()
        except Exception:  # noqa: BLE001 — исключения из системного обратного вызова недопустимы
            pass

    def _on_tip_message(self, hwnd, msg, wparam, lparam):
        return w.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == APP_FETCH_DONE:
                self._apply_pending()
                return 0
            if msg == APP_TOPFIX:
                self._enforce_top()
                return 0
            if msg == APP_NETCHANGE:
                self._network_event()
                return 0
            if msg == APP_LAYOUT:
                self._watch()
                return 0
            if msg == w.WM_TIMER:
                self._on_timer(int(wparam))
                return 0
            if msg == w.WM_SETTINGCHANGE:
                text = ctypes.cast(ctypes.c_void_p(lparam), ctypes.c_wchar_p).value if lparam else None
                if text in ("ImmersiveColorSet", "ImmersiveColorPolicySet"):
                    self._on_theme_changed()
                return 0
            if msg == w.WM_THEMECHANGED:
                self._on_theme_changed()
                return 0
            if msg == w.WM_DISPLAYCHANGE:
                self._watch()
                return 0
            if msg == w.WM_POWERBROADCAST:
                if int(wparam) in (w.PBT_APMRESUMEAUTOMATIC, w.PBT_APMRESUMESUSPEND):
                    w.SetTimer(hwnd, T_NETEVENT, RESUME_DELAY_MS, None)
                return 1
            if msg == w.WM_MOUSEACTIVATE:
                return w.MA_NOACTIVATE
            if msg == w.WM_NCHITTEST:
                return w.HTCLIENT
            if msg == w.WM_LBUTTONDOWN:
                self._pressed = True
                self._hide_tip()
                return 0
            if msg == w.WM_LBUTTONUP:
                if self._pressed:
                    self._pressed = False
                    self.refresh(manual=True)
                return 0
            if msg == w.WM_RBUTTONUP:
                self._show_menu()
                return 0
            if msg == w.WM_MOUSEMOVE:
                if not self.hover:
                    self.hover = True
                    self._render_and_show()
                    w.SetTimer(hwnd, T_TIP, TIP_DELAY_MS, None)
                if not self._tracking_leave:
                    tme = w.TRACKMOUSEEVENT(ctypes.sizeof(w.TRACKMOUSEEVENT), 0x00000002, hwnd, 0)
                    w.TrackMouseEvent(ctypes.byref(tme))
                    self._tracking_leave = True
                return 0
            if msg == w.WM_MOUSELEAVE:
                self._tracking_leave = False
                self._pressed = False
                self.hover = False
                self._hide_tip()
                self._render_and_show()
                return 0
            if msg == w.WM_SETCURSOR:
                w.SetCursor(w.LoadCursorW(None, ctypes.c_void_p(w.IDC_HAND)))
                return 1
            if msg == w.WM_CLOSE:
                self._quit = True
                w.DestroyWindow(hwnd)
                return 0
            if msg == w.WM_DESTROY:
                for timer in range(1, 9):
                    w.KillTimer(hwnd, timer)
                if self.tip_hwnd:
                    w.DestroyWindow(self.tip_hwnd)
                    self.tip_hwnd = None
                self.hwnd = None
                if self._quit:
                    w.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            log.error("ошибка в оконной процедуре (сообщение 0x%X):\n%s", msg, traceback.format_exc())
        return w.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_timer(self, timer: int) -> None:
        hwnd = self.hwnd
        if timer == T_WATCH:
            self._watch()
        elif timer == T_REFRESH or timer == T_RETRY:
            if timer == T_RETRY:
                w.KillTimer(hwnd, T_RETRY)
            self.refresh()
        elif timer == T_NETEVENT:
            self._network_settled()
        elif timer == T_RECHECK:
            w.KillTimer(hwnd, T_RECHECK)
            self.refresh()
        elif timer == T_TIP:
            w.KillTimer(hwnd, T_TIP)
            self._show_tip()
        elif timer == T_CHANGED:
            w.KillTimer(hwnd, T_CHANGED)
            self._render_and_show()                 # подсветка «изменилось» закончилась
        elif timer == T_TRIM:
            w.KillTimer(hwnd, T_TRIM)
            system.trim_memory()
        elif timer == T_RAISE:
            w.KillTimer(hwnd, T_RAISE)
            self._enforce_top()
        elif timer == T_FOCUS:
            w.KillTimer(hwnd, T_FOCUS)
            previous, self._pending_focus = self._pending_focus, None
            if previous and w.GetForegroundWindow() in (0, self.hwnd, taskbar.tray_hwnd()):
                self._restore_foreground(previous)
