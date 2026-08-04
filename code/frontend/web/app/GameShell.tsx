"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, GameApi } from "./lib/api";

type Dict = Record<string, any>;
type Line = { id: string; kind: string; speaker?: string; text: string };
type PanelName = "scene" | "actions" | "opportunities" | "governance" | "desk" | "knowledge" | "map" | "review" | "night-dialogues" | "manual-saves" | "validation" | "settings";

const NAV: { id: PanelName; label: string; glyph: string; hint: string }[] = [
  { id: "scene", label: "现场", glyph: "⌁", hint: "剧情与决策" },
  { id: "actions", label: "行动", glyph: "↳", hint: "四项基础行动" },
  { id: "opportunities", label: "会谈", glyph: "◎", hint: "NPC 互动" },
  { id: "governance", label: "治理", glyph: "▦", hint: "会议 · 文件 · 合同" },
  { id: "desk", label: "案头", glyph: "▤", hint: "卷宗与财政" },
  { id: "knowledge", label: "材料", glyph: "◇", hint: "事实 · 线索 · 证据" },
  { id: "map", label: "地图", glyph: "⌖", hint: "地点与事件" },
  { id: "review", label: "复盘", glyph: "↺", hint: "已发生内容" },
  { id: "night-dialogues", label: "夜话", glyph: "☾", hint: "观察模式" },
  { id: "manual-saves", label: "存档", glyph: "◫", hint: "快照与时间线" },
  { id: "validation", label: "校验", glyph: "✓", hint: "发布包状态" },
];

const PANEL_TITLES: Record<PanelName, string> = {
  scene: "当前现场", actions: "可执行行动", opportunities: "会谈机会", governance: "治理工作台",
  desk: "县长案头", knowledge: "已掌握材料", map: "清江县地图", review: "本局复盘",
  "night-dialogues": "NPC 夜间对话", "manual-saves": "存档与快照", validation: "内容包校验", settings: "连接设置",
};

const get = (obj: Dict | null, path: string, fallback: any = undefined) => path.split(".").reduce((v, k) => v?.[k], obj) ?? fallback;
const arr = (value: unknown): Dict[] => Array.isArray(value) ? value.filter(v => v && typeof v === "object") as Dict[] : [];
const textOf = (value: unknown) => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);
const displayValue = (value: unknown, fallback: string | number = "—"): string | number => {
  if (typeof value === "string" || typeof value === "number") return value;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const record = value as Dict;
    for (const key of ["label", "name", "text", "remaining", "current", "available", "value", "signed", "total"]) {
      if (typeof record[key] === "string" || typeof record[key] === "number") return record[key];
    }
  }
  return fallback;
};

function JsonTree({ value, depth = 0 }: { value: any; depth?: number }) {
  if (value == null) return <span className="json-null">—</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (Array.isArray(value)) return <div className="json-list">{value.length ? value.map((item, index) => <div className="json-row" key={index}><span className="json-key">{index + 1}</span><div><JsonTree value={item} depth={depth + 1} /></div></div>) : <span className="json-null">暂无</span>}</div>;
  return <div className="json-list">{Object.entries(value).map(([key, item]) => <div className="json-row" key={key}><span className="json-key">{key.replaceAll("_", " ")}</span><div><JsonTree value={item} depth={depth + 1} /></div></div>)}</div>;
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><div className="modal-head"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭">×</button></div>{children}</div></div>;
}

export default function GameShell() {
  const [baseUrl, setBaseUrl] = useState("/api/backend");
  const api = useMemo(() => new GameApi(baseUrl), [baseUrl]);
  const [connected, setConnected] = useState(false);
  const [account, setAccount] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [state, setState] = useState<Dict>({});
  const [commands, setCommands] = useState<Dict>({});
  const [cursor, setCursor] = useState(0);
  const [lines, setLines] = useState<Line[]>([
    { id: "boot-1", kind: "system", text: "清江治理终端 / WEB CLIENT v1.0" },
    { id: "boot-2", kind: "system", text: "正在等待连接权威游戏后端……" },
  ]);
  const [panel, setPanel] = useState<PanelName>("scene");
  const [panelData, setPanelData] = useState<Dict | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [authOpen, setAuthOpen] = useState(false);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [formOpen, setFormOpen] = useState<null | { title: string; kind: string; item?: Dict }>(null);
  const [commandInput, setCommandInput] = useState("");
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
      const savedBase = localStorage.getItem("qingjiang-api-base");
    const savedToken = sessionStorage.getItem("qingjiang-csrf");
      if (savedBase && savedBase !== "http://127.0.0.1:8100") setBaseUrl(savedBase);
    if (savedToken) api.csrfToken = savedToken;
  }, [api]);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return;
    terminal.scrollTop = terminal.scrollHeight;
  }, [lines]);

  const log = (text: string, kind = "system", speaker?: string) => setLines(old => [...old, { id: crypto.randomUUID(), kind, text, speaker }]);
  const fail = (error: unknown) => {
    const e = error as ApiError;
    const message = e?.message || "操作失败";
    setNotice(message);
    log(`[${e?.code || "ERROR"}] ${message}`, "error");
  };

  async function connect() {
    setBusy(true); setNotice("");
    try {
      const health = await api.health();
      if (health.terminal_protocol_version && health.terminal_protocol_version !== "text-gameplay-v3") throw new ApiError("后端协议版本不匹配，请重启最新后端。", "BACKEND_RESTART_REQUIRED");
      const ready = await api.ready();
      setConnected(true);
      log(`已连接 ${baseUrl} · text-gameplay-v3`, "success");
      if (ready.authentication_required) {
        try { const me = await api.me() as Dict; setAccount(String(me.account_id || "已登录")); log(`身份已恢复：${me.account_id}`, "success"); }
        catch { setAuthOpen(true); }
      } else setAccount("开发沙盒");
    } catch (e) { setConnected(false); fail(e); }
    finally { setBusy(false); }
  }

  useEffect(() => { connect(); /* initial connection */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const result = await api.auth(String(data.get("mode")) as "login" | "register", String(data.get("username")), String(data.get("password")));
      api.csrfToken = result.csrf_token; sessionStorage.setItem("qingjiang-csrf", result.csrf_token);
      setAccount(result.account_id); setAuthOpen(false); log(`账号已登录：${result.account_id}`, "success"); setSessionOpen(true);
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  async function refresh(after = cursor, targetSession = sessionId) {
    if (!targetSession) return;
    setBusy(true);
    try {
      const view = await api.view(targetSession, after) as Dict;
      const nextState = view.state || view.visible_state || view;
      setState(nextState); setCommands(view.commands || {});
      const feed = view.feed || {}; const incoming = arr(feed.items);
      if (incoming.length) setLines(old => [...old, ...incoming.map(item => ({ id: String(item.content_instance_id || item.cursor || crypto.randomUUID()), kind: String(item.kind || "narrative"), speaker: item.speaker ? String(item.speaker) : undefined, text: String(item.text || "") }))]);
      if (typeof feed.cursor === "number") setCursor(feed.cursor);
      setPanelData(nextState); setPanel("scene");
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  async function openSession(kind: "new" | "latest" | "load", value?: string) {
    setBusy(true);
    try {
      const result = kind === "new" ? await api.newSession(value) : kind === "latest" ? await api.latest() : await api.session(value || "");
      const id = String(result.session_id || get(result, "state.session_id") || value || "");
      if (!id) throw new ApiError("没有找到可继续的活动存档。", "SESSION_NOT_FOUND");
      setSessionId(id); setCursor(0); setLines([{ id: crypto.randomUUID(), kind: "system", text: `SESSION ${id} 已载入` }]); setSessionOpen(false);
      await refresh(0, id);
    } catch (e) { fail(e); } finally { setBusy(false); }
  }

  async function loadPanel(name: PanelName) {
    setPanel(name); setNotice("");
    if (name === "scene") { setPanelData(state); return; }
    if (name === "settings") { setPanelData(null); return; }
    setBusy(true);
    try {
      const data = name === "validation" ? await api.validation() : await api.panel(sessionId, name);
      setPanelData(data);
    } catch (e) { fail(e); setPanelData(null); } finally { setBusy(false); }
  }

  async function submitDecision(option: Dict) {
    const pending = state.pending_decision || {};
    await perform(() => api.action(sessionId, { input_mode: "decision", client_action_id: api.key("decision"), state_version: state.state_version, decision_id: pending.decision_id, option_id: option.option_id }));
  }

  async function perform(action: () => Promise<Dict>, success = "操作已提交") {
    setBusy(true); setNotice("");
    try { await action(); log(success, "success"); setFormOpen(null); await refresh(); }
    catch (e) { fail(e); }
    finally { setBusy(false); }
  }

  async function endDay(activeRest = false) {
    await perform(() => api.write(sessionId, "/end-day", "POST", { client_action_id: api.key("end"), state_version: state.state_version, active_rest: activeRest }), activeRest ? "已主动收工" : "夜间结算完成");
  }

  async function runCommand(event: FormEvent) {
    event.preventDefault(); const raw = commandInput.trim(); if (!raw) return;
    setCommandInput(""); log(`县长@清江 $ ${raw}`, "input");
    const [cmd, ...args] = raw.split(/\s+/); const rest = args.join(" ");
    if (get(state, "active_conversation.conversation_id") && !["leave", "help", "refresh"].includes(cmd)) {
      const c = state.active_conversation;
      return perform(() => api.action(sessionId, { input_mode: "free_text", client_action_id: api.key("talk"), state_version: state.state_version, conversation_id: c.conversation_id, opportunity_id: c.opportunity_id, target_npc_id: c.npc_id || c.target_npc_id, player_text: raw }), "对话已送达");
    }
    const panels: Record<string, PanelName> = { actions: "actions", talk: "opportunities", governance: "governance", desk: "desk", knowledge: "knowledge", map: "map", review: "review", night: "night-dialogues", saves: "manual-saves", validate: "validation", settings: "settings" };
    if (panels[cmd]) return loadPanel(panels[cmd]);
    if (["refresh", "scene", "status"].includes(cmd)) return refresh();
    if (cmd === "end") return endDay(false);
    if (cmd === "rest") return endDay(true);
    if (cmd === "leave" && get(state, "active_conversation.conversation_id")) return perform(() => api.action(sessionId, { input_mode: "conversation_end", client_action_id: api.key("conversation-end"), state_version: state.state_version, conversation_id: state.active_conversation.conversation_id }), "会谈已结束");
    if (cmd === "overtime" && ["1", "2", "3"].includes(args[0])) return perform(() => api.action(sessionId, { input_mode: "overtime", client_action_id: api.key("overtime"), state_version: state.state_version, parameters: { points: Number(args[0]) } }), `已申请加班 ${args[0]} 点`);
    if (cmd === "group" && rest) return perform(() => api.write(sessionId, "/group-conversation/turn", "POST", { state_version: state.state_version, player_text: rest }), "群组回应已提交");
    if (cmd === "help") return log("可用命令：refresh / actions / talk / governance / desk / knowledge / map / review / night / saves / validate / end / rest / overtime 1|2|3 / leave / group <回应>。会谈中直接输入文字即可。", "help");
    log("未识别命令。输入 help，或使用左侧功能栏。", "error");
  }

  const story = state.story || {}; const ledger = state.ledger || {}; const indicators = state.indicators || {};
  const pending = state.pending_decision || null; const options = arr(pending?.options);
  const signed = displayValue(get(ledger, "signed_households.signed", get(ledger, "signed_households", 0)), 0);
  const total = displayValue(get(ledger, "signed_households.total", 36), 36);
  const actionPoints = displayValue(
    get(state, "action_points.remaining", get(state, "action_points.current", get(ledger, "action_points.remaining", get(ledger, "action_points", "—")))),
  );
  const dailyCap = displayValue(
    get(state, "action_points.daily_cap", get(state, "action_points.maximum", get(ledger, "action_points.daily_cap", 8))),
    8,
  );
  const budget = displayValue(get(ledger, "budget.available", get(ledger, "budget.remaining", get(ledger, "budget", "—"))));
  const publicTrust = displayValue(get(indicators, "public_trust.label", get(indicators, "public_trust", "未判定")), "未判定");

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="seal">清</span><div><h1>浊流之下<span>·</span>清江搬迁记</h1><p>县域治理情境模拟系统</p></div></div>
      <div className="top-status">
        <span className={connected ? "online" : "offline"}><i />{connected ? "后端在线" : "未连接"}</span>
        <button onClick={() => setSessionOpen(true)}>{sessionId ? `存档 ${sessionId.slice(-8)}` : "进入游戏"}</button>
        <button className="avatar" onClick={() => setAuthOpen(true)} aria-label="账号">{account ? account.slice(0, 1).toUpperCase() : "?"}</button>
      </div>
    </header>

    <aside className="rail" aria-label="功能导航">
      {NAV.map(item => <button key={item.id} className={panel === item.id ? "active" : ""} onClick={() => loadPanel(item.id)} disabled={!sessionId && item.id !== "validation"}><span>{item.glyph}</span><b>{item.label}</b><small>{item.hint}</small></button>)}
      <button className={panel === "settings" ? "active rail-settings" : "rail-settings"} onClick={() => loadPanel("settings")}><span>⚙</span><b>设置</b><small>后端连接</small></button>
    </aside>

    <section className="workspace">
      <div className="metric-strip">
        <div><small>模拟日</small><strong>D{displayValue(story.day)}</strong><em>第 {displayValue(story.chapter)} 章</em></div>
        <div><small>行动点</small><strong>{actionPoints}</strong><em>/ {dailyCap}</em></div>
        <div><small>财政余额</small><strong>{budget}</strong><em>万元</em></div>
        <div><small>签约进度</small><strong>{signed}</strong><em>/ {total} 户</em></div>
        <div><small>群众信任</small><strong>{publicTrust}</strong><em>趋势</em></div>
      </div>

      <div className="main-grid">
        <section className="terminal-card">
          <div className="window-bar"><div className="lights"><i/><i/><i/></div><span>qingjiang-governance — live</span><button onClick={() => refresh()} disabled={!sessionId || busy}>刷新</button></div>
          <div className="terminal-output" ref={terminalRef} aria-live="polite">
            {lines.map(line => <div className={`terminal-line ${line.kind}`} key={line.id}>{line.speaker && <b>{line.speaker}</b>}<span>{line.text}</span></div>)}
            {!sessionId && <div className="welcome-block"><p>这里不是一份政策答卷。</p><h2>你有 90 天，处理一场正在失控的搬迁。</h2><p>每次会谈、批示、承诺和沉默，都会留下痕迹。</p><button onClick={() => setSessionOpen(true)}>建立治理档案 →</button></div>}
            {pending && <div className="decision-block"><div className="eyebrow">必须决策 · {pending.decision_id}</div><h3>{pending.title || pending.prompt || pending.situation || "当前事项需要你的决定"}</h3>{pending.description && <p>{pending.description}</p>}<div className="decision-options">{options.map((option, index) => <button key={option.option_id || index} onClick={() => submitDecision(option)} disabled={busy}><span>{String.fromCharCode(65 + index)}</span><div><b>{option.text || option.label}</b>{option.description && <small>{option.description}</small>}</div><i>选择</i></button>)}</div>{pending.input_type && pending.input_type !== "single" && <button className="secondary-action" onClick={() => setFormOpen({ title: "提交结构化决策", kind: "decision", item: pending })}>排序 / 分配题表单</button>}</div>}
          </div>
          <form className="command-bar" onSubmit={runCommand}><span>县长@清江</span><b>$</b><input value={commandInput} onChange={e => setCommandInput(e.target.value)} placeholder={get(state, "active_conversation.conversation_id") ? "输入你要对 NPC 说的话…" : "输入命令，或键入 help…"} disabled={!sessionId || busy} aria-label="终端命令"/><button disabled={!commandInput.trim() || busy}>执行 ↵</button></form>
        </section>

        <aside className="context-panel">
          <div className="panel-head"><div><small>WORKSPACE</small><h2>{PANEL_TITLES[panel]}</h2></div><div className="panel-tools">{busy && <span className="sync-state" aria-live="polite">同步中</span>}{panel !== "scene" && panel !== "settings" && <button onClick={() => loadPanel(panel)} disabled={busy}>↻</button>}</div></div>
          <div className="panel-body">
            {notice && <div className="notice">{notice}</div>}
            {panel === "settings" && <Settings baseUrl={baseUrl} onSave={url => { localStorage.setItem("qingjiang-api-base", url); setBaseUrl(url); setConnected(false); setNotice("地址已保存，请重新连接。"); }} onConnect={connect} />}
            {panel === "scene" && <SceneSummary state={state} commands={commands} />}
            {panel === "actions" && <ActionPanel data={panelData} onRun={item => setFormOpen({ title: `执行行动 · ${item.name || item.action_name || item.action_id}`, kind: "resource", item })} />}
            {panel === "opportunities" && <OpportunityPanel data={panelData} onStart={item => perform(() => api.action(sessionId, { input_mode: "conversation_start", client_action_id: api.key("conversation-start"), state_version: state.state_version, opportunity_id: item.opportunity_id, target_npc_id: item.npc_id || item.target_npc_id }), `已进入与 ${item.npc_name || "NPC"} 的会谈`)} />}
            {panel === "governance" && <GovernancePanel data={panelData} />}
            {!["scene", "actions", "opportunities", "governance", "settings"].includes(panel) && <JsonTree value={panelData} />}
          </div>
        </aside>
      </div>
    </section>

    <footer><span>权威状态由后端结算 · 客户端不展示隐藏数值</span><span>{sessionId ? `state v${state.state_version ?? "—"}` : "未载入存档"}</span></footer>

    {authOpen && <Modal title="账号入口" onClose={() => setAuthOpen(false)}><form className="stack-form" onSubmit={authenticate}><label>操作<select name="mode"><option value="login">登录已有账号</option><option value="register">注册新账号</option></select></label><label>用户名<input name="username" required autoFocus /></label><label>密码<input name="password" type="password" minLength={8} required /></label><button disabled={busy}>确认并进入</button></form></Modal>}
    {sessionOpen && <Modal title="进入清江县" onClose={() => setSessionOpen(false)}><div className="session-actions"><button onClick={() => openSession("new")}>开始新游戏<span>创建一条新的 90 天时间线</span></button><button onClick={() => openSession("latest")}>继续活动存档<span>恢复当前账号最近进度</span></button></div><form className="inline-form" onSubmit={e => { e.preventDefault(); openSession("load", String(new FormData(e.currentTarget).get("session"))); }}><input name="session" placeholder="输入 session_id" required/><button>载入指定存档</button></form></Modal>}
    {formOpen && <Modal title={formOpen.title} onClose={() => setFormOpen(null)}><ContextForm config={formOpen} state={state} api={api} sessionId={sessionId} onPerform={perform} /></Modal>}
  </main>;
}

function Settings({ baseUrl, onSave, onConnect }: { baseUrl: string; onSave: (url: string) => void; onConnect: () => void }) {
  const [value, setValue] = useState(baseUrl);
  return <div className="settings"><p>网页只通过玩家 API 与游戏通信。本地版默认使用同源转发连接 8100 端口，无需额外配置跨域。</p><label>后端服务地址<input value={value} onChange={e => setValue(e.target.value)} /></label><div className="button-row"><button onClick={() => onSave(value)}>保存地址</button><button className="primary" onClick={onConnect}>重新连接</button></div><small>保持 /api/backend 可连接本机后端；公开部署时可填写已启用 HTTPS 的后端地址。</small></div>;
}

function SceneSummary({ state, commands }: { state: Dict; commands: Dict }) {
  const active = state.active_conversation;
  return <div className="scene-summary"><div className="status-card"><small>当前阶段</small><strong>{displayValue(get(state, "story.beat_name", get(state, "story.beat_id", "等待进入游戏")), "等待进入游戏")}</strong><p>{state.status === "completed" ? "本局已经终结，可前往复盘查看结果。" : active ? `正在与 ${displayValue(active.npc_name, "NPC")} 会谈` : state.pending_decision ? "有一项必须处理的决策。" : "请从当前开放的行动、会谈或剧情决策中继续。"}</p></div><div className="command-grid">{Object.entries(commands).map(([key, value]) => <div key={key}><i className={value ? "yes" : "no"}/><span>{key.replace("can_", "")}</span></div>)}</div></div>;
}

function ActionPanel({ data, onRun }: { data: Dict | null; onRun: (item: Dict) => void }) {
  const items = arr(data?.actions || data?.items || data);
  return <div className="card-list">{items.length ? items.map((item, i) => <article key={item.action_id || i}><div><small>{item.action_id}</small><h3>{item.name || item.action_name || "治理行动"}</h3><p>{item.description || item.unavailable_reason || "按服务端当前条件执行"}</p></div><div className="item-foot"><span>{item.action_point_cost ?? item.cost ?? "—"} 行动点</span><button onClick={() => onRun(item)} disabled={item.available === false}>{item.available === false ? "暂不可用" : "配置并报价"}</button></div></article>) : <Empty text="当前没有可执行行动"/>}</div>;
}

function OpportunityPanel({ data, onStart }: { data: Dict | null; onStart: (item: Dict) => void }) {
  const items = arr(data?.opportunities || data?.items || data);
  return <div className="card-list people">{items.length ? items.map((item, i) => <article key={item.opportunity_id || i}><div className="person-mark">{String(item.npc_name || "人").slice(0, 1)}</div><div><small>{item.npc_title || item.action_name}</small><h3>{item.npc_name || "未命名角色"}</h3><p>{item.conversation_context || item.opening_narrative || item.conversation_goal}</p><div className="item-foot"><span>{item.action_point_cost ?? "—"} 行动点</span><button onClick={() => onStart(item)} disabled={item.available === false}>进入会谈</button></div></div></article>) : <Empty text="当前没有开放的会谈机会"/>}</div>;
}

function GovernancePanel({ data }: { data: Dict | null }) {
  const groups = ["active_actions", "meetings", "documents", "contract_batches", "contracts"];
  return <div><div className="governance-grid">{groups.map(key => <div key={key}><strong>{arr(data?.[key]).length}</strong><span>{key.replaceAll("_", " ")}</span></div>)}</div><JsonTree value={data} /></div>;
}

function ContextForm({ config, state, api, sessionId, onPerform }: { config: { kind: string; item?: Dict }; state: Dict; api: GameApi; sessionId: string; onPerform: (fn: () => Promise<Dict>, text?: string) => Promise<void> }) {
  const item = config.item || {};
  if (config.kind === "resource") return <form className="stack-form" onSubmit={async e => { e.preventDefault(); const fd = new FormData(e.currentTarget); let params: Dict = {}; try { params = JSON.parse(String(fd.get("parameters") || "{}")); } catch { return; } const targets = String(fd.get("targets") || "").split(",").map(x => x.trim()).filter(Boolean); await onPerform(async () => { const quote = await api.write(sessionId, "/actions/quote", "POST", { state_version: state.state_version, action_id: item.action_id, target_ids: targets, parameters: params }); if (!confirm(`报价已生成：${JSON.stringify(quote, null, 2)}\n\n确认执行？`)) return quote; return api.action(sessionId, { input_mode: "resource_action", client_action_id: api.key("resource"), state_version: state.state_version, action_id: item.action_id, target_ids: targets, parameters: params, quote_id: quote.quote_id }); }, "行动已执行"); }}><p>{item.description}</p><label>目标 ID（多个用逗号分隔）<input name="targets" placeholder={arr(item.target_choices).map(x => x.target_id || x.id).slice(0, 3).join(", ")} /></label><label>行动参数 JSON<textarea name="parameters" defaultValue="{}" /></label><button>获取报价并执行</button></form>;
  return <form className="stack-form" onSubmit={async e => { e.preventDefault(); const fd = new FormData(e.currentTarget); let payload: Dict = {}; try { payload = JSON.parse(String(fd.get("payload"))); } catch { return; } await onPerform(() => api.action(sessionId, { input_mode: "decision", client_action_id: api.key("decision"), state_version: state.state_version, decision_id: item.decision_id, ...payload }), "决策已提交"); }}><p>排序题请提交 ordered_option_ids；分配题请提交 parameters。</p><label>结构化答案 JSON<textarea name="payload" defaultValue={JSON.stringify({ ordered_option_ids: arr(item.options).map(x => x.option_id) }, null, 2)} /></label><button>提交决策</button></form>;
}

function Empty({ text }: { text: string }) { return <div className="empty"><i>·</i><p>{text}</p></div>; }
