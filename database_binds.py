"""SQLAlchemy 多绑定：三库分离，业务代码不感知底层引擎。

bind key 命名约定（与文档 4.3.3 一致）：
- bind_trade  -> 海关明细 / HS6 主数据
- bind_user   -> 用户、订阅、报告、付费、邀请码、验证码、登录日志
- bind_content-> 网站配置、页面、公告、导航菜单
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import scoped_session, sessionmaker

from config.settings import settings

BIND_TRADE: str = "bind_trade"
BIND_USER: str = "bind_user"
BIND_CONTENT: str = "bind_content"

ALL_BINDS: tuple[str, ...] = (BIND_TRADE, BIND_USER, BIND_CONTENT)

_BIND_TO_URL = {
    BIND_TRADE: settings.trade_db_url,
    BIND_USER: settings.user_db_url,
    BIND_CONTENT: settings.content_db_url,
}


def _make_engine(url: str) -> Engine:
    """SQLite 用单连接 + 关闭 WAL/共享内存文件，规避 tmpfs noexec 挂载问题；
    PostgreSQL 开 pool_pre_ping。"""
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=StaticPool,
            pool_reset_on_return=None,
        )
        _apply_sqlite_pragmas(engine)
        return engine
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def _apply_sqlite_pragmas(engine: Engine) -> None:
    """首次建连时应用 PRAGMA，关闭 WAL 相关文件，兼容 noexec 的 tmpfs。"""
    if not str(engine.url).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.close()


_engines: dict[str, Engine] = {}
_session_factories: dict[str, scoped_session] = {}


def _get_engine(bind: str) -> Engine:
    if bind not in _engines:
        _engines[bind] = _make_engine(_BIND_TO_URL[bind])
    return _engines[bind]


def _get_session_factory(bind: str) -> scoped_session:
    if bind not in _session_factories:
        sf = scoped_session(
            sessionmaker(
                bind=_get_engine(bind),
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
                future=True,
            )
        )
        _session_factories[bind] = sf
    return _session_factories[bind]


trade_engine: Engine = property(lambda _: _get_engine(BIND_TRADE))  # type: ignore[assignment]
user_engine: Engine = property(lambda _: _get_engine(BIND_USER))  # type: ignore[assignment]
content_engine: Engine = property(lambda _: _get_engine(BIND_CONTENT))  # type: ignore[assignment]

trade_session_factory: scoped_session = property(lambda _: _get_session_factory(BIND_TRADE))  # type: ignore[assignment]
user_session_factory: scoped_session = property(lambda _: _get_session_factory(BIND_USER))  # type: ignore[assignment]
content_session_factory: scoped_session = property(lambda _: _get_session_factory(BIND_CONTENT))  # type: ignore[assignment]


def get_session(bind: str) -> scoped_session:
    """业务代码通过 bind key 取 Session，禁止跨库 JOIN。"""
    if bind not in _BIND_TO_URL:
        raise ValueError(f"未知 bind: {bind}，合法取值: {list(_BIND_TO_URL)}")
    return _get_session_factory(bind)


@contextmanager
def session_scope(bind: str) -> Iterator[scoped_session]:
    """事务性 Session 上下文：异常自动回滚。"""
    sf = get_session(bind)
    session = sf()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        sf.remove()


def init_all_engines() -> None:
    """导入全部模型并建表（SQLite 开发态）。生产态请走 Alembic。

    逐表 checkfirst 创建：在部分网络文件系统（virtiofs 等）上，
    批量建表可能触发锁竞争导致 I/O 报错，逐表 + 退避重试规避。

    整个建表过程用进程级锁串行化，避免多个进程同时 touch 同一 db 文件。
    """
    import time
    from contextlib import contextmanager

    from web.models import user_models, content_models  # noqa: F401
    from core import models as trade_models  # noqa: F401

    # 进程级互斥：防止多个 worker / 测试进程同时建表
    lock_path = None
    try:
        import os
        import fcntl
        lock_path = os.open("/dev/shm/customs_report_init.lock",
                            os.O_CREAT | os.O_RDWR, 0o600)
    except Exception:
        lock_path = None

    @contextmanager
    def _plock():
        if lock_path is not None:
            try:
                fcntl.flock(lock_path, fcntl.LOCK_EX)
            except Exception:
                pass
            try:
                yield
            finally:
                try:
                    fcntl.flock(lock_path, fcntl.LOCK_UN)
                except Exception:
                    pass
        else:
            yield

    with _plock():
        for bind in ALL_BINDS:
            engine = _get_engine(bind)
            for m in _models_for_bind(bind):
                for tbl in m.Base.metadata.sorted_tables:
                    for attempt in range(6):
                        try:
                            with engine.begin() as conn:
                                tbl.create(conn, checkfirst=True)
                            # 建完立即验证可读，提前暴露锁问题
                            with engine.begin() as conn:
                                conn.execute(__import__("sqlalchemy").text("select 1"))
                            break
                        except Exception:
                            if attempt == 5:
                                raise
                            time.sleep(0.3 * (attempt + 1))
            # 每个库建完后释放连接，避免持锁跨库
            try:
                engine.dispose()
            except Exception:
                pass


def _models_for_bind(bind: str):
    if bind == BIND_TRADE:
        from core import models as trade_models
        return [trade_models]
    if bind == BIND_USER:
        from web.models import user_models
        return [user_models]
    if bind == BIND_CONTENT:
        from web.models import content_models
        return [content_models]
    return []


def dispose_all_engines() -> None:
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
    _session_factories.clear()


def bind_url_for(bind: str) -> str:
    return _BIND_TO_URL[bind]
