"""运行时配置辅助工具。"""

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen-plus"
DEFAULT_OPENSEARCH_INDEX = "serious_game_sources"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path = ".env", override: bool = True) -> None:
    """从 .env 文件加载简单的 KEY=VALUE 配置，默认覆盖已有环境变量。

    先用工作目录查找，找不到再回退到项目根目录。
    """

    env_path = Path(path)
    if not env_path.is_absolute():
        # 相对路径：先尝试 CWD，再尝试项目根目录
        candidates = [Path.cwd() / env_path, _PROJECT_ROOT / env_path]
    else:
        candidates = [env_path]

    resolved = None
    for candidate in candidates:
        if candidate.exists():
            resolved = candidate
            break

    if resolved is None:
        print(f"[load_dotenv] ⚠ 未找到 .env 文件（搜索路径: {', '.join(str(c) for c in candidates)}）")
        print("[load_dotenv]   请将 .env.example 复制为 .env 并填入真实配置。")
        return

    print(f"[load_dotenv] ✓ 加载配置: {resolved}")

    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


@dataclass(frozen=True)
class QwenConfig:
    """DashScope OpenAI 兼容模式下的 Qwen 对话配置。"""

    api_key: str
    base_url: str = DEFAULT_QWEN_BASE_URL
    model: str = DEFAULT_QWEN_MODEL
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls, dotenv_path: str | Path = ".env") -> "QwenConfig":
        load_dotenv(dotenv_path)
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is required to use Qwen generation")

        timeout_raw = os.getenv("QWEN_TIMEOUT_SECONDS", "60").strip()
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError as exc:
            raise ValueError("QWEN_TIMEOUT_SECONDS must be an integer") from exc

        return cls(
            api_key=api_key,
            base_url=os.getenv("QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL).strip().rstrip("/"),
            model=os.getenv("QWEN_MODEL", DEFAULT_QWEN_MODEL).strip(),
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class OpenSearchConfig:
    """OpenSearch 检索配置。"""

    host: str
    port: int
    username: str
    password: str
    index: str = DEFAULT_OPENSEARCH_INDEX
    use_ssl: bool = True
    verify_certs: bool = False
    timeout_seconds: int = 10
    top_k: int = 5
    inner_hits_size: int = 2
    full_document_chunk_limit: int = 20
    chunk_order_field: str = "chunk_index"
    identifier_field: str = "identifier"
    title_field: str = "title"
    content_field: str = "content"
    search_fields: tuple[str, ...] = ("title^2", "content", "summary", "tags")

    @classmethod
    def from_env(cls, dotenv_path: str | Path = ".env") -> "OpenSearchConfig":
        load_dotenv(dotenv_path)
        host = os.getenv("OPENSEARCH_HOST", "localhost").strip()
        username = os.getenv("OPENSEARCH_USERNAME", "").strip()
        password = os.getenv("OPENSEARCH_PASSWORD", "").strip()

        if not host:
            raise ValueError("OPENSEARCH_HOST is required")
        if not username:
            raise ValueError("OPENSEARCH_USERNAME is required")
        if not password:
            raise ValueError("OPENSEARCH_PASSWORD is required")

        return cls(
            host=host,
            port=_int_from_env("OPENSEARCH_PORT", 9200),
            username=username,
            password=password,
            index=os.getenv("OPENSEARCH_INDEX", DEFAULT_OPENSEARCH_INDEX).strip(),
            use_ssl=_bool_from_env("OPENSEARCH_USE_SSL", True),
            verify_certs=_bool_from_env("OPENSEARCH_VERIFY_CERTS", False),
            timeout_seconds=_int_from_env("OPENSEARCH_TIMEOUT_SECONDS", 10),
            top_k=_int_from_env("OPENSEARCH_TOP_K", 5),
            inner_hits_size=_int_from_env("OPENSEARCH_INNER_HITS_SIZE", 2),
            full_document_chunk_limit=_int_from_env("OPENSEARCH_FULL_DOCUMENT_CHUNK_LIMIT", 20),
            chunk_order_field=os.getenv("OPENSEARCH_CHUNK_ORDER_FIELD", "chunk_index").strip(),
            identifier_field=os.getenv("OPENSEARCH_IDENTIFIER_FIELD", "identifier").strip(),
            title_field=os.getenv("OPENSEARCH_TITLE_FIELD", "title").strip(),
            content_field=os.getenv("OPENSEARCH_CONTENT_FIELD", "content").strip(),
            search_fields=_tuple_from_env(
                "OPENSEARCH_SEARCH_FIELDS",
                ("title^2", "content", "summary", "tags"),
            ),
        )


@dataclass(frozen=True)
class PABackendConfig:
    """pa_backend agent API configuration."""

    base_url: str = ""
    login_endpoint: str = "/api/auth/sso/login-password"
    agent_endpoint: str = "/agent/os-search/general"
    supabase_url: str = ""
    supabase_key: str = ""
    account: str = ""
    password: str = ""
    timeout_seconds: int = 180
    search_preference: str = "breadth"
    enable_web_search: bool = True
    verify_ssl: bool = True
    collection_id: str = ""

    @classmethod
    def from_env(cls, dotenv_path: str | Path = ".env") -> "PABackendConfig":
        load_dotenv(dotenv_path)
        return cls(
            base_url=os.getenv("PA_BACKEND_BASE_URL", "").strip().rstrip("/"),
            login_endpoint=os.getenv(
                "PA_BACKEND_LOGIN_ENDPOINT",
                "/api/auth/sso/login-password",
            ).strip(),
            agent_endpoint=os.getenv(
                "PA_BACKEND_AGENT_ENDPOINT",
                "/agent/os-search/general",
            ).strip(),
            supabase_url=os.getenv("PA_BACKEND_SUPABASE_URL", "").strip().rstrip("/"),
            supabase_key=os.getenv("PA_BACKEND_SUPABASE_KEY", "").strip(),
            account=os.getenv("PA_BACKEND_ACCOUNT", "").strip(),
            password=os.getenv("PA_BACKEND_PASSWORD", "").strip(),
            timeout_seconds=_int_from_env("PA_BACKEND_TIMEOUT_SECONDS", 180),
            search_preference=os.getenv("PA_BACKEND_SEARCH_PREFERENCE", "breadth").strip(),
            enable_web_search=_bool_from_env("PA_BACKEND_ENABLE_WEB_SEARCH", True),
            verify_ssl=_bool_from_env("PA_BACKEND_VERIFY_SSL", True),
            collection_id=os.getenv("PA_BACKEND_COLLECTION_ID", "").strip(),
        )


@dataclass(frozen=True)
class IterativeRetrievalConfig:
    """多步资料检索配置。"""

    max_rounds: int = 3
    rewrite_initial_query: bool = True
    max_queries_per_round: int = 3
    max_full_documents: int = 2
    final_context_limit: int = 8
    context_preview_chars: int = 700

    @classmethod
    def from_env(cls, dotenv_path: str | Path = ".env") -> "IterativeRetrievalConfig":
        load_dotenv(dotenv_path)
        return cls(
            max_rounds=_int_from_env("RETRIEVAL_MAX_ROUNDS", 3),
            rewrite_initial_query=_bool_from_env("RETRIEVAL_REWRITE_INITIAL_QUERY", True),
            max_queries_per_round=_int_from_env("RETRIEVAL_MAX_QUERIES_PER_ROUND", 3),
            max_full_documents=_int_from_env("RETRIEVAL_MAX_FULL_DOCUMENTS", 2),
            final_context_limit=_int_from_env("RETRIEVAL_FINAL_CONTEXT_LIMIT", 8),
            context_preview_chars=_int_from_env("RETRIEVAL_CONTEXT_PREVIEW_CHARS", 700),
        )


def _int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_from_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _tuple_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    values = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    return values or default
