"""海关库仓储 + CSV 导入服务。"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import and_, distinct, func, inspect, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert

from config.database_binds import BIND_TRADE, get_session, session_scope
from core.models import ExportRecord, HSMaster, DataImportBatch
from core.parser import iter_csv_file

log = logging.getLogger(__name__)


# ============================ 仓储 ==========================================

class ExportRepository:
    """海关出口明细仓储。所有查询限定 bind_trade，禁止跨库 JOIN。"""

    bind = BIND_TRADE

    # ---- 基础写入 ----
    def bulk_insert(self, records: list[ExportRecord]) -> int:
        if not records:
            return 0
        sf = get_session(self.bind)
        session = sf()
        try:
            session.add_all(records)
            session.commit()
            return len(records)
        except Exception:
            session.rollback()
            raise
        finally:
            sf.remove()

    def bulk_upsert(self, records: list[ExportRecord]) -> tuple[int, int]:
        """按唯一约束 upsert（sqlite 用 ON CONFLICT 忽略重复行，幂等）。"""
        if not records:
            return (0, 0)
        sf = get_session(self.bind)
        session = sf()
        inserted, ignored = 0, 0
        try:
            for rec in records:
                stmt = sqlite_upsert(ExportRecord).values(
                    period=rec.period, hs_code=rec.hs_code, partner_name=rec.partner_name,
                    trade_mode_code=rec.trade_mode_code, source_file=rec.source_file,
                    hs_name=rec.hs_name, partner_code=rec.partner_code,
                    trade_mode_name=rec.trade_mode_name, amount_usd=rec.amount_usd,
                    quantity=rec.quantity, quantity_unit=rec.quantity_unit,
                    batch_id=rec.batch_id,
                )
                # 2.0.54 及以下不支持 constraint=，改用 index_elements（约束列明确列出）
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["period", "hs_code", "partner_name",
                                    "trade_mode_code", "source_file"],
                )
                result = session.execute(stmt)
                if result.rowcount == 1:
                    inserted += 1
                else:
                    ignored += 1
            session.commit()
            return inserted, ignored
        except Exception:
            session.rollback()
            raise
        finally:
            sf.remove()

    # ---- 查询 ----
    def list_periods(self) -> list[str]:
        sf = get_session(self.bind)
        session = sf()
        try:
            rows = session.execute(select(distinct(ExportRecord.period)).order_by(ExportRecord.period)).scalars().all()
            return list(rows)
        finally:
            sf.remove()

    def latest_period(self) -> str | None:
        sf = get_session(self.bind)
        session = sf()
        try:
            return session.execute(select(ExportRecord.period).order_by(ExportRecord.period.desc()).limit(1)).scalar_one_or_none()
        finally:
            sf.remove()

    def latest_data_month(self) -> date | None:
        """数据表中最新的年月，返回该月最后一天（用于「数据截至」标注）。"""
        p = self.latest_period()
        if not p:
            return None
        y, m = int(p[:4]), int(p[5:7])
        last_day = (date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)).replace(day=1)
        import calendar
        return date(y, m, calendar.monthrange(y, m)[1])

    def list_hs_codes(self, period: str | None = None) -> list[str]:
        sf = get_session(self.bind)
        session = sf()
        try:
            stmt = select(distinct(ExportRecord.hs_code)).order_by(ExportRecord.hs_code)
            if period:
                stmt = stmt.where(ExportRecord.period == period)
            return list(session.execute(stmt).scalars().all())
        finally:
            sf.remove()

    def hs_codes_with_sufficient_history(self, min_months: int) -> list[str]:
        """需求 4.3.1：至少连续 N 个月有数据的 HS 编码，才允许生成报告。"""
        sf = get_session(self.bind)
        session = sf()
        try:
            subq = (
                select(ExportRecord.hs_code, func.count(distinct(ExportRecord.period)).label("mc"))
                .group_by(ExportRecord.hs_code)
                .having(func.count(distinct(ExportRecord.period)) >= min_months)
                .subquery()
            )
            rows = session.execute(select(subq.c.hs_code).order_by(subq.c.hs_code)).scalars().all()
            return list(rows)
        finally:
            sf.remove()

    def query(
        self,
        hs_code: str,
        period: str | None = None,
        periods: list[str] | None = None,
        partner_name: str | None = None,
        min_amount: Decimal | None = None,
    ) -> list[ExportRecord]:
        sf = get_session(self.bind)
        session = sf()
        try:
            stmt = select(ExportRecord).where(ExportRecord.hs_code == hs_code)
            if period:
                stmt = stmt.where(ExportRecord.period == period)
            if periods:
                stmt = stmt.where(ExportRecord.period.in_(periods))
            if partner_name:
                stmt = stmt.where(ExportRecord.partner_name == partner_name)
            if min_amount is not None:
                stmt = stmt.where(ExportRecord.amount_usd >= min_amount)
            return list(session.execute(stmt).scalars().all())
        finally:
            sf.remove()

    def aggregate_by_period(self, hs_code: str, periods: list[str]) -> dict[str, Decimal]:
        """按数据期聚合全球总额，供同比/环比使用。"""
        sf = get_session(self.bind)
        session = sf()
        try:
            stmt = (
                select(ExportRecord.period, func.coalesce(func.sum(ExportRecord.amount_usd), 0))
                .where(and_(ExportRecord.hs_code == hs_code, ExportRecord.period.in_(periods)))
                .group_by(ExportRecord.period)
            )
            return {p: Decimal(str(v)) for p, v in session.execute(stmt).all()}
        finally:
            sf.remove()

    def aggregate_by_country(self, hs_code: str, period: str) -> dict[str, Decimal]:
        sf = get_session(self.bind)
        session = sf()
        try:
            stmt = (
                select(ExportRecord.partner_name, func.coalesce(func.sum(ExportRecord.amount_usd), 0))
                .where(and_(ExportRecord.hs_code == hs_code, ExportRecord.period == period))
                .group_by(ExportRecord.partner_name)
            )
            return {c: Decimal(str(v)) for c, v in session.execute(stmt).all()}
        finally:
            sf.remove()

    def total_amount(self, hs_code: str, period: str) -> Decimal:
        sf = get_session(self.bind)
        session = sf()
        try:
            v = session.execute(
                select(func.coalesce(func.sum(ExportRecord.amount_usd), 0)).where(
                    and_(ExportRecord.hs_code == hs_code, ExportRecord.period == period)
                )
            ).scalar_one()
            return Decimal(str(v))
        finally:
            sf.remove()

    def data_health(self) -> dict:
        """数据完备性自检，供后台监控与降级告警（需求 7.3）。"""
        sf = get_session(self.bind)
        session = sf()
        try:
            total_rows = session.execute(select(func.count(ExportRecord.id))).scalar_one()
            total_batches = session.execute(select(func.count(DataImportBatch.id))).scalar_one()
            periods = self.list_periods()
            hs_codes = self.list_hs_codes()
            latest = self.latest_period()
            partner_cnt = session.execute(select(func.count(distinct(ExportRecord.partner_name)))).scalar_one()
            return {
                "total_rows": int(total_rows),
                "total_batches": int(total_batches),
                "period_count": len(periods),
                "periods": periods,
                "hs_code_count": len(hs_codes),
                "hs_codes": hs_codes,
                "latest_period": latest,
                "latest_data_month": self.latest_data_month().isoformat() if self.latest_data_month() else None,
                "partner_count": int(partner_cnt),
            }
        finally:
            sf.remove()


class HSMasterRepository:
    bind = BIND_TRADE

    def upsert(self, items: list[dict]) -> int:
        sf = get_session(self.bind)
        session = sf()
        n = 0
        try:
            for it in items:
                stmt = sqlite_upsert(HSMaster).values(
                    code=it["code"], name=it["name"], en_name=it.get("en_name"),
                    unit=it.get("unit"), chapter=it.get("chapter"), note=it.get("note"),
                    version=it.get("version", "HS2022"),
                ).on_conflict_do_update(
                    constraint="ix_hs_master_code",
                    set_={"name": it["name"], "en_name": it.get("en_name"),
                          "unit": it.get("unit"), "note": it.get("note"),
                          "updated_at": datetime.utcnow()},
                )
                session.execute(stmt)
                n += 1
            session.commit()
            return n
        except Exception:
            session.rollback()
            raise
        finally:
            sf.remove()

    def list_all(self) -> list[HSMaster]:
        sf = get_session(self.bind)
        session = sf()
        try:
            return list(session.execute(select(HSMaster).order_by(HSMaster.code)).scalars().all())
        finally:
            sf.remove()

    def search(self, keyword: str, limit: int = 50) -> list[HSMaster]:
        if not keyword:
            return self.list_all()[:limit]
        kw = f"%{keyword}%"
        sf = get_session(self.bind)
        session = sf()
        try:
            stmt = (
                select(HSMaster)
                .where(or_(HSMaster.code.like(kw), HSMaster.name.like(kw), HSMaster.en_name.like(kw)))
                .order_by(HSMaster.code).limit(limit)
            )
            return list(session.execute(stmt).scalars().all())
        finally:
            sf.remove()


class ImportBatchRepository:
    bind = BIND_TRADE

    def create(self, source_file: str, file_hash: str | None, total_rows: int) -> DataImportBatch:
        sf = get_session(self.bind)
        session = sf()
        try:
            b = DataImportBatch(source_file=source_file, file_hash=file_hash,
                                total_rows=total_rows, status="processing")
            session.add(b)
            session.commit()
            session.refresh(b)
            return b
        finally:
            sf.remove()

    def update_status(self, batch_id: int, status: str, imported_rows: int = 0,
                      failed_rows: int = 0, error_message: str | None = None) -> None:
        sf = get_session(self.bind)
        session = sf()
        try:
            b = session.get(DataImportBatch, batch_id)
            if b:
                b.status = status
                b.imported_rows = imported_rows
                b.failed_rows = failed_rows
                b.error_message = error_message
                session.commit()
        finally:
            sf.remove()


# ============================ 导入服务 ======================================

@dataclass
class ImportResult:
    source_file: str
    file_hash: str | None
    batch_id: int | None
    total: int
    inserted: int
    ignored: int       # 重复行
    failed: int
    errors: list[str]
    warnings: list[str]
    status: str

    @property
    def ok(self) -> bool:
        return self.status == "success"


class CSVImportService:
    """海关 CSV 导入服务：解析 -> 校验 -> 溯源批次 -> 幂等入库。"""

    def __init__(self, exports: ExportRepository | None = None,
                 batches: ImportBatchRepository | None = None):
        self.exports = exports or ExportRepository()
        self.batches = batches or ImportBatchRepository()

    @staticmethod
    def sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def import_file(self, path: str, skip_on_duplicate_hash: bool = True) -> ImportResult:
        """导入单个 CSV。同一文件哈希已成功导入则直接跳过。"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        file_hash = self.sha256(str(p))
        source = p.name

        # 幂等：同一文件哈希已成功入库，跳过
        if skip_on_duplicate_hash:
            sf = get_session(BIND_TRADE)
            session = sf()
            try:
                existing = session.execute(
                    select(DataImportBatch).where(
                        and_(DataImportBatch.file_hash == file_hash, DataImportBatch.status == "success")
                    )
                ).scalar_one_or_none()
                if existing:
                    return ImportResult(source, file_hash, existing.id, 0, 0, 0, 0,
                                       [], [f"文件已导入（批次 {existing.id}），跳过"], "skipped")
            finally:
                sf.remove()

        # 解析
        res = iter_csv_file(str(p))
        warnings: list[str] = []
        for msg in res.errors[:20]:
            warnings.append(f"解析异常：{msg}")

        # 校验：HS 编码合法性 / HS6 只 / 单 HS 单文件
        records: list[ExportRecord] = []
        seen_hs: set[str] = set()
        for r in res.rows:
            hs = r["hs_code"]
            seen_hs.add(hs)
            if len(hs) != 6:
                warnings.append(f"第{r['_line']}行 HS 编码 {hs} 非 6 位，跳过")
                continue
            records.append(ExportRecord(
                period=r["period"], hs_code=hs, hs_name=r.get("hs_name"),
                partner_code=r.get("partner_code"), partner_name=r["partner_name"],
                trade_mode_code=r.get("trade_mode_code"), trade_mode_name=r.get("trade_mode_name"),
                amount_usd=r["amount_usd"], quantity=r.get("quantity"),
                quantity_unit=r.get("quantity_unit"), source_file=source,
            ))
        if len(seen_hs) > 1:
            warnings.append(f"文件含多个 HS 编码 {sorted(seen_hs)}：建议按 HS 拆分文件，本工具仅取 HS6 行")
        # 丢弃 HS 非 6 位记录（HS4 汇总行等）
        non6 = [hs for hs in seen_hs if len(hs) != 6]
        if non6:
            warnings.append(f"已忽略非 HS6 行（编码 {non6}）")

        batch = self.batches.create(source, file_hash, len(records))

        # 溯源批量入库（upsert 幂等）
        inserted, ignored = self.exports.bulk_upsert(records)

        status = "success" if res.failed == 0 else ("partial" if inserted > 0 else "failed")
        err_msg = None
        if res.failed:
            err_msg = "; ".join(res.errors[:5])
        if not records and res.parsed == 0:
            status = "failed"
            err_msg = "无可入库记录"

        self.batches.update_status(batch.id, status, inserted, res.failed, err_msg)
        return ImportResult(source, file_hash, batch.id, res.total, inserted, ignored,
                           res.failed, res.errors[:20], warnings, status)


__all__ = [
    "ExportRepository", "HSMasterRepository", "ImportBatchRepository",
    "CSVImportService", "ImportResult",
]
