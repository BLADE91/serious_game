export type PlayerRecord = Record<string, unknown>;

export function aiConfigurationView(value: PlayerRecord | null | undefined): {
  configured: boolean;
  mode: string;
  summary: string;
  serverDefaultAvailable: boolean;
  serverDefaultSummary: string;
} {
  const active = value?.active === true;
  const mode = active ? String(value?.mode || "unconfigured") : "unconfigured";
  const endpoint = String(value?.endpoint || "");
  const model = String(value?.model || "");
  const serverDefault = value?.server_default && typeof value.server_default === "object" && !Array.isArray(value.server_default)
    ? value.server_default as PlayerRecord
    : {};
  const defaultEndpoint = String(serverDefault.endpoint || "");
  const defaultModel = String(serverDefault.model || "");
  return {
    configured: active,
    mode,
    summary: active
      ? `${mode === "personal" ? "个人 API" : "服务器默认"} · ${endpoint || "已启用"}${model ? ` · ${model}` : ""}`
      : "尚未配置 AI 接口",
    serverDefaultAvailable: value?.server_default_available === true,
    serverDefaultSummary: [defaultEndpoint, defaultModel].filter(Boolean).join(" · "),
  };
}

export function requiresAIConfiguration(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  return String((value as PlayerRecord).code || "") === "ROLE_LLM_CONFIGURATION_REQUIRED";
}

const SAFE_AI_CONFIGURATION_MESSAGES = new Set([
  "API Key 无效，或该账号没有模型权限",
  "AI 接口连接超时或暂时不可用",
  "该接口不兼容游戏所需的结构化输出",
  "服务器未配置可用的默认 AI 接口",
  "Base URL 必须是公共 HTTPS 地址，且不能包含账号、查询参数或片段",
  "Base URL 域名无法安全解析",
  "Base URL 不能指向内网、回环、链路本地或保留地址",
  "API Key 和模型名不能为空",
]);

export function aiConfigurationErrorMessage(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "AI 接口测试失败，请检查地址、Key 和模型名后重试。";
  }
  const error = value as PlayerRecord;
  const message = String(error.message || "");
  if (
    String(error.code || "") === "ROLE_LLM_CONFIGURATION_INVALID"
    && SAFE_AI_CONFIGURATION_MESSAGES.has(message)
  ) {
    return message;
  }
  return "AI 接口测试失败，请检查地址、Key 和模型名后重试。";
}

const RELATIONSHIP_LABELS: Record<string, string> = {
  closed: "封闭", guarded: "谨慎", working: "可协作", trusted: "信任",
  hostile: "对立", resistant: "抵触", neutral: "中立", cooperative: "合作", supportive: "支持",
  calm: "平稳", uneasy: "不安", worried: "担忧", strained: "紧张", critical: "高度焦虑",
  not_assessed: "尚待观察",
};

export function qualitativeRelationshipLabel(value: unknown): string {
  return RELATIONSHIP_LABELS[String(value)] || "尚待观察";
}

export function archivePlayerSections(value: PlayerRecord | null | undefined): Array<{ heading: string; body: string }> {
  const sections = Array.isArray(value?.player_sections) ? value.player_sections : [];
  const projected = sections.flatMap(raw => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    const item = raw as PlayerRecord;
    const heading = toPlayerText(item.heading, "档案记录");
    const body = toPlayerText(item.body);
    return body ? [{ heading, body }] : [];
  });
  return projected.length ? projected : [{ heading: "档案正文", body: "这份档案暂无可读正文。" }];
}

export function conversationSpeakerLabel(turn: PlayerRecord, npcName: string): string {
  const speaker = String(turn.speaker_type || turn.speaker || "npc");
  return speaker === "player" ? "你" : npcName || "对方";
}

export function budgetEnvelopeChoices(value: unknown): Array<PlayerRecord & { envelope_id: string; resource_id: string; name: string }> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as PlayerRecord).flatMap(([envelopeId, raw]) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    const item = raw as PlayerRecord;
    return [{
      ...item,
      envelope_id: envelopeId,
      resource_id: String(item.resource_id || `budget:${envelopeId}`),
      name: toPlayerText(item.label, "专项预算"),
      unit: toPlayerText(item.unit, "万元"),
    }];
  });
}

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

export function governanceCancelMessage(action: PlayerRecord | null | undefined): string {
  return action?.cost_status === "pending"
    ? "确认中止当前行动？尚未形成有效交流，不会消耗精力。"
    : "确认中止当前行动？已经消耗的精力不会返还。";
}

export function governanceFinishMessage(action: PlayerRecord | null | undefined): string {
  return action?.cost_status === "pending"
    ? "本次行动未形成有效交流，未消耗精力，也未产生完成效果。"
    : "本次行动已经收束，取得的材料已收入案头。";
}

export function governanceLocationLocked(descriptor: PlayerRecord | null | undefined): boolean {
  return Boolean(descriptor?.opportunity_id) || descriptor?.location_locked === true;
}

export function governanceLocationLockMessage(
  descriptor: PlayerRecord | null | undefined,
): string {
  if (descriptor?.opportunity_id) return "人物会谈机会已锁定本次办理地点。";
  if (descriptor?.location_locked === true) return "地图入口已锁定本次办理地点。";
  return "";
}

export function governanceActionTitle(
  action: PlayerRecord | null | undefined,
  fallback: string,
): string {
  const title = action?.display_title;
  return typeof title === "string" && title.trim() ? title.trim() : fallback;
}

export function governanceActionProgressLabels(
  action: PlayerRecord | null | undefined,
  fallback: string,
): { footer: string; task: string } {
  const title = governanceActionTitle(action, fallback);
  return {
    footer: `${title}进行中`,
    task: `完成正在进行的${title}`,
  };
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
  relationship_reasons: { trust: string; attitude: string; anxiety: string };
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
    const rawReasons = item.relationship_reasons && typeof item.relationship_reasons === "object" && !Array.isArray(item.relationship_reasons)
      ? item.relationship_reasons as PlayerRecord : {};
    return [{
      npc_id: String(item.npc_id || ""),
      name: String(item.name || ""),
      contact_state: contactState as PublicPerson["contact_state"],
      trust_band: String(item.trust_band || "not_assessed"),
      attitude_band: String(item.attitude_band || "not_assessed"),
      anxiety_band: String(item.anxiety_band || "not_assessed"),
      relationship_reasons: {
        trust: String(rawReasons.trust || ""),
        attitude: String(rawReasons.attitude || ""),
        anxiety: String(rawReasons.anxiety || ""),
      },
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

export function canonicalActionEntry(value: PlayerRecord): PlayerRecord | null {
  const raw = value.canonical_action_descriptor;
  const descriptor = raw && typeof raw === "object" && !Array.isArray(raw)
    ? raw as PlayerRecord
    : value;
  const actionId = String(descriptor.action_id || "");
  const variantId = String(descriptor.variant_id || "");
  if (!CANONICAL_ACTION_IDS.includes(actionId as typeof CANONICAL_ACTION_IDS[number]) || !variantId) return null;
  const targetChoices = Array.isArray(descriptor.target_choices) ? descriptor.target_choices as PlayerRecord[] : [];
  const locationChoices = Array.isArray(descriptor.location_choices) ? descriptor.location_choices as PlayerRecord[] : [];
  const targetIds = (Array.isArray(descriptor.preselected_npc_ids) ? descriptor.preselected_npc_ids : []).map(String);
  const legalTargetIds = new Set(targetChoices.map(item => String(item.target_id || item.id || "")));
  const preselectedNpcIds = targetIds.filter(id => legalTargetIds.has(id));
  const requestedLocationId = String(descriptor.preselected_location_id || descriptor.location_id || "");
  const legalLocationIds = new Set(locationChoices.map(item => String(item.location_id || "")));
  const preselectedLocationId = legalLocationIds.has(requestedLocationId)
    ? requestedLocationId
    : String(locationChoices[0]?.location_id || "");
  return {
    ...descriptor,
    action_id: actionId,
    variant_id: variantId,
    preselected_npc_ids: preselectedNpcIds,
    preselected_location_id: preselectedLocationId,
  };
}

type GovernanceWriter = {
  write: (sessionId: string, path: string, method: string, body: PlayerRecord) => Promise<unknown>;
};

export function submitGovernanceAction(
  api: GovernanceWriter,
  sessionId: string,
  input: {
    state_version: number;
    descriptor: PlayerRecord;
    location_id: string;
    target_ids: string[];
    topic: string;
    archive_ids: string[];
    proposed_document_type: string | null;
    lead_npc_id: string | null;
  },
) {
  const isCanonicalOpportunity = Boolean(input.descriptor.opportunity_id);
  const isLockedLocation = governanceLocationLocked(input.descriptor);
  const canonicalTargets = (Array.isArray(input.descriptor.preselected_npc_ids)
    ? input.descriptor.preselected_npc_ids
    : []).map(String);
  return api.write(sessionId, "/governance/actions", "POST", {
    state_version: input.state_version,
    action_kind: String(input.descriptor.action_id || ""),
    variant_id: String(input.descriptor.variant_id || ""),
    location_id: isLockedLocation
      ? String(input.descriptor.preselected_location_id || "")
      : input.location_id,
    ...(input.descriptor.map_entry_id
      ? { map_entry_id: String(input.descriptor.map_entry_id) }
      : {}),
    opportunity_id: input.descriptor.opportunity_id || null,
    target_ids: isCanonicalOpportunity ? canonicalTargets : input.target_ids,
    topic: isCanonicalOpportunity
      ? String(input.descriptor.canonical_topic || "")
      : input.topic,
    archive_ids: isCanonicalOpportunity ? [] : input.archive_ids,
    proposed_document_type: isCanonicalOpportunity
      ? null
      : input.proposed_document_type,
    lead_npc_id: isCanonicalOpportunity ? null : input.lead_npc_id,
  });
}

export function primaryScenePlan(value: PlayerRecord): string[] {
  if (!value.has_session) return [];
  if (value.active_group_conversation) return ["forced_group_conversation"];
  const action = value.active_governance_action as PlayerRecord | null;
  if (action) {
    return value.active_meeting && action.action_kind === "leadership_meeting"
      ? ["leadership_meeting"]
      : ["governance_action"];
  }
  return [value.active_conversation ? "conversation" : "narrative"];
}

export function sessionEntry(value: PlayerRecord): {
  session_id: string;
  mode: "review" | "continue" | "unavailable";
  label: string;
  canContinue: boolean;
  openKind: "review" | "load" | null;
  unavailableReason: string;
} {
  const retired = value.package_status === "retired" || value.review_only === true || value.mode === "review_only";
  const unavailable = value.mode === "content_unavailable"
    || value.content_available === false
    || (value.loadable === false && !retired);
  if (unavailable) return {
    session_id: String(value.session_id || ""),
    mode: "unavailable",
    label: "内容不可用",
    canContinue: false,
    openKind: null,
    unavailableReason: String(value.unavailable_reason || "该进度锁定的剧本内容已不在当前版本中，暂时无法打开。"),
  };
  return retired
    ? { session_id: String(value.session_id || ""), mode: "review", label: "仅可复盘", canContinue: false, openKind: "review", unavailableReason: "" }
    : { session_id: String(value.session_id || ""), mode: "continue", label: "继续游戏", canContinue: true, openKind: "load", unavailableReason: "" };
}
