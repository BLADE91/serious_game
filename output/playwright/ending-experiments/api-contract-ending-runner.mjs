import fs from "node:fs";

const base = "http://127.0.0.1:3001/api/backend/api";
const account = process.env.PLAYER_ACCOUNT;
const csrf = process.env.PLAYER_CSRF;
const cookie = process.env.PLAYER_COOKIE;
const config = JSON.parse(process.argv[2] || "{}");
const outputPath = process.argv[3];
const packageRoot = "E:/严肃游戏/serious_game_code/code/backend/content/packages/pkg_gameplay_v3";
const households = JSON.parse(fs.readFileSync(`${packageRoot}/households.json`, "utf8")).households;
const householdById = new Map(households.map((item) => [item.household_id, item]));

let serial = 0;
const tag = config.tag || "contract-route";
const uid = (prefix) => `${prefix}-${tag}-${Date.now()}-${++serial}`;
async function api(path, method = "GET", body) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: { "content-type": "application/json", "x-account-id": account, "x-csrf-token": csrf, cookie },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} -> ${response.status}: ${text}`);
  if (response.headers.get("content-type")?.includes("application/json")) return JSON.parse(text);
  return text;
}

let state = config.session_id
  ? await api(`/game/session/${config.session_id}`)
  : await api("/game/session", "POST", { client_request_id: uid("new") });
state = state.visible_state || state;
const sessionId = state.session_id;
const raw = { tag, session_id: sessionId, route: config.choices || {}, observations: [], contract_attempts: [], errors: [] };

async function refresh() {
  state = await api(`/game/session/${sessionId}`);
  return state;
}

async function decideAll() {
  while (state.pending_decision) {
    const decision = state.pending_decision;
    const available = (decision.options || []).filter((item) => item.available !== false);
    if (!available.length) throw new Error(`No available option for ${decision.decision_id}`);
    const wanted = config.choices?.[decision.decision_id];
    const selected = available.find((item) => item.option_id === wanted) || available[0];
    raw.observations.push({ day: state.story.day, type: "decision", id: decision.decision_id, title: decision.title, requested: wanted || null, selected: selected.option_id, text: selected.text || selected.label || selected.title, fallback: Boolean(wanted && selected.option_id !== wanted) });
    const body = { input_mode: "decision", client_action_id: uid("decision"), state_version: state.state_version, decision_id: decision.decision_id, option_id: selected.option_id };
    if (decision.decision_id === "dp2_10") body.parameters = { signing_compensation: 150, livelihood_support: 0, environmental_retest: 0, emergency_stability: 0 };
    const result = await api(`/game/session/${sessionId}/action`, "POST", body);
    state = result.visible_state || result;
  }
}

async function activeAction() {
  const governance = await api(`/game/session/${sessionId}/governance`);
  const action = (governance.governance_actions || []).find((item) => item.status === "active");
  return { governance, action };
}

async function finishAction(actionId) {
  await refresh();
  const result = await api(`/game/session/${sessionId}/governance/actions/${actionId}/finish`, "POST", { state_version: state.state_version });
  state = result.visible_state || result;
}

async function completeOpportunity(opportunityId) {
  const listing = await api(`/game/session/${sessionId}/opportunities`);
  const opportunity = (listing.opportunities || []).find((item) => item.opportunity_id === opportunityId && item.cta_available !== false);
  if (!opportunity) {
    raw.errors.push({ day: state.story.day, type: "opportunity_missing", opportunity_id: opportunityId });
    return false;
  }
  const d = opportunity.canonical_action_descriptor;
  const started = await api(`/game/session/${sessionId}/governance/actions`, "POST", {
    state_version: state.state_version,
    action_kind: d.action_id,
    variant_id: d.variant_id,
    location_id: d.preselected_location_id,
    target_ids: d.preselected_npc_ids,
    topic: d.canonical_topic,
    opportunity_id: opportunity.opportunity_id,
  });
  state = started.visible_state || state;
  const { governance, action } = await activeAction();
  state.state_version = governance.state_version;
  const targetedText = {
    opp_03_zhou_kuiyuan_contact: "请把周家迁坟的旧例、完整仪程和你能接受的条件逐项说清楚。",
    opp_03_zhou_mancang_contact: "请把村账应当先核哪一笔、公开顺序和你要看的原始凭证说清楚。",
    opp_03_ning_dehai_contact: "请把补偿程序、书面审阅和逐户签字的先后要求说清楚。",
    opp_03_lao_juetou_contact: "请把你看房、验房和本人签署前必须落实的条件说清楚。",
    opp_03_miao_xiwang_contact: "请把复检、治疗、费用和孩子评估需要怎样写进协议说清楚。",
    opp_03_deng_shouben_contact: "请把交房、搬家和你本人签署前要确认的条件说清楚。",
  }[opportunityId] || `请围绕这次会谈目标，说明你亲历和关心的情况：${d.canonical_topic}`;
  await api(`/game/session/${sessionId}/governance/actions/${action.action_instance_id}/turn/stream`, "POST", {
    state_version: state.state_version,
    player_text: targetedText,
    client_action_id: uid("opportunity-turn"),
  });
  await refresh();
  await finishAction(action.action_instance_id);
  raw.observations.push({ day: state.story.day, type: "opportunity", opportunity_id: opportunityId, npc_id: opportunity.npc_id, npc_name: opportunity.npc_name });
  return true;
}

function policyMinimum(household) {
  const rates = { brick_concrete: 0.18, brick_wood: 0.15, earth_wood_tile: 0.12, brick_simple: 0.13 };
  const rawAmount = (rates[household.residential_structure] || 0) * household.legal_residential_area_m2
    + 0.06 * household.homestead_recognized_m2
    + 6 * household.contracted_land_mu
    + 2
    + 0.12 * household.resettlement_population * 12
    + 2;
  return Math.ceil(rawAmount - 1e-9);
}

function desiredArea(household) {
  if (household.resettlement_population <= 2) return 80;
  if (household.resettlement_population === 3) return 100;
  if (household.resettlement_population === 4) return 120;
  return 140;
}

function servicesFor(household) {
  const services = {};
  const preference = household.resettlement_preference || "";
  const hardship = (household.hardship_tags || []).join(" ");
  const medical = (household.medical_tags || []).join(" ");
  if (["shrine_core", "grave_executor", "grave_family", "grave_memory"].includes(household.grave_or_shrine_profile)) services.grave_relocation_service = 1;
  if (preference.includes("school") || hardship.includes("school_child")) services.school_transition_seat = 1;
  if (preference.includes("medical") || medical) services.lead_recheck_slot = 1;
  if (household.representative_npc === "npc_he_tiezhu") services.stable_job_slot = 1;
  if (preference.includes("business_restart")) services.business_restart_package = 1;
  if (household.representative_npc === "npc_yang_bo") {
    services.startup_interest_slot = 1;
    services.broadband_transition_slot = 1;
  }
  if (["npc_yuan_guilan", "npc_lao_juetou", "npc_deng_shouben"].includes(household.representative_npc)) services.elder_support_slot = 1;
  return services;
}

async function chooseHousing(household) {
  const governance = await api(`/game/session/${sessionId}/governance`);
  const area = desiredArea(household);
  const accessible = /(accessible|low_floor)/.test(household.resettlement_preference || "");
  const pools = (governance.resources?.resource_pools || []).filter((pool) => pool.category === "housing" && pool.attributes?.area_m2 === area && pool.available_to_reserve > 0);
  const choice = pools.find((pool) => Boolean(pool.attributes?.accessible) === accessible) || pools[0];
  if (!choice) throw new Error(`No housing remaining for ${household.household_id} area ${area}`);
  return choice.resource_id;
}

async function signContract(contract, day) {
  const household = householdById.get(contract.household_id);
  const housing = await chooseHousing(household);
  const terms = {
    state_version: state.state_version,
    policy_document_id: "doc_compensation_policy_v1",
    cash_amount: policyMinimum(household),
    budget_envelope: "property_land",
    housing_resource_id: housing,
    service_allocations: servicesFor(household),
    payment_day: day,
    move_out_day: Math.min(90, day + 4),
    housing_delivery_day: day,
    transition_months: 12,
    public_window_reward: day <= 75,
    approval_document_ids: ["doc_compensation_policy_v1"],
    authorization_confirmed: true,
    real_unit_viewed: true,
    ledger_disclosed: true,
    old_case_resolved: household.representative_npc === "npc_tan_laoliu",
    prior_payment_verified: true,
  };
  let result = await api(`/game/session/${sessionId}/governance/contracts/${contract.contract_id}/terms`, "PUT", terms);
  state.state_version = result.state_version;
  let review = await api(`/game/session/${sessionId}/governance/contracts/${contract.contract_id}/review`, "POST", { state_version: state.state_version });
  state.state_version = review.state_version;
  if (review.contract?.status !== "accepted") {
    raw.contract_attempts.push({ day, household_id: contract.household_id, contract_id: contract.contract_id, stage: "review_not_accepted", missing: review.missing_hard_conditions, status: review.contract?.status, reason: review.contract?.review_reason, terms });
    return false;
  }
  const signed = await api(`/game/session/${sessionId}/governance/contracts/${contract.contract_id}/sign`, "POST", { state_version: state.state_version, confirmed: true });
  state = signed.visible_state || state;
  raw.contract_attempts.push({ day, household_id: contract.household_id, contract_id: contract.contract_id, stage: "signed", status: signed.contract?.status, signed_total: state.ledger.signed_households.signed, terms: signed.contract?.term_sheet });
  return signed.contract?.status === "signed";
}

async function proposeAndSign(representativeNpcId) {
  const started = await api(`/game/session/${sessionId}/governance/actions`, "POST", {
    state_version: state.state_version,
    action_kind: "household_visit",
    variant_id: "field_visit",
    location_id: "loc_liulin_village",
    target_ids: [representativeNpcId],
    topic: "按已公示政策启动逐户合同审阅与本人签署",
  });
  state = started.visible_state || state;
  const action = started.action;
  const turn = await api(`/game/session/${sessionId}/governance/actions/${action.action_instance_id}/turn`, "POST", {
    state_version: started.state_version,
    player_text: "我明确提出逐户签约。请建立该户群的逐户合同批次，交每户本人审阅。",
    client_action_id: uid("contract-proposal"),
  });
  state = turn.visible_state || state;
  const proposal = turn.contract_batch_proposal;
  if (!proposal) {
    raw.contract_attempts.push({ day: state.story.day, representative_npc_id: representativeNpcId, stage: "no_batch_proposal", replies: turn.replies });
    await finishAction(action.action_instance_id);
    return false;
  }
  const confirmed = await api(`/game/session/${sessionId}/governance/contract-batches/${proposal.batch_id}/confirm`, "POST", { state_version: turn.state_version, confirmed: true });
  state.state_version = confirmed.state_version;
  if (config.hold_representative === representativeNpcId) {
    raw.observations.push({ day: state.story.day, type: "contract_batch_held", representative_npc_id: representativeNpcId, batch_id: proposal.batch_id, contracts: confirmed.contracts });
    await finishAction(action.action_instance_id);
    return true;
  }
  let signedCount = 0;
  try {
    for (const contract of confirmed.contracts || []) {
      try {
        if (await signContract(contract, state.story.day)) signedCount++;
      } catch (error) {
        raw.errors.push({ day: state.story.day, type: "contract_error", representative_npc_id: representativeNpcId, household_id: contract.household_id, message: String(error) });
      }
    }
  } finally {
    await finishAction(action.action_instance_id);
  }
  raw.observations.push({ day: state.story.day, type: "contract_batch", representative_npc_id: representativeNpcId, batch_id: proposal.batch_id, households: proposal.household_ids, signed_count: signedCount });
  return true;
}

const contactSchedule = new Map([
  [3, ["opp_d03_zhou_dashan_first_talk"]],
  [5, ["opp_03_yuan_guilan_contact"]],
  [15, ["opp_03_ma_changshun_contact"]],
  [26, ["opp_03_tan_laoliu_contact"]],
  [51, ["opp_03_zhou_kuiyuan_contact"]],
  [54, ["opp_03_yang_bo_contact"]],
  [56, ["opp_03_he_tiezhu_contact"]],
  [64, ["opp_03_zhou_mancang_contact"]],
  [70, ["opp_03_ning_dehai_contact"]],
  [77, ["opp_03_lao_juetou_contact"]],
  [78, ["opp_03_miao_xiwang_contact"]],
  [84, ["opp_03_deng_shouben_contact"]],
]);
const batchSchedule = new Map([
  [53, ["npc_zhou_dashan", "npc_tan_laoliu"]],
  [56, ["npc_yuan_guilan", "npc_wu_xiuying"]],
  [57, ["npc_he_tiezhu", "npc_yang_bo"]],
  [64, ["npc_zhou_kuiyuan"]],
  [68, ["npc_zhou_mancang"]],
  [71, ["npc_ning_dehai"]],
  [73, ["npc_ma_changshun"]],
  [77, ["npc_lao_juetou"]],
  [78, ["npc_miao_xiwang"]],
  [84, ["npc_deng_shouben"]],
]);

let wuDone = false;
while (state.status !== "ended") {
  await decideAll();
  if (state.story.day === 2 && !wuDone) {
    await completeOpportunity("opp_d02_wu_xiuying_first_talk");
    wuDone = true;
  }
  for (const opportunityId of contactSchedule.get(state.story.day) || []) await completeOpportunity(opportunityId);
  for (const npcId of batchSchedule.get(state.story.day) || []) {
    if ((config.skip_representatives || []).includes(npcId)) continue;
    try { await proposeAndSign(npcId); }
    catch (error) { raw.errors.push({ day: state.story.day, type: "contract_batch_error", npc_id: npcId, message: String(error) }); await refresh(); }
  }
  if (config.stop_day === state.story.day) break;
  await decideAll();
  const before = state.story.day;
  const ended = await api(`/game/session/${sessionId}/end-day`, "POST", { client_action_id: uid("end-day"), state_version: state.state_version, active_rest: false });
  state = ended.visible_state || ended;
  if (state.story.day === before && state.status !== "ended") throw new Error(`Day did not advance from D${before}`);
}

raw.ending = state.ending || null;
raw.final_ledger = state.ledger;
raw.visible_events = state.visible_events;
raw.conversations = await api(`/game/session/${sessionId}/conversations?limit=100`);
raw.review = state.status === "ended" ? await api(`/game/session/${sessionId}/review`) : null;
raw.governance = await api(`/game/session/${sessionId}/governance`);
const json = JSON.stringify(raw, null, 2);
if (outputPath) fs.writeFileSync(outputPath, json, "utf8");
console.log(JSON.stringify({ session_id: sessionId, day: state.story.day, status: state.status, ending: state.ending || null, signed: state.ledger.signed_households.signed, contract_attempts: raw.contract_attempts.length, errors: raw.errors }));
