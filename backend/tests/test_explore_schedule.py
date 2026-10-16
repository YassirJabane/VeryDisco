from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.triggers.cron import CronTrigger
from starlette.requests import Request

from backend.app import main
from backend.app.scheduler import SchedulerManager


def manager_for(now):
    manager = SchedulerManager(SimpleNamespace())
    jobs = {
        'daily_sync_job': SimpleNamespace(next_run_time=CronTrigger(hour=4, timezone='UTC').get_next_fire_time(None, now)),
        'weekly_sync_job': SimpleNamespace(next_run_time=CronTrigger(day_of_week='tue', hour=4, timezone='UTC').get_next_fire_time(None, now)),
    }
    manager.scheduler = SimpleNamespace(get_job=jobs.get)
    return manager, jobs


def test_weekly_schedule_does_not_advance_when_daily_schedule_rolls_forward():
    before, _ = manager_for(datetime.fromisoformat('2026-10-21T12:00:00+00:00'))
    after, _ = manager_for(datetime.fromisoformat('2026-10-22T12:00:00+00:00'))
    assert before.get_next_run_time('daily-jams') == '2026-10-22T04:00:00+00:00'
    assert after.get_next_run_time('daily-jams') == '2026-10-23T04:00:00+00:00'
    for source in ['weekly-jams', 'weekly-exploration']:
        assert before.get_next_run_time(source) == after.get_next_run_time(source) == '2026-10-27T04:00:00+00:00'
    assert before.get_next_run_time() == before.get_next_run_time('daily-jams')
    assert before.get_next_run_time('unknown') is None


def test_absent_or_paused_weekly_job_does_not_fall_back_to_daily():
    manager, jobs = manager_for(datetime.fromisoformat('2026-10-21T12:00:00+00:00'))
    jobs['weekly_sync_job'].next_run_time = None
    assert manager.get_next_run_time('weekly-jams') is None
    jobs.pop('weekly_sync_job')
    assert manager.get_next_run_time('weekly-exploration') is None
    jobs['weekly_sync_job'] = SimpleNamespace()  # Pending job, scheduler not started.
    assert manager.get_next_run_time('weekly-jams') is None


def test_source_schedule_preserves_job_timezone_offset():
    manager, jobs = manager_for(datetime.fromisoformat('2026-10-21T12:00:00+00:00'))
    jobs['weekly_sync_job'].next_run_time = CronTrigger(
        day_of_week='tue', hour=4, timezone='Europe/Rome').get_next_fire_time(
            None, datetime.fromisoformat('2026-10-21T12:00:00+02:00'))
    assert manager.get_next_run_time('weekly-jams') == '2026-10-27T04:00:00+01:00'


@pytest.mark.asyncio
@pytest.mark.parametrize('active', [['daily-jams', 'weekly-jams', 'weekly-exploration'], ['weekly-jams'], []])
async def test_status_exposes_schedule_only_for_user_enabled_sources(monkeypatch, active):
    manager, _ = manager_for(datetime.fromisoformat('2026-10-21T12:00:00+00:00'))
    monkeypatch.setattr(main, 'scheduler_manager', manager)
    monkeypatch.setattr(main, 'config_manager', SimpleNamespace(is_configured=True, validation_errors=None))
    monkeypatch.setattr(main, 'db', SimpleNamespace(
        get_user_config=AsyncMock(return_value={'active_playlists': active}),
        get_latest_run=AsyncMock(return_value=None)))
    monkeypatch.setattr(main.sync_module, 'is_syncing', False)
    monkeypatch.setattr('backend.app.auth.get_current_user', AsyncMock(return_value={'id': 'alice'}))
    response = await main.get_status(Request({'type': 'http'}))
    assert response['next_run'] == manager.get_next_run_time()
    for source in ['daily-jams', 'weekly-jams', 'weekly-exploration']:
        assert response['next_runs'][source] == (manager.get_next_run_time(source) if source in active else None)
