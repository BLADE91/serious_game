"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */

import Image from "next/image";
import { FormEvent, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApiError, GameApi } from "./lib/api";
import { resolveCharacter, type Character } from "./lib/characters";
import { initialNarrativeState, narrativeItemFromFeed, narrativeReducer, type NarrativeItem } from "./lib/narrative-model";
import { resolveSceneForView } from "./lib/scene-resolver";
import { actionPointLabel, toPlayerText } from "./lib/player-ui";

type Dict = Record<string, any>;
type Line = NarrativeItem;
type PanelName = "scene" | "actions" | "opportunities" | "governance" | "desk" | "knowledge" | "map" | "review" | "night-dialogues" | "manual-saves";

const NAV: { id: PanelName; label: string; hint: string }[] = [
  { id: "scene", label: "今日", hint: "目标与现场" },
  { id: "actions", label: "行动", hint: "安排工作" },
  { id: "opportunities", label: "会谈", hint: "走近人物" },
  { id: "governance", label: "治理", hint: "会议与资源" },
  { id: "desk", label: "卷宗", hint: "任务与政策" },
  { id: "knowledge", label: "线索", hint: "事实与证据" },
  { id: "map", label: "地图", hint: "地点与动向" },
  { id: "review", label: "复盘", hint: "选择与后果" },
  { id: "night-dialogues", label: "夜话", hint: "夜间记录" },
  { id: "manual-saves", label: "存档", hint: "保留进度" },
];

const PANEL_TITLES: Record<PanelName, string> = {
  scene: "今日案头", actions: "可安排的行动", opportunities: "可以会谈的人", governance: "治理进展",
  desk: "县长卷宗", knowledge: "已掌握的线索", map: "云溪县地图", review: "本局纪要",
  "night-dialogues": "夜间纪要", "manual-saves": "存档管理",
};

const MEETING_TOPICS = [
  "明确搬迁补偿口径与签约推进安排",
  "协调安置住房、医疗与就学资源",
  "封存并审计村级搬迁账目",
  "启动环境污染调查与第三方检测",
  "处理重点户矛盾与群体风险",
  "明确部门分工、责任人与完成期限",
];

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  implementation_notice: "实施工作通知", medical_guarantee: "医疗保障文件", grave_or_shrine_approval: "迁葬专项批复",
  compensation_adjustment: "补偿调整方案", hearing_notice: "公开听证通知", investigation_notice: "专项调查通知",
};

const PARAMETER_LABELS: Record<string, string> = {
  topic: "议题", public_scope: "公开范围", staffing_principle: "用人原则", scope: "核查范围",
  archive_type: "档案类型", request_type: "请示事项", message_scope: "传达内容", consent_status: "知情同意情况",
  relief_status: "救济程序状态", legal_basis: "法律依据", plan_status: "现场处置预案", inspection_theme: "走访重点",
  agenda: "协商议程", case_type: "案件类型", procedure: "办理方式", public_matter: "公开事项",
};

const GOVERNANCE_ACTION_LABELS: Record<string, string> = {
  household_visit: "入户走访",
  cadre_interview: "干部访谈",
  leadership_meeting: "班子会议",
  inspect_archives: "查阅档案",
};

const get = (obj: Dict | null, path: string, fallback: any = undefined) => path.split(".").reduce((value, key) => value?.[key], obj) ?? fallback;
const arr = (value: unknown): Dict[] => Array.isArray(value) ? value.filter(item => item && typeof item === "object") as Dict[] : [];
const values = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const displayValue = (value: unknown, fallback: string | number = "待定"): string | number => {
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
const playerText = toPlayerText;
const chineseIndex = (index: number) => {
  const digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"];
  const value = index + 1;
  if (value < 10) return digits[value];
  if (value === 10) return "十";
  if (value < 20) return `十${digits[value - 10]}`;
  if (value < 100) return `${digits[Math.floor(value / 10)]}十${value % 10 ? digits[value % 10] : ""}`;
  return String(value);
};
const friendlyStatus = (value: unknown) => {
  const labels: Record<string, string> = {
    active: "进行中", ended: "已结束", completed: "已完成", published: "已发布", draft: "草拟中",
    pending: "待处理", checkpoint: "自动保存", manual: "手动存档", public: "公开", internal: "内部掌握",
    confidential: "保密", secret: "机密", E1: "初步材料", E2: "可核材料", E3: "正式证据",
  };
  return labels[String(value)] || "已记录";
};
const playerErrorMessage = (error: unknown) => {
  const value = error as ApiError;
  if (value?.status === 401) return "登录状态已失效，请重新登录后继续。";
  if (value?.status === 403) return "当前账号没有执行这项操作的权限。";
  if (value?.status === 404) return "没有找到这份进度，它可能已经被移除。";
  if (value?.status === 409) return "进度刚刚发生了变化，已为你重新读取最新状态。";
  if (value?.status === 422) return "所填内容还不完整，请检查后再提交。";
  if (!value?.status || value.status >= 500) return "游戏服务暂时没有响应，请稍后重试。";
  const message = String(value?.message || "");
  if (message && !/HTTP|API|backend|frontend|session|state|version|token|JSON|ID|端口|后端|客户端/i.test(message)) return message;
  return "这项操作暂时无法完成，请换一种安排或稍后重试。";
};
const isPlayerFacingLine = (line: Line) => {
  if (["system", "success", "error", "input", "help"].includes(line.kind)) return false;
  const text = line.text.trim();
  if (!text) return false;
  return !/^(SESSION\s|清江治理终端|正在等待连接|已连接\s+\/api|操作已提交$)/i.test(text)
    && !/游戏开局，玩家|玩家在到任第一天/.test(text);
};
function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><div className="modal-head"><div><small>清江县政府</small><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭">×</button></div>{children}</div></div>;
}

function CharacterPortrait({ character, fallbackName, priority = false }: { character: Character | null; fallbackName: string; priority?: boolean }) {
  const [failedPath, setFailedPath] = useState("");
  if (!character || failedPath === character.portraitPath) {
    return <div className="portrait-fallback" aria-label={`${fallbackName}暂无立绘`}>{fallbackName.slice(0, 1) || "人"}</div>;
  }
  return <Image className="character-portrait" src={character.portraitPath} alt={`${character.name}立绘`} fill sizes="(max-width: 640px) 38vw, 220px" priority={priority} unoptimized style={{ objectFit: "contain", objectPosition: "center bottom" }} onError={() => setFailedPath(character.portraitPath)} />;
}

export default function GameShell() {
  const [baseUrl] = useState("/api/backend");
  const api = useMemo(() => new GameApi(baseUrl), [baseUrl]);
  const [connected, setConnected] = useState(false);
  const [account, setAccount] = useState("");
  const [authRequired, setAuthRequired] = useState(false);
  const [selfRegistration, setSelfRegistration] = useState(false);
  const [csrfCookieName, setCsrfCookieName] = useState("serious_game_session_csrf");
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authError, setAuthError] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [state, setState] = useState<Dict>({});
  const [commands, setCommands] = useState<Dict>({});
  const [narrative, dispatchNarrative] = useReducer(narrativeReducer, initialNarrativeState);
  const [showHistory, setShowHistory] = useState(false);
  const [panel, setPanel] = useState<PanelName>("scene");
  const [panelData, setPanelData] = useState<Dict | null>(null);
  const [governance, setGovernance] = useState<Dict | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [authOpen, setAuthOpen] = useState(false);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [savedSessionsOpen, setSavedSessionsOpen] = useState(false);
  const [savedSessions, setSavedSessions] = useState<Dict[]>([]);
  const [savedSessionsError, setSavedSessionsError] = useState("");
  const [formOpen, setFormOpen] = useState<null | { title: string; kind: string; item?: Dict }>(null);
  const [conversationInput, setConversationInput] = useState("");
  const contextRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const savedToken = sessionStorage.getItem("qingjiang-csrf");
    if (savedToken) api.setCsrfToken(savedToken);
  }, [api]);

  const fail = (error: unknown) => setNotice(playerErrorMessage(error));

  async function connect() {
    setBusy(true); setNotice("");
    try {
      await api.health();
      const ready = await api.ready();
      const requiresAuth = Boolean(ready.authentication_required);
      const cookieName = ready.csrf_cookie_name || "serious_game_session_csrf";
      setAuthRequired(requiresAuth);
      setSelfRegistration(Boolean(ready.self_registration));
      setCsrfCookieName(cookieName);
      setConnected(true);
      if (requiresAuth) {
        const restoredCsrf = api.restoreCsrf(cookieName);
        try {
          const me = await api.me();
          if (!restoredCsrf) throw new Error("需要重新登录");
          api.setAccountId(me.account_id);
          setAccount(me.username || "已登录");
          setAuthOpen(false);
        } catch {
          setAccount("");
          setAuthMode("login");
          setAuthOpen(true);
        }
      } else {
        api.enableSandboxAccount();
        setAccount("本地试玩");
      }
    } catch (error) { setConnected(false); fail(error); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void connect(), 0);
    return () => window.clearTimeout(timer);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setAuthError("");
    const data = new FormData(event.currentTarget);
    try {
      const result = await api.auth(authMode, String(data.get("username")), String(data.get("password")));
      api.setCsrfToken(result.csrf_token);
      api.setAccountId(result.account_id);
      setAccount(result.username || "已登录"); setAuthOpen(false); setSessionOpen(true);
    } catch (error) { setAuthError(playerErrorMessage(error)); }
    finally { setBusy(false); }
  }

  async function logoutAccount() {
    setBusy(true); setAuthError("");
    try {
      await api.logout();
      api.clearCsrf(csrfCookieName);
      setAccount(""); setSessionId(""); setState({}); setCommands({}); dispatchNarrative({ type: "CLEAR" }); setPanel("scene"); setPanelData(null); setGovernance(null);
      setAuthMode("login"); setAuthOpen(false);
    } catch (error) { setAuthError(playerErrorMessage(error)); }
    finally { setBusy(false); }
  }

  async function refresh(after = narrative.feedCursor, targetSession = sessionId, clearNotice = true, rebuild = false) {
    if (!targetSession) return;
    setBusy(true);
    if (clearNotice) setNotice("");
    try {
      const [view, governanceOverview] = await Promise.all([
        api.view(targetSession, after) as Promise<Dict>,
        api.panel(targetSession, "governance").catch(() => null),
      ]);
      const nextState = view.state || view.visible_state || view;
      setState(nextState); setCommands(view.commands || {});
      setGovernance(governanceOverview);
      const feed = view.feed || {};
      const incoming = arr(feed.items)
        .map((item, index) => {
          const line = narrativeItemFromFeed(item, `${targetSession}:${index}:${String(item.text || "")}`);
          return { ...line, text: playerText(line.text) };
        })
        .filter(isPlayerFacingLine);
      dispatchNarrative({
        type: rebuild ? "SESSION_REBUILD" : "FEED_MERGE",
        sessionId: targetSession,
        items: incoming,
        cursor: typeof feed.cursor === "number" ? feed.cursor : undefined,
      });
      setPanelData(nextState); setPanel("scene");
    } catch (error) { fail(error); }
    finally { setBusy(false); }
  }

  async function openSession(kind: "new" | "load", value?: string) {
    setBusy(true); setNotice("");
    try {
      const result = kind === "new" ? await api.newSession(value) : await api.session(value || "");
      const id = String(result.session_id || get(result, "state.session_id") || value || "");
      if (!id) throw new ApiError("没有找到可继续的游戏进度。", "SESSION_NOT_FOUND", 404);
      setSessionId(id); dispatchNarrative({ type: "SESSION_OPEN", sessionId: id }); setShowHistory(false); setSessionOpen(false); setSavedSessionsOpen(false);
      await refresh(0, id, true, true);
    } catch (error) { fail(error); }
    finally { setBusy(false); }
  }

  async function showSavedSessions() {
    setSavedSessionsOpen(true); setSavedSessionsError(""); setBusy(true);
    try { const result = await api.sessions(); setSavedSessions(arr(result.sessions)); }
    catch (error) { setSavedSessionsError(playerErrorMessage(error)); }
    finally { setBusy(false); }
  }

  async function loadPanel(name: PanelName) {
    setPanel(name); setNotice("");
    const revealOnMobile = () => {
      if (window.matchMedia("(max-width: 780px)").matches) window.requestAnimationFrame(() => contextRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };
    if (name === "scene") { setPanelData(state); revealOnMobile(); return; }
    setBusy(true);
    try { setPanelData(await api.panel(sessionId, name)); }
    catch (error) { fail(error); setPanelData(null); }
    finally { setBusy(false); revealOnMobile(); }
  }

  async function perform(action: () => Promise<Dict>, success = "安排已落实", rebuildNarrative = false) {
    setBusy(true); setNotice("");
    try { await action(); setFormOpen(null); setConversationInput(""); await refresh(rebuildNarrative ? 0 : narrative.feedCursor, sessionId, false, rebuildNarrative); setNotice(success); }
    catch (error) {
      if ((error as ApiError)?.status === 409) {
        await refresh(0, sessionId, false, true);
        setNotice("现场情况刚刚更新，请根据最新信息重新选择。");
      } else fail(error);
    }
    finally { setBusy(false); }
  }

  async function submitDecision(option: Dict) {
    const pending = state.pending_decision || {};
    await perform(() => api.action(sessionId, {
      input_mode: "decision", client_action_id: api.key("decision"), state_version: state.state_version,
      decision_id: pending.decision_id, option_id: option.option_id,
    }), "决定已经记录，后续影响会在进程中逐步显现");
  }

  async function endDay(activeRest = false) {
    const prompt = activeRest ? "确认提前收工，让团队恢复状态？" : "确认结束今天的工作并进入夜间结算？";
    if (!window.confirm(prompt)) return;
    await perform(() => api.write(sessionId, "/end-day", "POST", {
      client_action_id: api.key("end-day"), state_version: state.state_version, active_rest: activeRest,
    }), activeRest ? "今天的工作已提前收束" : "夜间结算完成，新一天已经开始");
  }

  async function submitConversation(event: FormEvent) {
    event.preventDefault();
    const text = conversationInput.trim();
    if (!text) return;
    const group = state.active_group_conversation;
    const conversation = state.active_conversation;
    if (group) {
      await perform(() => api.write(sessionId, "/group-conversation/turn", "POST", {
        state_version: state.state_version, player_text: text,
      }), "你的回应已经传达给在场各方");
    } else if (conversation) {
      await perform(() => api.action(sessionId, {
        input_mode: "free_text", client_action_id: api.key("talk"), state_version: state.state_version,
        conversation_id: conversation.conversation_id, opportunity_id: conversation.opportunity_id,
        target_npc_id: conversation.npc_id || conversation.target_npc_id, player_text: text,
      }), "你的话已经传达");
    }
  }

  async function leaveConversation() {
    const conversation = state.active_conversation;
    if (!conversation) return;
    await perform(() => api.action(sessionId, {
      input_mode: "conversation_end", client_action_id: api.key("conversation-end"), state_version: state.state_version,
      conversation_id: conversation.conversation_id,
    }), "会谈已经结束");
  }

  const activeGovernanceAction = arr(governance?.governance_actions).find(item => item.status === "active") || null;
  const activeMeeting = activeGovernanceAction?.action_kind === "leadership_meeting"
    ? arr(governance?.meetings).find(item => item.action_instance_id === activeGovernanceAction.action_instance_id) || null
    : null;
  const activeConversationCharacter = state.active_conversation
    ? resolveCharacter(state.active_conversation.npc_id, state.active_conversation.target_npc_id, state.active_conversation.npc_name)
    : null;
  const activeConversationName = state.active_conversation
    ? activeConversationCharacter?.name
      || playerText(state.active_conversation.npc_name)
      || playerText(arr(get(governance, "target_catalogs.meeting_participants")).find(item => item.target_id === state.active_conversation.npc_id)?.label, "对方")
    : "";

  async function submitGovernanceTurn(event: FormEvent) {
    event.preventDefault();
    const text = conversationInput.trim();
    if (!text || !activeGovernanceAction) return;
    if (activeMeeting) {
      await perform(() => api.write(sessionId, `/governance/meetings/${encodeURIComponent(String(activeMeeting.meeting_id))}/turn`, "POST", {
        state_version: state.state_version, player_text: text, addressed_npc_id: null,
      }), "你的意见已经传达给全体参会人员");
      return;
    }
    await perform(() => api.write(sessionId, `/governance/actions/${encodeURIComponent(String(activeGovernanceAction.action_instance_id))}/turn`, "POST", {
      state_version: state.state_version, player_text: text,
    }), "你的询问已经得到回应");
  }

  async function finishGovernanceAction() {
    if (!activeGovernanceAction) return;
    if (activeMeeting) {
      if (!arr(activeMeeting.transcript).length) {
        setNotice("会议需要至少进行一轮公开讨论，才能形成决议。");
        return;
      }
      const participants = arr(get(governance, "target_catalogs.meeting_participants"));
      const responsible = activeMeeting.participant_ids?.[0] || participants[0]?.target_id;
      await perform(() => api.write(sessionId, `/governance/meetings/${encodeURIComponent(String(activeMeeting.meeting_id))}/resolve`, "POST", {
        state_version: state.state_version,
        adopt: true,
        resolution: {
          decision: activeMeeting.topic || "按会议形成的意见推进相关工作",
          target_scope: "本次会议议题涉及的相关部门和群众",
          resources: {},
          responsible_ids: responsible ? [responsible] : [],
          deadline_day: Math.min(90, Number(get(state, "story.day", 1)) + 7),
          public_scope: ["参会部门", "柳林村相关群众"],
          document_title: `${activeMeeting.topic || "专项工作"}会议决议`,
        },
      }), "会议已经形成决议并收入卷宗");
      return;
    }
    await perform(() => api.write(sessionId, `/governance/actions/${encodeURIComponent(String(activeGovernanceAction.action_instance_id))}/finish`, "POST", {
      state_version: state.state_version,
    }), "本次行动已经收束，取得的材料已收入案头");
  }

  async function cancelGovernanceAction() {
    if (!activeGovernanceAction || !window.confirm("确认中止当前行动？已消耗的精力不会返还。")) return;
    await perform(() => api.write(sessionId, `/governance/actions/${encodeURIComponent(String(activeGovernanceAction.action_instance_id))}/cancel`, "POST", {
      state_version: state.state_version,
    }), "当前行动已经中止");
  }

  const story = state.story || {}; const ledger = state.ledger || {}; const indicators = state.indicators || {};
  const pending = state.pending_decision || null; const options = arr(pending?.options);
  const signed = displayValue(get(ledger, "signed_households.signed", get(ledger, "signed_households", 0)), 0);
  const total = displayValue(get(ledger, "signed_households.total", 36), 36);
  const actionPoints = displayValue(get(state, "action_points.remaining", get(ledger, "action_points.remaining", "待定")));
  const dailyCap = displayValue(get(state, "action_points.daily_cap", get(ledger, "action_points.daily_cap", 8)), 8);
  const budget = displayValue(get(ledger, "budget.available", get(ledger, "budget.remaining", "待定")));
  const publicTrust = displayValue(get(indicators, "public_trust.label", get(indicators, "public_trust", "未判定")), "未判定");
  const playerLines = narrative.items;
  const currentLine = playerLines[narrative.currentIndex] || null;
  const currentScene = resolveSceneForView({
    line: currentLine || undefined,
    lines: playerLines,
    currentIndex: narrative.currentIndex,
    itemCount: playerLines.length,
    currentStoryDay: story.day,
    decisionId: pending?.decision_id,
    mainEndingId: get(state, "ending.main_ending_id") || get(state, "ending_result.main_ending_id") || state.main_ending_id,
    beatId: get(state, "story.beat_id") || get(state, "story.story_beat_id") || state.story_beat_id,
  });
  const lineCharacter = currentLine?.speaker ? resolveCharacter(currentLine.speaker) : null;
  const lineIsPlayer = lineCharacter?.id === "player_li_zhiyuan" || /^(你|李致远|李县长)$/.test(currentLine?.speaker || "");
  const stageCharacter = currentLine?.speaker && !lineIsPlayer ? lineCharacter : !currentLine && state.active_conversation ? activeConversationCharacter : null;
  const stageSpeaker = currentLine?.speaker && !lineIsPlayer ? playerText(currentLine.speaker) : !currentLine && state.active_conversation ? activeConversationName : "";
  const activeConversation = state.active_conversation || state.active_group_conversation;

  return <main className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="seal">清</span><div><h1>浊流之下<span>·</span>清江搬迁记</h1><p>县域治理情境模拟</p></div></div>
      <div className="top-status">
        <span className={connected ? "online" : "offline"}><i />{connected ? "游戏已就绪" : "正在连接"}</span>
        <button onClick={() => authRequired && !account ? setAuthOpen(true) : setSessionOpen(true)}>{sessionId ? `第 ${story.day || 1} 日 · 游戏进度` : authRequired && !account ? "登录" : "进入游戏"}</button>
        <button className="avatar" onClick={() => setAuthOpen(true)} aria-label="账号与身份">{account ? account.slice(0, 1).toUpperCase() : "?"}</button>
      </div>
    </header>

    <aside className="rail" aria-label="游戏导航">
      {NAV.map((item, index) => <button key={item.id} className={panel === item.id ? "active" : ""} onClick={() => loadPanel(item.id)} disabled={!sessionId}><small>卷宗 {chineseIndex(index)}</small><b>{item.label}</b><em>{item.hint}</em></button>)}
    </aside>

    <section className="workspace">
      <div className="metric-strip" aria-label="当前治理状态">
        <div><small>当前日期</small><strong>第 {displayValue(story.day)} 日</strong><em>余 {Math.max(0, 90 - Number(story.day || 0))} 日</em></div>
        <div><small>今日精力</small><strong>{actionPoints}</strong><em>/ {dailyCap} 点</em></div>
        <div><small>财政余额</small><strong>{budget}</strong><em>万元</em></div>
        <div><small>签约进度</small><strong>{signed}</strong><em>/ {total} 户</em></div>
        <div><small>群众信任</small><strong>{publicTrust}</strong><em>当前态势</em></div>
      </div>

      <div className="main-grid">
        <section className="story-card">
          <Image key={currentScene.asset} className="scene-backdrop" src={currentScene.asset} alt={currentScene.title} fill priority sizes="(max-width: 980px) 100vw, 70vw" unoptimized />
          <div className="story-head"><div><small>县长手记 · 第 {displayValue(story.day, "待定")} 日</small><h2>{sessionId ? currentScene.title : "一纸调令，九十天限期"}</h2></div>{sessionId && <button className="refresh-button" onClick={() => refresh()} disabled={busy}>更新现场</button>}</div>
          <div className="story-scroll" aria-live="polite" data-scene-match={currentScene.matchedBy}>
            {notice && <div className="notice" role="status"><b>案头提醒</b><span>{notice}</span></div>}
            {!sessionId && <div className="welcome-block"><span className="eyebrow">云溪县 · 柳林村搬迁专班</span><h2>你有九十天，处理一场正在失控的搬迁。</h2><p>三十六户人家、八千万元预算，还有一条没人愿意说透的旧账。你的每次会谈、批示、承诺和沉默，都会留下痕迹。</p><button onClick={() => authRequired && !account ? setAuthOpen(true) : setSessionOpen(true)}>{authRequired && !account ? "登录后赴任" : "接下调令，前往云溪"}</button></div>}
            {sessionId && <section className="gal-stage" data-testid={state.active_conversation ? "active-conversation-character" : undefined}>
              {stageSpeaker && <div className="gal-portrait" aria-label={`${stageSpeaker}立绘`}><CharacterPortrait character={stageCharacter} fallbackName={stageSpeaker} priority /></div>}
              <div className={stageSpeaker ? "gal-dialogue has-speaker" : "gal-dialogue narration"}>
                <header><span>{stageSpeaker || (currentLine ? "县长手记" : "现场暂歇")}</span><small>{playerLines.length ? `${Math.max(1, narrative.currentIndex + 1)} / ${playerLines.length}` : "等待新消息"}</small></header>
                <p>{currentLine ? playerText(currentLine.text) : pending ? "请阅读当前事项并作出决定。" : "案头暂时平静。可以从行动、会谈或卷宗继续推进。"}</p>
                <nav className="narrative-controls" aria-label="剧情阅读控制">
                  <button onClick={() => dispatchNarrative({ type: "PREVIOUS" })} disabled={narrative.currentIndex <= 0}>上一段</button>
                  <button onClick={() => dispatchNarrative({ type: "NEXT" })} disabled={narrative.currentIndex >= playerLines.length - 1}>下一段</button>
                  <button className="latest" onClick={() => dispatchNarrative({ type: "GO_LATEST" })} disabled={narrative.currentIndex >= playerLines.length - 1}>回到最新{narrative.unreadCount ? ` (${narrative.unreadCount})` : ""}</button>
                  <button className="history-toggle" onClick={() => setShowHistory(value => !value)}>{showHistory ? "关闭回看" : "剧情回看"}</button>
                </nav>
              </div>
            </section>}
            {sessionId && showHistory && <section className="history-drawer" aria-label="剧情回看"><header><h3>剧情回看</h3><button onClick={() => setShowHistory(false)}>关闭</button></header><div>{playerLines.map((line, index) => <button className={index === narrative.currentIndex ? "current" : ""} key={line.id} onClick={() => dispatchNarrative({ type: "GO_TO", index })}><small>{line.speaker ? playerText(line.speaker) : "旁白"}</small><span>{playerText(line.text)}</span></button>)}</div></section>}
            {activeGovernanceAction && <GovernanceActionScene action={activeGovernanceAction} meeting={activeMeeting} overview={governance} />}
            {pending && <div className="decision-block"><div className="eyebrow">当前必须作出决定</div><h3>{playerText(pending.title || pending.prompt || pending.situation, "当前事项需要你的决定")}</h3>{pending.description && <p>{playerText(pending.description)}</p>}{["sorting", "allocation"].includes(pending.input_kind) ? <StructuredDecision key={pending.decision_id} pending={pending} busy={busy} onSubmit={payload => perform(() => api.action(sessionId, {
              input_mode: "decision", client_action_id: api.key("decision"), state_version: state.state_version, decision_id: pending.decision_id, ...payload,
            }), "决定已经记录，后续影响会在进程中逐步显现")} /> : <div className="decision-options">{options.map((option, index) => <button key={option.option_id || index} onClick={() => submitDecision(option)} disabled={busy || option.available === false}><span>{chineseIndex(index)}</span><div><b>{playerText(option.text || option.label, `方案${chineseIndex(index)}`)}</b>{option.description && <small>{playerText(option.description)}</small>}</div><i>{option.available === false ? playerText(option.unavailable_reason, "条件不足") : "采纳"}</i></button>)}</div>}</div>}
          </div>
          <PlayerActionBar state={state} commands={commands} busy={busy} pending={pending} governanceAction={activeGovernanceAction} meeting={activeMeeting} conversationName={activeConversationName} value={conversationInput} onChange={setConversationInput} onSubmit={activeGovernanceAction ? submitGovernanceTurn : submitConversation} onLeave={leaveConversation} onFinishGovernance={finishGovernanceAction} onCancelGovernance={cancelGovernanceAction} onNavigate={loadPanel} onEndDay={endDay} />
        </section>

        <aside className="context-panel" ref={contextRef}>
          <div className="panel-head"><div><small>清江县政府 · 案头</small><h2>{PANEL_TITLES[panel]}</h2></div>{busy && <span className="sync-state" aria-live="polite">正在整理</span>}</div>
          <div className="panel-body">
            {sessionId && <PlayerIdentityCard />}
            {panel === "scene" && <SceneSummary state={state} commands={commands} governanceAction={activeGovernanceAction} onNavigate={loadPanel} onEndDay={endDay} />}
            {panel === "actions" && <ActionPanel data={panelData} onRun={item => setFormOpen({ title: item.name || item.action_name || "安排治理行动", kind: "resource", item })} />}
            {panel === "opportunities" && <OpportunityPanel data={panelData} activeConversation={state.active_conversation || null} onContinue={() => void loadPanel("scene")} onStart={item => perform(() => api.action(sessionId, {
              input_mode: "conversation_start", client_action_id: api.key("conversation-start"), state_version: state.state_version,
              opportunity_id: item.opportunity_id, target_npc_id: item.npc_id || item.target_npc_id,
            }), `已进入与 ${playerText(item.npc_name, "对方")} 的会谈`)} />}
            {panel === "governance" && <GovernancePanel data={panelData} />}
            {panel === "desk" && <DeskPanel data={panelData} />}
            {panel === "knowledge" && <KnowledgePanel data={panelData} />}
            {panel === "map" && <MapPanel data={panelData} blocked={commands.can_act === false} onRun={item => item.entry_type === "conversation" ? perform(() => api.action(sessionId, {
              input_mode: "conversation_start", client_action_id: api.key("conversation-start"), state_version: state.state_version,
              opportunity_id: item.submit?.opportunity_id, target_npc_id: item.submit?.npc_id,
            }), `已进入与 ${playerText(item.title).replace(/^与|交谈$/g, "") || "对方"} 的会谈`) : setFormOpen({ title: item.title || "安排现场事务", kind: "resource", item })} />}
            {panel === "review" && <ReviewPanel data={panelData} />}
            {panel === "night-dialogues" && <NightPanel data={panelData} />}
            {panel === "manual-saves" && <SavePanel data={panelData} state={state} api={api} sessionId={sessionId} busy={busy} onPerform={perform} />}
          </div>
        </aside>
      </div>
    </section>

    <footer><span>{sessionId ? "每次行动与决定都会自动保存" : "准备好后，从右上角进入游戏"}</span><span>{activeGovernanceAction ? `${GOVERNANCE_ACTION_LABELS[activeGovernanceAction.action_kind] || "治理行动"}进行中` : activeConversation ? "会谈进行中" : pending ? "等待你的决定" : sessionId ? "请合理分配今日精力" : "清江水急，民心难测"}</span></footer>

    {authOpen && <Modal title={authRequired ? account ? "账号中心" : "登录治理档案" : "本地试玩"} onClose={() => setAuthOpen(false)}>{authRequired ? account ? <div className="account-card"><small>当前账号</small><strong>{account}</strong><p>你的游戏进度已绑定当前账号，重新登录后仍可继续。</p>{authError && <div className="notice">{authError}</div>}<button onClick={logoutAccount} disabled={busy}>退出登录</button></div> : <form className="stack-form" onSubmit={authenticate}><div className="auth-tabs"><button type="button" className={authMode === "login" ? "active" : ""} onClick={() => { setAuthMode("login"); setAuthError(""); }}>登录</button>{selfRegistration && <button type="button" className={authMode === "register" ? "active" : ""} onClick={() => { setAuthMode("register"); setAuthError(""); }}>注册</button>}</div><p>{authMode === "login" ? "登录后可继续这个账号的历史进度。" : "创建账号后即可保留多条游戏进度。"}</p>{authError && <div className="notice">{authError}</div>}<label>用户名<input name="username" minLength={authMode === "register" ? 3 : 1} maxLength={32} autoComplete="username" required autoFocus /></label><label>密码<input name="password" type="password" minLength={authMode === "register" ? 8 : 1} maxLength={256} autoComplete={authMode === "register" ? "new-password" : "current-password"} required /></label><button disabled={busy}>{busy ? "正在处理…" : authMode === "login" ? "登录并继续" : "注册并开始"}</button></form> : <div className="account-card"><small>当前身份</small><strong>本地试玩</strong><p>无需注册。游戏进度会保存在这台电脑上。</p></div>}</Modal>}
    {sessionOpen && <Modal title="进入清江县" onClose={() => { setSessionOpen(false); setSavedSessionsOpen(false); }}><div className="session-actions"><button onClick={() => openSession("new")}>开始新游戏<span>从上任第一天开始一条新的九十天时间线</span></button><button onClick={showSavedSessions} className={savedSessionsOpen ? "selected" : ""}>继续已有进度<span>查看当前账号下保存的游戏</span></button></div>{savedSessionsOpen && <div className="saved-session-list" aria-live="polite">{busy && !savedSessions.length && <div className="form-loading">正在整理存档…</div>}{savedSessionsError && <div className="notice">{savedSessionsError}</div>}{!busy && !savedSessionsError && !savedSessions.length && <div className="empty-state"><p>还没有保存过的游戏，可以从新游戏开始。</p></div>}{savedSessions.map((saved, index) => <button key={saved.session_id} onClick={() => openSession("load", String(saved.session_id))}><span><b>进度{chineseIndex(index)}</b><small>第 {saved.story_day || 1} 日 · {friendlyStatus(saved.status)}</small></span><time>{saved.updated_at ? new Date(saved.updated_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</time></button>)}</div>}</Modal>}
    {formOpen && <Modal title={formOpen.title} onClose={() => setFormOpen(null)}><ContextForm config={formOpen} state={state} api={api} sessionId={sessionId} onPerform={perform} /></Modal>}
  </main>;
}

function PlayerIdentityCard() {
  const player = resolveCharacter("player_li_zhiyuan");
  return <section className="player-identity-card" data-testid="player-identity-card"><div className="player-portrait"><CharacterPortrait character={player} fallbackName="李致远" /></div><div><small>你的身份</small><h3>{player?.name || "李致远"}</h3><p>{player?.role || "云溪县县长"}</p></div></section>;
}

function GovernanceActionScene({ action, meeting, overview }: { action: Dict; meeting: Dict | null; overview: Dict | null }) {
  const catalogs = [
    ...arr(get(overview, "target_catalogs.household_representative")),
    ...arr(get(overview, "target_catalogs.cadre")),
    ...arr(get(overview, "target_catalogs.meeting_participants")),
  ];
  const names = new Map(catalogs.map(item => [String(item.target_id), String(item.label || item.name || "相关人员")]));
  const targets = (Array.isArray(action.target_ids) ? action.target_ids : []).map((id: unknown) => names.get(String(id)) || "相关人员");
  const transcript = arr(meeting?.transcript || action.transcript);
  const title = GOVERNANCE_ACTION_LABELS[String(action.action_kind)] || "治理行动";
  return <section className="governance-scene">
    <header><div><small>正在进行 · {title}</small><h3>{action.topic || (targets.length ? `与${targets.join("、")}当面沟通` : title)}</h3></div><span>第 {action.story_day || "待定"} 日</span></header>
    {targets.length > 0 && <p className="scene-participants">在场：{targets.join("、")}</p>}
    {transcript.length ? <div className="scene-transcript">{transcript.map((entry, index) => <article className={entry.speaker_type === "player" ? "player" : "npc"} key={index}><strong>{entry.speaker_type === "player" ? "你" : entry.npc_name || "对方"}</strong><p>{entry.text || "对方暂未表态。"}</p></article>)}</div> : <div className="scene-opening"><span>启</span><p>{action.action_kind === "leadership_meeting" ? "人员已经到齐。先陈述问题与方案，听取各方意见后再形成决议。" : "你已经到达现场。先说明来意，再围绕事实、诉求与可行安排展开交流。"}</p></div>}
  </section>;
}

function PlayerActionBar({ state, commands, busy, pending, governanceAction, meeting, conversationName, value, onChange, onSubmit, onLeave, onFinishGovernance, onCancelGovernance, onNavigate, onEndDay }: { state: Dict; commands: Dict; busy: boolean; pending: Dict | null; governanceAction: Dict | null; meeting: Dict | null; conversationName: string; value: string; onChange: (value: string) => void; onSubmit: (event: FormEvent) => void; onLeave: () => void; onFinishGovernance: () => void; onCancelGovernance: () => void; onNavigate: (panel: PanelName) => void; onEndDay: (rest?: boolean) => void }) {
  if (!state.session_id) return <div className="next-action pending"><span>等待赴任</span><p>接下调令后，今天可以执行的事务会显示在这里。</p></div>;
  const conversation = state.active_conversation;
  const group = state.active_group_conversation;
  const compactCharacter = conversation ? resolveCharacter(conversation.npc_id, conversation.target_npc_id, conversation.npc_name) : null;
  if (conversation || group) return <form className="conversation-bar" onSubmit={onSubmit}>{conversation && <header className="conversation-compact" data-testid="active-conversation-compact"><div className="compact-portrait"><CharacterPortrait character={compactCharacter} fallbackName={conversationName || "对方"} /></div><div><small>正在会谈</small><strong>{compactCharacter?.name || conversationName || "对方"}</strong><span>{compactCharacter?.role || playerText(conversation.npc_title, "身份待确认")}</span></div><b>第 {Number(conversation.turn_count || conversation.turns_completed || 0)} 轮</b></header>}<label><span>{group ? "回应在场各方" : `回应 ${conversationName || "对方"}`}</span><textarea value={value} onChange={event => onChange(event.target.value)} placeholder="说清事实、诉求、承诺或你要追问的问题…" maxLength={1000} disabled={busy} /></label><div><small>{value.length} / 1000</small>{conversation && <button type="button" className="secondary" onClick={onLeave} disabled={busy}>结束会谈</button>}<button disabled={busy || !value.trim()}>送出回应</button></div></form>;
  if (pending) return <div className="next-action pending"><span>先处理上方事项</span><p>作出决定后，行动与会谈会重新开放。</p></div>;
  if (governanceAction) {
    const isMeeting = governanceAction.action_kind === "leadership_meeting";
    const hasDiscussion = arr(meeting?.transcript).length > 0;
    return <form className="conversation-bar governance-bar" onSubmit={onSubmit}><label><span>{isMeeting ? "向班子成员说明你的意见" : "继续询问或说明"}</span><textarea value={value} onChange={event => onChange(event.target.value)} placeholder={isMeeting ? "说明方案、责任分工、期限，或回应在场意见…" : "追问事实、了解诉求、解释政策或提出具体方案…"} maxLength={1000} disabled={busy} /></label><div><small>{value.length} / 1000</small><button type="button" className="danger-quiet" onClick={onCancelGovernance} disabled={busy}>中止行动</button><button type="button" className="secondary" onClick={onFinishGovernance} disabled={busy || (isMeeting && !hasDiscussion)}>{isMeeting ? "形成会议决议" : "结束本次行动"}</button><button disabled={busy || !value.trim()}>送出回应</button></div></form>;
  }
  return <div className="next-action"><div><span>下一步</span><p>{commands.can_end_day ? "今日工作可以收束，也可以继续使用剩余精力。" : "从行动或会谈中选择一个推进方向。"}</p></div><div className="next-buttons"><button onClick={() => onNavigate("actions")} disabled={busy}>安排行动</button><button onClick={() => onNavigate("opportunities")} disabled={busy}>寻找会谈</button>{commands.can_end_day && <button className="primary" onClick={() => onEndDay(false)} disabled={busy}>结束今日</button>}</div></div>;
}

function SceneSummary({ state, commands, governanceAction, onNavigate, onEndDay }: { state: Dict; commands: Dict; governanceAction: Dict | null; onNavigate: (panel: PanelName) => void; onEndDay: (rest?: boolean) => void }) {
  if (!state.session_id) return <div className="scene-summary"><div className="empty-state"><span>令</span><h3>尚未赴任</h3><p>进入游戏后，这里会显示今日目标和可行安排。</p></div></div>;
  const active = state.active_conversation || state.active_group_conversation;
  const pending = state.pending_decision;
  const day = Number(get(state, "story.day", 1));
  const current = pending ? "处理当前必须决定的事项" : governanceAction ? `完成正在进行的${GOVERNANCE_ACTION_LABELS[governanceAction.action_kind] || "治理行动"}` : active ? "完成正在进行的会谈" : commands.can_end_day ? "决定继续工作还是结束今日" : "选择一项行动或会谈";
  return <div className="scene-summary"><section className="objective-card"><small>当前首要事项</small><h3>{current}</h3><p>{pending ? "其他安排暂时搁置。先阅读现场信息，再作出选择。" : governanceAction ? "在左侧现场继续交流；取得所需信息后，记得正式结束行动。" : active ? "认真回应对方；你的措辞和承诺都会被记录。" : "查看行动成本和开放条件，再决定如何使用今日精力。"}</p></section>{day <= 3 && <section className="tutorial-card"><small>上手指引</small><ol><li className={pending ? "active" : "done"}><b>处理必须决定的事项</b><span>决策本身不消耗精力</span></li><li className={!pending && !commands.can_end_day ? "active" : ""}><b>安排工作或展开会谈</b><span>行动前会明确显示精力成本</span></li><li className={governanceAction ? "active" : commands.can_end_day ? "active" : ""}><b>{governanceAction ? "收束当前行动" : "结束今日"}</b><span>{governanceAction ? "交流后从左下方结束行动" : "夜间会结算后续影响"}</span></li></ol></section>}<div className="quick-links"><button onClick={() => onNavigate(governanceAction ? "governance" : "actions")}>{governanceAction ? "查看治理进展" : "查看行动"}</button><button onClick={() => onNavigate("desk")}>阅读任务卷宗</button>{commands.can_end_day && !governanceAction && <button className="primary" onClick={() => onEndDay(false)}>结束今日</button>}</div></div>;
}

function StructuredDecision({ pending, busy, onSubmit }: { pending: Dict; busy: boolean; onSubmit: (payload: Dict) => Promise<void> }) {
  const schema = pending.input_schema || {};
  const items = Array.isArray(schema.items) ? schema.items.map(String) : [];
  const fields = Array.isArray(schema.fields) ? schema.fields.map(String) : [];
  const total = Number(schema.total || 0);
  const [order, setOrder] = useState(items);
  const [allocations, setAllocations] = useState<Record<string, number>>(() => Object.fromEntries(fields.map((field, index) => [field, index === 0 ? total : 0])));
  const itemLabel = (item: string, index: number) => playerText(schema.labels?.[item] || arr(pending.options).find(option => option.option_id === item)?.text, `事项${chineseIndex(index)}`);
  if (pending.input_kind === "sorting") {
    const move = (index: number, delta: number) => setOrder(current => {
      const target = index + delta;
      if (target < 0 || target >= current.length) return current;
      const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next;
    });
    return <div className="structured-decision"><p>请按优先顺序排列，越靠上越先推进。</p>{order.map((item, index) => <div className="sort-row" key={item}><strong>{index + 1}</strong><span>{itemLabel(item, index)}</span><button onClick={() => move(index, -1)} disabled={busy || index === 0} aria-label={`上移第${index + 1}项`}>↑</button><button onClick={() => move(index, 1)} disabled={busy || index === order.length - 1} aria-label={`下移第${index + 1}项`}>↓</button></div>)}<button className="structured-submit" onClick={() => onSubmit({ ordered_option_ids: order })} disabled={busy || !order.length}>确认优先顺序</button></div>;
  }
  const allocated = Object.values(allocations).reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
  return <div className="structured-decision"><p>请分配全部 {total} {schema.unit || "份"}，不能剩余或超额。</p>{fields.map((field, index) => <label className="allocation-row" key={field}><span>{schema.labels?.[field] || `项目${chineseIndex(index)}`}</span><input type="number" min="0" step="1" value={allocations[field] ?? 0} onChange={event => setAllocations(current => ({ ...current, [field]: Math.max(0, Number(event.target.value) || 0) }))}/><em>{schema.unit || ""}</em></label>)}<div className={allocated === total ? "allocation-total valid" : "allocation-total invalid"}>已分配 {allocated} / {total}</div><button className="structured-submit" onClick={() => onSubmit({ parameters: { allocations } })} disabled={busy || allocated !== total}>确认分配</button></div>;
}

function ActionPanel({ data, onRun }: { data: Dict | null; onRun: (item: Dict) => void }) {
  const items = arr(data?.actions || data?.items || data);
  return <div className="card-list action-list">{items.length ? items.map((item, index) => <article key={item.action_id || index} className={item.available === false ? "unavailable" : ""}><div className="card-number">{chineseIndex(index)}</div><div><h3>{playerText(item.name || item.action_name, "治理行动")}</h3><p>{playerText(item.description || item.unavailable_reason, "根据当前情况安排工作")}</p>{item.available === false && <div className="blocked-reason">暂不可用：{playerText(item.unavailable_reason, "当前条件尚未满足")}</div>}<div className="item-foot"><span>{actionPointLabel(item)}</span><button onClick={() => onRun(item)} disabled={item.available === false}>{item.available === false ? "条件不足" : "选择对象"}</button></div></div></article>) : <Empty text="目前没有可安排的行动。先处理现场事项，或结束今天。"/>}</div>;
}

function OpportunityPanel({ data, activeConversation, onStart, onContinue }: { data: Dict | null; activeConversation: Dict | null; onStart: (item: Dict) => void; onContinue: () => void }) {
  const items = arr(data?.opportunities || data?.items || data);
  const blocked = data?.blocked_reason;
  return <div>{blocked && <div className="panel-note">{playerText(blocked)}</div>}<div className="card-list people">{items.length ? items.map((item, index) => {
    const character = resolveCharacter(item.npc_id, item.target_npc_id, item.npc_name);
    const name = character?.name || playerText(item.npc_name, "尚未公开身份");
    const isActive = Boolean(activeConversation) && (activeConversation?.opportunity_id === item.opportunity_id || resolveCharacter(activeConversation?.npc_id, activeConversation?.target_npc_id, activeConversation?.npc_name)?.id === character?.id);
    const anotherConversationActive = Boolean(activeConversation) && !isActive;
    return <article key={item.opportunity_id || index} data-character-id={character?.id || "unknown"}><div className="person-portrait"><CharacterPortrait character={character} fallbackName={name} /></div><div className="person-copy"><small>{character?.role || playerText(item.npc_title || item.action_name, "可会谈人物")}</small><h3>{name}</h3><p>{playerText(item.opening_narrative || item.conversation_goal || item.conversation_context, "与对方交换信息，了解其诉求与底线。")}</p>{item.available === false && !isActive && <div className="blocked-reason">{playerText(item.unavailable_reason, "当前无法会谈")}</div>}<div className="item-foot"><span>{actionPointLabel(item)}</span><button onClick={() => isActive ? onContinue() : onStart(item)} disabled={anotherConversationActive || (!isActive && item.available === false)}>{isActive ? "继续会谈" : anotherConversationActive ? "先结束当前会谈" : "进入会谈"}</button></div></div></article>;
  }) : <Empty text="当前没有开放的会谈。可以先安排行动，或阅读卷宗寻找突破口。"/>}</div></div>;
}

function GovernancePanel({ data }: { data: Dict | null }) {
  if (!data) return <Empty text="正在整理治理进展…"/>;
  const actions = arr(data.governance_actions);
  const activeActions = actions.filter(item => item.status === "active");
  const stats = [
    ["进行中的行动", activeActions.length], ["已召开会议", arr(data.meetings).length],
    ["已形成文件", arr(data.documents).length], ["逐户合同", arr(data.contracts).length],
  ];
  const cash = get(data, "resources.cash_ledger");
  return <div className="governance-panel"><div className="governance-grid">{stats.map(([label, value]) => <div key={String(label)}><strong>{value}</strong><span>{label}</span></div>)}</div>{cash && <section className="resource-card"><small>财政资源</small><h3>可安排 {displayValue(cash.available_unencumbered, "待定")} 万元</h3><p>已承诺 {displayValue(cash.committed, 0)} 万元 · 已支付 {displayValue(cash.paid, 0)} 万元</p></section>}<PanelSection title="行动记录" items={actions.slice().reverse().slice(0, 6)} empty="尚未开展治理行动" render={(item) => <><div className="evidence-head"><h4>{GOVERNANCE_ACTION_LABELS[item.action_kind] || "治理行动"}</h4><span>{friendlyStatus(item.status)}</span></div><p>{playerText(item.topic, `第 ${item.story_day || "待定"} 日开展`)}</p></>} /><PanelSection title="近期会议" items={arr(data.meetings)} empty="尚未召开正式会议" render={(item) => <><h4>{playerText(item.topic || item.title, "治理协调会")}</h4><p>第 {item.story_day || "待定"} 日 · {friendlyStatus(item.status)}</p></>} /><PanelSection title="已形成文件" items={arr(data.documents)} empty="尚未形成新的正式文件" render={(item) => <><h4>{playerText(item.title || DOCUMENT_TYPE_LABELS[item.document_type], "治理文件")}</h4><p>{friendlyStatus(item.status)} · 第 {item.issued_day || item.story_day || "待定"} 日</p></>} /></div>;
}

function DeskPanel({ data }: { data: Dict | null }) {
  if (!data) return <Empty text="正在整理案头卷宗…"/>;
  const mission = data.mission || {};
  const policy = data.compensation_policy || {};
  return <div className="desk-panel"><section className="mission-card"><small>专班任务书</small><h3>{playerText(mission.title, "清江搬迁任务")}</h3><p>{playerText(mission.summary, "在期限、财政、稳定和程序约束下推进柳林村整村搬迁。")}</p></section><div className="constraint-grid">{arr(mission.hard_constraints).map((item, index) => <article key={item.key || index}><small>{playerText(item.label, `约束${chineseIndex(index)}`)}</small><strong>{playerText(displayValue(item.value))}</strong><p>{playerText(item.detail)}</p></article>)}</div>{policy.title && <details className="policy-details"><summary>查看补偿与安置执行口径</summary><section><h3>{playerText(policy.title)}</h3><p>{playerText(policy.status)}</p>{arr(policy.funding).length > 0 && <div className="policy-funding">{arr(policy.funding).map((item, index) => <div key={index}><small>{playerText(item.label)}</small><strong>{playerText(item.value)}</strong></div>)}</div>}<h4>适用原则</h4><ul>{values(policy.principles).map((item, index) => <li key={index}>{playerText(item)}</li>)}</ul><h4>县长权限边界</h4><ul>{values(policy.authority_boundaries).map((item, index) => <li key={index}>{playerText(item)}</li>)}</ul></section></details>}<PanelSection title="背景卷宗" items={arr(data.dossiers)} empty="暂无可读卷宗" render={(item) => <><h4>{playerText(item.title, "背景材料")}</h4><p>{playerText(item.summary)}</p>{values(item.known_points).length > 0 && <ul>{values(item.known_points).map((point, index) => <li key={index}>{playerText(point)}</li>)}</ul>}</>} /></div>;
}

function KnowledgePanel({ data }: { data: Dict | null }) {
  if (!data) return <Empty text="正在核对已掌握材料…"/>;
  const groups = [["已确认事实", arr(data.facts)], ["待核线索", arr(data.clues)], ["证据材料", arr(data.evidence)]] as const;
  return <div className="knowledge-panel">{groups.map(([title, items]) => <PanelSection key={title} title={title} items={items} empty={`暂无${title}`} render={(item) => <><div className="evidence-head"><h4>{playerText(item.title || item.name || item.label, "新材料")}</h4>{(item.evidence_level || item.confidentiality) && <span>{friendlyStatus(item.evidence_level || item.confidentiality)}</span>}</div><p>{playerText(item.text || item.summary || item.description || item.content, "已收入案头，等待进一步核实。")}</p></>} />)}</div>;
}

function MapPanel({ data, blocked, onRun }: { data: Dict | null; blocked: boolean; onRun: (item: Dict) => void }) {
  const locations = arr(data?.locations);
  return <div className="map-panel"><div className="panel-note map-intro">地点会随调查与剧情推进开放；这里只展示你当前已经掌握的去处。</div>{blocked && <div className="panel-note">先处理当前必须决定的事项，随后即可安排现场工作。</div>}<div className="card-list location-list">{locations.length ? locations.map((item, index) => {
    const entries = item.visual_state === "available" ? arr(item.entry_cards) : [];
    return <article key={item.location_id || index}><div className="location-mark">{index + 1}</div><div><h3>{playerText(item.name, `地点${chineseIndex(index)}`)}</h3><p>{playerText(item.description, "暂无新的现场信息。")}</p><small>{item.visual_state === "available" ? "可以前往" : "尚未开放"}</small>{entries.length > 0 && <details className="location-actions"><summary>可办理事项（{entries.length}）</summary><div>{entries.map((entry, actionIndex) => <section key={entry.title || actionIndex}><div><b>{playerText(entry.title, "现场事务")}</b><p>{playerText(entry.description, "根据当前情况推进这项工作。")}</p><small>{actionPointLabel(entry)}{Number(entry.direct_budget_cost || 0) > 0 ? ` · 预算 ${entry.direct_budget_cost} 万元` : ""}</small></div><button disabled={blocked || entry.available === false} onClick={() => onRun({ ...entry, location_id: item.location_id })}>{entry.available === false ? "条件不足" : "办理"}</button></section>)}</div></details>}</div></article>;
  }) : <Empty text="地图上暂时没有可公开的地点。"/>}</div></div>;
}

function ReviewPanel({ data }: { data: Dict | null }) {
  if (!data) return <Empty text="正在整理本局纪要…"/>;
  const timelines = [
    ...arr(data.decision_timeline).map(item => ({ ...item, typeLabel: "你的决定" })),
    ...arr(data.action_timeline).map(item => ({ ...item, typeLabel: "治理行动" })),
    ...arr(data.conversation_timeline).map(item => ({ ...item, typeLabel: "人物会谈" })),
    ...arr(data.visible_events).map(item => ({ ...item, typeLabel: "重要事件" })),
  ].sort((a, b) => Number(a.story_day || a.day || 0) - Number(b.story_day || b.day || 0));
  const timelineTitle = (item: Dict) => {
    if (item.typeLabel === "人物会谈") {
      if (item.event === "conversation_started") return `开始与${item.npc_name || "相关人员"}会谈`;
      if (item.event === "conversation_ended") return `结束与${item.npc_name || "相关人员"}的会谈${item.completion_status === "incomplete" ? "（仍有事项未谈妥）" : ""}`;
    }
    return playerText(item.title || item.name || item.summary || item.text, "已记录事项");
  };
  return <div className="review-panel"><section className="review-summary"><small>当前进程</small><h3>{friendlyStatus(data.status)}</h3><p>这里只记录你已经经历的事件，不会提前透露尚未发生的剧情。</p></section><div className="timeline">{timelines.length ? timelines.map((item, index) => <article key={index}><time>第 {item.story_day || item.day || "待定"} 日</time><div><small>{item.typeLabel}</small><h4>{timelineTitle(item)}</h4>{item.choice && <p>你的选择：{playerText(item.choice)}</p>}{item.summary && item.title && <p>{playerText(item.summary)}</p>}</div></article>) : <Empty text="还没有足够的经历可供复盘。"/>}</div></div>;
}

function NightPanel({ data }: { data: Dict | null }) {
  const nights = arr(data?.nights);
  return <div className="night-panel">{nights.length ? nights.map((item, index) => <article key={index}><small>第 {item.story_day || item.day || "待定"} 日夜间</small><h3>{playerText(item.title, "夜间动向")}</h3><p>{playerText(item.summary || item.text, "夜色里有人在行动，但完整影响仍有待观察。")}</p></article>) : <Empty text="目前还没有可公开的夜间纪要。结束一天后，这里会记录你已经知晓的动向。"/>}</div>;
}

function SavePanel({ data, state, api, sessionId, busy, onPerform }: { data: Dict | null; state: Dict; api: GameApi; sessionId: string; busy: boolean; onPerform: (action: () => Promise<Dict>, success?: string, rebuildNarrative?: boolean) => Promise<void> }) {
  const saves = arr(data?.manual_saves);
  const [slot, setSlot] = useState(1);
  const [name, setName] = useState(`第${get(state, "story.day", 1)}日进度`);
  const occupied = saves.some(item => Number(item.slot_number) === slot);
  return <div className="save-panel">
    <section className="save-create">
      <small>另存一份进度</small><h3>保留关键节点</h3><p>日常行动会自动保存。手动存档适合在重要抉择前保留一份独立进度。</p>
      <label>存档位置<select value={slot} onChange={event => setSlot(Number(event.target.value))}>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>位置{chineseIndex(value - 1)}{saves.some(item => Number(item.slot_number) === value) ? "（已有存档）" : ""}</option>)}</select></label>
      <label>存档名称<input value={name} maxLength={40} onChange={event => setName(event.target.value)} /></label>
      <button disabled={busy || !name.trim()} onClick={() => { if (occupied && !window.confirm("这个位置已有存档，确认覆盖吗？")) return; void onPerform(() => api.manualSave(sessionId, { client_action_id: api.key("manual-save"), state_version: state.state_version, slot_number: slot, display_name: name.trim(), overwrite: occupied }), "手动存档已保存"); }}>{occupied ? "覆盖这个位置" : "保存当前进度"}</button>
    </section>
    <PanelSection title="手动存档" items={saves} empty="还没有手动存档" render={(item, index) => <div className="save-row"><div><h4>{item.display_name || `存档${chineseIndex(index)}`}</h4><p>第 {item.story_day || 1} 日 · {item.created_at ? new Date(item.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "已保存"}</p></div><button disabled={busy} onClick={() => { if (!window.confirm("载入后，当前未另存的进度会被覆盖。确认继续吗？")) return; void onPerform(() => api.loadSnapshot(sessionId, { client_action_id: api.key("load-save"), state_version: state.state_version, snapshot_id: item.snapshot_id, confirmed: true }), "已载入所选存档", true); }}>载入</button></div>} />
  </div>;
}

function PanelSection({ title, items, empty, render }: { title: string; items: Dict[]; empty: string; render: (item: Dict, index: number) => React.ReactNode }) {
  return <section className="panel-section"><h3>{title}</h3>{items.length ? <div>{items.map((item, index) => <article key={item.id || item.title || item.document_id || item.meeting_id || index}>{render(item, index)}</article>)}</div> : <p className="section-empty">{empty}</p>}</section>;
}

function ContextForm({ config, state, api, sessionId, onPerform }: { config: { kind: string; item?: Dict }; state: Dict; api: GameApi; sessionId: string; onPerform: (fn: () => Promise<Dict>, text?: string) => Promise<void> }) {
  const item = config.item || {};
  if (config.kind === "resource" && (item.execution_mode === "governance" || ["household_visit", "cadre_interview", "leadership_meeting", "inspect_archives"].includes(item.action_id))) {
    return <GovernanceActionForm item={item} state={state} api={api} sessionId={sessionId} onPerform={onPerform} />;
  }
  if (config.kind === "resource" && (item.action_id || item.submit?.action_id)) {
    return <ResourceActionForm item={item} state={state} api={api} sessionId={sessionId} onPerform={onPerform} />;
  }
  return <div className="empty-state"><span>缓</span><h3>这项安排尚需完善</h3><p>当前页面还没有足够的信息来安全执行它，请从地图或会谈入口尝试。</p></div>;
}

function ResourceActionForm({ item, state, api, sessionId, onPerform }: { item: Dict; state: Dict; api: GameApi; sessionId: string; onPerform: (fn: () => Promise<Dict>, text?: string) => Promise<void> }) {
  const actionId = String(item.action_id || item.submit?.action_id || "");
  const parameterSchema = item.parameter_schema || {};
  const properties = parameterSchema.properties || {};
  const required = new Set(Array.isArray(parameterSchema.required) ? parameterSchema.required.map(String) : []);
  const targetSchema = item.target_schema || {};
  const minTargets = Number(targetSchema.min_items || 0);
  const maxTargets = Number(targetSchema.max_items ?? Math.max(minTargets, 8));
  const targetKind = targetSchema.target_kind || (["field_visit"].includes(actionId) ? "location" : ["cross_validate_clues", "zheng_clue_summary"].includes(actionId) ? "fact" : "npc");
  const [catalogs, setCatalogs] = useState<{ npc: Dict[]; household: Dict[]; fact: Dict[]; location: Dict[] }>({ npc: [], household: [], fact: [], location: [] });
  const [loadError, setLoadError] = useState("");
  const [targets, setTargets] = useState<string[]>(() => actionId === "field_visit" && item.location_id ? [String(item.location_id)] : []);
  const [parameters, setParameters] = useState<Dict>(() => Object.fromEntries(Object.entries(properties).map(([key, raw]) => {
    const spec = raw as Dict;
    if (Array.isArray(spec.enum) && spec.enum.length) return [key, String(spec.enum[0])];
    if (spec.type === "integer") return [key, Number(spec.minimum || 0)];
    return [key, ""];
  })));

  useEffect(() => {
    let active = true;
    Promise.all([
      api.panel(sessionId, "governance"), api.panel(sessionId, "desk"), api.panel(sessionId, "knowledge"), api.panel(sessionId, "map"),
    ]).then(([governanceData, deskData, knowledgeData, mapData]) => {
      if (!active) return;
      const npc = arr(get(governanceData, "target_catalogs.meeting_participants")).map(value => ({ id: value.target_id, label: value.label }));
      const household = arr(deskData.household_registry).map(value => ({ id: value.household_id, label: value.signatory_name || "未命名家庭" }));
      const fact = [...arr(knowledgeData.facts), ...arr(knowledgeData.clues), ...arr(knowledgeData.evidence)].map(value => ({ id: value.fact_id || value.id, label: value.title || value.text || value.name || "已掌握材料" })).filter(value => value.id);
      const location = arr(mapData.locations).filter(value => value.visual_state === "available").map(value => ({ id: value.location_id, label: value.name }));
      setCatalogs({ npc, household, fact, location });
    }).catch(error => { if (active) setLoadError(playerErrorMessage(error)); });
    return () => { active = false; };
  }, [api, sessionId]);

  const choices = catalogs[targetKind as keyof typeof catalogs] || catalogs.npc;
  const parametersValid = [...required].every(key => {
    const value = parameters[key];
    return typeof value === "number" ? Number.isFinite(value) : String(value || "").trim().length > 0;
  });
  const targetsValid = targets.length >= minTargets && targets.length <= maxTargets;
  const toggleTarget = (id: string) => setTargets(current => {
    if (current.includes(id)) return current.filter(value => value !== id);
    if (maxTargets === 1) return [id];
    if (current.length >= maxTargets) return current;
    return [...current, id];
  });

  return <form className="stack-form resource-action-form" onSubmit={async event => {
    event.preventDefault();
    if (!targetsValid || !parametersValid) return;
    await onPerform(async () => {
      const quote = await api.write(sessionId, "/actions/quote", "POST", { state_version: state.state_version, action_id: actionId, target_ids: targets, parameters });
      return api.action(sessionId, {
        input_mode: "resource_action", client_action_id: api.key("resource-action"), state_version: quote.state_version || state.state_version,
        action_id: actionId, target_ids: targets, parameters, quote_id: quote.quote_id,
      });
    }, `${playerText(item.title || item.name, "现场事务")}已经办理`);
  }}>
    <p>{playerText(item.description || item.narrative, "根据当前情况推进这项工作。")}</p>
    <div className="action-cost-summary"><span>预计消耗</span><strong>{actionPointLabel(item)}</strong>{Number(item.direct_budget_cost || 0) > 0 && <em>另需预算 {item.direct_budget_cost} 万元</em>}</div>
    {maxTargets > 0 && (minTargets > 0 || choices.length > 0) && <fieldset className="choice-fieldset"><legend>{targetKind === "household" ? "选择涉及家庭" : targetKind === "location" ? "选择前往地点" : targetKind === "fact" ? "选择用于核验的材料" : "选择涉及人员"}</legend><div className="choice-grid">{choices.map((choice, index) => { const id = String(choice.id); const selected = targets.includes(id); return <label className={selected ? "choice-card selected" : "choice-card"} key={id || index}><input type={maxTargets === 1 ? "radio" : "checkbox"} checked={selected} onChange={() => toggleTarget(id)} /><span>{playerText(choice.label, `对象${chineseIndex(index)}`)}</span></label>; })}</div><small>需选择 {minTargets}{maxTargets !== minTargets ? ` 至 ${maxTargets}` : ""} 项 · 当前已选 {targets.length} 项</small>{!choices.length && minTargets > 0 && <div className="blocked-reason">目前没有符合条件的可选对象。</div>}</fieldset>}
    {Object.entries(properties).map(([key, raw]) => { const spec = raw as Dict; const label = PARAMETER_LABELS[key] || "具体说明"; const value = parameters[key]; if (Array.isArray(spec.enum)) return <label key={key}>{label}<select value={String(value)} onChange={event => setParameters(current => ({ ...current, [key]: event.target.value }))}>{spec.enum.map((option: unknown) => <option key={String(option)} value={String(option)}>{playerText(option)}</option>)}</select></label>; if (spec.type === "integer") return <label key={key}>{label}<input type="number" min={spec.minimum} max={spec.maximum} value={Number(value)} onChange={event => setParameters(current => ({ ...current, [key]: Number(event.target.value) }))} required={required.has(key)} /></label>; return <label key={key}>{label}<textarea value={String(value)} onChange={event => setParameters(current => ({ ...current, [key]: event.target.value }))} maxLength={500} required={required.has(key)} placeholder={`请填写${label}`} /></label>; })}
    {loadError && <div className="notice">{loadError}</div>}
    <button disabled={!targetsValid || !parametersValid || Boolean(loadError)}>确认办理</button>
  </form>;
}

function GovernanceActionForm({ item, state, api, sessionId, onPerform }: { item: Dict; state: Dict; api: GameApi; sessionId: string; onPerform: (fn: () => Promise<Dict>, text?: string) => Promise<void> }) {
  const actionId = String(item.action_id || "");
  const [overview, setOverview] = useState<Dict | null>(null);
  const [loadError, setLoadError] = useState("");
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [selectedArchives, setSelectedArchives] = useState<string[]>([]);
  const [topic, setTopic] = useState(actionId === "household_visit" ? "了解对方对搬迁安排的核心诉求与底线" : actionId === "cadre_interview" ? "核实负责事项、现有材料与程序风险" : MEETING_TOPICS[0]);
  const [documentType, setDocumentType] = useState("");
  const isMeeting = actionId === "leadership_meeting";
  const isArchive = actionId === "inspect_archives";

  useEffect(() => {
    let active = true;
    api.panel(sessionId, "governance").then(data => { if (active) setOverview(data); }).catch(error => { if (active) setLoadError(playerErrorMessage(error)); });
    return () => { active = false; };
  }, [api, sessionId]);

  const targetChoices = arr(get(overview, `target_catalogs.${item.target_kind}`, []));
  const archiveChoices = arr(overview?.archives);
  const documentTypes = arr(overview?.document_types);
  const minTargets = actionId === "household_visit" ? 1 : actionId === "cadre_interview" ? 1 : isMeeting ? 2 : 0;
  const maxTargets = actionId === "household_visit" ? 1 : actionId === "cadre_interview" ? 3 : isMeeting ? 8 : 0;
  const validSelection = isArchive ? selectedArchives.length > 0 : selectedTargets.length >= minTargets && selectedTargets.length <= maxTargets;

  const toggleTarget = (targetId: string) => setSelectedTargets(current => {
    if (current.includes(targetId)) return current.filter(value => value !== targetId);
    if (maxTargets === 1) return [targetId];
    if (current.length >= maxTargets) return current;
    return [...current, targetId];
  });
  const toggleArchive = (archiveId: string) => setSelectedArchives(current => current.includes(archiveId) ? current.filter(value => value !== archiveId) : [...current, archiveId]);

  if (loadError) return <div className="notice">{loadError}</div>;
  if (!overview) return <div className="form-loading">正在整理可选对象…</div>;

  return <form className="stack-form governance-action-form" onSubmit={async event => {
    event.preventDefault(); if (!validSelection) return;
    await onPerform(() => api.write(sessionId, "/governance/actions", "POST", {
      state_version: state.state_version, action_kind: actionId, target_ids: isArchive ? [] : selectedTargets,
      topic: isArchive ? "" : topic.trim(), archive_ids: isArchive ? selectedArchives : [],
      proposed_document_type: isMeeting && documentType ? documentType : null,
    }), isMeeting ? "班子会议已经发起" : isArchive ? "档案查阅已经开始" : "行动已经发起");
  }}>
    <p>{item.description}</p>
    {isMeeting && <fieldset className="choice-fieldset"><legend>本次会议要解决什么</legend><p className="field-help">发言和最终决议都会围绕这个核心问题展开。</p><div className="choice-grid topic-choices">{MEETING_TOPICS.map(value => <label className={topic === value ? "choice-card selected" : "choice-card"} key={value}><input type="radio" name="meeting-topic" value={value} checked={topic === value} onChange={() => setTopic(value)} /><span>{value}</span></label>)}</div></fieldset>}
    {!isMeeting && !isArchive && <label>本次重点了解什么<textarea value={topic} onChange={event => setTopic(event.target.value)} maxLength={500} required placeholder="例如：核实对方最关心的补偿、住房或程序问题" /></label>}
    {!isArchive && <fieldset className="choice-fieldset"><legend>{isMeeting ? "参会人员（选择二至八人）" : actionId === "cadre_interview" ? "访谈对象（选择一至三人）" : "走访对象（选择一人）"}</legend><div className="choice-grid">{targetChoices.map(choice => { const id = String(choice.target_id || choice.id); const selected = selectedTargets.includes(id); return <label className={selected ? "choice-card selected" : "choice-card"} key={id}><input type={maxTargets === 1 ? "radio" : "checkbox"} name="targets" value={id} checked={selected} onChange={() => toggleTarget(id)} /><span>{choice.label || choice.name || "未命名对象"}</span></label>; })}</div><small>已选择 {selectedTargets.length} 人{selectedTargets.length < minTargets ? `，还需选择 ${minTargets - selectedTargets.length} 人` : ""}</small></fieldset>}
    {isArchive && <fieldset className="choice-fieldset"><legend>要查阅的档案（可多选）</legend><div className="choice-grid">{archiveChoices.map(choice => { const id = String(choice.archive_id); const selected = selectedArchives.includes(id); return <label className={selected ? "choice-card selected" : "choice-card"} key={id}><input type="checkbox" value={id} checked={selected} onChange={() => toggleArchive(id)} /><span><b>{choice.title || "未命名档案"}</b><small>{friendlyStatus(choice.evidence_level)}</small></span></label>; })}</div>{!archiveChoices.length && <div className="empty-state"><p>目前没有已取得、可查阅的档案。</p></div>}</fieldset>}
    {isMeeting && documentTypes.length > 0 && <label>拟形成文件（可选）<select value={documentType} onChange={event => setDocumentType(event.target.value)}><option value="">仅形成会议纪要</option>{documentTypes.map(value => <option key={value.document_type} value={value.document_type}>{DOCUMENT_TYPE_LABELS[value.document_type] || "专项治理文件"}</option>)}</select></label>}
    <button disabled={!validSelection}>{isMeeting ? "发起班子会议" : isArchive ? "开始查阅" : "发起行动"}</button>
  </form>;
}

function Empty({ text }: { text: string }) { return <div className="empty-state"><span>待</span><p>{text}</p></div>; }
