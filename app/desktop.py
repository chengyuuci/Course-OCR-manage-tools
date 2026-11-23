"""把课表固定到桌面上：一个无边框、置底、可拖动的小窗，以及托盘图标。

小窗用自绘的课表卡片，拖动窗口可移动、拖右下角可缩放，位置和大小会记在
settings.ini 里，下次固定回到原处。用托盘图标或右键菜单可以解除。
"""
from __future__ import annotations

import ctypes
import os
import sys
import zlib

from PyQt5.QtCore import (
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import QApplication, QMenu, QVBoxLayout, QWidget

from .models import DAY_NAMES
from .settings import load_desktop_geometry, save_desktop_geometry

# 桌面小窗的默认不透明度（0.85），可在「设置 → 外观」里用滑块调整
DESKTOP_OPACITY = 0.85
MIN_DESKTOP_OPACITY = 0.10
MAX_DESKTOP_OPACITY = 1.00

# 默认小窗尺寸（逻辑像素）与允许的最小尺寸
DEFAULT_PIN_SIZE = QSize(560, 700)
MIN_PIN_SIZE = QSize(320, 240)

_UI_FONT = "Microsoft YaHei UI"

# 桌面课表固定使用深色底 + 浅色字：深色卡片在浅色与深色壁纸/窗口上都好认。
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
        self.week: int | None = None  # None = 整学期
        self.slots: tuple[tuple[int, int], ...] | None = None  # ((周次, 星期), ...)
        self.opacity = DESKTOP_OPACITY
        self._cache: QImage | None = None
        self._cache_key = None

    def set_timetable(self, timetable) -> None:
        self.timetable = timetable
        self._invalidate()

    def set_week(self, week: int | None) -> None:
        if week == self.week:
            return
        self.week = week
        self._invalidate()

    def set_slots(self, slots) -> None:
        """只保留指定的 (周次, 星期) 组合，用于「只看今天和明天」。"""
        normalized = None
        if slots:
            normalized = tuple(sorted({(int(w), int(d)) for w, d in slots}))
        if normalized == self.slots:
            return
        self.slots = normalized
        self._invalidate()

    def _visible_courses(self) -> list:
        """按当前周次/日期筛选课程；未填周次的课程视为整学期每周都有。"""
        timetable = self.timetable
        if timetable is None:
            return []
        if self.slots:
            picked = []
            for course in timetable.courses:
                if not 1 <= course.day <= 7:
                    continue
                for week, day in self.slots:
                    if course.day == day and (not course.weeks or week in course.weeks):
                        picked.append(course)
                        break
            return picked
        if self.week is None:
            return list(timetable.courses)
        return [
            course
            for course in timetable.courses
            if not course.weeks or self.week in course.weeks
        ]

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
        key = (self.width(), self.height(), ratio, id(self.timetable), self.week, self.slots)
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
        # 小窗是圆角卡片：内容全部裁到圆角路径里，四角透出后面的桌面/窗口
        card_radius = max(8.0, min(20.0, rect.height() * 0.015))
        card = QPainterPath()
        card.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), card_radius, card_radius)

        painter.save()
        painter.setClipPath(card)
        painter.fillRect(rect, QColor(_BG))

        def finish() -> None:
            painter.restore()
            painter.setPen(QPen(QColor(_LINE), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(card)

        timetable = self.timetable
        if timetable is None:
            painter.setPen(QColor(_TEXT))
            hint_font = QFont(_UI_FONT)
            hint_font.setPixelSize(max(11, min(18, int(rect.height() * 0.03))))
            painter.setFont(hint_font)
            painter.drawText(rect, Qt.AlignCenter, "暂无课表")
            finish()
            return

        courses = self._visible_courses()
        days = 7
        max_period = max(8, max((c.period_end for c in courses), default=0))
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
        for course in courses:
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

        finish()


class DesktopPinWindow(QWidget):
    """固定在桌面上的课表小窗：无边框、置底、半透明，可拖动可缩放。

    位置与大小会写进 settings.ini，下次固定回到原处；右键菜单与托盘都能解除。
    """

    unlockRequested = pyqtSignal()
    fixTrayRequested = pyqtSignal()
    showMainRequested = pyqtSignal()

    _GRIP = 18  # 右下角缩放热区（逻辑像素）
    _KEEP_BOTTOM_MS = 1500  # 定时把窗口重新压到底层

    def __init__(self, timetable=None, parent=None):
        super().__init__(
            parent,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnBottomHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        # 关闭这层课表不应触发「最后一个窗口关闭 → 退出程序」
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setWindowTitle("课表")
        self.setMinimumSize(MIN_PIN_SIZE)
        self._drag_offset: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_size: QSize | None = None
        self._keep_bottom = QTimer(self)
        self._keep_bottom.setInterval(self._KEEP_BOTTOM_MS)
        self._keep_bottom.timeout.connect(self._send_to_bottom)
        self.grid = DesktopGrid(self)
        # 网格只是画出来的，把鼠标事件让给窗口本身，拖动/缩放才收得到
        self.grid.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.grid)
        if timetable is not None:
            self.grid.set_timetable(timetable)

    # ------------------------------------------------------------ 内容

    def set_timetable(self, timetable) -> None:
        self.grid.set_timetable(timetable)

    def set_week(self, week: int | None) -> None:
        """限定只显示某一周；传入 None 显示整学期。"""
        self.grid.set_week(week)

    def set_slots(self, slots) -> None:
        """只显示指定 (周次, 星期) 的课，用于「只看今天和明天」。"""
        self.grid.set_slots(slots)

    def set_opacity(self, value) -> None:
        self.grid.set_opacity(value)

    def opacity(self) -> float:
        return self.grid.opacity

    # ------------------------------------------------------------ 显示与摆放

    def show_on_screen(self) -> None:
        """按上次的位置/大小显示小窗；没有记录时放在屏幕右侧居中。"""
        self._restore_placement()
        self.show()
        self._send_to_bottom()
        self._keep_bottom.start()
        self.grid.update()

    def _send_to_bottom(self) -> None:
        """用 Win32 把窗口压到 z 序最底（Qt 的 bottom hint 在部分场景会失效）。"""
        if not self.isVisible():
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            user32.SetWindowPos(
                hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )  # HWND_BOTTOM, NOSIZE|NOMOVE|NOACTIVATE
        except Exception:
            pass

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        super().showEvent(event)
        self._send_to_bottom()
        self._keep_bottom.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        self._keep_bottom.stop()
        super().hideEvent(event)

    def _restore_placement(self) -> None:
        saved = load_desktop_geometry()
        if saved is not None:
            x, y, width, height = saved
            screen = _screen_for_point(QPoint(x + width // 2, y + height // 2))
            if screen is not None:
                area = screen.availableGeometry()
                width = max(MIN_PIN_SIZE.width(), min(width, area.width()))
                height = max(MIN_PIN_SIZE.height(), min(height, area.height()))
                x = max(area.left(), min(x, area.right() - width + 1))
                y = max(area.top(), min(y, area.bottom() - height + 1))
                self.setGeometry(x, y, width, height)
                return
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else None
        width = DEFAULT_PIN_SIZE.width()
        height = DEFAULT_PIN_SIZE.height()
        if area is not None:
            width = min(width, int(area.width() * 0.9))
            height = min(height, int(area.height() * 0.9))
            x = area.right() - width - max(16, int(area.width() * 0.02))
            y = area.top() + max(16, (area.height() - height) // 3)
        else:  # pragma: no cover - 没有屏幕时只求能显示
            x = y = 100
        self.setGeometry(x, y, width, height)

    def _save_placement(self) -> None:
        geometry = self.geometry()
        save_desktop_geometry(
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )

    # ------------------------------------------------------------ 鼠标交互

    def _in_grip(self, pos: QPoint) -> bool:
        return (
            pos.x() >= self.width() - self._GRIP
            and pos.y() >= self.height() - self._GRIP
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.pos()
        if self._in_grip(pos):
            self._resize_origin = event.globalPos()
            self._resize_size = self.size()
        else:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        if self._resize_origin is not None and self._resize_size is not None:
            delta = event.globalPos() - self._resize_origin
            width = max(MIN_PIN_SIZE.width(), self._resize_size.width() + delta.x())
            height = max(MIN_PIN_SIZE.height(), self._resize_size.height() + delta.y())
            self.resize(width, height)
            event.accept()
            return
        if self._drag_offset is not None:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        # 悬停在右下角时给个缩放光标提示
        if self._in_grip(event.pos()):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        changed = self._drag_offset is not None or self._resize_origin is not None
        self._drag_offset = None
        self._resize_origin = None
        self._resize_size = None
        if changed:
            self._save_placement()
        # 拖完/缩放完重新压回底层，别在这次点击里被抬到上面
        self._send_to_bottom()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        menu = QMenu(self)
        menu.addAction("显示主窗口", self.showMainRequested.emit)
        menu.addAction("把图标放回任务栏", self.fixTrayRequested.emit)
        menu.addAction("解除固定", self.unlockRequested.emit)
        menu.exec_(event.globalPos())
        self._send_to_bottom()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 回调
        self._save_placement()
        super().closeEvent(event)


def _screen_for_point(point: QPoint):
    """找到包含该点的屏幕；找不到就返回 None。"""
    screen = QApplication.screenAt(point)
    if screen is not None:
        return screen
    for candidate in QApplication.screens():
        if candidate.geometry().contains(point):
            return candidate
    return None



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


def tray_registry_key() -> str | None:
    """找到本程序在 Windows 通知区域设置里的条目名（按 exe 路径匹配）。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings"
        ) as root:
            count = winreg.QueryInfoKey(root)[0]
            target = os.path.normcase(os.path.abspath(sys.executable))
            for index in range(count):
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    continue
                try:
                    with winreg.OpenKey(root, name) as item:
                        value, _ = winreg.QueryValueEx(item, "ExecutablePath")
                except OSError:
                    continue
                if os.path.normcase(os.path.abspath(value)) == target:
                    return name
    except OSError:
        return None
    return None


def tray_promoted_state() -> int:
    """读取本程序托盘条目的 IsPromoted：1 显示在任务栏、0 收在「^」里、-1 没有条目。"""
    try:
        import winreg
    except ImportError:
        return -1
    name = tray_registry_key()
    if name is None:
        return -1
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\NotifyIconSettings\%s" % name,
        ) as item:
            try:
                promoted, _ = winreg.QueryValueEx(item, "IsPromoted")
            except OSError:
                return 0
            return 1 if int(promoted) == 1 else 0
    except OSError:
        return -1


def promote_tray_icon() -> str:
    """把托盘图标从任务栏的「^」溢出区提到任务栏上（Windows 11 默认会藏进去）。

    返回状态字符串：
      "promoted" 刚设置成功
      "already"  本来就是显示在任务栏上的
      "missing"  系统里还没有本程序的托盘条目（图标可能刚出现，需要稍后重试）
      "error"    读写注册表失败
    只写当前用户自己的通知区域设置，不需要管理员权限。
    """
    try:
        import winreg
    except ImportError:
        return "error"
    name = tray_registry_key()
    if name is None:
        return "missing"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\NotifyIconSettings\%s" % name,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as item:
            try:
                promoted, _ = winreg.QueryValueEx(item, "IsPromoted")
            except OSError:
                promoted = 0
            if int(promoted) == 1:
                return "already"
            winreg.SetValueEx(item, "IsPromoted", 0, winreg.REG_DWORD, 1)
            # 回读确认，避免写了个寂寞
            try:
                check, _ = winreg.QueryValueEx(item, "IsPromoted")
            except OSError:
                return "error"
            return "promoted" if int(check) == 1 else "error"
    except OSError:
        return "error"
