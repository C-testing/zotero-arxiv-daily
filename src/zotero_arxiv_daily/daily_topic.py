"""Select one-day configuration overrides using the user's Shanghai calendar."""

from datetime import datetime, timedelta, timezone


SHANGHAI = timezone(timedelta(hours=8))


def override_for_day(config, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Daily topic selection requires a timezone-aware timestamp.")
    day = now.astimezone(SHANGHAI).date().isoformat()
    return config.get("daily_overrides", {}).get(day, {})
