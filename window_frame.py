"""Native sizing borders for a frameless WebView2 window."""
import ctypes
from ctypes import wintypes as w


def hit_test(x, y, width, height, border):
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top and left:
        return 13  # HTTOPLEFT
    if top and right:
        return 14
    if bottom and left:
        return 16
    if bottom and right:
        return 17
    if left:
        return 10
    if right:
        return 11
    if top:
        return 12
    if bottom:
        return 15
    return None


class ResizeFrame:
    def __init__(self, window):
        self.window = window
        self.callback = None

    def install(self):
        from System import Action
        from System.Windows.Forms import Padding
        native = self.window.native

        def configure():
            user = ctypes.WinDLL("user32", use_last_error=True)
            common = ctypes.WinDLL("comctl32", use_last_error=True)
            dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
            dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
            result_type = ctypes.c_ssize_t
            callback_type = ctypes.WINFUNCTYPE(result_type, w.HWND, w.UINT,
                                               w.WPARAM, w.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
            common.DefSubclassProc.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            common.DefSubclassProc.restype = result_type
            common.SetWindowSubclass.argtypes = [w.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
            common.SetWindowSubclass.restype = w.BOOL
            user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
            user.IsZoomed.argtypes = [w.HWND]
            user.GetDpiForWindow.argtypes = [w.HWND]
            user.GetDpiForWindow.restype = w.UINT
            get_style = user.GetWindowLongPtrW
            set_style = user.SetWindowLongPtrW
            get_style.argtypes = [w.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_style.restype = ctypes.c_ssize_t
            user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, w.UINT]

            def procedure(hwnd, message, wp, lp, subclass_id, reference):
                try:
                    if message == 0x0083:  # WM_NCCALCSIZE: keep the native titlebar hidden.
                        return 0
                    if message == 0x0085:  # WM_NCPAINT: native sizing, custom dark appearance.
                        return 0
                    if message == 0x0086:
                        return 1
                    if message == 0x0084 and not user.IsZoomed(hwnd):
                        rect = w.RECT()
                        if user.GetWindowRect(hwnd, ctypes.byref(rect)):
                            x = ctypes.c_short(lp & 0xFFFF).value - rect.left
                            y = ctypes.c_short((lp >> 16) & 0xFFFF).value - rect.top
                            border = max(6, round(6 * user.GetDpiForWindow(hwnd) / 96))
                            edge = hit_test(x, y, rect.right - rect.left, rect.bottom - rect.top, border)
                            if edge is not None:
                                return edge
                except Exception:
                    pass
                return common.DefSubclassProc(hwnd, message, wp, lp)

            self.callback = callback_type(procedure)  # Keep alive for the window lifetime.
            handle = native.Handle.ToInt64()
            if not common.SetWindowSubclass(handle, self.callback, 1, 0):
                raise OSError("Unable to attach native window resize border")
            # Leave real native space around WebView2 so it cannot consume border input.
            native.Padding = Padding(max(6, round(6 * user.GetDpiForWindow(handle) / 96)))
            set_style(handle, -16, get_style(handle, -16) | 0x00040000)  # WS_THICKFRAME
            policy = ctypes.c_int(1)  # DWMNCRP_DISABLED: no light system frame around dark UI.
            dwm.DwmSetWindowAttribute(handle, 2, ctypes.byref(policy), ctypes.sizeof(policy))
            user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x0037)

        native.Invoke(Action(configure))
