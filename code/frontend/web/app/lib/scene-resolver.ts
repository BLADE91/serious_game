export type SceneMatchedBy =
  | "content_instance_id"
  | "block_id"
  | "decision_id"
  | "main_ending_id"
  | "beat_id"
  | "scene_id"
  | "fallback";

export type SceneDefinition = {
  id: string;
  title: string;
  asset: string;
  beatIds: readonly string[];
  blockIds: readonly string[];
  decisionIds: readonly string[];
};

export type SceneResolveInput = {
  contentInstanceId?: unknown;
  blockId?: unknown;
  decisionId?: unknown;
  mainEndingId?: unknown;
  beatId?: unknown;
  sceneId?: unknown;
};

export type SceneViewInput = {
  line?: SceneResolveInput & { storyDay?: unknown };
  lines?: readonly (SceneResolveInput & { storyDay?: unknown })[];
  currentIndex: number;
  itemCount: number;
  currentStoryDay?: unknown;
  decisionId?: unknown;
  pendingSceneId?: unknown;
  mainEndingId?: unknown;
  beatId?: unknown;
};

export type ResolvedScene = SceneDefinition & {
  matchedBy: SceneMatchedBy;
  matchedId: string | null;
};

const scene = (
  id: string,
  title: string,
  beatIds: readonly string[],
  blockIds: readonly string[] = [],
  decisionIds: readonly string[] = [],
): SceneDefinition => ({
  id,
  title,
  asset: `/scenes/${id.toLowerCase().replace("_", "-")}.webp`,
  beatIds,
  blockIds,
  decisionIds,
});

// Every entry is tied to identifiers that exist in pkg_gameplay_v2. A scene may
// share a beat with another shot; block-level matching is what selects the
// precise camera position inside that beat.
export const STORY_SCENES: readonly SceneDefinition[] = [
  scene("C01_S01", "雨后抵达柳林村", ["beat_d01_arrival_and_reception"], ["d01_opening", "d01_arrival_geography", "d01_arrival_economy", "d01_arrival_drive", "d01_arrival_factory", "d01_arrival_stop", "d01_arrival_structure"]),
  scene("C01_S02", "县政府办公室", ["beat_d01_arrival_and_reception"], ["d01_briefing_office", "d01_briefing_greeting", "d01_briefing_files", "d01_briefing_intro", "d01_briefing_pause", "d01_briefing_dossier_1", "d01_briefing_dossier_2", "d01_briefing_dossier_3", "d01_briefing_dossier_4", "d01_briefing_dossier_5", "d01_briefing_policy", "d01_briefing_actions", "d01_briefing_ledger", "d01_briefing_role", "d01_briefing_look", "d01_briefing_boundary"]),
  scene("C01_S03", "接风宴", ["beat_d01_arrival_and_reception"], ["d01_reception_scene", "d01_reception_toast_intro", "d01_reception_toast", "d01_reception_turn", "d01_reception_qian_position", "d01_reception_zhao_intro", "d01_reception_zhao_position", "d01_reception_sun_intro", "d01_reception_sun_position", "d01_reception_pressure", "d01_reception_bag", "d01_qian_offer", "d01_bag_detail", "d01_night_qian_departure"], ["ev1_01_reception_bag"]),
  scene("C01_S04", "县委书记办公室", ["beat_d02_party_secretary"], ["d02_morning_call", "d02_morning_office", "d02_jiang_open", "d02_jiang_first", "d02_jiang_second", "d02_jiang_third", "d02_jiang_account"]),
  scene("C01_S05", "柳林村委会", ["beat_d03_faction_map_closure"], ["d03_compensation_storm"], ["dp1_02"]),
  scene("C01_S06", "渡口镇工作组例会", ["beat_d03_faction_map_closure", "beat_d07_m2", "beat_d25_m2"], ["d02_taskforce_intro", "d02_taskforce_context", "d02_stage_summary", "d02_notebook_summary", "d03_faction_map_formed", "d07_source_opening", "d25_meeting", "d25_miao", "d25_sun_words", "d25_choice_setup", "dp2_05_presentation", "dp2_05_followup"], ["dp1_01_taskforce_faction_map", "dp1_04", "dp2_05"]),
  scene("C01_S07", "袁桂兰旧瓦房", ["beat_d05_m2", "beat_d06_m2"], ["d05_source_opening", "d06_source_opening"], ["dp1_03", "ev1_03"]),
  scene("C01_S08", "赵建国办公室", ["beat_d08_m2", "beat_d09_m2"], ["d08_source_opening", "d09_source_opening", "d18_phone_pressure", "d18_phone_choice_setup", "dp2_02_phone_presentation", "dp2_02_phone_followup"], ["dp1_05"]),
  scene("C01_S09", "首轮签约", ["beat_d11_m2", "beat_d12_m2", "beat_d13_m2"], ["d11_source_opening", "d12_source_opening", "d13_source_opening"], ["dp1_06", "dp1_07", "dp1_08"]),
  scene("C01_S10", "渡口堵路", ["beat_d10_m2"], ["d10_source_opening", "d10_source_night"], ["ev1_02"]),
  scene("C01_S11", "三盏灯", ["beat_d14_m2", "beat_d15_m2"], ["d14_source_night", "d15_source_night"], ["dp1_09"]),

  scene("C02_S01", "晨间三张纸与茶叶盒", ["beat_d17_m2", "beat_d18_m2"], ["d17_source_opening", "d18_source_opening"], ["dp2_01", "dp2_02"]),
  scene("C02_S02", "三张连号发票", ["beat_d19_m2"]),
  scene("C02_S03", "废弃粮站", ["beat_d20_m2", "beat_d21_m2"], ["d20_source_opening"], ["dp2_03"]),
  scene("C02_S04", "三年不变的数据", ["beat_d22_m2", "beat_d23_m2"], ["d22_source_opening"], ["dp2_04"]),
  scene("C02_S05", "吴秀英红圈名册", ["beat_d24_m2"], ["d24_source_opening"]),
  scene("C02_S06", "台阶上的状纸", ["beat_d26_m2"], ["d26_source_opening"], ["ev2_01", "dp2_06"]),
  scene("C02_S07", "红头文件", ["beat_d27_m2"], ["d27_source_opening"], ["dp2_07"]),
  scene("C02_S08", "烟盒纸", ["beat_d28_m2", "beat_d29_m2", "beat_d30_m2"], ["d28_source_opening", "d29_source_opening", "d29_teahouse", "d29_comparison", "d29_hospital", "d30_source_opening"], ["dp2_08", "dp2_09", "dp2_10"]),

  scene("C03_S01", "巡察组进城", ["beat_d31_m2"], [], ["dp3_01"]),
  scene("C03_S02", "档案库房", ["beat_d32_m2", "beat_d33_m2", "beat_d34_m2"], [], ["dp3_02", "dp3_03", "dp3_04"]),
  scene("C03_S03", "雨夜叩门", ["beat_d36_m2", "beat_d38_m2", "beat_d39_m2", "beat_d40_m2"], [], ["dp3_05", "dp3_06"]),
  scene("C03_S04", "医院血铅急诊", ["beat_d41_m2", "beat_d42_m2"], ["d42_he_tiezhu_known"], ["dp3_07", "ev3_01", "ev3_01_followup"]),
  scene("C03_S05", "个别谈话室", ["beat_d43_m2", "beat_d44_m2"], [], ["dp3_08", "dp3_09", "dp3_tea_disposition"]),
  scene("C03_S06", "巡察组下村", ["beat_d45_m2"], [], ["dp3_10"]),

  scene("C04_S01", "一百零七份体检单", ["beat_d46_m2", "beat_d47_m2"], ["d46_public", "d46_internal", "d46_locked", "d46_city", "d46_village", "d46_office"], ["dp4_01", "dp4_roster_disposition"]),
  scene("C04_S02", "台阶上的账本", ["beat_d48_m2"], ["d48_source_opening", "d48_family", "d48_factory", "d48_sent", "d48_held", "d48_ledger", "d48_dark"], ["dp4_02"]),
  scene("C04_S03", "果篮与信封", ["beat_d49_m2", "beat_d50_m2"], ["d49_source_opening"], ["dp4_03"]),
  scene("C04_S04", "夜间祠堂", ["beat_d51_m2", "beat_d52_m2", "beat_d53_m2", "beat_d54_m2"], ["d51_zhou_kuiyuan_intro", "d52_source_opening", "d53_source_opening", "d54_yang_bo_intro"], ["dp4_04", "dp4_05", "dp4_06"]),
  scene("C04_S05", "拥挤门诊楼", ["beat_d55_m2", "beat_d56_m2"], ["d55_source_opening", "d56_he_tiezhu_intro"], ["ev4_01", "dp4_07"]),
  scene("C04_S06", "铁盒第二本账", ["beat_d57_m2", "beat_d58_m2"], ["d57_source_opening", "d57_zhao_visit", "d58_source_opening", "d58_dam"], ["dp4_08", "ev4_02", "dp4_09", "ev4_03"]),
  scene("C04_S07", "拂晓迎检材料", ["beat_d59_m2", "beat_d60_m2"], ["d59_source_opening", "d60_source_opening", "d60_source_night"], ["ev4_04", "dp4_10", "dp4_11"]),

  scene("C05_S01", "三十天倒计时", ["beat_d61_m2", "beat_d62_m2"], ["d61_source_opening"], ["dp5_01"]),
  scene("C05_S02", "白日祠堂", ["beat_d63_m2"], ["d63_source_opening", "dp5_09_presentation", "dp5_09_followup"], ["dp5_02", "dp5_09"]),
  scene("C05_S03", "后山坟地控制线", ["beat_d64_m2", "beat_d65_m2", "beat_d66_m2"], ["d64_source_opening", "d66_source_opening"], ["dp5_03", "ev5_01"]),
  scene("C05_S04", "周满仓院落账本", ["beat_d67_m2", "beat_d68_m2", "beat_d69_m2"], ["d67_source_opening", "d69_source_opening"], ["dp5_04", "dp5_05", "dp5_04_recovery", "dp5_05_recovery"]),
  scene("C05_S05", "四十一年未签字", ["beat_d70_m2", "beat_d71_m2"], ["d70_source_opening", "d71_source_opening"], ["dp5_06"]),
  scene("C05_S06", "小卖部账本", ["beat_d72_m2", "beat_d73_m2"], ["d72_source_opening", "d73_source_opening", "d73_source_night"], ["ev5_02", "dp5_07"]),
  scene("C05_S07", "儿科走廊补偿信封", ["beat_d74_m2", "beat_d75_m2"], ["d74_source_opening", "d75_source_opening", "d75_phone"], ["dp5_08", "dp5_10", "dp5_11", "dp5_12", "ev5_03"]),

  scene("C06_S01", "招待所深夜审账", ["beat_d76_m2", "beat_d77_m2"], ["d76_source_opening", "d77_source_opening"], ["dp6_01", "dp6_02"]),
  scene("C06_S02", "铁盒证物", ["beat_d78_m2", "beat_d79_m2", "beat_d80_m2", "beat_d81_m2"], ["d78_source_opening", "d79_source_opening", "d80_source_opening", "d81_source_opening"], ["dp6_03", "ev6_01", "dp6_04", "dp6_05"]),
  scene("C06_S03", "第二波舆情", ["beat_d82_m2"], ["d82_source_opening"], ["ev6_02"]),
  scene("C06_S04", "记者的稿子", ["beat_d83_m2"], ["d83_source_opening"], ["dp6_06"]),
  scene("C06_S05", "十一项整改", ["beat_d84_m2", "beat_d85_m2"], ["d84_source_opening", "d85_source_opening"], ["dp6_07", "dp6_08"]),
  scene("C06_S06", "搬迁后的空村", ["beat_d86_m2"], ["d86_source_opening", "d86_he_receipt", "d86_zhou_approval"]),
  scene("C06_S07", "会前的门", ["beat_d87_m2", "beat_d88_m2"], ["d87_source_opening", "d88_source_opening"], ["dp6_09"]),
  scene("C06_S08", "常委会", ["beat_d89_m2"], ["d89_source_opening"], ["dp6_10"]),
  scene("C06_S09", "第九十日县礼堂", ["beat_d90_m2"], ["d90_source_opening"]),
];

export const ENDING_SCENES: readonly SceneDefinition[] = [
  "铁窗之内", "一票否决", "强手收场", "祠堂封门", "掩耳盗铃", "全线溃败",
  "悲壮的失守", "功亏一篑", "串供过关", "粉饰太平", "报喜不报忧", "一手遮天",
  "弃车保帅", "捂住的账", "铁腕的代价", "人走心散", "独木难支", "记者的沉默",
  "巡察组的档案", "无知者的功劳", "揭而未治", "山河可鉴", "清白收官", "尘埃落定",
].map((title, index) => {
  const number = String(index + 1).padStart(2, "0");
  return {
    id: `E${number}`,
    title,
    asset: `/scenes/ending-${number}.webp`,
    beatIds: [],
    blockIds: [],
    decisionIds: [],
  };
});

const firstIndex = (pairs: readonly (readonly [string, SceneDefinition])[]) => {
  const index = new Map<string, SceneDefinition>();
  for (const [key, value] of pairs) if (key && !index.has(key)) index.set(key, value);
  return index;
};

const blockIndex = firstIndex(STORY_SCENES.flatMap(item => item.blockIds.map(id => [id, item] as const)));
const decisionIndex = firstIndex(STORY_SCENES.flatMap(item => item.decisionIds.map(id => [id, item] as const)));
const beatIndex = firstIndex(STORY_SCENES.flatMap(item => item.beatIds.map(id => [id, item] as const)));
const sceneIndex = firstIndex(STORY_SCENES.map(item => [item.id, item] as const));
const endingIndex = firstIndex(ENDING_SCENES.map((item, index) => [`ending_${String(index + 1).padStart(2, "0")}`, item] as const));

const id = (value: unknown) => typeof value === "string" ? value.trim() : "";

function result(definition: SceneDefinition, matchedBy: SceneMatchedBy, matchedId: string | null): ResolvedScene {
  return { ...definition, matchedBy, matchedId };
}

export function blockIdFromContentInstance(value: unknown): string {
  const contentId = id(value);
  return contentId.startsWith("block:") ? contentId.slice("block:".length) : "";
}

export function resolveScene(input: SceneResolveInput = {}): ResolvedScene {
  const explicitSceneId = id(input.sceneId);
  if (explicitSceneId && sceneIndex.has(explicitSceneId)) {
    return result(sceneIndex.get(explicitSceneId)!, "scene_id", explicitSceneId);
  }
  const contentBlockId = blockIdFromContentInstance(input.contentInstanceId);
  if (contentBlockId && blockIndex.has(contentBlockId)) {
    return result(blockIndex.get(contentBlockId)!, "content_instance_id", contentBlockId);
  }
  const explicitBlockId = id(input.blockId);
  if (explicitBlockId && blockIndex.has(explicitBlockId)) {
    return result(blockIndex.get(explicitBlockId)!, "block_id", explicitBlockId);
  }
  const decisionId = id(input.decisionId);
  if (decisionId && decisionIndex.has(decisionId)) {
    return result(decisionIndex.get(decisionId)!, "decision_id", decisionId);
  }
  const endingId = id(input.mainEndingId);
  if (endingId && endingIndex.has(endingId)) {
    return result(endingIndex.get(endingId)!, "main_ending_id", endingId);
  }
  const beatId = id(input.beatId);
  if (beatId && beatIndex.has(beatId)) {
    return result(beatIndex.get(beatId)!, "beat_id", beatId);
  }
  return result({
    id: "N00",
    title: "县长办公室",
    asset: "/scenes/c01-s02.webp",
    beatIds: [],
    blockIds: [],
    decisionIds: [],
  }, "fallback", null);
}

// Keep identifiers from one narrative moment together. A historical line must
// not inherit today's decision/beat, while a new decision, ending, or day with
// no opening block must not be masked by yesterday's final feed item.
export function resolveSceneForView(input: SceneViewInput): ResolvedScene {
  const pendingSceneId = id(input.pendingSceneId);
  if (pendingSceneId) return resolveScene({ sceneId: pendingSceneId });
  const mainEndingId = id(input.mainEndingId);
  if (mainEndingId && !input.line) return resolveScene({ mainEndingId });
  if (input.line) return resolveScene(input.line);
  return resolveScene({ beatId: input.beatId });
}

function nearestLineScene(input: SceneViewInput, expectedDay: number): ResolvedScene | null {
  const lines = input.lines?.length ? input.lines : input.line ? [input.line] : [];
  const start = input.lines?.length
    ? Math.min(Math.max(0, input.currentIndex), lines.length - 1)
    : lines.length - 1;
  for (let index = start; index >= 0; index -= 1) {
    const line = lines[index];
    const lineDay = Number(line.storyDay);
    if (Number.isFinite(expectedDay) && Number.isFinite(lineDay) && lineDay !== expectedDay) break;
    const resolved = resolveScene(line);
    if (resolved.matchedBy !== "fallback") return resolved;
  }
  return null;
}
