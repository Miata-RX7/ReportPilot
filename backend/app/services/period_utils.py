"""统计周期工具。

约定：全链路 UTC；period 字符串支持两种粒度：

- **月** ``"YYYY-MM"``（如 "2026-09"）
- **周** ``"YYYY-Www"``（ISO 周，如 "2026-W41"）

平移规则（禁止固定 30 天，避免 2 月/31 天月错位）：

- 月粒度：基线平移用**日历月**（dateutil.relativedelta）
- 周粒度：平移**整周**，边界按**站点时区**（``report_timezone``）周一 00:00 起算，
  因此 "2026-W41" 覆盖本地 10/05 00:00 ~ 10/12 00:00

向后兼容：月粒度行为与函数签名一字未变（被 17 个单测固化）；周粒度由同一组
函数按编码自动分派，调用方（calc_engine）拿到的仍是 ``tuple[int, int]`` 区间。
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta

from app.config import get_settings

logger = logging.getLogger(__name__)

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")

#: 周期字符串校验正则（月 + ISO 周）：API Query 与 pydantic Field 共用，
#: 避免同一个正则在多处散落、改一处漏一处。
PERIOD_PATTERN = r"^\d{4}-(?:\d{2}|W\d{2})$"

#: 任务周期偏移允许范围：-MAX_PERIOD_OFFSET ~ 0（0=本期，-1=上一个周期）
MAX_PERIOD_OFFSET = 24

_WEEK_DAYS = 7


def site_tz() -> ZoneInfo:
    """站点时区（周边界与触发时刻换算用）；配置非法时回退 UTC 并告警。"""
    name = get_settings().report_timezone or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("report_timezone=%r 非法，回退 UTC", name)
        return ZoneInfo("UTC")


def is_week(period: str) -> bool:
    """是否周周期（"YYYY-Www"）。空串/非法格式返回 False。"""
    return bool(_WEEK_RE.match(period or ""))


def granularity_of(period: str) -> str:
    """周期粒度："month" | "week"；非法格式抛 ValueError。"""
    if _WEEK_RE.match(period or ""):
        return "week"
    if _MONTH_RE.match(period or ""):
        return "month"
    raise ValueError(f"非法 period: {period!r}，格式应为 YYYY-MM 或 YYYY-Www")


def parse_period(period: str) -> tuple[int, int]:
    """"2026-09" -> (2026, 9)。非法格式抛 ValueError。

    月粒度专用（返回值语义已被单测固化：调用方拿 (year, month) 直接构造
    ``datetime(year, month, 1)``），周粒度请用 ``parse_week``。
    """
    m = _MONTH_RE.match(period)
    if not m:
        raise ValueError(f"非法 period: {period!r}，格式应为 YYYY-MM")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"非法 period: {period!r}，月份必须在 01-12")
    return year, month


def parse_week(period: str) -> tuple[int, int]:
    """"2026-W41" -> (ISO 年, ISO 周号)。非法格式抛 ValueError。"""
    m = _WEEK_RE.match(period)
    if not m:
        raise ValueError(f"非法 period: {period!r}，格式应为 YYYY-Www")
    year, week = int(m.group(1)), int(m.group(2))
    if not 1 <= week <= 53:
        raise ValueError(f"非法 period: {period!r}，周号必须在 01-53")
    return year, week


def _month_unix_range(period: str) -> tuple[int, int]:
    year, month = parse_period(period)
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = start + relativedelta(months=1)
    return int(start.timestamp()), int(end.timestamp())


def _week_start(period: str) -> datetime:
    """周周期在站点时区下的周一 00:00。"""
    year, week = parse_week(period)
    return datetime.fromisocalendar(year, week, 1).replace(tzinfo=site_tz())


def period_to_unix_range(period: str) -> tuple[int, int]:
    """本期区间 [起, 止)，Unix 秒。

    - 月："2026-09" -> [当月首日 00:00 UTC, 次月首日 00:00 UTC)
    - 周："2026-W41" -> [周一 00:00, 下周一 00:00)（站点时区，恒 7 天）
    """
    if is_week(period):
        start = _week_start(period)
        end = start + timedelta(days=_WEEK_DAYS)
        return int(start.timestamp()), int(end.timestamp())
    return _month_unix_range(period)


def shift_period(period: str, steps: int) -> str:
    """周期平移（展示与基线定位用）。

    - 月：shift_period("2026-01", -1) -> "2025-12"
    - 周：shift_period("2026-W01", -1) -> "2025-W53"（ISO 年随周号回退）

    ``steps`` 对两种粒度都表示"平移几个周期"：月=月数，周=周数。
    """
    if is_week(period):
        year, week = parse_week(period)
        shifted = datetime.fromisocalendar(year, week, 1) + relativedelta(weeks=steps)
        iso = shifted.isocalendar()
        return f"{iso[0]:04d}-W{iso[1]:02d}"
    year, month = parse_period(period)
    shifted = datetime(year, month, 1, tzinfo=timezone.utc) + relativedelta(
        months=steps
    )
    return shifted.strftime("%Y-%m")


def baseline_range(period: str, lookback: int) -> tuple[int, int]:
    """基线区间 = 本期区间两端同步向前平移 lookback 个周期。

    两端同减（而非按天数折算），保证基线始终是完整日历月/完整周，
    与本期同结构、同聚合口径。
    """
    if lookback < 1:
        raise ValueError("lookback 必须 >= 1")
    if is_week(period):
        start = _week_start(period) - timedelta(days=_WEEK_DAYS * lookback)
        end = start + timedelta(days=_WEEK_DAYS)
        return int(start.timestamp()), int(end.timestamp())
    year, month = parse_period(period)
    delta = relativedelta(months=lookback)
    b_start = datetime(year, month, 1, tzinfo=timezone.utc) - delta
    b_end = b_start + relativedelta(months=1)
    return int(b_start.timestamp()), int(b_end.timestamp())


def period_label(period: str) -> str:
    """人读标签：月 -> "2026-09"；周 -> "2026年第41周（10/05–10/11）"。"""
    if not is_week(period):
        return period
    year, week = parse_week(period)
    start = _week_start(period)
    end = start + timedelta(days=_WEEK_DAYS - 1)
    return (
        f"{year}年第{week}周"
        f"（{start.strftime('%m/%d')}–{end.strftime('%m/%d')}）"
    )


def report_kind(period: str) -> str:
    """报告种类文案："月报" / "周报"（邮件主题、正文共用）。"""
    return "周报" if is_week(period) else "月报"


def period_of_moment(
    moment: datetime, granularity: str = "month", offset: int = 0
) -> str:
    """触发时刻 -> 统计周期编码（按站点时区）。

    - granularity="month" -> "2026-10"；"week" -> "2026-W41"
    - offset<0：取上一/上 N 个周期（如周一触发取上周 = -1）；0 = 触发时刻所在周期
    """
    local = moment.astimezone(site_tz())
    if granularity == "week":
        iso = local.isocalendar()
        base = f"{iso[0]:04d}-W{iso[1]:02d}"
    else:
        base = local.strftime("%Y-%m")
    return shift_period(base, offset) if offset else base