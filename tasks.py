"""Celery 任务定义。

生产环境：由 celery worker 消费；开发态可直接调用函数本身。
"""

from __future__ import annotations

import logging

from core.report import ReportInput, generate_report
from core.hs_service import hs_name

log = logging.getLogger(__name__)

try:
    from worker.celery_app import app as celery_app

    def _task(**opts):
        return celery_app.task(**opts)
except Exception:  # 未安装 celery / broker 不可用时退化
    def _task(**opts):
        def deco(f):
            f.delay = staticmethod(lambda *a, **k: f(*a, **k))
            f.apply_async = staticmethod(lambda *a, **k: type("R", (), {"id": "sync", "get": lambda s: f(*a, **k)})())
            return f
        return deco


@_task(name="worker.tasks.generate_report_task", bind=True, max_retries=2)
def generate_report_task(self, hs_code: str, period: str, hs_name_param: str = "",
                         focus_countries: list[str] | None = None,
                         output_dir: str | None = None) -> dict:
    """生成单份报告任务。"""
    try:
        if output_dir:
            from config.settings import settings
            import os
            os.makedirs(output_dir, exist_ok=True)
            original = settings.REPORTS_DIR
            settings.REPORTS_DIR = os.path.abspath(output_dir)
            try:
                result = generate_report(ReportInput(hs_code=hs_code, period=period,
                                                    hs_name=hs_name_param, focus_countries=focus_countries or []))
            finally:
                settings.REPORTS_DIR = original
        else:
            result = generate_report(ReportInput(hs_code=hs_code, period=period,
                                                hs_name=hs_name_param, focus_countries=focus_countries or []))
        return {
            "file_path": result.file_path,
            "file_url": result.file_url,
            "file_size": result.file_size,
            "period": result.period,
            "hs_code": result.hs_code,
        }
    except Exception as e:
        log.exception("报告生成失败 hs=%s period=%s", hs_code, period)
        raise self.retry(exc=e, countdown=60)


@_task(name="worker.tasks.check_data_readiness_task")
def check_data_readiness_task() -> dict:
    """每月定时检查：数据是否已就绪。"""
    from datetime import date
    from core.repository import ExportRepository

    repo = ExportRepository()
    periods = repo.list_periods()
    latest = periods[-1] if periods else None

    today = date.today()
    if today.month == 1:
        expected = f"{today.year - 1}-12"
    else:
        expected = f"{today.year}-{today.month - 1:02d}"

    return {
        "latest_period": latest,
        "expected": expected,
        "ready": latest == expected,
        "checked_at": today.isoformat(),
    }


@_task(name="worker.tasks.monthly_report_task")
def monthly_report_task(hs_codes: list[str] | None = None) -> dict:
    """每月定时任务：为每个订阅品类生成最新期报告。

    未指定 hs_codes 时，从有效订阅中聚合品类列表。
    """
    from datetime import date
    from core.repository import ExportRepository
    from web.models.user_models import Subscription
    from config.database_binds import get_session, BIND_USER

    today = date.today()
    if today.month == 1:
        period = f"{today.year - 1}-12"
    else:
        period = f"{today.year}-{today.month - 1:02d}"

    if not hs_codes:
        sf = get_session(BIND_USER)
        sess = sf()
        try:
            subs = sess.query(Subscription).filter(Subscription.status == "active").all()
            hs_codes = sorted({s.hs_code for s in subs})
        finally:
            sf.remove()

    results = []
    for code in hs_codes:
        try:
            out = generate_report_task(code, period, hs_name_param=hs_name(code) or "")
            results.append({"hs_code": code, "ok": True, "file_url": out["file_url"]})
        except Exception as e:
            log.exception("月度报告生成失败 hs=%s", code)
            results.append({"hs_code": code, "ok": False, "error": str(e)[:200]})
    return {"period": period, "total": len(hs_codes), "results": results}
