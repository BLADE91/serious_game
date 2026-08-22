const base = "http://127.0.0.1:3001/api/backend/api";
const account = process.env.PLAYER_ACCOUNT;
const csrf = process.env.PLAYER_CSRF;
const cookie = process.env.PLAYER_COOKIE;
const strategy = JSON.parse(process.argv[2] || "{}");
const tag = process.argv[3] || "route";

if (!account || !csrf || !cookie) throw new Error("PLAYER_ACCOUNT/PLAYER_CSRF/PLAYER_COOKIE are required");

let serial = 0;
const uid = (prefix) => `${prefix}-${tag}-${Date.now()}-${++serial}`;
async function api(path, method = "GET", body) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "x-account-id": account,
      "x-csrf-token": csrf,
      cookie,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} -> ${response.status}: ${text}`);
  if (response.headers.get("content-type")?.includes("application/json")) return JSON.parse(text);
  return text;
}

const created = strategy.__session_id
  ? await api(`/game/session/${strategy.__session_id}`)
  : await api("/game/session", "POST", { client_request_id: uid("new") });
let state = created.visible_state || created;
const sessionId = state.session_id;
const observations = [];

async function refresh() {
  state = await api(`/game/session/${sessionId}`);
  return state;
}

async function decide() {
  const d = state.pending_decision;
  if (!d) return false;
  const available = (d.options || []).filter((option) => option.available !== false);
  if (!available.length) throw new Error(`No available option for ${d.decision_id}`);
  const requested = strategy[d.decision_id];
  const selected = available.find((option) => option.option_id === requested) || available[0];
  observations.push({ day: state.story.day, type: "decision", id: d.decision_id, title: d.title, option_id: selected.option_id, option_text: selected.text || selected.label || selected.title });
  const payload = {
    input_mode: "decision",
    client_action_id: uid("decision"),
    state_version: state.state_version,
    decision_id: d.decision_id,
    option_id: selected.option_id,
  };
  if (d.decision_id === "dp2_10") {
    payload.parameters = {
      signing_compensation: 150,
      livelihood_support: 0,
      environmental_retest: 0,
      emergency_stability: 0,
    };
  }
  const result = await api(`/game/session/${sessionId}/action`, "POST", payload);
  state = result.visible_state || result;
  return true;
}

async function completeWuTalk() {
  const listing = await api(`/game/session/${sessionId}/opportunities`);
  const opportunity = (listing.opportunities || []).find((item) => item.opportunity_id === "opp_d02_wu_xiuying_first_talk");
  if (!opportunity) throw new Error(`D2 Wu opportunity unavailable: ${JSON.stringify(listing)}`);
  const descriptor = opportunity.canonical_action_descriptor || opportunity.action_descriptor || opportunity.descriptor || opportunity;
  observations.push({ day: 2, type: "conversation", opportunity_id: opportunity.opportunity_id, public: opportunity });
  const started = await api(`/game/session/${sessionId}/governance/actions`, "POST", {
    state_version: state.state_version,
    action_kind: descriptor.action_id || descriptor.action_kind,
    variant_id: descriptor.variant_id,
    location_id: descriptor.preselected_location_id || descriptor.location_id,
    target_ids: descriptor.preselected_npc_ids || descriptor.preselected_participant_ids || descriptor.target_ids,
    topic: descriptor.canonical_topic || descriptor.topic,
    opportunity_id: opportunity.opportunity_id,
  });
  state = started.visible_state || state;
  const governanceAfterStart = await api(`/game/session/${sessionId}/governance`);
  const activeAction = (governanceAfterStart.governance_actions || []).find((item) => item.status === "active" && item.opportunity_id === opportunity.opportunity_id);
  const actionId = activeAction?.action_instance_id;
  if (!actionId) throw new Error(`Started Wu action but no public active action was returned: ${JSON.stringify(governanceAfterStart.governance_actions)}`);
  state.state_version = governanceAfterStart.state_version;
  await api(`/game/session/${sessionId}/governance/actions/${actionId}/turn/stream`, "POST", {
    client_action_id: uid("wu-turn"),
    state_version: state.state_version,
    player_text: "吴秀英同志，请把村里的实际顾虑和各家的情况具体说说。",
  });
  await refresh();
  const finished = await api(`/game/session/${sessionId}/governance/actions/${actionId}/finish`, "POST", {
    state_version: state.state_version,
  });
  state = finished.visible_state || finished;
}

let wuDone = state.story.day > 2;
while (state.status !== "ended" && (!strategy.__stop_day || state.story.day < strategy.__stop_day)) {
  while (state.pending_decision) await decide();
  if (state.story.day === 2 && !wuDone) {
    await completeWuTalk();
    wuDone = true;
    while (state.pending_decision) await decide();
  }
  if (state.active_group_conversation || state.active_conversation) {
    throw new Error(`Unexpected forced conversation at D${state.story.day}: ${JSON.stringify({ group: state.active_group_conversation, single: state.active_conversation })}`);
  }
  const before = state.story.day;
  const result = await api(`/game/session/${sessionId}/end-day`, "POST", {
    client_action_id: uid("end-day"),
    state_version: state.state_version,
    active_rest: false,
  });
  state = result.visible_state || result;
  if (state.story.day === before && state.status !== "ended") throw new Error(`Day did not advance from D${before}: ${JSON.stringify(state)}`);
}

const review = state.status === "ended" ? await api(`/game/session/${sessionId}/review`) : null;
const conversations = await api(`/game/session/${sessionId}/conversations?limit=100`);
const governance = await api(`/game/session/${sessionId}/governance`);
const output = {
  tag,
  session_id: sessionId,
  ending: state.ending,
  observations,
  conversations,
  governance,
  visible_events: state.visible_events,
  review,
};
console.log(JSON.stringify(output));
