"""全局配置：从环境变量 / .env 读取，密钥不硬编码。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 环境 ----
    APP_ENV: str = "development"          # development | production
    APP_DEBUG: bool = True
    APP_NAME: str = "出口商品国际市场观测报告工具"

    # ---- 三库分离 (SQLAlchemy binds) ----
    # 开发态默认三个独立 SQLite 文件，生产态切 PostgreSQL 即可。
    #
    # 数据库文件存放目录选择策略（沙盒环境实测）：
    #   1) CUSTOMS_DB_DIR 环境变量（最高优先级，可强制指向 tmpfs）；
    #   2) 优先 /tmp：可读写 tmpfs，允许 SQLite 创建 journal 文件；
    #   3) 其次 /dev/shm：同为 tmpfs，但部分环境挂载为 nosuid/noexec，
    #      偶发 journal/锁文件创建失败导致 disk I/O error；
    #   4) 最后回退项目内 data/ 目录（virtiofs 挂载点，不保证 SQLite 文件锁）。
    # 生产部署一律 PostgreSQL，不受此策略影响。
    #
    # 注意：DATABASE_*_URL 在 .env 中默认指向 ./data/*.db（相对路径），
    # 这会落到项目目录而非 tmpfs。沙盒内务必通过 CUSTOMS_DB_DIR 或显式 URL 覆盖。
    CUSTOMS_DB_DIR: str = os.getenv("CUSTOMS_DB_DIR", "")
    DATABASE_TRADE_URL: str = os.getenv("DATABASE_TRADE_URL", "")
    DATABASE_USER_URL: str = os.getenv("DATABASE_USER_URL", "")
    DATABASE_CONTENT_URL: str = os.getenv("DATABASE_CONTENT_URL", "")

    @property
    def _DB_DIR(self) -> str:
        return self.CUSTOMS_DB_DIR or (
            "/tmp" if os.path.isdir("/tmp") else ("/dev/shm" if os.path.isdir("/dev/shm") else "")
        )

    def _norm_sqlite(self, url: str, shm_name: str) -> str:
        if url:
            # 用户显式配置且为绝对路径 -> 直接使用
            if url.startswith("sqlite:////"):
                return url
            # 用户显式配置但为相对路径 -> 规范化到项目绝对路径
            if url.startswith("sqlite:///"):
                p = url[len("sqlite:///"):]
                if not os.path.isabs(p):
                    p = str(PROJECT_ROOT / p)
                return f"sqlite:///{p}"
            return url
        # 未配置：开发态默认 tmpfs，规避文件锁问题
        if self._DB_DIR:
            return f"sqlite:///{self._DB_DIR}/customs_report-{shm_name}.db"
        return f"sqlite:///{PROJECT_ROOT / 'data' / f'{shm_name}.db'}"

    @property
    def trade_db_url(self) -> str:
        return self._norm_sqlite(self.DATABASE_TRADE_URL, "trade")

    @property
    def user_db_url(self) -> str:
        return self._norm_sqlite(self.DATABASE_USER_URL, "user")

    @property
    def content_db_url(self) -> str:
        return self._norm_sqlite(self.DATABASE_CONTENT_URL, "content")

    # ---- 异步任务 ----
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "")
    CELERY_RESULT_BACKEND: str = os.getenv("CELERY_RESULT_BACKEND", "")

    @property
    def celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    # ---- 认证 ----
    JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production-min-32-chars")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440      # 1440 分钟 = 24h
    BCRYPT_ROUNDS: int = 12

    # ---- DeepSeek ----
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL_DEFAULT: str = "deepseek-chat"     # v4 系列上线前用通用 chat 兜底
    DEEPSEEK_MODEL_PRO: str = "deepseek-chat"
    DEEPSEEK_TIMEOUT_SECONDS: int = 60

    # ---- 新闻源 ----
    GDELT_BASE_URL: str = "https://api.gdeltproject.org/api/v2/doc/doc"
    GDELT_TIMEOUT_SECONDS: int = 30
    NEWSAPI_KEY: str = os.getenv("NEWSAPI_KEY", "")
    BING_NEWS_KEY: str = os.getenv("BING_NEWS_KEY", "")
    NEWSDATA_KEY: str = os.getenv("NEWSDATA_KEY", "")
    NEWS_FALLBACK_MOCK: bool = True

    # ---- 业务参数 ----
    HS_VERSION: str = "HS2022"                       # 固定当前版本，转版时调整
    REPORT_TOP_N: int = 15
    REPORT_CHART_TOP_N: int = 5
    REPORT_LOOKBACK_MONTHS: int = 6
    NEWS_PER_SECTION: int = 5
    NEWS_GROWTH_UP_THRESHOLD: float = 10.0
    NEWS_GROWTH_DOWN_THRESHOLD: float = -10.0
    UNIT_PRICE_MIN_MONTHS: int = 3                   # 均价仅在连续 N 月计量单位一致时计算

    # ---- 报告输出 ----
    DATA_DIR: Path = PROJECT_ROOT / "data"
    REPORTS_DIR: Path = PROJECT_ROOT / "data" / "reports"
    CHARTS_DIR: Path = PROJECT_ROOT / "data" / "charts"
    INBOUND_CSV_DIR: Path = PROJECT_ROOT / "data" / "inbound" / "customs"
    HS_MASTER_PATH: Path = PROJECT_ROOT / "data" / "hs" / "hs_master.json"
    HS_TABLE_PATH: Path = PROJECT_ROOT / "config" / "hs_table.json"

    # ---- 验证码 ----
    SMS_CODE_TTL_SECONDS: int = 300
    EMAIL_CODE_TTL_SECONDS: int = 300
    SMS_CODE_SAME_PHONE_INTERVAL_SECONDS: int = 60
    SMS_CODE_MAX_PER_DAY: int = 5
    EMAIL_CODE_SAME_EMAIL_INTERVAL_SECONDS: int = 60
    EMAIL_CODE_MAX_PER_DAY: int = 5

    # ---- 邀请码 ----
    INVITE_CODE_PRICE: int = 568                      # 元 / 12 份报告
    INVITE_CODE_REPORTS: int = 12

    # ---- 定时任务 ----
    REPORT_GENERATE_CRON_DAY: int = 25                # 每月 25 号
    DATA_READINESS_CHECK_DAY: int = 20                # 每月 20 号后管理员需导入最新数据

    # ---- CORS ----
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        for p in (self.REPORTS_DIR, self.CHARTS_DIR, self.INBOUND_CSV_DIR, self.HS_MASTER_PATH.parent):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


def reset_settings() -> Settings:
    """测试 / 动态环境切换时重置缓存。"""
    get_settings.cache_clear()
    return get_settings()


settings = get_settings()
