"""课表自定义格式（.crs）的读写。

.crs 是本应用自己的课表存档格式，本质是一个 UTF-8 JSON 文件，
保存课程列表、作息时间、本学期第一天以及来源文件列表，
供「保存课表 / 打开课表 / 锁定到桌面」这一套功能使用。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from .models import DEFAULT_PERIOD_TIMES, Course, Timetable

CRS_SUFFIX = ".crs"
CRS_FORMAT = "TimetableOCR-CRS"
CRS_VERSION = 1


def save_crs(timetable: Timetable, first_day: str, path: str) -> None:
    """把课表写入 .crs 文件（先写临时文件再替换，避免写坏存档）。"""
    if not path:
        raise ValueError("未指定保存路径")
    data = {
        "format": CRS_FORMAT,
        "version": CRS_VERSION,
        "savedAt": datetime.now().isoformat(timespec="seconds"),
        "firstDay": str(first_day or ""),
        "periodTimes": {
            str(period): [str(start), str(end)]
            for period, (start, end) in sorted(timetable.period_times.items())
        },
        "sources": [str(source) for source in timetable.sources],
        "courses": [
            {
                "name": course.name,
                "day": course.day,
                "periodStart": course.period_start,
                "periodEnd": course.period_end,
                "teacher": course.teacher,
                "location": course.location,
                "weeks": list(course.weeks),
                "weeksText": course.weeks_text,
                "timeStart": course.time_start,
                "timeEnd": course.time_end,
                "note": course.note,
            }
            for course in timetable.courses
        ],
    }
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def load_crs(path: str) -> tuple[Timetable, str]:
    """读取 .crs 文件，返回 (课表, 本学期第一天的日期字符串)。"""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("format") != CRS_FORMAT:
        raise ValueError("不是有效的 .crs 课表文件")

    timetable = Timetable()
    period_times: dict[int, tuple[str, str]] = {}
    for key, value in (data.get("periodTimes") or {}).items():
        try:
            period_times[int(key)] = (str(value[0]), str(value[1]))
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    if period_times:
        timetable.period_times = period_times
    timetable.sources = [str(source) for source in (data.get("sources") or [])]

    for item in data.get("courses") or []:
        if not isinstance(item, dict):
            continue
        try:
            course = Course(
                name=str(item.get("name") or "").strip(),
                day=int(item.get("day") or 0),
                period_start=int(item.get("periodStart") or 0),
                period_end=int(item.get("periodEnd") or 0),
                teacher=str(item.get("teacher") or ""),
                location=str(item.get("location") or ""),
                weeks=[int(week) for week in (item.get("weeks") or [])],
                weeks_text=str(item.get("weeksText") or ""),
                time_start=str(item.get("timeStart") or ""),
                time_end=str(item.get("timeEnd") or ""),
                note=str(item.get("note") or ""),
            )
        except (TypeError, ValueError):
            continue
        if not course.name or not 1 <= course.day <= 7:
            continue
        course.period_start = max(1, course.period_start)
        course.period_end = max(course.period_start, course.period_end)
        timetable.courses.append(course)

    first_day = data.get("firstDay")
    if not first_day:
        # 兼容旧存档：v0.3.x 里这个字段叫 firstMonday，含义是第 1 周周一
        first_day = data.get("firstMonday")
    return timetable, str(first_day or "")


def default_crs_path() -> str:
    """应用目录下默认的课表文件名（未保存过时使用）。"""
    from .settings import _app_dir

    return os.path.join(_app_dir(), "课表" + CRS_SUFFIX)
