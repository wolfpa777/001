"""Celery 应用配置：任务注册 + 定时调度。

启动 worker：
    celery -A worker.celery_app worker --loglevel=info

启动 beat（定时调度）：
    celery -A worker.celery_app beat --loglevel=info

或直接一起：
    celery -A worker.celery_app worker --beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from config.settings import settings

app = Celery(
    "customs_report",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=["worker.tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600 * 24,
)

# 定时任务（Crontab，Asia/Shanghai 时区）
app.conf.beat_schedule = {
    "data-readiness-check": {
        "task": "worker.tasks.check_data_readiness_task",
        "schedule": crontab(day_of_month=str(settings.DATA_READINESS_CHECK_DAY), hour=6, minute=0),
    },
    "monthly-report-generation": {
        "task": "worker.tasks.monthly_report_task",
        "schedule": crontab(day_of_month=str(settings.REPORT_GENERATE_CRON_DAY), hour=3, minute=0),
    },
}


if __name__ == "__main__":
    app.start()
