"""启动父母官剧本生成器 HTTP 服务。"""

from argparse import ArgumentParser


def main() -> None:
    parser = ArgumentParser(description="父母官剧本生成器 HTTP 服务")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址")
    parser.add_argument("--port", type=int, default=8000, help="绑定端口")
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    import uvicorn
    print(f"\n  访问地址: http://localhost:{args.port}\n")
    uvicorn.run(
        "src.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
