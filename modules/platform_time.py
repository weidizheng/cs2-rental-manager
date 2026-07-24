"""Platform-specific order-page clock rules and absolute-time conversion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

# C5 renders order times in the browser's local clock.  ECO and IGXE order
# pages have been calibrated against the user's Steam transaction history and
# display China Standard Time.
PLATFORM_ORDER_TIME_RULES = {
    "C5GAME": "浏览器本地时间",
    "ECOSteam": "北京时间（Asia/Shanghai）",
    "IGXE": "北京时间（Asia/Shanghai）",
}


def platform_order_time_rule(platform: str) -> str:
    """Return the persisted/display rule for a rental platform."""
    return PLATFORM_ORDER_TIME_RULES.get(str(platform or "").strip(), "网页时区未校准")


def utc_now() -> datetime:
    """Return a naive UTC value for safe comparisons with converted page times."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _local_timezone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _source_timezone(platform: str, local_timezone: tzinfo | None = None) -> tzinfo:
    if str(platform or "").strip() in {"ECOSteam", "IGXE"}:
        return BEIJING_TIMEZONE
    return local_timezone or _local_timezone()


def parse_platform_datetime_utc(
    value: str | datetime,
    platform: str,
    *,
    local_timezone: tzinfo | None = None,
) -> datetime:
    """Interpret a page timestamp using its platform rule and return naive UTC.

    Invalid values return ``datetime.min`` for compatibility with the existing
    dashboard lifecycle helpers.  Raw page strings stay untouched in storage
    and UI; only calculation paths use this converted value.
    """
    if isinstance(value, datetime):
        parsed = value.replace(tzinfo=None)
    else:
        try:
            parsed = datetime.strptime(" ".join(str(value or "").split()), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return datetime.min
    return parsed.replace(tzinfo=_source_timezone(platform, local_timezone)).astimezone(timezone.utc).replace(tzinfo=None)


def local_datetime_to_utc(value: datetime, *, local_timezone: tzinfo | None = None) -> datetime:
    """Convert a locally-created application timestamp to naive UTC."""
    if not isinstance(value, datetime) or value <= datetime.min:
        return datetime.min
    return value.replace(tzinfo=local_timezone or _local_timezone()).astimezone(timezone.utc).replace(tzinfo=None)
