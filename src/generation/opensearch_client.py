"""OpenSearch 检索客户端封装。"""

from typing import Any

from src.config import OpenSearchConfig


class OpenSearchClientError(RuntimeError):
    """当 OpenSearch 客户端初始化或查询失败时抛出。"""


class OpenSearchSourceClient:
    """面向资料检索的 OpenSearch 客户端。"""

    def __init__(self, config: OpenSearchConfig) -> None:
        self._config = config
        self._client = self._build_client(config)

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._client.search(
                index=self._config.index,
                body=body,
                params={"request_timeout": self._config.timeout_seconds},
            )
        except Exception as exc:
            raise OpenSearchClientError(f"OpenSearch query failed: {exc}") from exc

    def _build_client(self, config: OpenSearchConfig) -> Any:
        try:
            from opensearchpy import OpenSearch
        except ImportError as exc:
            raise OpenSearchClientError(
                "opensearch-py is required for OpenSearch retrieval. "
                "Install it with: pip install opensearch-py"
            ) from exc

        return OpenSearch(
            hosts=[{"host": config.host, "port": config.port}],
            http_auth=(config.username, config.password),
            use_ssl=config.use_ssl,
            verify_certs=config.verify_certs,
            ssl_show_warn=False,
            timeout=config.timeout_seconds,
        )
