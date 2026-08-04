export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export class ApiError extends Error {
  code: string;
  status: number;
  details: Record<string, unknown>;

  constructor(message: string, code = "CLIENT_HTTP_ERROR", status = 0, details: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export class GameApi {
  baseUrl: string;
  csrfToken = "";

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  key(prefix: string) {
    return `web-${prefix}-${crypto.randomUUID()}`;
  }

  async request<T = Record<string, unknown>>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json; charset=utf-8";
    if (this.csrfToken && !["GET", "HEAD"].includes(method)) headers["X-CSRF-Token"] = this.csrfToken;
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        credentials: "include",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("无法连接游戏后端，请确认服务地址和后端状态。", "CLIENT_CONNECTION_ERROR");
    }
    const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = (data as { error?: Record<string, unknown> }).error ?? {};
      throw new ApiError(
        String(error.message ?? `请求失败（HTTP ${response.status}）`),
        String(error.code ?? "CLIENT_HTTP_ERROR"),
        response.status,
        (error.details as Record<string, unknown>) ?? {},
      );
    }
    return data as T;
  }

  auth(mode: "login" | "register", username: string, password: string) {
    return this.request<{ account_id: string; csrf_token: string }>("POST", `/api/auth/${mode}`, { username, password });
  }
  logout() { return this.request("POST", "/api/auth/logout"); }
  me() { return this.request("GET", "/api/auth/me"); }
  health() { return this.request<{ terminal_protocol_version?: string }>("GET", "/health/live"); }
  ready() { return this.request<{ authentication_required?: boolean }>("GET", "/health/ready"); }
  origins() { return this.request<{ origins?: Record<string, unknown>[] }>("GET", "/api/game/origins"); }
  newSession(originId?: string) {
    return this.request<Record<string, unknown>>("POST", "/api/game/session", {
      client_request_id: this.key("new"),
      ...(originId ? { origin_id: originId } : {}),
    });
  }
  latest() { return this.request<Record<string, unknown>>("GET", "/api/game/session/latest-active"); }
  view(sessionId: string, after = 0) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}/view?after=${after}`); }
  session(sessionId: string) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}`); }
  panel(sessionId: string, name: string) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}/${name}`); }
  validation() { return this.request<Record<string, unknown>>("GET", "/api/game/package/validation"); }
  write(sessionId: string, suffix: string, method: "POST" | "PUT", body: Record<string, unknown>) {
    return this.request<Record<string, unknown>>(method, `/api/game/session/${encodeURIComponent(sessionId)}${suffix}`, body);
  }
  action(sessionId: string, body: Record<string, unknown>) {
    return this.write(sessionId, "/action", "POST", body);
  }
}
