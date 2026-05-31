from datetime import datetime, timezone, timedelta

_UTC8 = timezone(timedelta(hours=8))

def now_ts() -> str:
    return datetime.now(_UTC8).strftime("%Y-%m-%dT%H:%M:%S")
