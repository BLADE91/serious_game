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
