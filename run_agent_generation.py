"""本地调试 Agent 生成工作流的脚本。"""

from argparse import ArgumentParser

from src.config import load_dotenv
from src.generation import IterativeAgentContextProvider, OpenSearchAgentContextProvider
from src.generation.opensearch_client import OpenSearchClientError
from src.services import NpcAgentService


DEFAULT_QUERY = (
    "生成一个跨域水污染治理严肃游戏中的A县环保局长NPC。"
    "该角色掌握本县企业排污台账，知道存在历史异常数据，"
    "需要在上级督察、县长绩效压力、群众举报和跨县协调之间做出策略性行为。"
)


def main() -> None:
    load_dotenv(override=True)

    parser = ArgumentParser(description="本地调试 NPC Agent 生成工作流")
    parser.add_argument(
        "query",
        nargs="?",
        default=DEFAULT_QUERY,
        help="用于检索资料并生成 NPC Agent 的初始 query",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="运行完整链路：多步检索 + Qwen 生成 NPC Agent",
    )
    parser.add_argument(
        "--iterative",
        action="store_true",
        help="只运行多步检索，会调用 Qwen 做检索规划，但不生成 NPC Agent",
    )

    args = parser.parse_args()

    try:
        if args.full:
            run_full_generation(args.query)
        elif args.iterative:
            run_iterative_search(args.query)
        else:
            run_search_only(args.query)
    except OpenSearchClientError as exc:
        print_opensearch_error(exc)


def run_search_only(query: str) -> None:
    """只测试单次 OpenSearch 检索，不调用 Qwen。"""

    print_queries("单次检索 query", [query])
    provider = OpenSearchAgentContextProvider.from_env()
    contexts = provider.find_contexts(query)
    print_contexts("单次 OpenSearch 检索结果", contexts)


def run_iterative_search(query: str) -> None:
    """测试多步检索，会调用 Qwen 判断是否继续检索。"""

    provider = IterativeAgentContextProvider.from_env()
    contexts = provider.find_contexts(query)
    print_queries("第一轮改写后的检索 query", provider.last_initial_queries)
    print_contexts("多步检索结果", contexts)


def run_full_generation(query: str) -> None:
    """运行完整 Agent 生成链路。"""

    service = NpcAgentService.from_env()
    result = service.generate_agent(query)
    context_provider = getattr(service, "_context_provider", None)
    initial_queries = getattr(context_provider, "last_initial_queries", [])

    if initial_queries:
        print_queries("第一轮改写后的检索 query", initial_queries)

    print("\n=== 生成的 NPC Agent ===")
    print(f"ID: {result.agent.id}")
    print(f"Name: {result.agent.name}")
    print(f"Role: {result.agent.role}")
    print(f"Background: {result.agent.background}")
    print_items("Goals", result.agent.goals)
    print_items("Personality Traits", result.agent.personality_traits)
    print("Knowledge Summary:")
    print(result.agent.knowledge_summary)
    print(f"Dialogue Style: {result.agent.dialogue_style}")
    print_items("Behavior Rules", result.agent.behavior_rules)
    print(f"Source Context IDs: {result.agent.source_context_ids}")
    print(f"Generation Notes: {result.generation_notes}")

    print_contexts("使用到的资料", result.contexts_used)


def print_contexts(title: str, contexts) -> None:
    print(f"\n=== {title} ===")
    print(f"Total: {len(contexts)}")

    for index, context in enumerate(contexts, start=1):
        print(f"\n[{index}] {context.title}")
        print(f"ID: {context.id}")
        print(f"Metadata: {context.metadata}")
        print("Content Preview:")
        print(_preview(context.content))


def print_queries(title: str, queries: list[str]) -> None:
    print(f"\n=== {title} ===")
    for index, query in enumerate(queries, start=1):
        print(f"{index}. {query}")


def print_items(title: str, items: list[str]) -> None:
    print(f"{title}:")
    for index, item in enumerate(items, start=1):
        print(f"  {index}. {item}")


def _preview(content: str, limit: int = 600) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def print_opensearch_error(exc: OpenSearchClientError) -> None:
    print("\n=== OpenSearch 连接或查询失败 ===")
    print(str(exc))
    print("\n建议先检查：")
    print("1. OPENSEARCH_HOST 和 OPENSEARCH_PORT 是否能从当前网络访问。")
    print("2. OPENSEARCH_USE_SSL 是否匹配服务端协议；HTTPS 服务通常需要设为 true。")
    print("3. OPENSEARCH_USERNAME / OPENSEARCH_PASSWORD 是否正确。")
    print("4. OPENSEARCH_INDEX 是否存在，当前脚本默认会查询 .env 里的索引。")
    print("5. 远端 OpenSearch 是否限制了 IP 白名单或安全组入口。")
    print("\n可以先只检查服务连通性，再跑完整生成链路。")


if __name__ == "__main__":
    main()
