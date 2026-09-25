#!/usr/bin/env python3
"""海关 CSV 导入脚本：解析 -> 校验 -> 溯源批次 -> 幂等入库。

用法:
    python scripts/import_csv.py /path/to/data.csv [--no-skip-duplicate]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许直接以脚本方式运行（脚本位于项目根 scripts/ 下，父目录即项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.database_binds import init_all_engines  # noqa: E402
from core.repository import CSVImportService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="海关 CSV 导入工具")
    parser.add_argument("path", help="CSV 文件路径（支持 GB18030 / UTF-8，自动识别）")
    parser.add_argument("--no-skip-duplicate", action="store_true",
                        help="关闭重复文件哈希检测（同一文件会重新导入）")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"[错误] 文件不存在: {path}", file=sys.stderr)
        return 2

    init_all_engines()
    svc = CSVImportService()
    result = svc.import_file(str(path), skip_on_duplicate_hash=not args.no_skip_duplicate)

    print("=" * 60)
    print(f"源文件       : {result.source_file}")
    print(f"文件哈希     : {result.file_hash}")
    print(f"批次号       : {result.batch_id}")
    print(f"总行数       : {result.total}")
    print(f"新插入       : {result.inserted}")
    print(f"重复跳过     : {result.ignored}")
    print(f"失败         : {result.failed}")
    print(f"状态         : {result.status}")
    if result.warnings:
        print(f"\n警告（{len(result.warnings)} 条）:")
        for w in result.warnings[:10]:
            print(f"  - {w}")
    if result.errors:
        print(f"\n错误（{len(result.errors)} 条）:")
        for e in result.errors[:10]:
            print(f"  - {e}")
    print("=" * 60)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
