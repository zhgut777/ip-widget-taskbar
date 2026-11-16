"""Минимальный клиент UI Automation на ctypes (без comtypes).

Нужен одному: узнать, какие области панели задач заняты (Пуск, значки, виджеты,
трей), чтобы плашка встала в свободное место. В Windows 11 содержимое панели
рисует XAML, и обычными окнами (EnumChildWindows) его не увидеть.

Номера методов в таблицах виртуальных функций взяты из UIAutomationClient.h;
при любой ошибке вызывающий код откатывается на запасной способ.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
from uuid import UUID

from .logs import log

S_OK = 0
COINIT_MULTITHREADED = 0
CLSCTX_INPROC_SERVER = 1
TREE_SCOPE_DESCENDANTS = 4

# IUIAutomation
_AUTOMATION_ELEMENT_FROM_HANDLE = 6
_AUTOMATION_CREATE_CACHE_REQUEST = 20
_AUTOMATION_CREATE_TRUE_CONDITION = 21
# IUIAutomationCacheRequest
_CACHE_ADD_PROPERTY = 3
UIA_BOUNDING_RECTANGLE = 30001
UIA_CLASS_NAME = 30012
UIA_IS_OFFSCREEN = 30022
# IUIAutomationElement
_ELEMENT_FIND_ALL = 6
_ELEMENT_FIND_ALL_BUILD_CACHE = 8
_ELEMENT_CLASS_NAME = 30
_ELEMENT_AUTOMATION_ID = 29
_ELEMENT_IS_OFFSCREEN = 38
_ELEMENT_BOUNDING_RECT = 43
_ELEMENT_CACHED_CLASS_NAME = 62
_ELEMENT_CACHED_IS_OFFSCREEN = 70
_ELEMENT_CACHED_BOUNDING_RECT = 75
# IUIAutomationElementArray
_ARRAY_LENGTH = 3
_ARRAY_GET_ELEMENT = 4
# IUnknown
_RELEASE = 2


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    u = UUID(text)
    return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


CLSID_CUIAutomation = _guid("ff48dba4-60ef-4201-aa87-54103eef594e")
IID_IUIAutomation = _guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_ole32 = ctypes.WinDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")
_ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wt.DWORD)
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = ()
_ole32.CoCreateInstance.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, wt.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_ole32.CoCreateInstance.restype = ctypes.c_long
_oleaut32.SysFreeString.argtypes = (ctypes.c_void_p,)


class _Com:
    """Указатель на COM-объект с вызовом методов по номеру в таблице виртуальных функций."""

    def __init__(self, ptr: int) -> None:
        self.ptr = ctypes.c_void_p(ptr)

    def call(self, index: int, *argtypes_and_args) -> int:
        argtypes = [a[0] for a in argtypes_and_args]
        args = [a[1] for a in argtypes_and_args]
        vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        fn_addr = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        hr = proto(fn_addr)(self.ptr, *args)
        if hr < 0:
            raise OSError(f"COM вызов #{index}: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return hr

    def out_ptr(self, index: int, *pre) -> int:
        out = ctypes.c_void_p()
        self.call(index, *pre, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out)))
        return out.value or 0

    def bstr(self, index: int) -> str:
        out = ctypes.c_void_p()
        self.call(index, (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out)))
        if not out.value:
            return ""
        try:
            return ctypes.wstring_at(out.value)
        finally:
            _oleaut32.SysFreeString(out.value)

    def release(self) -> None:
        if self.ptr.value:
            try:
                vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p))[0]
                fn = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[_RELEASE]
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(fn)(self.ptr)
            finally:
                self.ptr = ctypes.c_void_p(0)


@dataclass(frozen=True)
class Item:
    cls: str
    auto_id: str
    left: int
    top: int
    right: int
    bottom: int
    offscreen: bool


class Automation:
    """Клиент UIA. Создавать и использовать в одном потоке (COM в режиме MTA)."""

    def __init__(self) -> None:
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        self._co_inited = hr in (0, 1)         # S_OK / S_FALSE; иначе (уже STA) просто пробуем
        ptr = ctypes.c_void_p()
        hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_CUIAutomation), None, CLSCTX_INPROC_SERVER,
                                     ctypes.byref(IID_IUIAutomation), ctypes.byref(ptr))
        if hr < 0 or not ptr.value:
            raise OSError(f"CoCreateInstance(CUIAutomation): 0x{hr & 0xFFFFFFFF:08X}")
        self._auto = _Com(ptr.value)
        self._cond = _Com(self._auto.out_ptr(_AUTOMATION_CREATE_TRUE_CONDITION))
        # Кэш-запрос: сервер отдаёт нужные свойства всех элементов одним обращением,
        # а не по три вызова между процессами на каждый элемент (в разы быстрее).
        self._cache = None
        try:
            cache = _Com(self._auto.out_ptr(_AUTOMATION_CREATE_CACHE_REQUEST))
            for prop in (UIA_BOUNDING_RECTANGLE, UIA_CLASS_NAME, UIA_IS_OFFSCREEN):
                cache.call(_CACHE_ADD_PROPERTY, (ctypes.c_int, prop))
            self._cache = cache
        except OSError as exc:
            log.info("UIA: кэш-запрос недоступен (%s), работаем без него", exc)

    def scan(self, hwnd: int, with_ids: bool = False) -> list[Item]:
        """Все потомки окна (элементы панели задач) с их экранными прямоугольниками."""
        if self._cache is not None and not with_ids:
            try:
                return self._scan_cached(hwnd)
            except OSError as exc:
                log.info("UIA: кэшированный обход не удался (%s), переход на обычный", exc)
                self._cache = None
        return self._scan_plain(hwnd, with_ids)

    def _scan_cached(self, hwnd: int) -> list[Item]:
        root = _Com(self._auto.out_ptr(_AUTOMATION_ELEMENT_FROM_HANDLE, (wt.HWND, hwnd)))
        items: list[Item] = []
        try:
            arr = _Com(root.out_ptr(_ELEMENT_FIND_ALL_BUILD_CACHE,
                                    (ctypes.c_int, TREE_SCOPE_DESCENDANTS),
                                    (ctypes.c_void_p, self._cond.ptr),
                                    (ctypes.c_void_p, self._cache.ptr)))
            try:
                count = ctypes.c_int()
                arr.call(_ARRAY_LENGTH, (ctypes.POINTER(ctypes.c_int), ctypes.byref(count)))
                for i in range(count.value):
                    el = _Com(arr.out_ptr(_ARRAY_GET_ELEMENT, (ctypes.c_int, i)))
                    try:
                        rect = RECT()
                        el.call(_ELEMENT_CACHED_BOUNDING_RECT, (ctypes.POINTER(RECT), ctypes.byref(rect)))
                        off = ctypes.c_int()
                        el.call(_ELEMENT_CACHED_IS_OFFSCREEN, (ctypes.POINTER(ctypes.c_int), ctypes.byref(off)))
                        items.append(Item(el.bstr(_ELEMENT_CACHED_CLASS_NAME), "", rect.left, rect.top,
                                          rect.right, rect.bottom, bool(off.value)))
                    finally:
                        el.release()
            finally:
                arr.release()
        finally:
            root.release()
        return items

    def _scan_plain(self, hwnd: int, with_ids: bool) -> list[Item]:
        root = _Com(self._auto.out_ptr(_AUTOMATION_ELEMENT_FROM_HANDLE, (wt.HWND, hwnd)))
        items: list[Item] = []
        try:
            arr = _Com(root.out_ptr(_ELEMENT_FIND_ALL, (ctypes.c_int, TREE_SCOPE_DESCENDANTS),
                                    (ctypes.c_void_p, self._cond.ptr)))
            try:
                count = ctypes.c_int()
                arr.call(_ARRAY_LENGTH, (ctypes.POINTER(ctypes.c_int), ctypes.byref(count)))
                for i in range(count.value):
                    el = _Com(arr.out_ptr(_ARRAY_GET_ELEMENT, (ctypes.c_int, i)))
                    try:
                        rect = RECT()
                        el.call(_ELEMENT_BOUNDING_RECT, (ctypes.POINTER(RECT), ctypes.byref(rect)))
                        off = ctypes.c_int()
                        el.call(_ELEMENT_IS_OFFSCREEN, (ctypes.POINTER(ctypes.c_int), ctypes.byref(off)))
                        cls = el.bstr(_ELEMENT_CLASS_NAME)
                        aid = el.bstr(_ELEMENT_AUTOMATION_ID) if with_ids else ""
                        items.append(Item(cls, aid, rect.left, rect.top, rect.right, rect.bottom,
                                          bool(off.value)))
                    finally:
                        el.release()
            finally:
                arr.release()
        finally:
            root.release()
        return items

    def close(self) -> None:
        try:
            if self._cache is not None:
                self._cache.release()
            self._cond.release()
            self._auto.release()
        finally:
            if self._co_inited:
                _ole32.CoUninitialize()
                self._co_inited = False
