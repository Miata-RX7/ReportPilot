"""**模型声明与 alembic 迁移建出来的表必须一致**（2026-11-03 线上故障的回归）。

故障现象：确认页上传 Excel 返回 500 ——
``NotNullViolationError: null value in column "created_at" of relation
"report_task_files" violates not-null constraint``。

根因：迁移 0019 建表时 created_at 漏了 ``server_default=now()``，而模型侧
（``TimestampMixin.created_at``）是带 server_default 的 —— SQLAlchemy 于是把该列
从 INSERT 里省略，数据库又没默认值可填 -> NULL -> 违反 NOT NULL。

**测试为什么抓不到**：测试用 sqlite + ``Base.metadata.create_all``，建表用的是
**模型的** server_default（sqlite 里有默认值）。只有真库（按迁移建的）才暴露。

所以这个测试直接读**迁移文件里的列定义**，与模型声明比对：
凡是模型声明了 server_default 的列，迁移里也必须有。
"""

import glob
import re
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _migration_files() -> list[Path]:
    return sorted(Path(p) for p in glob.glob(str(MIGRATIONS_DIR / "*.py")))


def _created_at_columns(text: str) -> list[str]:
    """取出所有 created_at 列的定义片段。

    注意**不能用简单正则**：``sa.Column("created_at", sa.DateTime(timezone=True), ...)``
    里 DateTime 自己的右括号会让非贪婪匹配提前截断，从而看不到后面的
    server_default（这会让测试误报）。所以这里做括号配平扫描。
    """
    out: list[str] = []
    needle = 'sa.Column("created_at"'
    idx = 0
    while True:
        i = text.find(needle, idx)
        if i < 0:
            break
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[i : j + 1])
        idx = j + 1
    return out


class TestMigrationModelParity:
    def test_created_at_always_has_server_default(self) -> None:
        """凡是建了 created_at 列的迁移，都必须带 server_default。

        这是本次线上故障的直接防线：漏了 server_default，模型侧会省略该列，
        插入 NULL，违反 NOT NULL。
        """
        missing: list[str] = []
        for f in _migration_files():
            text = f.read_text(encoding="utf-8")
            for frag in _created_at_columns(text):
                # server_default 可能写在 Column(...) 内部，也可能靠
                # op.alter_column 后续补 —— 只要片段里出现就算有
                if "server_default" not in frag:
                    missing.append(f"{f.name}: {frag.splitlines()[0][:80]}")
        assert not missing, "\n".join(
            ["以下迁移的 created_at 列缺少 server_default（会导致插入 NULL）:"] + missing
        )

    def test_report_task_files_migration_has_default(self) -> None:
        """本次出问题的两张表要单独确认。"""
        for name in ("report_task_files", "report_summaries"):
            hit = False
            for f in _migration_files():
                text = f.read_text(encoding="utf-8")
                if f'"{name}"' in text and "created_at" in text:
                    for frag in _created_at_columns(text):
                        if "server_default" in frag:
                            hit = True
            assert hit, f"{name} 的 created_at 找不到带 server_default 的定义"

    def test_model_timestamp_mixin_has_server_default(self) -> None:
        """模型侧必须声明 server_default（否则 SQLAlchemy 会把列从 INSERT 省略）。

        TimestampMixin 是 mixin、没有自己的 __table__，所以用一个实际继承它的
        模型（Report）来检查它带出来的列。
        """
        from app.models.report import Report

        col = Report.__table__.c["created_at"]
        assert col.server_default is not None, (
            "模型侧缺 server_default 时，SQLAlchemy 会把该列从 INSERT 里省略；"
            "若数据库列也没有默认值就会插入 NULL 违反 NOT NULL"
        )
