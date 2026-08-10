export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

const SANDBOX_ACCOUNT_KEY = "qingjiang-sandbox-account";

function browserCookieValue(name: string) {
  if (typeof document === "undefined") return "";
  const prefix = `${name}=`;
  const value = document.cookie
    .split(";")
    .map(part => part.trim())
    .find(part => part.startsWith(prefix))
    ?.slice(prefix.length);
  return value ? decodeURIComponent(value) : "";
}

function browserSandboxAccountId() {
  if (typeof window === "undefined") return "";
  let accountId = "";
  try { accountId = localStorage.getItem(SANDBOX_ACCOUNT_KEY) || ""; } catch { /* storage may be disabled */ }
  accountId ||= browserCookieValue(SANDBOX_ACCOUNT_KEY);
  accountId ||= `sandbox_web_${crypto.randomUUID().replaceAll("-", "")}`;
  try { localStorage.setItem(SANDBOX_ACCOUNT_KEY, accountId); } catch { /* cookie remains as fallback */ }
  document.cookie = `${SANDBOX_ACCOUNT_KEY}=${encodeURIComponent(accountId)}; Path=/; Max-Age=31536000; SameSite=Lax`;
  return accountId;
}

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
  accountId = "";

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  key(prefix: string) {
    return `web-${prefix}-${crypto.randomUUID()}`;
  }

  setAccountId(accountId: string) {
    this.accountId = accountId;
  }

  enableSandboxAccount() {
    this.accountId = browserSandboxAccountId();
    return this.accountId;
  }

  setCsrfToken(token: string) {
    this.csrfToken = token;
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem("qingjiang-csrf", token);
  }

  restoreCsrf(cookieName: string) {
    const value = browserCookieValue(cookieName)
      || (typeof sessionStorage === "undefined" ? "" : sessionStorage.getItem("qingjiang-csrf") || "");
    this.csrfToken = value;
    if (value && typeof sessionStorage !== "undefined") sessionStorage.setItem("qingjiang-csrf", value);
    return value;
  }

  clearCsrf(cookieName: string) {
    this.csrfToken = "";
    if (typeof sessionStorage !== "undefined") sessionStorage.removeItem("qingjiang-csrf");
    if (typeof document !== "undefined") document.cookie = `${cookieName}=; Path=/; Max-Age=0; SameSite=Lax`;
  }

  async request<T = Record<string, unknown>>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json; charset=utf-8";
    if (this.accountId) headers["X-Account-ID"] = this.accountId;
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
      throw new ApiError("游戏服务暂时无法连接，请稍后重试。", "CLIENT_CONNECTION_ERROR");
    }
    const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = (data as { error?: Record<string, unknown> }).error ?? {};
      throw new ApiError(
        String(error.message ?? "这项操作暂时无法完成，请稍后重试。"),
        String(error.code ?? "CLIENT_HTTP_ERROR"),
        response.status,
        (error.details as Record<string, unknown>) ?? {},
      );
    }
    return data as T;
  }

  auth(mode: "login" | "register", username: string, password: string) {
    return this.request<{ account_id: string; username: string; csrf_token: string }>("POST", `/api/auth/${mode}`, { username, password });
  }
  logout() { return this.request("POST", "/api/auth/logout"); }
  me() { return this.request<{ account_id: string; username: string; roles: string[] }>("GET", "/api/auth/me"); }
  health() { return this.request<{ terminal_protocol_version?: string }>("GET", "/health/live"); }
  ready() { return this.request<{ authentication_required?: boolean; self_registration?: boolean; csrf_cookie_name?: string }>("GET", "/health/ready"); }
  origins() { return this.request<{ origins?: Record<string, unknown>[] }>("GET", "/api/game/origins"); }
  newSession(originId?: string) {
    return this.request<Record<string, unknown>>("POST", "/api/game/session", {
      client_request_id: this.key("new"),
      ...(originId ? { origin_id: originId } : {}),
    });
  }
  latest() { return this.request<Record<string, unknown>>("GET", "/api/game/session/latest-active"); }
  async sessions() {
    try {
      return await this.request<{ sessions: Record<string, unknown>[] }>("GET", "/api/game/sessions");
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
      const latest = await this.latest();
      const story = latest.story as Record<string, unknown> | undefined;
      return {
        sessions: latest.session_id ? [{
          session_id: latest.session_id,
          story_day: story?.day || 1,
          status: latest.status || "active",
          updated_at: latest.updated_at || "",
        }] : [],
      };
    }
  }
  view(sessionId: string, after = 0) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}/view?after=${after}`); }
  session(sessionId: string) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}`); }
  panel(sessionId: string, name: string) { return this.request<Record<string, unknown>>("GET", `/api/game/session/${encodeURIComponent(sessionId)}/${name}`); }
  manualSave(sessionId: string, body: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", `/api/game/session/${encodeURIComponent(sessionId)}/manual-saves`, body);
  }
  loadSnapshot(sessionId: string, body: Record<string, unknown>) {
    return this.request<Record<string, unknown>>("POST", `/api/game/session/${encodeURIComponent(sessionId)}/load-snapshot`, body);
  }
  validation() { return this.request<Record<string, unknown>>("GET", "/api/game/package/validation"); }
  write(sessionId: string, suffix: string, method: "POST" | "PUT", body: Record<string, unknown>) {
    return this.request<Record<string, unknown>>(method, `/api/game/session/${encodeURIComponent(sessionId)}${suffix}`, body);
  }
  action(sessionId: string, body: Record<string, unknown>) {
    return this.write(sessionId, "/action", "POST", body);
  }
}
