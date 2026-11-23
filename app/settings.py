"""用户设置持久化（作息时间与导出提醒）。

设置文件保存在应用目录下（打包后为 exe 所在目录，源码运行为项目根目录）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import time as dtime

from PyQt5.QtCore import QSettings

from .models import DEFAULT_PERIOD_TIMES


def _app_dir() -> str:
    """返回应用目录：打包后为 exe 所在目录，源码运行为项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _settings() -> QSettings:
    folder = _app_dir()
    ini = os.path.join(folder, "settings.ini")

    # 旧版本把设置放在 %LOCALAPPDATA%\TimetableOCR，迁移到应用目录
    old_dir = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TimetableOCR"
    )
    old_ini = os.path.join(old_dir, "settings.ini")
    if os.path.isfile(old_ini) and not os.path.isfile(ini):
        try:
            os.makedirs(folder, exist_ok=True)
            shutil.copy2(old_ini, ini)
        except OSError:
            pass

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return QSettings("TimetableOCR", "TimetableOCR")
    return QSettings(ini, QSettings.IniFormat)


def load_period_times() -> dict[int, tuple[str, str]]:
    raw = _settings().value("schedule/period_times", "")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        result: dict[int, tuple[str, str]] = {}
        for key, value in data.items():
            period = int(key)
            start, end = value
            dtime.fromisoformat(start)
            dtime.fromisoformat(end)
            result[period] = (start, end)
        if result:
            return result
    except (AttributeError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return dict(DEFAULT_PERIOD_TIMES)


def save_period_times(period_times: dict[int, tuple[str, str]]) -> None:
    settings = _settings()
    settings.setValue(
        "schedule/period_times",
        json.dumps({str(k): list(v) for k, v in sorted(period_times.items())}),
    )
    settings.sync()


def load_reminders() -> list[int]:
    """读取 ICS 导出提醒列表（每个元素为提前分钟数，升序；空列表表示不提醒）。"""
    raw = _settings().value("ics/reminders", "")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        data = None
    if isinstance(data, list):
        minutes = sorted({int(x) for x in data if isinstance(x, (int, float)) and int(x) >= 0})
        if minutes:
            return minutes
    return []


def save_reminders(minutes: list[int]) -> None:
    settings = _settings()
    cleaned = sorted({int(m) for m in minutes if int(m) >= 0})
    settings.setValue("ics/reminders", json.dumps(cleaned))
    settings.sync()


def load_theme() -> str:
    """读取界面主题：'light' 或 'dark'。"""
    return "dark" if _settings().value("ui/theme", "light") == "dark" else "light"


def save_theme(theme: str) -> None:
    settings = _settings()
    settings.setValue("ui/theme", "dark" if theme == "dark" else "light")
    settings.sync()


def load_desktop_opacity() -> float:
    """桌面课表小窗的不透明度（0.1 ~ 1.0，默认 0.85）。"""
    raw = _settings().value("desktop/opacity", None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.85
    value = min(1.0, max(0.1, value))
    # 早期版本是「铺满全屏的半透明层」，默认 30%；现在改成悬浮小窗，
    # 30% 会淡到看不清，所以把这个旧默认值一次性抬到 85%（自己调过的不动）。
    if abs(value - 0.3) < 1e-6:
        return 0.85
    return value


def save_desktop_opacity(value: float) -> None:
    settings = _settings()
    try:
        number = min(1.0, max(0.1, float(value)))
    except (TypeError, ValueError):
        number = 0.85
    settings.setValue("desktop/opacity", round(number, 3))
    settings.sync()


def load_desktop_geometry() -> tuple[int, int, int, int] | None:
    """桌面课表小窗上次的位置与大小 (x, y, w, h)；没有记录时返回 None。"""
    raw = str(_settings().value("desktop/pin_geometry", "") or "").strip()
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(float(p)) for p in parts)
    except (TypeError, ValueError):
        return None
    if w < 100 or h < 100:
        return None
    return x, y, w, h


def save_desktop_geometry(x: int, y: int, width: int, height: int) -> None:
    settings = _settings()
    settings.setValue(
        "desktop/pin_geometry", f"{int(x)},{int(y)},{int(width)},{int(height)}"
    )
    settings.sync()


def load_crs_path() -> str:
    """最近一次保存 / 打开的 .crs 文件路径；未保存过时返回空字符串。"""
    return str(_settings().value("crs/last_path", "") or "")


def save_crs_path(path: str) -> None:
    settings = _settings()
    if path:
        settings.setValue("crs/last_path", path)
    else:
        settings.remove("crs/last_path")
    settings.sync()


def load_desktop_enabled() -> bool:
    """是否启用实验性的「锁定到桌面」功能（默认关闭）。"""
    raw = _settings().value("desktop/enabled", False)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def save_desktop_enabled(enabled: bool) -> None:
    settings = _settings()
    settings.setValue("desktop/enabled", bool(enabled))
    settings.sync()


def load_desktop_follow_week() -> bool:
    """桌面课表是否跟随「预览周次」只显示某一周（默认关闭，即整学期）。"""
    raw = _settings().value("desktop/follow_week", False)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def save_desktop_follow_week(enabled: bool) -> None:
    settings = _settings()
    settings.setValue("desktop/follow_week", bool(enabled))
    settings.sync()


def load_desktop_today_tomorrow() -> bool:
    """桌面课表是否只显示「今天和明天」的课（默认关闭）。"""
    raw = _settings().value("desktop/today_tomorrow", False)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def save_desktop_today_tomorrow(enabled: bool) -> None:
    settings = _settings()
    settings.setValue("desktop/today_tomorrow", bool(enabled))
    settings.sync()


def load_week_start() -> str:
    """导出日历时「每周第一天」：sat / sun / mon，默认周一。"""
    raw = str(_settings().value("export/week_start", "mon") or "mon").strip().lower()
    return raw if raw in ("sat", "sun", "mon") else "mon"


def save_week_start(value: str) -> None:
    settings = _settings()
    settings.setValue("export/week_start", str(value or "mon").strip().lower())
    settings.sync()


def load_tray_promoted() -> str:
    """记录上一次「把托盘图标提到任务栏」时用的是哪个可执行文件。"""
    return str(_settings().value("ui/tray_promoted_exe", "") or "")


def save_tray_promoted(executable: str) -> None:
    settings = _settings()
    settings.setValue("ui/tray_promoted_exe", str(executable or ""))
    settings.sync()
