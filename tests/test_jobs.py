import threading
import time
import pytest
from fastapi import HTTPException
from app.main import launch
from app.db import connect, initialize
from app.integrations import IntegrationError


def wait_job(jid):
    for _ in range(100):
        with connect() as db:
            row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        if row['state']!='running':
            return row
        time.sleep(.01)
    raise AssertionError('Job did not complete')


def test_job_progress_serialization_and_failure():
    gate=threading.Event()
    def operation(progress):
        progress('Fetching page 2')
        assert gate.wait(timeout=3)
        return 'Done'
    first=launch('test',operation)
    try:
        with pytest.raises(HTTPException) as error:
            launch('duplicate',operation)
        assert error.value.status_code==409
    finally:
        gate.set()
    assert wait_job(first['id'])['state']=='complete'
    def failure(progress):
        raise IntegrationError('Source unavailable')
    failed=wait_job(launch('test',failure)['id'])
    assert failed['state']=='failed' and failed['message']=='Source unavailable'


def test_restart_marks_interrupted_jobs():
    with connect() as db:
        db.execute("INSERT INTO jobs(kind,state,message,started_at) VALUES('scan','running','Page 2','2026-10-31')")
    initialize()
    with connect() as db:
        assert db.execute('SELECT state FROM jobs').fetchone()[0]=='interrupted'
