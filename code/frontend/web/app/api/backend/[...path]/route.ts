import { NextRequest } from "next/server";

const backendBase = () => (process.env.GAME_BACKEND_URL || "http://127.0.0.1:8100").replace(/\/$/, "");
const sandboxCookie = "qingjiang-sandbox-account";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = new URL(`${backendBase()}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("accept-encoding");
  const providedAccount = (headers.get("X-Account-ID") || "").trim();
  const cookieAccount = request.cookies.get(sandboxCookie)?.value || "";
  const anonymousAccount = cookieAccount
    || (providedAccount.startsWith("sandbox_web_") ? providedAccount : "")
    || `sandbox_web_${crypto.randomUUID().replaceAll("-", "")}`;
  if (!providedAccount) headers.set("X-Account-ID", anonymousAccount);

  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: "manual",
    });
    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    if (!cookieAccount) {
      responseHeaders.append("set-cookie", `${sandboxCookie}=${encodeURIComponent(anonymousAccount)}; Path=/; Max-Age=31536000; SameSite=Lax`);
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({
      error: {
        code: "BACKEND_PROXY_UNAVAILABLE",
        message: "本机游戏后端暂时无法连接，请确认 8100 端口服务已经启动。",
        details: {},
      },
    }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
