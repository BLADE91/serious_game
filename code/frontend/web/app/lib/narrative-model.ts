export type NarrativeItem = {
  id: string;
  cursor?: number;
  storyDay?: number;
  kind: string;
  speaker?: string;
  text: string;
  contentInstanceId?: string;
  blockId?: string;
  decisionId?: string;
  mainEndingId?: string;
  beatId?: string;
};

export type NarrativeState = {
  sessionId: string;
  items: NarrativeItem[];
  currentIndex: number;
  unreadCount: number;
  feedCursor: number;
  rebuildCount: number;
};

export type NarrativeAction =
  | { type: "SESSION_OPEN"; sessionId: string }
  | { type: "FEED_MERGE"; sessionId: string; items: NarrativeItem[]; cursor?: number }
  | { type: "SESSION_REBUILD"; sessionId: string; items: NarrativeItem[]; cursor?: number }
  | { type: "PREVIOUS" }
  | { type: "NEXT" }
  | { type: "GO_TO"; index: number }
  | { type: "GO_LATEST" }
  | { type: "CLEAR" };

export const initialNarrativeState: NarrativeState = {
  sessionId: "",
  items: [],
  currentIndex: -1,
  unreadCount: 0,
  feedCursor: 0,
  rebuildCount: 0,
};

const keyFor = (item: NarrativeItem) => item.contentInstanceId
  ? `content:${item.contentInstanceId}`
  : Number.isFinite(item.cursor)
    ? `cursor:${item.cursor}`
    : `id:${item.id}`;

export function dedupeNarrative(items: readonly NarrativeItem[]): NarrativeItem[] {
  const known = new Set<string>();
  return items.filter(item => {
    const key = keyFor(item);
    if (known.has(key)) return false;
    known.add(key);
    return true;
  });
}

export function narrativeReducer(state: NarrativeState, action: NarrativeAction): NarrativeState {
  switch (action.type) {
    case "CLEAR":
      return initialNarrativeState;
    case "SESSION_OPEN":
      return action.sessionId === state.sessionId
        ? state
        : { ...initialNarrativeState, sessionId: action.sessionId };
    case "SESSION_REBUILD": {
      const items = dedupeNarrative(action.items);
      return {
        sessionId: action.sessionId,
        items,
        currentIndex: items.length - 1,
        unreadCount: 0,
        feedCursor: action.cursor ?? Math.max(0, ...items.map(item => item.cursor || 0)),
        rebuildCount: state.rebuildCount + 1,
      };
    }
    case "FEED_MERGE": {
      const base = action.sessionId === state.sessionId ? state : { ...initialNarrativeState, sessionId: action.sessionId };
      const atLatest = base.currentIndex >= base.items.length - 1;
      const before = base.items.length;
      const items = dedupeNarrative([...base.items, ...action.items]);
      const added = items.length - before;
      return {
        ...base,
        items,
        currentIndex: atLatest ? items.length - 1 : base.currentIndex,
        unreadCount: atLatest ? 0 : base.unreadCount + added,
        feedCursor: action.cursor ?? base.feedCursor,
      };
    }
    case "PREVIOUS":
      return state.currentIndex > 0 ? { ...state, currentIndex: state.currentIndex - 1 } : state;
    case "NEXT": {
      if (state.currentIndex >= state.items.length - 1) return state;
      const currentIndex = state.currentIndex + 1;
      const atLatest = currentIndex === state.items.length - 1;
      return { ...state, currentIndex, unreadCount: atLatest ? 0 : state.unreadCount };
    }
    case "GO_TO": {
      if (!state.items.length) return state;
      const currentIndex = Math.max(0, Math.min(state.items.length - 1, Math.trunc(action.index)));
      return { ...state, currentIndex, unreadCount: currentIndex === state.items.length - 1 ? 0 : state.unreadCount };
    }
    case "GO_LATEST":
      return { ...state, currentIndex: state.items.length - 1, unreadCount: 0 };
  }
}

export function narrativeItemFromFeed(value: Record<string, unknown>, fallbackId: string): NarrativeItem {
  const contentInstanceId = typeof value.content_instance_id === "string" ? value.content_instance_id : undefined;
  const cursor = typeof value.cursor === "number" ? value.cursor : undefined;
  return {
    id: String(contentInstanceId || cursor || fallbackId),
    cursor,
    storyDay: typeof value.story_day === "number" ? value.story_day : undefined,
    kind: String(value.kind || "narrative"),
    speaker: value.speaker ? String(value.speaker) : undefined,
    text: String(value.text || ""),
    contentInstanceId,
    blockId: typeof value.block_id === "string" ? value.block_id : undefined,
    decisionId: typeof value.decision_id === "string" ? value.decision_id : undefined,
    mainEndingId: typeof value.main_ending_id === "string" ? value.main_ending_id : undefined,
    beatId: typeof value.beat_id === "string" ? value.beat_id : undefined,
  };
}
