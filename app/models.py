"""课表数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

DEFAULT_PERIOD_TIMES: dict[int, tuple[str, str]] = {
    1: ("08:00", "08:45"),
    2: ("08:55", "09:40"),
    3: ("10:00", "10:45"),
    4: ("10:55", "11:40"),
    5: ("14:00", "14:45"),
    6: ("14:55", "15:40"),
    7: ("16:00", "16:45"),
    8: ("16:55", "17:40"),
    9: ("19:00", "19:45"),
    10: ("19:55", "20:40"),
    11: ("20:50", "21:35"),
    12: ("21:45", "22:30"),
}

DEFAULT_TERM_WEEKS = list(range(1, 17))

# 「每周第一天」：下拉选项的值（周六 / 周日 / 周一）与 Python weekday() 的对应
WEEK_START_CHOICES: tuple[tuple[str, str], ...] = (
    ("sat", "周六"),
    ("sun", "周日"),
    ("mon", "周一"),
)
WEEK_START_WEEKDAY = {"sat": 5, "sun": 6, "mon": 0}
DEFAULT_WEEK_START = "mon"


def _week_start_weekday(week_start: str) -> int:
    return WEEK_START_WEEKDAY.get((week_start or DEFAULT_WEEK_START).lower(), 0)


def term_anchor(first_day: date, week_start: str = DEFAULT_WEEK_START) -> date:
    """第 1 周的第一天：不晚于「本学期第一天」的那个「每周第一天」。

    默认周一 + 学期从周一开始时，锚点就是本学期第一天本身。
    """
    start = _week_start_weekday(week_start)
    return first_day - timedelta(days=(first_day.weekday() - start) % 7)


def term_week_of(day: date, first_day: date, week_start: str = DEFAULT_WEEK_START) -> int:
    """某一天属于学期第几周（小于 1 表示还没到第 1 周）。"""
    anchor = term_anchor(first_day, week_start)
    return (day - anchor).days // 7 + 1


def course_date(
    first_day: date, week: int, weekday: int, week_start: str = DEFAULT_WEEK_START
) -> date:
    """第 week 周、星期 weekday（1=周一 … 7=周日）对应的日期。"""
    anchor = term_anchor(first_day, week_start)
    start = _week_start_weekday(week_start)
    offset = (weekday - 1 - start) % 7
    return anchor + timedelta(days=(week - 1) * 7 + offset)



def compress_weeks(weeks: list[int]) -> str:
    """把周次列表压缩成 1-16周 / 1-15周(单) / 1-4,6,8-16周 这样的文本。"""
    ws = sorted({w for w in weeks if 1 <= w <= 60})
    if not ws:
        return ""
    if len(ws) > 2 and all(ws[i + 1] - ws[i] == 2 for i in range(len(ws) - 1)):
        tag = "单" if ws[0] % 2 == 1 else "双"
        return f"{ws[0]}-{ws[-1]}周({tag})"
    parts: list[str] = []
    start = prev = ws[0]
    for w in ws[1:]:
        if w == prev + 1:
            prev = w
            continue
        parts.append(f"{start}-{prev}" if prev > start else str(start))
        start = prev = w
    parts.append(f"{start}-{prev}" if prev > start else str(start))
    return ",".join(parts) + "周"


@dataclass
class Course:
    name: str
    day: int  # 1=周一 ... 7=周日
    period_start: int
    period_end: int
    teacher: str = ""
    location: str = ""
    weeks: list[int] = field(default_factory=list)
    weeks_text: str = ""
    time_start: str = ""
    time_end: str = ""
    note: str = ""

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.day - 1] if 1 <= self.day <= 7 else ""

    @property
    def weeks_label(self) -> str:
        if self.weeks_text:
            return self.weeks_text
        return compress_weeks(self.weeks) or "未指定"

    def key(self) -> tuple:
        return (
            self.name,
            self.day,
            self.period_start,
            self.period_end,
            tuple(self.weeks),
            self.location,
            self.teacher,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "day": self.day,
            "day_name": self.day_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "periods_text": (
                f"{self.period_start}节"
                if self.period_start == self.period_end
                else f"{self.period_start}-{self.period_end}节"
            ),
            "weeks": self.weeks,
            "weeks_text": self.weeks_label,
            "teacher": self.teacher,
            "location": self.location,
            "time_start": self.time_start,
            "time_end": self.time_end,
            "note": self.note,
        }


@dataclass
class Timetable:
    courses: list[Course] = field(default_factory=list)
    period_times: dict[int, tuple[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_PERIOD_TIMES)
    )
    sources: list[str] = field(default_factory=list)

    def dedupe(self) -> int:
        seen: set = set()
        unique: list[Course] = []
        for course in self.courses:
            key = course.key()
            if key in seen:
                continue
            seen.add(key)
            unique.append(course)
        removed = len(self.courses) - len(unique)
        self.courses = unique
        return removed

    def max_period(self) -> int:
        return max((c.period_end for c in self.courses), default=12)


def find_conflicts(courses: list[Course]) -> list[tuple[int, int]]:
    """返回时间冲突的课程下标对 (i, j)，i < j。

    冲突判定：同一天 + 节次区间重叠 + 周次有交集。
    未识别到周次的课程按整学期 1-16 周处理（与日历导出保持一致）。
    """
    pairs: list[tuple[int, int]] = []
    full_weeks = set(range(1, 17))
    for i in range(len(courses)):
        a = courses[i]
        for j in range(i + 1, len(courses)):
            b = courses[j]
            if a.day != b.day:
                continue
            if a.period_end < b.period_start or b.period_end < a.period_start:
                continue
            wa = set(a.weeks) if a.weeks else full_weeks
            wb = set(b.weeks) if b.weeks else full_weeks
            if wa.isdisjoint(wb):
                continue
            pairs.append((i, j))
    return pairs
