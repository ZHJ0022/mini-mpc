from datetime import date, datetime, time, tzinfo


AUDIT_WINDOW_START_HOUR = 22
AUDIT_WINDOW_HOURS = 10


def audit_window_start(audit_date: date, *, tzinfo: tzinfo | None = None) -> datetime:
    """Return the configured audit-window start for a selected audit date.

    根据抽查日期生成当前策略下的窗口开始时间；当前固定为当天 22:00。
    Return the current policy's audit-window start for the selected date; the
    window currently starts at 22:00 on that date.
    """

    return datetime.combine(
        audit_date,
        time(hour=AUDIT_WINDOW_START_HOUR),
        tzinfo=tzinfo,
    )
