#!/usr/bin/env python3
"""开发态任务运行脚本：无需 Redis/Celery broker 即可生成报告。

用法:
    python scripts/run_worker.py <hs_code> <period>
    python scripts/run_worker.py 950691 2026-08
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.hs_service import hs_name  # noqa: E402
from core.report import ReportInput, generate_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成品类报告（开发态，无需 Celery）")
    parser.add_argument("hs_code", help="HS6 编码，如 950691")
    parser.add_argument("period", help="数据期 YYYY-MM，如 2026-08")
    parser.add_argument("--output", "-o", help="输出目录，默认 data/reports/")
    args = parser.parse_args()

    result = generate_report(ReportInput(
        hs_code=args.hs_code,
        period=args.period,
        hs_name=hs_name(args.hs_code) or "",
    ))

    print(f"✅ 报告已生成")
    print(f"   品类   : {args.hs_code}")
    print(f"   数据期 : {result.period}")
    print(f"   文件   : {result.file_path}")
    print(f"   大小   : {result.file_size / 1024:.1f} KB")
    print(f"   下载   : {result.file_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
