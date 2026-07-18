"""后端运行配置。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def load_local_env(path: Path | None = None) -> None:
    """加载后端本地 .env；已有进程环境变量始终优先。"""
    env_path = path or (BACKEND_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("'\"")


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "sandbox"
    host: str = "0.0.0.0"
    port: int = 8100
    default_package_id: str = "pkg_backend_dev_v1"
    content_root: Path = BACKEND_ROOT / "content" / "packages"
    repository: str = "sqlite"
    database_path: Path = BACKEND_ROOT / "data" / "serious_game.db"
    role_llm_provider: str = "fake"
    role_llm_base_url: str = "https://api.qianzhang-ai.cn/v1"
    role_llm_model: str = "qwen3.6-plus"
    role_llm_api_key_env: str = "DASHSCOPE_API_KEY"
    role_llm_timeout_seconds: float = 30.0
    role_llm_max_retries: int = 2
    role_llm_max_output_tokens: int = 700
    role_llm_max_calls_per_day: int = 12
    role_llm_max_calls_per_session: int = 120
    role_llm_max_tokens_per_session: int = 240_000
    role_llm_fallback_to_fake: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        load_local_env()
        content_value = os.getenv("GAME_CONTENT_ROOT", "content/packages").strip()
        content_root = Path(content_value)
        if not content_root.is_absolute():
            content_root = BACKEND_ROOT / content_root
        database_value = os.getenv(
            "GAME_DATABASE_PATH", "data/serious_game.db"
        ).strip()
        database_path = Path(database_value)
        if not database_path.is_absolute():
            database_path = BACKEND_ROOT / database_path
        settings = cls(
            environment=os.getenv("GAME_ENVIRONMENT", "sandbox").strip().lower(),
            host=os.getenv("GAME_HOST", "0.0.0.0").strip(),
            port=int(os.getenv("GAME_PORT", "8100")),
            default_package_id=os.getenv(
                "GAME_DEFAULT_PACKAGE_ID", "pkg_backend_dev_v1"
            ).strip(),
            content_root=content_root.resolve(),
            repository=os.getenv("GAME_REPOSITORY", "sqlite").strip().lower(),
            database_path=database_path.resolve(),
            role_llm_provider=os.getenv("ROLE_LLM_PROVIDER", "fake").strip().lower(),
            role_llm_base_url=os.getenv(
                "ROLE_LLM_BASE_URL",
                "https://api.qianzhang-ai.cn/v1",
            ).strip().rstrip("/"),
            role_llm_model=os.getenv("ROLE_LLM_MODEL", "qwen3.6-plus").strip(),
            role_llm_api_key_env=os.getenv(
                "ROLE_LLM_API_KEY_ENV", "DASHSCOPE_API_KEY"
            ).strip(),
            role_llm_timeout_seconds=float(
                os.getenv("ROLE_LLM_TIMEOUT_SECONDS", "30")
            ),
            role_llm_max_retries=int(os.getenv("ROLE_LLM_MAX_RETRIES", "2")),
            role_llm_max_output_tokens=int(
                os.getenv("ROLE_LLM_MAX_OUTPUT_TOKENS", "700")
            ),
            role_llm_max_calls_per_day=int(
                os.getenv("ROLE_LLM_MAX_CALLS_PER_DAY", "12")
            ),
            role_llm_max_calls_per_session=int(
                os.getenv("ROLE_LLM_MAX_CALLS_PER_SESSION", "120")
            ),
            role_llm_max_tokens_per_session=int(
                os.getenv("ROLE_LLM_MAX_TOKENS_PER_SESSION", "240000")
            ),
            role_llm_fallback_to_fake=os.getenv(
                "ROLE_LLM_FALLBACK_TO_FAKE", "true"
            ).strip().lower() in {"1", "true", "yes", "on"},
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"sandbox", "test", "production"}:
            raise ValueError("GAME_ENVIRONMENT must be sandbox, test, or production")
        if self.repository not in {"memory", "sqlite", "mysql"}:
            raise ValueError("GAME_REPOSITORY must be memory, sqlite, or mysql")
        if self.environment == "production" and self.repository != "mysql":
            raise ValueError("production must use the MySQL repository")
        if self.environment == "production" and self.role_llm_provider == "fake":
            raise ValueError("production must not use the fake role LLM")
        if self.role_llm_provider not in {"fake", "openai_compatible"}:
            raise ValueError("ROLE_LLM_PROVIDER must be fake or openai_compatible")
        if self.role_llm_provider == "openai_compatible":
            if not self.role_llm_base_url.startswith("https://"):
                raise ValueError("ROLE_LLM_BASE_URL must use https")
            if not self.role_llm_model or not self.role_llm_api_key_env:
                raise ValueError("real role LLM requires model and API key env name")
        if not 1 <= self.role_llm_max_output_tokens <= 4000:
            raise ValueError("ROLE_LLM_MAX_OUTPUT_TOKENS must be between 1 and 4000")
        if self.role_llm_timeout_seconds <= 0 or self.role_llm_max_retries not in range(0, 4):
            raise ValueError("invalid role LLM timeout or retry count")
        if min(
            self.role_llm_max_calls_per_day,
            self.role_llm_max_calls_per_session,
            self.role_llm_max_tokens_per_session,
        ) <= 0:
            raise ValueError("role LLM budgets must be positive")
