export type PlayerRecord = Record<string, unknown>;

const INTERNAL_PREFIX = /^\s*(?:(?:DP|BEAT|EV|CH|NPC)[A-Z0-9_-]+\s*[·:：\u2014-]\s*)/i;
const INTERNAL_BRACKET = /[【\[](?:突发[·:：-])?(?:(?:DP|BEAT|EV|CH|NPC)[A-Z0-9_-]+)[】\]]\s*/gi;

export function toPlayerText(value: unknown, fallback = ""): string {
  return String(value ?? fallback)
    .replace(INTERNAL_PREFIX, "")
    .replace(INTERNAL_BRACKET, "")
    .replace(/\bD(\d{1,2})\b/g, "第$1日")
    .replace(/\bNPC\b/gi, "人物")
    .replace(/玩家/g, "你")
    .replace(/剧本注册材料/g, "已归档材料")
    .replace(/已注册且/g, "已经归档且")
    .replace(/注册过的/g, "已有的")
    .replace(/对应剧情节点/g, "后续事态")
    .replace(/规则节点/g, "事态发展")
    .replace(/硬结算/g, "实际影响")
    .trim();
}

export function actionPointCost(item: PlayerRecord | null | undefined): number | null {
  if (!item) return null;
  const nested = item.cost && typeof item.cost === "object" && !Array.isArray(item.cost)
    ? (item.cost as PlayerRecord).action_points
    : undefined;
  const raw = item.cost_action_points
    ?? item.action_point_cost
    ?? nested
    ?? (typeof item.cost === "number" ? item.cost : undefined)
    ?? item.ap_cost;
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string" && raw.trim() !== "" && Number.isFinite(Number(raw))) return Number(raw);
  return null;
}

export function actionPointLabel(item: PlayerRecord | null | undefined): string {
  const cost = actionPointCost(item);
  return cost === null ? "消耗待确认" : cost === 0 ? "不消耗精力" : `消耗 ${cost} 点精力`;
}

const CANONICAL_ACTION_IDS = [
  "household_visit",
  "cadre_interview",
  "leadership_meeting",
  "inspect_archives",
] as const;

export function canonicalActionFamilies(value: unknown): PlayerRecord[] {
  const items = Array.isArray(value) ? value.filter(item => item && typeof item === "object") as PlayerRecord[] : [];
  const byId = new Map(items.map(item => [String(item.action_id || ""), item]));
  return CANONICAL_ACTION_IDS.flatMap(actionId => {
    const item = byId.get(actionId);
    return item ? [{ ...item, variants: Array.isArray(item.variants) ? item.variants : [] }] : [];
  });
}

type PublicPerson = {
  npc_id: string;
  name: string;
  contact_state: "known" | "contactable";
  trust_band: string;
  attitude_band: string;
  anxiety_band: string;
  recent_change_reasons: string[];
};

type PublicEdge = {
  edge_id: string;
  source_npc_id: string;
  target_npc_id: string;
  visibility: "suspected" | "confirmed";
  channel: string;
  discovery_reason: string;
  discovery_day?: number;
};

export function peopleRelationshipView(value: PlayerRecord | null | undefined): { people: PublicPerson[]; edges: PublicEdge[] } {
  const people = (Array.isArray(value?.people) ? value.people : []).flatMap(raw => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as PlayerRecord;
    const contactState = String(item.contact_state || "");
    if (!new Set(["known", "contactable"]).has(contactState)) return [];
    return [{
      npc_id: String(item.npc_id || ""),
      name: String(item.name || ""),
      contact_state: contactState as PublicPerson["contact_state"],
      trust_band: String(item.trust_band || "not_assessed"),
      attitude_band: String(item.attitude_band || "not_assessed"),
      anxiety_band: String(item.anxiety_band || "not_assessed"),
      recent_change_reasons: (Array.isArray(item.recent_change_reasons) ? item.recent_change_reasons : []).slice(0, 3).map(String),
    }];
  });
  const visibleNpcIds = new Set(people.map(item => item.npc_id));
  const edges = (Array.isArray(value?.relationship_edges) ? value.relationship_edges : []).flatMap(raw => {
    if (!raw || typeof raw !== "object") return [];
    const item = raw as PlayerRecord;
    const visibility = String(item.visibility || "");
    const source = String(item.source_npc_id || "");
    const target = String(item.target_npc_id || "");
    if (!new Set(["suspected", "confirmed"]).has(visibility) || !visibleNpcIds.has(source) || !visibleNpcIds.has(target)) return [];
    return [{
      edge_id: String(item.edge_id || ""),
      source_npc_id: source,
      target_npc_id: target,
      visibility: visibility as PublicEdge["visibility"],
      channel: String(item.channel || "association"),
      discovery_reason: String(item.discovery_reason || ""),
      ...(typeof item.discovery_day === "number" ? { discovery_day: item.discovery_day } : {}),
    }];
  });
  return { people, edges };
}

export type NpcStreamViewState = {
  thinking: Record<string, { npc_id: string; npc_name: string }>;
  replies: Array<{ stream_id: string; npc_id: string; npc_name: string; text: string; complete: boolean }>;
  error: string;
};

export function initialNpcStreamState(): NpcStreamViewState {
  return { thinking: {}, replies: [], error: "" };
}

export function reduceNpcStream(state: NpcStreamViewState, event: PlayerRecord): NpcStreamViewState {
  const type = String(event.type || "");
  const streamId = String(event.stream_id || "");
  if (type === "npc_thinking_start" && streamId) {
    return { ...state, error: "", thinking: { ...state.thinking, [streamId]: { npc_id: String(event.npc_id || ""), npc_name: String(event.npc_name || "") } } };
  }
  if (type === "npc_thinking_end" && streamId) {
    const thinking = { ...state.thinking };
    delete thinking[streamId];
    return { ...state, thinking };
  }
  if (type === "npc_start" && streamId) {
    return { ...state, replies: [...state.replies, { stream_id: streamId, npc_id: String(event.npc_id || ""), npc_name: String(event.npc_name || ""), text: "", complete: false }] };
  }
  if (type === "npc_delta" && streamId) {
    return { ...state, replies: state.replies.map(item => item.stream_id === streamId ? { ...item, text: item.text + String(event.delta || "") } : item) };
  }
  if (type === "npc_end" && streamId) {
    return { ...state, replies: state.replies.map(item => item.stream_id === streamId ? { ...item, complete: true } : item) };
  }
  if (type === "error") {
    return { ...state, thinking: {}, error: String(event.message || "对方暂时无法回应，请稍后重试。") };
  }
  return state;
}

export function createSingleFlight<Args extends unknown[], Result>(fn: (...args: Args) => Promise<Result>) {
  let pending: Promise<Result> | null = null;
  return (...args: Args): Promise<Result> => {
    if (pending) return pending;
    pending = Promise.resolve().then(() => fn(...args));
    pending.then(
      () => { pending = null; },
      () => { pending = null; },
    );
    return pending;
  };
}

export function sessionEntry(value: PlayerRecord): { session_id: string; mode: "review" | "continue"; label: string; canContinue: boolean } {
  const retired = value.package_status === "retired" || value.review_only === true || value.mode === "review_only";
  return retired
    ? { session_id: String(value.session_id || ""), mode: "review", label: "仅可复盘", canContinue: false }
    : { session_id: String(value.session_id || ""), mode: "continue", label: "继续游戏", canContinue: value.loadable !== false };
}
