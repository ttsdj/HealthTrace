import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/healthtrace",
)


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.001, float(os.getenv(name, str(default))))
    except ValueError:
        return default


_ENGINE_OPTIONS = {"pool_pre_ping": True}
if not DATABASE_URL.startswith("sqlite"):
    # Keep capacity explicit and bounded.  API-level admission control reserves
    # connections for jobs and recovery paths instead of allowing requests to
    # wait indefinitely in SQLAlchemy's implicit pool defaults.
    _ENGINE_OPTIONS.update(
        pool_size=_positive_int_env("HEALTHTRACE_DB_POOL_SIZE", 20),
        max_overflow=_positive_int_env("HEALTHTRACE_DB_MAX_OVERFLOW", 5),
        pool_timeout=_positive_float_env("HEALTHTRACE_DB_POOL_TIMEOUT_SECONDS", 2.0),
        pool_recycle=_positive_int_env("HEALTHTRACE_DB_POOL_RECYCLE_SECONDS", 1800),
    )

engine = create_engine(DATABASE_URL, **_ENGINE_OPTIONS)


import re
import unicodedata

# 编译非打印 C0/C1 控制字符的正则（保留常规排版字：\t, \n, \r）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 编译零宽字符和不可见格式化控制字符
_INVISIBLE_CHAR_RE = re.compile(r"[\u200b-\u200d\ufeff\u200f\u202a-\u202e]")


def _clean_nul_chars(val):
    """
    递归清除 Python 数据结构中所有可能破坏底层关系驱动、造成 JSON 解析异常或乱码的非标字符。
    1. 规范化 (Normalization)：自动规范化为标准 Unicode NFC 格式。
    2. 清洗不可见及非排版字：抹除零宽空字符、格式强制方向符、非排版控制字符及 PUA 私有使用区方块。
    3. 收敛并擦除代理项：利用 utf-8 ignore 安全剥离任何残损的、孤立的 UTF-16 代理。
    """
    if isinstance(val, str):
        # 1. 规范化为 NFC
        val = unicodedata.normalize("NFC", val)
        # 2. 擦除零宽、不可见控制和 BOM 字符
        val = _INVISIBLE_CHAR_RE.sub("", val)
        # 3. 擦除非排版 C0/C1 字符及 PUA 私有区字符
        val = _CONTROL_CHAR_RE.sub("", val)
        val = re.sub(r"[\ue000-\uf8ff]", "", val)
        # 4. 转换并确保 100% 合规的 UTF-8 (等同于 PG 的 utf8mb4 标准)
        try:
            return val.encode("utf-8", "ignore").decode("utf-8", "ignore")
        except Exception:
            chars = []
            for char in val:
                if 0xD800 <= ord(char) <= 0xDFFF:
                    continue
                chars.append(char)
            return "".join(chars)
    elif isinstance(val, dict):
        return {k: _clean_nul_chars(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_clean_nul_chars(v) for v in val]
    elif isinstance(val, tuple):
        return tuple(_clean_nul_chars(v) for v in val)
    return val


@event.listens_for(engine, "before_cursor_execute", retval=True)
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    """
    SQLAlchemy 全局引擎监听器：拦截所有底层的 SQL 执行和传入绑定的参数。
    在所有底层驱动执行前自动过滤、擦除所有参数中的 \x00 字符，
    彻底、优雅地解决 PostgreSQL 不允许 VARCHAR/TEXT 写入 NUL (0x00) 字节的异常，
    使业务层不需要在各处手动书写 replace() 代码。
    """
    if parameters is not None:
        if isinstance(parameters, dict):
            for k, v in list(parameters.items()):
                parameters[k] = _clean_nul_chars(v)
        elif isinstance(parameters, list):
            for i, v in enumerate(parameters):
                parameters[i] = _clean_nul_chars(v)
        elif isinstance(parameters, tuple):
            parameters = tuple(_clean_nul_chars(v) for v in parameters)
    return statement, parameters


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    # Delayed import to avoid circular dependency.
    import backend.db.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if os.getenv("HEALTHTRACE_AUTO_MIGRATE", "false").lower() == "true":
        from backend.infra.migrations import (
            apply_phase1_migration,
            apply_phase2_fact_candidate_migration,
            apply_phase3_long_term_task_migration,
            apply_phase4_notification_delivery_migration,
            apply_phase5_access_security_migration,
            apply_phase6_jobs_golden_migration,
            apply_phase7_incremental_document_migration,
            apply_phase8_reversible_document_migration,
        )

        apply_phase1_migration(engine)
        apply_phase2_fact_candidate_migration(engine)
        apply_phase3_long_term_task_migration(engine)
        apply_phase4_notification_delivery_migration(engine)
        apply_phase5_access_security_migration(engine)
        apply_phase6_jobs_golden_migration(engine)
        apply_phase7_incremental_document_migration(engine)
        apply_phase8_reversible_document_migration(engine)
