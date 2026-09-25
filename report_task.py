"""报告生成任务服务：管理异步任务状态（内存字典 + 文件锁）。

生产环境替换为 Celery + Redis 即可，接口保持一致。
任务状态：pending -> running -> success / failed
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.report import ReportInput, generate_report

log = logging.getLogger(__name__)

# 进程内任务存储：task_id -> ReportTask。生产环境替换为 Redis。
_lock = threading.Lock()
_tasks: dict[str, "ReportTask"] = {}


@dataclass
class ReportTask:
    task_id: str
    hs_code: str
    period: str
    report_type: str = "monthly"
    status: str = "pending"          # pending / running / success / failed
    progress: int = 0
    report_url: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d


def create_task(hs_code: str, period: str, report_type: str = "monthly",
                hs_name: Optional[str] = None,
                focus_countries: Optional[list[str]] = None) -> ReportTask:
    """创建任务并立即在后台线程执行。返回任务对象。"""
    task = ReportTask(
        task_id=uuid.uuid4().hex,
        hs_code=hs_code,
        period=period,
        report_type=report_type,
    )
    with _lock:
        _tasks[task.task_id] = task

    def _run():
        try:
            task.status = "running"
            task.progress = 10
            inp = ReportInput(hs_code=hs_code, period=period,
                              hs_name=hs_name, focus_countries=focus_countries or [])
            result = generate_report(inp)
            task.progress = 100
            task.status = "success"
            task.report_url = result.file_url
            task.file_path = result.file_path
            task.file_size = result.file_size
            task.finished_at = datetime.now()
        except Exception as e:
            log.exception("报告生成失败 task=%s", task.task_id)
            task.status = "failed"
            task.error = str(e)[:500]
            task.finished_at = datetime.now()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return task


def get_task(task_id: str) -> Optional[ReportTask]:
    with _lock:
        return _tasks.get(task_id)


def list_tasks(limit: int = 20) -> list[ReportTask]:
    with _lock:
        tasks = sorted(_tasks.values(), key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]
