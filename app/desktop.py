"""把课表固定到桌面（半透明、贴在壁纸层、不挡鼠标）以及托盘图标。

桌面课表会尽量挂到资源管理器的桌面 WorkerW 下面：这样它显示在壁纸之上、
**桌面图标与所有窗口之下**，像一层贴在桌面上的课表。挂接失败时退回到
「普通窗口 + 置底」的方式。
"""
from __future__ import annotations

import zlib

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget

from .models import DAY_NAMES

# 桌面课表的默认不透明度（0.3 = 30%），可在「设置 → 外观」里用滑块调整
DESKTOP_OPACITY = 0.3
MIN_DESKTOP_OPACITY = 0.10
MAX_DESKTOP_OPACITY = 1.00

_UI_FONT = "Microsoft YaHei UI"

# 桌面课表固定使用深色底 + 浅色字：整层只有 30% 不透明度，
# 深色底压暗桌面、浅色字提亮，这样在浅色和深色壁纸上都还能看清。
_BG = "#263238"
_HEADER = "#37474F"
_LINE = "#455A64"
_TEXT = "#ECEFF1"
_PERIOD = "#B0BEC5"
_BLOCK_TEXT = "#1A1A1A"
_BLOCK_COLORS = [
    "#FFF3E0", "#E8F5E9", "#E3F2FD", "#FCE4EC",
    "#F3E5F5", "#E0F7FA", "#FFFDE7", "#EFEBE9",
]


def clamp_opacity(value) -> float:
    """把外部传入的不透明度限制在允许范围内。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DESKTOP_OPACITY
    return min(MAX_DESKTOP_OPACITY, max(MIN_DESKTOP_OPACITY, number))


class DesktopGrid(QWidget):
    """桌面课表的自绘网格：7 天 × 节次，铺满整个屏幕。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timetable = None
        self.opacity = DESKTOP_OPACITY
        self._cache: QImage | None = None
        self._cache_key = None

    def set_timetable(self, timetable) -> None:
        self.timetable = timetable
        self._invalidate()

    def set_opacity(self, value) -> None:
        self.opacity = clamp_opacity(value)
        self.update()

    def _invalidate(self) -> None:
        self._cache = None
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        self._invalidate()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        image = self._content_image()
        painter = QPainter(self)
        # 整层一起降透明度：文字与色块之间的对比度不会被削弱
        painter.setOpacity(self.opacity)
        painter.drawImage(QPointF(0, 0), image)

    def _content_image(self) -> QImage:
        ratio = self.devicePixelRatioF() or 1.0
        key = (self.width(), self.height(), ratio, id(self.timetable))
        if self._cache is not None and self._cache_key == key:
            return self._cache
        width = max(1, int(round(self.width() * ratio)))
        height = max(1, int(round(self.height() * ratio)))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_content(painter, QRectF(0, 0, self.width(), self.height()))
        painter.end()
        self._cache = image
        self._cache_key = key
        return image

    def _paint_content(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(_BG))

        timetable = self.timetable
        if timetable is None:
            return

        days = 7
        max_period = max(timetable.max_period(), 8)
        width, height = rect.width(), rect.height()

        # 间距随屏幕大小缩放；上下限避免小窗口太挤、大屏太空
        margin = max(12.0, min(48.0, height * 0.022))

        def build_fonts(row: float) -> tuple[QFont, QFont, QFont]:
            header_font = QFont(_UI_FONT)
            header_font.setPixelSize(max(11, min(20, int(row * 0.20))))
            header_font.setBold(True)
            period_font = QFont(_UI_FONT)
            period_font.setPixelSize(max(9, min(15, int(row * 0.145))))
            block_font = QFont(_UI_FONT)
            block_font.setPixelSize(max(10, min(16, int(row * 0.155))))
            block_font.setBold(True)
            return header_font, period_font, block_font

        def measure(row: float) -> tuple[float, float, tuple[QFont, QFont, QFont]]:
            """按行高取字号，再用字体实际尺寸定表头高度和节次列宽。

            高 DPI（或宽屏窗口）下行高变小、字号被下限托住时，纯百分比算出的
            格子会比文字还窄，「08:00-08:45」就会被裁掉，所以这里按度量兜底。
            """
            fonts = build_fonts(row)
            header_font, period_font, _ = fonts
            pad = max(4.0, round(row * 0.06))
            header_h = max(
                26.0, height * 0.032, QFontMetricsF(header_font).height() + pad * 1.2
            )
            metrics = QFontMetricsF(period_font)
            widest = max(
                metrics.horizontalAdvance(f"{period} 节")
                for period in (1, 2, 11, 12, max_period)
            )
            for start, end in timetable.period_times.values():
                if start and end:
                    widest = max(widest, metrics.horizontalAdvance(f"{start}-{end}"))
            period_w = max(56.0, width * 0.052, widest + pad * 2)
            return header_h, period_w, fonts

        # 两轮：先按屏高估行高，再用修正后的行高重算一次字号与格子尺寸
        row_height = max(8.0, (height - 26.0 - 2 * margin) / max_period)
        header_height = 26.0
        period_width = 56.0
        fonts = build_fonts(row_height)
        for _ in range(2):
            header_height, period_width, fonts = measure(row_height)
            top = margin + header_height
            grid_height = height - top - margin
            if grid_height <= 0:
                return
            row_height = grid_height / max_period

        header_font, period_font, block_font = fonts
        left = margin + period_width
        top = margin + header_height
        grid_width = width - left - margin
        if grid_width <= 0:
            return
        column_width = grid_width / days

        # 块内缩与圆角同样随行高缩放
        pad = max(4.0, round(row_height * 0.06))
        inset = max(1.5, pad * 0.35)
        radius = max(2.0, pad * 0.55)

        # 表头
        painter.fillRect(QRectF(left, margin, grid_width, header_height), QColor(_HEADER))
        painter.setFont(header_font)
        painter.setPen(QColor(_TEXT))
        for index in range(days):
            painter.drawText(
                QRectF(left + index * column_width, margin, column_width, header_height),
                Qt.AlignCenter,
                DAY_NAMES[index],
            )

        # 网格线
        pen = QPen(QColor(_LINE))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        for index in range(days + 1):
            x = left + index * column_width
            painter.drawLine(QPointF(x, margin), QPointF(x, top + grid_height))
        for row in range(max_period + 1):
            y = top + row * row_height
            painter.drawLine(QPointF(margin, y), QPointF(left + grid_width, y))

        # 节次和时间
        painter.setFont(period_font)
        painter.setPen(QColor(_PERIOD))
        for row in range(max_period):
            period = row + 1
            start, end = timetable.period_times.get(period, ("", ""))
            label = f"{period} 节\n{start}-{end}" if start and end else f"{period} 节"
            painter.drawText(
                QRectF(margin, top + row * row_height, period_width, row_height),
                Qt.AlignCenter,
                label,
            )

        # 课程块
        for course in timetable.courses:
            if not 1 <= course.day <= 7:
                continue
            first_row = max(1, course.period_start) - 1
            last_row = min(max_period, course.period_end) - 1
            if last_row < 0 or first_row >= max_period or last_row < first_row:
                continue
            x = left + (course.day - 1) * column_width + inset
            y = top + first_row * row_height + inset
            block_w = max(2.0, column_width - inset * 2)
            block_h = max(2.0, (last_row - first_row + 1) * row_height - inset * 2)
            fill = QColor(
                _BLOCK_COLORS[zlib.crc32(course.name.encode("utf-8")) % len(_BLOCK_COLORS)]
            )
            painter.setPen(QPen(fill.darker(115), 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(x, y, block_w, block_h), radius, radius)
            painter.setPen(QColor(_BLOCK_TEXT))
            painter.setFont(block_font)
            label = course.name
            if course.location:
                label += "\n" + course.location
            text_pad = max(2.0, pad * 0.7)
            painter.drawText(
                QRectF(x + text_pad, y + text_pad * 0.6,
                       block_w - text_pad * 2, block_h - text_pad * 1.2),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                label,
            )


def _user32():
    import ctypes
    from ctypes import wintypes

    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    lib.FindWindowW.restype = wintypes.HWND
    lib.FindWindowExW.argtypes = [
        wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR
    ]
    lib.FindWindowExW.restype = wintypes.HWND
    lib.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    lib.GetWindow.restype = wintypes.HWND
    lib.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    lib.GetClassNameW.restype = ctypes.c_int
    lib.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    lib.EnumWindows.restype = wintypes.BOOL
    lib.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    lib.SendMessageTimeoutW.restype = wintypes.LPARAM
    return lib


def _class_name(user32, hwnd: int) -> str:
    if not hwnd:
        return ""
    import ctypes

    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _desktop_layer_target() -> tuple[int, int]:
    """找到桌面课表应挂接的位置：``(父窗口, 插到这个窗口之后)``。

    插入点在 **桌面图标之下、壁纸之上**，也就是把课表贴在图标层与壁纸层之间。
    返回 ``(0, 0)`` 表示当前桌面结构不支持这种挂接。
    """
    try:
        user32 = _user32()
    except Exception:  # pragma: no cover - 非 Windows
        return 0, 0

    progman = int(user32.FindWindowW("Progman", None) or 0)
    if not progman:
        return 0, 0

    # 让资源管理器把壁纸层与图标层拆开（Win10 / Win11 通用做法）
    try:
        import ctypes

        result = ctypes.c_size_t(0)
        for wparam in (0xD, 0):
            user32.SendMessageTimeoutW(
                progman, 0x052C, wparam, 0, 0, 1000, ctypes.byref(result)
            )
    except Exception:
        pass

    # 新结构：图标窗口 SHELLDLL_DefView 直接挂在 Progman 下，壁纸宿主 WorkerW 在它后面
    defview = int(user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) or 0)
    if defview:
        return progman, defview

    # 旧结构：图标窗口在某个顶层窗口里，壁纸 WorkerW 是同一层的下一个窗口
    hosts: list[int] = []
    callback_type = None
    try:
        import ctypes
        from ctypes import wintypes

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_cb(hwnd, _lparam):
            if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
                hosts.append(int(hwnd))
            return True

        user32.EnumWindows(callback_type(enum_cb), 0)
    except Exception:
        return 0, 0
    for host in hosts:
        worker = int(user32.FindWindowExW(None, host, "WorkerW", None) or 0)
        if worker:
            return worker, 0  # 直接放在壁纸宿主里最上层，图标仍压在它上面
    return 0, 0


# Wallpaper Engine 等动态壁纸会把壁纸画成独立的 D3D 宿主窗口（WPEImageWallpaper /
# WPEVideoWallpaper）。这类壁纸由合成器绘制在所有 GDI 子窗口之上，无法把课表插到
# 壁纸与图标之间，只能退化成「最底层普通窗口」。
_D3D_WALLPAPER_PREFIXES = ("WPE",)


def _wallpaper_is_d3d_hosted() -> bool:
    """壁纸是否由 D3D 宿主窗口绘制（此时无法把课表贴到壁纸上）。"""
    try:
        user32 = _user32()
    except Exception:  # pragma: no cover - 非 Windows
        return False

    progman = int(user32.FindWindowW("Progman", None) or 0)
    if not progman:
        return False
    defview = int(user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) or 0)
    candidates = []
    if defview:
        sibling = int(user32.GetWindow(defview, 2) or 0)  # GW_HWNDNEXT -> 壁纸宿主
        if sibling:
            candidates.append(sibling)
    if not candidates:
        candidates.append(progman)
    for host in candidates:
        child = int(user32.GetWindow(host, 5) or 0)  # GW_CHILD
        while child:
            name = _class_name(user32, child)
            if name.startswith(_D3D_WALLPAPER_PREFIXES):
                return True
            child = int(user32.GetWindow(child, 2) or 0)
    return False



class DesktopPinWindow(QWidget):
    """贴在桌面上的课表窗口：无边框、半透明、在图标与窗口之下、不挡鼠标。"""

    def __init__(self, timetable=None, parent=None):
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnBottomHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 关闭这层课表不应触发「最后一个窗口关闭 → 退出程序」
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setWindowTitle("课表（桌面）")
        self.attached_to_wallpaper = False
        self._bottom_timer: QTimer | None = None
        self.grid = DesktopGrid(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.grid)
        if timetable is not None:
            self.grid.set_timetable(timetable)

    def set_timetable(self, timetable) -> None:
        self.grid.set_timetable(timetable)

    def set_opacity(self, value) -> None:
        self.grid.set_opacity(value)

    def opacity(self) -> float:
        return self.grid.opacity

    def show_on_screen(self) -> None:
        """铺满主屏幕，并尽量贴到壁纸与桌面图标之间。"""
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.show()
        parent, insert_after = _desktop_layer_target()
        attached = False
        # 动态壁纸（Wallpaper Engine 等）把壁纸画在合成器图层上，
        # GDI 子窗口无法显示在它上面，这时直接退化为「最底层普通窗口」。
        if parent and not _wallpaper_is_d3d_hosted():
            attached = self._attach_to_wallpaper(parent, insert_after)
        self.attached_to_wallpaper = attached
        if not attached:
            self._fallback_to_bottom()
        self.grid.update()

    def _attach_to_wallpaper(self, parent: int, insert_after: int) -> bool:
        """把窗口挂到桌面壁纸层，让桌面图标叠在课表上面。"""
        try:
            import ctypes
            from ctypes import wintypes

            if not parent:
                return False
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
            user32.SetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = wintypes.LONG
            user32.SetWindowLongW.argtypes = [
                wintypes.HWND, ctypes.c_int, wintypes.LONG
            ]
            user32.SetWindowLongW.restype = wintypes.LONG
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL

            hwnd = int(self.winId())
            if not user32.SetParent(hwnd, parent):
                return False

            # 子窗口样式：去掉 WS_POPUP，加上 WS_CHILD / WS_VISIBLE
            gwl_style = -16
            style = user32.GetWindowLongW(hwnd, gwl_style) or 0
            style = (style | 0x40000000 | 0x10000000) & ~0x80000000
            user32.SetWindowLongW(hwnd, gwl_style, style)

            ratio = self.devicePixelRatioF() or 1.0
            width = max(1, int(round(self.width() * ratio)))
            height = max(1, int(round(self.height() * ratio)))
            swp_framechanged = 0x0020
            swp_nozorder = 0x0004
            swp_noactivate = 0x0010
            if insert_after:
                # 插到图标窗口之后：这一层位于图标之下、壁纸之上
                user32.SetWindowPos(
                    hwnd, insert_after, 0, 0, width, height,
                    swp_framechanged | swp_noactivate,
                )
            else:
                # 壁纸宿主内部放到最上层，图标仍在另一个窗口里压着它
                user32.SetWindowPos(
                    hwnd, 0, 0, 0, width, height,
                    swp_framechanged | swp_nozorder | swp_noactivate,
                )
            self._stop_bottom_timer()
            return True
        except Exception:
            return False

    def _fallback_to_bottom(self) -> None:
        """退路：当作最底层的普通窗口，盖不住任何普通窗口。"""
        self.lower()
        self._send_to_bottom()
        if self._bottom_timer is None:
            self._bottom_timer = QTimer(self)
            self._bottom_timer.setInterval(3000)
            self._bottom_timer.timeout.connect(self._keep_at_bottom)
        self._bottom_timer.start()

    def _keep_at_bottom(self) -> None:
        if self.isVisible() and not self.attached_to_wallpaper:
            self._send_to_bottom()

    def _stop_bottom_timer(self) -> None:
        if self._bottom_timer is not None:
            self._bottom_timer.stop()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        self._stop_bottom_timer()
        super().hideEvent(event)

    def _detach_from_wallpaper(self) -> None:
        """把挂到壁纸层的窗口还原成普通顶层窗口，避免关闭时留下收不掉的残影。"""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
            user32.SetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongW.restype = wintypes.LONG
            user32.SetWindowLongW.argtypes = [
                wintypes.HWND, ctypes.c_int, wintypes.LONG
            ]
            user32.SetWindowLongW.restype = wintypes.LONG

            hwnd = int(self.winId())
            user32.SetParent(hwnd, None)
            gwl_style = -16
            style = user32.GetWindowLongW(hwnd, gwl_style) or 0
            style = (style | 0x80000000) & ~0x40000000  # 恢复 WS_POPUP，去掉 WS_CHILD
            user32.SetWindowLongW(hwnd, gwl_style, style)
        except Exception:
            pass
        self.attached_to_wallpaper = False

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        self._stop_bottom_timer()
        if self.attached_to_wallpaper:
            self._detach_from_wallpaper()
        super().closeEvent(event)

    def _send_to_bottom(self) -> None:
        """Qt 的置底提示在部分 Windows 版本上不够稳，再用 Win32 压一次。"""
        try:
            import ctypes

            hwnd = int(self.winId())
            hwnd_bottom = 1
            flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            ctypes.windll.user32.SetWindowPos(hwnd, hwnd_bottom, 0, 0, 0, 0, flags)
        except Exception:
            pass


def build_tray_icon(size: int = 32) -> QIcon:
    """用代码画一个课表样式的托盘图标，避免依赖外部图片资源。"""
    # 高 DPI 屏上按物理像素出图，否则托盘里的图标会被放大得发糊
    ratio = 1.0
    screen = QApplication.primaryScreen()
    if screen is not None:
        try:
            ratio = max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            ratio = 1.0
    pixmap = QPixmap(int(round(size * ratio)), int(round(size * ratio)))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#1E88E5"))
    painter.drawRoundedRect(QRectF(2, 4, size - 4, size - 8), 6, 6)

    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(1.6)
    painter.setPen(pen)
    left, right = 8.0, float(size - 8)
    top, bottom = float(size) * 0.34, float(size) * 0.74
    middle_y = (top + bottom) / 2
    middle_x = (left + right) / 2
    painter.drawLine(QPointF(left, top), QPointF(right, top))
    painter.drawLine(QPointF(left, middle_y), QPointF(right, middle_y))
    painter.drawLine(QPointF(middle_x, top), QPointF(middle_x, bottom))

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawEllipse(QPointF(size * 0.36, size * 0.2), 1.8, 1.8)
    painter.drawEllipse(QPointF(size * 0.64, size * 0.2), 1.8, 1.8)
    painter.end()
    return QIcon(pixmap)
