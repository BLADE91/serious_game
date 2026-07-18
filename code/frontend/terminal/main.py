from __future__ import annotations

from argparse import ArgumentParser

from terminal_client import ApiClient, ApiError, TerminalApp


def main() -> int:
    parser = ArgumentParser(description="浊流之下·清江搬迁记文字测试客户端")
    parser.add_argument("--url", default="http://127.0.0.1:8100", help="后端基础地址")
    parser.add_argument(
        "--account-id",
        default="terminal-local",
        help="开发沙盒账号；生产环境不会使用该参数",
    )
    args = parser.parse_args()

    api = ApiClient(args.url, args.account_id)
    try:
        api.health()
    except ApiError as exc:
        print(f"无法连接游戏后端：{exc.message}")
        print("请先在 code/backend 下运行：python run_server.py")
        return 1
    return TerminalApp(api).run()


if __name__ == "__main__":
    raise SystemExit(main())
