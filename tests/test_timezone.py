import os
import subprocess
import sys


def test_warsaw_timezone_without_system_database():
    # Windows normally has no IANA timezone files. Force that case on every OS.
    result = subprocess.run(
        [sys.executable, '-c',
         "from datetime import datetime; from zoneinfo import ZoneInfo; "
         "from app.integrations import record_date; "
         "z=ZoneInfo('Europe/Warsaw'); "
         "assert datetime(2026,7,1,tzinfo=z).utcoffset().total_seconds()==7200; "
         "assert datetime(2026,12,1,tzinfo=z).utcoffset().total_seconds()==3600; "
         "assert record_date('dzisiaj')==datetime.now(z).date().isoformat()"],
        env={**os.environ, 'PYTHONTZPATH': ''}, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
