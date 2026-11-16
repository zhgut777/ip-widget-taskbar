"""Тонкая обёртка над Win32 (ctypes): структуры, константы, подписи функций."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

if sys.platform != "win32":      # pragma: no cover — модуль нужен только в Windows
    raise ImportError("ipwidget.winapi работает только в Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8
WPARAM = ctypes.c_ulonglong if IS_64 else ctypes.c_ulong
LPARAM = ctypes.c_longlong if IS_64 else ctypes.c_long
LRESULT = ctypes.c_longlong if IS_64 else ctypes.c_long
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG,
                                  wt.DWORD, wt.DWORD)
CHILDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
# VOID CALLBACK (PVOID context, PVOID row, MIB_NOTIFICATION_TYPE type)
NETCHANGEPROC = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)

# --- сообщения и стили -------------------------------------------------------
WM_DESTROY, WM_CLOSE, WM_TIMER, WM_COMMAND = 0x0002, 0x0010, 0x0113, 0x0111
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP = 0x0200, 0x0201, 0x0202, 0x0205
WM_MOUSELEAVE, WM_SETCURSOR, WM_APP = 0x02A3, 0x0020, 0x8000
WM_NCHITTEST, WM_MOUSEACTIVATE = 0x0084, 0x0021
WM_SETTINGCHANGE, WM_THEMECHANGED, WM_POWERBROADCAST = 0x001A, 0x031A, 0x0218
WM_DISPLAYCHANGE = 0x007E
PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC = 0x0007, 0x0012
MA_NOACTIVATE = 3
HTCLIENT = 1
GW_HWNDPREV = 3
GWLP_HWNDPARENT = -8
GWL_EXSTYLE = -20
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x00080000, 0x00000008, 0x00000080
WS_EX_NOACTIVATE, WS_EX_TRANSPARENT = 0x08000000, 0x00000020
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x00000002, 0x00, 0x01
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
IDC_ARROW, IDC_HAND = 32512, 32649
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_BOTTOMALIGN = 0x0100, 0x0002, 0x0020
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED, MF_POPUP = 0x000, 0x800, 0x008, 0x001, 0x010
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
ERROR_ALREADY_EXISTS = 183
MONITOR_DEFAULTTONEAREST = 2
QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE = 2, 3, 4
AF_UNSPEC = 0

HWND_TOPMOST = ctypes.c_void_p((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1)
HWND_NOTOPMOST = ctypes.c_void_p((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 2)


# --- структуры ---------------------------------------------------------------
class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", WPARAM),
                ("lParam", LPARAM), ("time", wt.DWORD), ("pt", POINT), ("lPrivate", wt.DWORD)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("dwFlags", wt.DWORD), ("hwndTrack", wt.HWND),
                ("dwHoverTime", wt.DWORD)]


class OSVERSIONINFOW(ctypes.Structure):
    _fields_ = [("size", wt.ULONG), ("major", wt.ULONG), ("minor", wt.ULONG), ("build", wt.ULONG),
                ("platform", wt.ULONG), ("csd", wt.WCHAR * 128)]


def _bind(lib, name, restype, *argtypes):
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# --- user32 ------------------------------------------------------------------
DefWindowProcW = _bind(user32, "DefWindowProcW", LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
CreateWindowExW = _bind(user32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR,
                        wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                        wt.HMENU, wt.HINSTANCE, ctypes.c_void_p)
RegisterClassW = _bind(user32, "RegisterClassW", wt.ATOM, ctypes.POINTER(WNDCLASSW))
UpdateLayeredWindow = _bind(user32, "UpdateLayeredWindow", wt.BOOL, wt.HWND, wt.HDC,
                            ctypes.POINTER(POINT), ctypes.POINTER(SIZE), wt.HDC,
                            ctypes.POINTER(POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD)
SetWindowPos = _bind(user32, "SetWindowPos", wt.BOOL, wt.HWND, ctypes.c_void_p, ctypes.c_int,
                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
GetCursorPos = _bind(user32, "GetCursorPos", wt.BOOL, ctypes.POINTER(POINT))
LoadCursorW = _bind(user32, "LoadCursorW", wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p)
SetCursor = _bind(user32, "SetCursor", wt.HANDLE, wt.HANDLE)
SetTimer = _bind(user32, "SetTimer", ctypes.c_size_t, wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p)
KillTimer = _bind(user32, "KillTimer", wt.BOOL, wt.HWND, ctypes.c_size_t)
PostMessageW = _bind(user32, "PostMessageW", wt.BOOL, wt.HWND, wt.UINT, WPARAM, LPARAM)
DestroyWindow = _bind(user32, "DestroyWindow", wt.BOOL, wt.HWND)
ShowWindow = _bind(user32, "ShowWindow", wt.BOOL, wt.HWND, ctypes.c_int)
GetMessageW = _bind(user32, "GetMessageW", ctypes.c_int, ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT)
TranslateMessage = _bind(user32, "TranslateMessage", wt.BOOL, ctypes.POINTER(MSG))
DispatchMessageW = _bind(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(MSG))
PostQuitMessage = _bind(user32, "PostQuitMessage", None, ctypes.c_int)
CreatePopupMenu = _bind(user32, "CreatePopupMenu", wt.HMENU)
AppendMenuW = _bind(user32, "AppendMenuW", wt.BOOL, wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR)
TrackPopupMenu = _bind(user32, "TrackPopupMenu", ctypes.c_int, wt.HMENU, wt.UINT, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, wt.HWND, ctypes.c_void_p)
DestroyMenu = _bind(user32, "DestroyMenu", wt.BOOL, wt.HMENU)
SetForegroundWindow = _bind(user32, "SetForegroundWindow", wt.BOOL, wt.HWND)
OpenClipboard = _bind(user32, "OpenClipboard", wt.BOOL, wt.HWND)
EmptyClipboard = _bind(user32, "EmptyClipboard", wt.BOOL)
SetClipboardData = _bind(user32, "SetClipboardData", wt.HANDLE, wt.UINT, wt.HANDLE)
CloseClipboard = _bind(user32, "CloseClipboard", wt.BOOL)
TrackMouseEvent = _bind(user32, "TrackMouseEvent", wt.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
GetDC = _bind(user32, "GetDC", wt.HDC, wt.HWND)
ReleaseDC = _bind(user32, "ReleaseDC", ctypes.c_int, wt.HWND, wt.HDC)
FindWindowW = _bind(user32, "FindWindowW", wt.HWND, wt.LPCWSTR, wt.LPCWSTR)
GetWindowRect = _bind(user32, "GetWindowRect", wt.BOOL, wt.HWND, ctypes.POINTER(RECT))
IsWindowVisible = _bind(user32, "IsWindowVisible", wt.BOOL, wt.HWND)
IsWindow = _bind(user32, "IsWindow", wt.BOOL, wt.HWND)
GetForegroundWindow = _bind(user32, "GetForegroundWindow", wt.HWND)
GetWindowLongW = _bind(user32, "GetWindowLongW", ctypes.c_long, wt.HWND, ctypes.c_int)
SetWindowLongW = _bind(user32, "SetWindowLongW", ctypes.c_long, wt.HWND, ctypes.c_int, ctypes.c_long)
SetWindowLongPtrW = _bind(user32, "SetWindowLongPtrW", ctypes.c_ssize_t, wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
GetWindowThreadProcessId = _bind(user32, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
AttachThreadInput = _bind(user32, "AttachThreadInput", wt.BOOL, wt.DWORD, wt.DWORD, wt.BOOL)
GetClassNameW = _bind(user32, "GetClassNameW", ctypes.c_int, wt.HWND, ctypes.c_wchar_p, ctypes.c_int)
GetWindow = _bind(user32, "GetWindow", wt.HWND, wt.HWND, wt.UINT)
SetWinEventHook = _bind(user32, "SetWinEventHook", wt.HANDLE, wt.DWORD, wt.DWORD, wt.HMODULE,
                        WINEVENTPROC, wt.DWORD, wt.DWORD, wt.DWORD)
UnhookWinEvent = _bind(user32, "UnhookWinEvent", wt.BOOL, wt.HANDLE)
MonitorFromWindow = _bind(user32, "MonitorFromWindow", wt.HANDLE, wt.HWND, wt.DWORD)
GetMonitorInfoW = _bind(user32, "GetMonitorInfoW", wt.BOOL, wt.HANDLE, ctypes.POINTER(MONITORINFO))
SystemParametersInfoW = _bind(user32, "SystemParametersInfoW", wt.BOOL, wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT)
RegisterWindowMessageW = _bind(user32, "RegisterWindowMessageW", wt.UINT, wt.LPCWSTR)
WindowFromPoint = _bind(user32, "WindowFromPoint", wt.HWND, POINT)
EnumChildWindows = _bind(user32, "EnumChildWindows", wt.BOOL, wt.HWND, CHILDENUMPROC, wt.LPARAM)

# --- gdi32 -------------------------------------------------------------------
CreateCompatibleDC = _bind(gdi32, "CreateCompatibleDC", wt.HDC, wt.HDC)
CreateDIBSection = _bind(gdi32, "CreateDIBSection", wt.HBITMAP, wt.HDC, ctypes.POINTER(BITMAPINFO),
                         wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
SelectObject = _bind(gdi32, "SelectObject", wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
DeleteObject = _bind(gdi32, "DeleteObject", wt.BOOL, wt.HGDIOBJ)
DeleteDC = _bind(gdi32, "DeleteDC", wt.BOOL, wt.HDC)
GetPixel = _bind(gdi32, "GetPixel", wt.DWORD, wt.HDC, ctypes.c_int, ctypes.c_int)
GdiFlush = _bind(gdi32, "GdiFlush", wt.BOOL)

# --- kernel32 / shell32 / iphlpapi -----------------------------------------------
GetModuleHandleW = _bind(kernel32, "GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
CreateMutexW = _bind(kernel32, "CreateMutexW", wt.HANDLE, ctypes.c_void_p, wt.BOOL, wt.LPCWSTR)
GetCurrentThreadId = _bind(kernel32, "GetCurrentThreadId", wt.DWORD)
GlobalAlloc = _bind(kernel32, "GlobalAlloc", wt.HGLOBAL, wt.UINT, ctypes.c_size_t)
GlobalFree = _bind(kernel32, "GlobalFree", wt.HGLOBAL, wt.HGLOBAL)
GlobalLock = _bind(kernel32, "GlobalLock", ctypes.c_void_p, wt.HGLOBAL)
GlobalUnlock = _bind(kernel32, "GlobalUnlock", wt.BOOL, wt.HGLOBAL)
ShellExecuteW = _bind(shell32, "ShellExecuteW", wt.HINSTANCE, wt.HWND, wt.LPCWSTR, wt.LPCWSTR,
                      wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int)
SHQueryUserNotificationState = _bind(shell32, "SHQueryUserNotificationState", ctypes.c_long,
                                     ctypes.POINTER(ctypes.c_int))
NotifyIpInterfaceChange = _bind(iphlpapi, "NotifyIpInterfaceChange", wt.ULONG, wt.ULONG, NETCHANGEPROC,
                                ctypes.c_void_p, wt.BOOLEAN, ctypes.POINTER(wt.HANDLE))
NotifyRouteChange2 = _bind(iphlpapi, "NotifyRouteChange2", wt.ULONG, wt.ULONG, NETCHANGEPROC,
                           ctypes.c_void_p, wt.BOOLEAN, ctypes.POINTER(wt.HANDLE))
CancelMibChangeNotify2 = _bind(iphlpapi, "CancelMibChangeNotify2", wt.ULONG, wt.HANDLE)
RtlGetVersion = _bind(ntdll, "RtlGetVersion", ctypes.c_long, ctypes.POINTER(OSVERSIONINFOW))


# --- необязательные функции (есть не во всех сборках) ------------------------------
def _optional(lib, name, restype, *argtypes):
    try:
        return _bind(lib, name, restype, *argtypes)
    except AttributeError:
        return None


GetDpiForWindow = _optional(user32, "GetDpiForWindow", wt.UINT, wt.HWND)
GetDpiForSystem = _optional(user32, "GetDpiForSystem", wt.UINT)
SetProcessDpiAwarenessContext = _optional(user32, "SetProcessDpiAwarenessContext", wt.BOOL, ctypes.c_void_p)
SetProcessDPIAware = _optional(user32, "SetProcessDPIAware", wt.BOOL)


def windows_build() -> int:
    """Номер сборки Windows (например, 22631 для 23H2); 0, если узнать не удалось."""
    info = OSVERSIONINFOW()
    info.size = ctypes.sizeof(OSVERSIONINFOW)
    return int(info.build) if RtlGetVersion(ctypes.byref(info)) == 0 else 0
