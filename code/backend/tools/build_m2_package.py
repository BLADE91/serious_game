"""从《最终剧本》重建 M2 的日历、内容目录、事件、地图与结局登记表。"""

from __future__ import annotations

import hashlib
import itertools
import json
import copy
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "最终剧本.md"
PACKAGE = REPO_ROOT / "code" / "backend" / "content" / "packages" / "pkg_backend_dev_v1"
PLAYABLE_STOPS = {1, 2, 3, 16, 31, 45, 46, 59, 60, 61, 76, 90}
CHAPTER_END = (15, 30, 45, 60, 75, 90)
DECISION_COUNTS = {1: 9, 2: 10, 3: 10, 4: 11, 5: 12, 6: 10}
# 母稿中的编号决策并非都在章首发生。这里按正文的绝对日期、幕内
# “第 N 天触发”与逐日标题锁定；同日多节点保持编号顺序进入队列。
DECISION_DAY_BY_ID = {
    "DP1-01": 2, "DP1-02": 3, "DP1-03": 5, "DP1-04": 7,
    "DP1-05": 9, "DP1-06": 11, "DP1-07": 12, "DP1-08": 13,
    "DP1-09": 15,
    "DP2-01": 17, "DP2-02": 18, "DP2-03": 20, "DP2-04": 22,
    "DP2-05": 25, "DP2-06": 26, "DP2-07": 27, "DP2-08": 28,
    "DP2-09": 28, "DP2-10": 29,
    "DP3-01": 31, "DP3-02": 32, "DP3-03": 34, "DP3-04": 34,
    "DP3-05": 36, "DP3-06": 38, "DP3-07": 41, "DP3-08": 43,
    "DP3-09": 44, "DP3-10": 45,
    "DP4-01": 46, "DP4-02": 48, "DP4-03": 49, "DP4-04": 51,
    "DP4-05": 52, "DP4-06": 53, "DP4-07": 56, "DP4-08": 57,
    "DP4-09": 58, "DP4-10": 59, "DP4-11": 60,
    "DP5-01": 61, "DP5-02": 63, "DP5-03": 64, "DP5-04": 67,
    "DP5-05": 68, "DP5-06": 71, "DP5-07": 73, "DP5-08": 74,
    "DP5-09": 74, "DP5-10": 75, "DP5-11": 75, "DP5-12": 75,
    "DP6-01": 76, "DP6-02": 77, "DP6-03": 78, "DP6-04": 80,
    "DP6-05": 81, "DP6-06": 83, "DP6-07": 84, "DP6-08": 85,
    "DP6-09": 87, "DP6-10": 89,
}
SUPPORTING_DECISION_DAY = {
    "dp4_roster_disposition": 46,
    "ev3_01_followup": 43,
}
EVENT_IDS = (
    "EV1-01", "EV1-02", "EV1-03", "EV2-01", "EV3-01",
    "EV4-01", "EV4-02", "EV4-03", "EV4-04", "EV5-01",
    "EV5-02", "EV5-03", "EV6-01", "EV6-02",
)
EVENT_DAYS = {
    "EV1-01": 1, "EV1-02": 8, "EV1-03": 10, "EV2-01": 26,
    "EV3-01": 42, "EV4-01": 55, "EV4-02": 57, "EV4-03": 58,
    "EV4-04": 59, "EV5-01": 65, "EV5-02": 72, "EV5-03": 75,
    "EV6-01": 79, "EV6-02": 82,
}

OPTION_CONDITIONS = {
    ("DP2-02", "c"): {"forbidden_flags": ["与钱伟撕破脸"]},
    ("DP2-04", "d"): {"forbidden_flags": ["与钱伟撕破脸"]},
    ("DP3-02", "a"): {"required_flags": ["见过原件"]},
    ("DP3-02", "c"): {"required_any_flags": ["见过原件", "罗健留底"]},
    ("DP3-03", "a"): {"required_any_flags": ["见过原件", "罗健留底"]},
    ("DP3-10", "a"): {"forbidden_flags": ["官话"]},
    ("DP4-09", "b"): {"forbidden_flags": ["赵建国书面自证在手"]},
    ("DP4-09", "c"): {
        "forbidden_flags": ["赵建国书面自证在手", "赵建国转全面敌对"],
    },
    ("DP4-09", "d"): {"forbidden_flags": ["赵建国书面自证在手"]},
    ("DP5-03", "a"): {"required_flags": ["迁坟旧例在手"]},
    ("DP5-04", "a"): {"required_flags": ["村账在手"]},
    ("DP5-04", "b"): {"required_flags": ["村账在手"]},
    ("DP5-04", "c"): {"required_flags": ["村账在手"]},
    ("DP5-04", "d"): {"required_flags": ["村账在手"]},
    ("DP5-05", "a"): {"required_flags": ["村账已摊"]},
    ("DP5-05", "b"): {"required_flags": ["村账已摊"]},
    ("DP5-05", "c"): {"required_flags": ["村账已摊"]},
    ("DP5-05", "d"): {"required_flags": ["村账已摊"]},
    ("DP6-05", "a"): {"required_flags": ["刘三反水"]},
    ("DP6-05", "b"): {"required_flags": ["刘三反水"]},
    ("DP6-01", "d"): {"required_flags": ["串供口径"]},
    ("DP6-09", "a"): {"required_any_flags": ["掌握血铅", "旧账缺口已坐实"]},
}


def dump(name: str, value: dict) -> None:
    (PACKAGE / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    tens, ones = divmod(value, 10)
    return digits[tens] + "十" + (digits[ones] if ones else "")


def chapter_for(day: int) -> int:
    return next(index for index, end in enumerate(CHAPTER_END, 1) if day <= end)


def first_source_line(lines: list[str], day: int, *, night: bool = False) -> str | None:
    token = f"第{chinese_number(day)}日"
    for raw in lines:
        line = raw.strip()
        if token not in line or not line:
            continue
        if night and "夜" not in line:
            continue
        if line.startswith(("编号：", "所在日：", "第")) and len(line) < 25:
            continue
        return line.lstrip("# ").strip()
    return None


def build_story_beats(lines: list[str]) -> None:
    existing = json.loads((PACKAGE / "story_beats.json").read_text(encoding="utf-8"))
    beats = {int(item["story_day"]): item for item in existing["beats"]}
    # M1 intentionally stopped at D3.  Once M2 is built, D3 becomes an ordinary
    # playable checkpoint and must be able to enter the remaining story clock.
    beats[3]["allow_end_day"] = True
    beats[3]["opening_blocks"] = [
        block
        for block in beats[3].get("opening_blocks", [])
        if block.get("block_id") not in {"d03_slice_boundary", "d03_faction_map_formed"}
    ]
    beats[3]["opening_blocks"].append({
        "block_id": "d03_faction_map_formed",
        "kind": "narration",
        "text": (
            "派系图有了骨架，队伍搭起了架子，接风那道坎也趟了过去。"
            "头三天，你算是在云溪站住了脚跟，虽说站得还不太稳。"
            "真正的硬仗还没开打。补偿的盘子怎么分、三十六户的心气怎么顺、"
            "那座锈迹斑斑的旧厂房底下究竟埋着什么，这些个问号都还在前头的日子里等着你。"
        ),
    })
    for day in range(4, 91):
        opening = first_source_line(lines, day)
        night = first_source_line(lines, day, night=True)
        day_mode = "ending" if day == 90 else (
            "playable" if day in PLAYABLE_STOPS else "simulated"
        )
        opening_blocks = []
        if opening:
            opening_blocks.append({
                "block_id": f"d{day:02d}_source_opening",
                "kind": "narration",
                "text": opening,
            })
        night_blocks = []
        if night and night != opening:
            night_blocks.append({
                "block_id": f"d{day:02d}_source_night",
                "kind": "night",
                "text": night,
            })
        beats[day] = {
            "beat_id": f"beat_d{day:02d}_m2",
            "story_day": day,
            "chapter": chapter_for(day),
            "day_mode": day_mode,
            "title": opening[:36] if opening else f"第{chinese_number(day)}日",
            "allow_actions": day_mode == "playable",
            "allow_end_day": day != 90,
            "opening_blocks": opening_blocks,
            "night_blocks": night_blocks,
            "night_effects": {},
        }
    # 生成器可能以上一次生成物为输入；先清掉旧版“整章堆在章首”的队列，
    # 再按母稿日期重建，保证重复执行仍是幂等的。
    for beat in beats.values():
        beat["decision_ids"] = []
        beat.setdefault("night_conditional_effects", [])
    for source_id, day in DECISION_DAY_BY_ID.items():
        # DP1-01 是 M1 已锁定的 D2 opening_decision，不重复排队。
        if source_id == "DP1-01":
            continue
        beats[day].setdefault("decision_ids", []).append(
            source_id.lower().replace("-", "_")
        )
    for decision_id, day in SUPPORTING_DECISION_DAY.items():
        beats[day].setdefault("decision_ids", []).append(decision_id)

    beats[29]["night_blocks"] = [
        {"block_id": "d29_teahouse", "kind": "night", "text": "县城茶楼昨晚有人订了包间，订到子夜。", "forbidden_flags": ["与钱伟撕破脸"]},
        {"block_id": "d29_comparison", "kind": "night", "text": "柳林村昨夜有人挨家串门，说的还是苗喜旺那笔钱。", "required_flags": ["砸钱普涨"]},
        {"block_id": "d29_hospital", "kind": "night", "text": "县医院昨天下午来了个外地口音的人，问哪个科室能查血里的铅。"},
    ]
    beats[29]["night_conditional_effects"] = [
        {
            "forbidden_flags": ["与钱伟撕破脸"],
            "effects": {"metric_deltas": {"corruption_evidence": [3, 8]}, "ledger_deltas": {}, "open_flags": ["攻守同盟已成"], "close_flags": []},
        },
        {
            "required_flags": ["与钱伟撕破脸"],
            "effects": {"metric_deltas": {"corruption_evidence": [0, 3]}, "ledger_deltas": {}, "open_flags": ["钱赵生隙"], "close_flags": []},
        },
        {
            "required_any_flags": ["已立项审计", "秘密摸底"],
            "effects": {"metric_deltas": {"corruption_evidence": [-8, -3]}, "ledger_deltas": {}, "open_flags": ["原件缺失"], "close_flags": []},
        },
        {
            "required_flags": ["砸钱普涨"],
            "effects": {"metric_deltas": {"social_stability": [-5, -2]}, "ledger_deltas": {}, "open_flags": ["攀比已成风"], "close_flags": []},
        },
    ]
    beats[46]["night_blocks"] = [
        {"block_id": "d46_public", "kind": "night", "text": "卫生院门口昨夜贴出了一张纸，天亮前被人围住。", "required_flags": ["普查结果公开"]},
        {"block_id": "d46_internal", "kind": "night", "text": "卫生院的灯昨夜亮到很晚，有护士在楼道里坐着没走。", "required_flags": ["普查结果内报"]},
        {"block_id": "d46_locked", "kind": "night", "text": "有一只柜子昨夜锁上了，钥匙在你身上。", "required_flags": ["普查结果压卷"]},
        {"block_id": "d46_city", "kind": "night", "text": "有一辆车昨夜出了县界，走的是往市里的路。", "required_flags": ["普查结果直送巡察组"]},
        {"block_id": "d46_village", "kind": "night", "text": "村口小卖部昨夜聚了人，散得很迟。", "required_flags": ["掌握血铅"]},
        {"block_id": "d46_office", "kind": "night", "text": "天亮了，办公楼的走廊比往常静。", "required_flags": ["巡察警觉"]},
    ]
    beats[48]["night_blocks"] = [
        {"block_id": "d48_family", "kind": "night", "text": "有几个受检的家长昨夜到村委会门口坐了一阵。", "required_flags": ["先告村民"]},
        {"block_id": "d48_factory", "kind": "night", "text": "宏达化工的门卫昨夜换了班，厂区的灯一直没关。", "required_flags": ["先告企业"]},
        {"block_id": "d48_sent", "kind": "night", "text": "有一份材料昨夜走了机要，往市里去。", "required_flags": ["巡察组前置"]},
        {"block_id": "d48_held", "kind": "night", "text": "有一份材料昨夜还压在你桌上。", "required_flags": ["巡察组后置"]},
        {"block_id": "d48_ledger", "kind": "night", "text": "有人昨夜把一本旧簿子翻到很晚。", "required_flags": ["台账已对"]},
        {"block_id": "d48_dark", "kind": "night", "text": "周家老屋昨夜没有点灯。", "required_flags": ["周家算账派拒谈"]},
    ]
    beats[57]["night_blocks"] = [{
        "block_id": "d57_zhao_visit", "kind": "night",
        "text": "第五十七日夜里十一点四十，你住的那栋楼下停了一辆车。车灯灭了很久，人才下来。",
    }]
    beats[58]["night_blocks"] = [{
        "block_id": "d58_dam", "kind": "night",
        "text": "夜里十一点四十，环保站的值班电话打进来。",
    }]
    beats[75]["night_blocks"] = [{
        "block_id": "d75_phone", "kind": "night",
        "text": "晚上九点十分,你办公室的座机响了。",
    }]
    beats[75]["night_conditional_effects"] = [
        {
            "required_flags": ["马长顺待自然触发"],
            "forbidden_flags": ["马长顺已入账", "进度榜已上墙"],
            "minimum_ledger_values": {"signed_households": 3},
            "effects": {
                "metric_deltas": {},
                "ledger_deltas": {"signed_households": [3, 3]},
                "open_flags": ["马长顺已入账"],
                "close_flags": ["马长顺待自然触发"],
            },
        },
        {
            "required_flags": ["马长顺待自然触发", "进度榜已上墙"],
            "forbidden_flags": ["马长顺已入账"],
            "minimum_ledger_values": {"signed_households": 1},
            "effects": {
                "metric_deltas": {},
                "ledger_deltas": {"signed_households": [3, 3]},
                "open_flags": ["马长顺已入账"],
                "close_flags": ["马长顺待自然触发"],
            },
        },
    ]
    beats[86]["night_blocks"] = [
        {
            "block_id": "d86_he_receipt", "kind": "night",
            "text": "何铁柱把那张二百八十七的化验单压在门房窗台上，转身前按下了手印。",
            "required_flags": ["血铅补实"], "forbidden_flags": ["何铁柱已入账"],
        },
        {
            "block_id": "d86_zhou_approval", "kind": "night",
            "text": "周大山把盖着红章的批文折好收进上衣口袋，族里当晚开了会。",
            "forbidden_flags": ["周大山归心已入账", "祖坟被冒犯"],
        },
    ]
    beats[86]["night_conditional_effects"] = [
        {
            "required_flags": ["血铅补实"],
            "forbidden_flags": ["何铁柱已入账"],
            "effects": {
                "metric_deltas": {"public_trust": [5, 10]},
                "ledger_deltas": {"signed_households": [4, 4]},
                "open_flags": ["何铁柱已入账"], "close_flags": [],
            },
        },
        {
            "required_flags": ["周大山预付已入账"],
            "forbidden_flags": ["周大山归心已入账", "祖坟被冒犯"],
            "effects": {
                "metric_deltas": {"political_credit": [-7, -4], "cadre_discontent": [8, 12]},
                "ledger_deltas": {"signed_households": [4, 4]},
                "open_flags": [
                    "周大山归心已入账", "周大山补账已入账", "祠堂地块批文已签发",
                ],
                "close_flags": ["祠堂地块只有口头承诺"],
            },
        },
        {
            "forbidden_flags": [
                "周大山预付已入账", "周大山归心已入账", "祖坟被冒犯",
            ],
            "effects": {
                "metric_deltas": {"political_credit": [-7, -4], "cadre_discontent": [8, 12]},
                "ledger_deltas": {"signed_households": [6, 6]},
                "open_flags": [
                    "周大山归心已入账", "周大山补账已入账", "祠堂地块批文已签发",
                ],
                "close_flags": ["祠堂地块只有口头承诺"],
            },
        },
    ]
    dump("story_beats.json", {"schema_version": 2, "beats": list(beats.values())})


def build_npc_profiles(lines: list[str]) -> None:
    document = json.loads((PACKAGE / "npc_profiles.json").read_text(encoding="utf-8"))
    profiles = {item["name"]: item for item in document["npcs"]}
    headings: list[tuple[int, str]] = []
    for index, raw in enumerate(lines):
        match = re.match(r"^####\s+([^：:]+)[：:]", raw.strip())
        if match and match.group(1).strip() in profiles:
            headings.append((index, match.group(1).strip()))
    role_profile_names = {name for _, name in headings}
    if len(role_profile_names) != 23:
        raise RuntimeError(f"母稿九维 NPC 数量应为 23，实际 {len(role_profile_names)}")
    for position, (start, name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else start + 1
        # 人物群像止于 5.10；最后一个人物段落不得吞入配角名录。
        next_section = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("### 5.10")),
            len(lines),
        )
        end = min(end, next_section)
        role_lines = [item.rstrip() for item in lines[start:end]]
        role_setting = "\n".join(role_lines).strip()
        profile = profiles[name]
        profile["profile_id"] = f"profile_{profile['npc_id'].removeprefix('npc_')}"
        profile["role_setting"] = role_setting
        profile["source_line"] = start + 1
    deep_names = {
        "张立", "赵建国", "钱伟", "刘三", "陈默", "石文斌",
        "周大山", "周奎元", "周满仓", "吴秀英", "何铁柱", "谭老六",
        "马长顺", "宁德海", "袁桂兰", "杨波", "老倔头", "苗喜旺", "邓守本",
    }
    limited_names = {
        "蒋崇岳", "郑向东", "孙强", "冯敬之", "贺兴邦",
        "罗健", "柯启年", "顾克明", "崔广林",
    }
    for name, profile in profiles.items():
        if name in deep_names:
            profile["state_tier"] = "deep"
        elif name in limited_names:
            profile["state_tier"] = "limited"
        elif name == "王芳":
            profile["state_tier"] = "ambient"
        else:
            raise RuntimeError(f"NPC 未进入 7.4.1 三档表：{name}")
    dump("npc_profiles.json", {
        "schema_version": 2,
        "npcs": list(profiles.values()),
    })


def build_facts_and_opportunities(lines: list[str]) -> None:
    fact_rows = [
        ("fact_clan_power_map", "柳林村宗族权力图", "fact", "周家、何家、杨家，面上一团和气，底下各有各的算盘。", "周家、何家、杨家"),
        ("fact_wu_independent_voice", "吴秀英的独立立场", "fact", "吴秀英不怕周大山，也看不惯他那套论资排辈、按姓分肥的老规矩。", "吴秀英是柳林村小学退休的老教师"),
        ("fact_connected_invoices", "三张连号发票", "clue", "三张发票开在同一天，抬头是三家不同的公司，金额加起来八十七万。", "金额加起来八十七万"),
        ("fact_original_vouchers", "四十一张原始凭证", "evidence", "四十一张原始凭证还在财政局的档案袋里。", "四十一张原始凭证"),
        ("fact_identical_reports", "三份一模一样的报告", "evidence", "三份一模一样的报告指向同一批被改写的监测口径。", "三份一模一样的报告"),
        ("fact_liu_old_ledger", "刘三的老账底稿", "evidence", "刘三答应把前年那本账的底稿留一份。", "前年那本账的底稿"),
        ("fact_shi_usb", "石文斌的优盘", "evidence", "全书的优盘只有一件，属于石文斌。", "全书的优盘只有一件"),
        ("fact_eia_original", "环评原始数据", "evidence", "环评的原始数据可以与公示版本交叉印证。", "环评的原始数据"),
        ("fact_water_sample", "封存水样", "evidence", "玩家手中的水样取自冲沟之前，具备证据效力。", "具备证据效力"),
        ("fact_lead_census", "血铅普查总表", "evidence", "受检 107、超一百微克每升 41、超两百 11、最高 287。", "受检 107"),
        ("fact_lead_287", "血铅二八七", "evidence", "何铁柱七岁孙子的检测值为 287 微克每升。", "287 微克每升"),
        ("fact_false_signing", "真假签约台账", "evidence", "账面签约户数与真实签约户数必须分开。", "账面签约户数"),
        ("fact_grave_protocol", "迁坟四件事", "fact", "择地、择日、起灵、祭祀延续，四件事一件不缺。", "择地、择日、起灵、祭祀延续"),
        ("fact_zhou_ledger_order", "周满仓的三环顺序", "fact", "先摊台账、再摆原始检测数据、最后才谈钱。", "先摊台账、再摆原始检测数据、最后才谈钱"),
        ("fact_two_million_fee", "两百万前期协调费", "evidence", "两百万前期协调费的凭证需要说明真实去处。", "两百万前期协调费"),
        ("fact_shell_house", "样板房与毛坯房", "evidence", "验收组看的样板房不能代替真正的毛坯房。", "真正的毛坯房"),
        ("fact_inspection_anchors", "三组检查时点", "fact", "市委巡察组第 31 日进驻、第 45 日撤离，环保迎检组第 59 日进驻。", "市委巡察组已于第 45 天撤离"),
        ("fact_total_households", "三十六户总盘", "fact", "全村总盘 36 户，不许改。目标线 30 户。", "全村总盘 36 户"),
    ]
    facts = []
    for fact_id, title, category, text_value, token in fact_rows:
        source_line = next(
            index for index, line in enumerate(lines, 1) if token in line
        )
        facts.append({
            "fact_id": fact_id,
            "title": title,
            "category": category,
            "text": text_value,
            "source_line": source_line,
        })
    dump("facts.json", {"schema_version": 2, "facts": facts})

    existing = json.loads(
        (PACKAGE / "interaction_opportunities.json").read_text(encoding="utf-8")
    )
    conversation_contexts = existing.get("conversation_contexts", {})
    preserved = {
        item["npc_id"]: item
        for item in existing["opportunities"]
        if item["npc_id"] in {"npc_wu_xiuying", "npc_zhou_dashan"}
    }
    profiles = json.loads((PACKAGE / "npc_profiles.json").read_text(encoding="utf-8"))["npcs"]
    windows = {
        "张立": (31, 89, "liaise_zhang_li"), "赵建国": (16, 89, "interview_cadre"),
        "钱伟": (18, 60, "interview_cadre"), "刘三": (20, 58, "private_testimony"),
        "陈默": (26, 83, "contact_reporter"), "石文斌": (22, 45, "private_testimony"),
        "蒋崇岳": (33, 87, "meet_party_secretary"), "郑向东": (3, 89, "zheng_clue_summary"),
        "孙强": (7, 45, "interview_cadre"), "冯敬之": (16, 80, "interview_cadre"),
        "贺兴邦": (46, 83, "interview_cadre"), "罗健": (31, 45, "cross_validate_clues"),
        "柯启年": (46, 60, "interview_cadre"), "顾克明": (59, 60, "interview_cadre"),
        "崔广林": (59, 60, "interview_cadre"), "王芳": (1, 90, "home_visit"),
    }
    evidence_people = {"刘三", "石文斌", "罗健", "陈默", "冯敬之", "贺兴邦"}
    officials = {"张立", "赵建国", "钱伟", "蒋崇岳", "郑向东", "孙强", "冯敬之", "贺兴邦", "柯启年", "顾克明", "崔广林"}
    opportunities = list(preserved.values())
    for profile in profiles:
        if profile["npc_id"] in preserved:
            continue
        name = profile["name"]
        day_min, day_max, action_id = windows.get(name, (3, 89, "home_visit"))
        if name in evidence_people:
            allowed = [item[0] for item in fact_rows if item[2] in {"clue", "evidence"}]
        elif name in officials:
            allowed = [
                "fact_connected_invoices", "fact_false_signing",
                "fact_two_million_fee", "fact_inspection_anchors",
            ]
        else:
            allowed = [
                "fact_clan_power_map", "fact_lead_287", "fact_grave_protocol",
                "fact_zhou_ledger_order", "fact_total_households",
            ]
        opportunities.append({
            "opportunity_id": f"opp_{day_min:02d}_{profile['npc_id'].removeprefix('npc_')}_contact",
            "npc_id": profile["npc_id"],
            "entry_type": "story_window",
            "day_min": day_min,
            "day_max": day_max,
            "action_id": action_id,
            "availability_mode": (
                "closed" if profile["state_tier"] == "ambient"
                else "limited" if profile["state_tier"] == "limited"
                else "free"
            ),
            "allowed_fact_ids": allowed,
        })
    opportunities.extend([
        {
            "opportunity_id": "opp_d53_tan_laoliu_paid_recovery",
            "npc_id": "npc_tan_laoliu",
            "entry_type": "scripted_recovery",
            "day_min": 53,
            "day_max": 60,
            "action_id": "home_visit",
            "availability_mode": "free",
            "requires_flags": ["谭老六被强压", "旧账缺口已坐实"],
            "closes_on_flags": ["谭老六已入账", "谭老六永久关闭"],
            "completion_effects": {
                "metric_deltas": {},
                "ledger_deltas": {"signed_households": [3, 3]},
                "open_flags": ["谭老六已安抚", "谭老六已入账"],
                "close_flags": [],
            },
        },
        {
            "opportunity_id": "opp_d55_yuan_guilan_paid_recovery",
            "npc_id": "npc_yuan_guilan",
            "entry_type": "scripted_recovery",
            "day_min": 55,
            "day_max": 60,
            "action_id": "welfare_medical_safety_net",
            "availability_mode": "free",
            "requires_flags": ["赔付换谅解在册", "复查费县垫"],
            "closes_on_flags": ["袁桂兰已入账"],
            "completion_effects": {
                "metric_deltas": {},
                "ledger_deltas": {"signed_households": [2, 2]},
                "open_flags": ["困难户帮扶", "袁桂兰已入账"],
                "close_flags": [],
            },
        },
        {
            "opportunity_id": "opp_d69_zhou_mancang_restart",
            "npc_id": "npc_zhou_mancang",
            "entry_type": "scripted_recovery",
            "day_min": 69,
            "day_max": 69,
            "action_id": "home_visit",
            "availability_mode": "free",
            "requires_flags": ["周满仓已冷"],
            "closes_on_flags": ["周满仓顺序已错", "周满仓已入账", "周满仓重启已付费"],
            "completion_flags": ["周满仓重启已付费"],
            "completion_decision_id": "dp5_04_recovery",
            "allowed_fact_ids": ["fact_zhou_ledger_order"],
        },
    ])
    dump("interaction_opportunities.json", {
        "schema_version": 2,
        "conversation_contexts": conversation_contexts,
        "opportunities": opportunities,
        "notes": "M2 为 29 名 NPC 登记故事窗口，并增加谭老六、袁桂兰、周满仓三条母稿指定的付费恢复入口；王芳为环境人物，不开放直接对话。",
    })


def first_line_number(lines: list[str], content_id: str) -> int:
    definition_patterns = (
        re.compile(rf"^\s*#+.*(?:^|\s){re.escape(content_id)}(?:\b|\s|·)"),
        re.compile(rf"编号(?:\*\*)?[：:]\s*{re.escape(content_id)}(?:\b|。|·)"),
    )
    for index, line in enumerate(lines, 1):
        if any(pattern.search(line) for pattern in definition_patterns):
            return index
    return next(index for index, line in enumerate(lines, 1) if content_id in line)


def build_catalog(lines: list[str], source_bytes: bytes) -> None:
    decisions = [f"DP{chapter}-{index:02d}" for chapter, count in DECISION_COUNTS.items()
                 for index in range(1, count + 1)]
    events = list(EVENT_IDS)
    dump("content_catalog.json", {
        "schema_version": 1,
        "source_file": "最终剧本.md",
        "source_sha256": f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        "decisions": [
            {
                "content_id": item,
                "chapter": int(item[2]),
                "source_line": first_line_number(lines, item),
            }
            for item in decisions
        ],
        "events": [
            {
                "content_id": item,
                "chapter": int(item[2]),
                "source_line": first_line_number(lines, item),
            }
            for item in events
        ],
    })


METRIC_NAMES = {
    "民众信任度": "public_trust", "民众信任": "public_trust",
    "社会稳定度": "social_stability", "社会稳定": "social_stability",
    "政治资本": "political_credit", "舆论压力": "media_pressure",
    "环评线索": "env_clue", "廉政清白指数": "integrity",
    "领导班子不满度": "cadre_discontent", "领导班子不满": "cadre_discontent",
    "腐败证据": "corruption_evidence",
}
LEDGER_NAMES = {
    "财政预算": "budget_remaining", "签约户数": "signed_households",
    "账面签约户数": "reported_signed_households",
}


def clean_line(value: str) -> str:
    return value.strip().lstrip("- ").replace("**", "").strip()


def parse_delta(text: str, label: str) -> tuple[int, int] | None:
    escaped = r"(?<!账面)签约户数" if label == "签约户数" else re.escape(label)
    if label == "签约户数":
        household = re.search(r"(?:带动[^。；]{0,30}?共|共)\s*(\d+)\s*户签约", text)
        if household:
            value = int(household.group(1))
            return value, value
        household = re.search(r"签约户数\s*(?:增量|增加|加|\+)\s*(\d+)\s*户?", text)
        if household:
            value = int(household.group(1))
            return value, value
    signed = re.search(
        rf"{escaped}[^。；]{{0,12}}?([+−-]\s*\d+)\s*(?:到|至)\s*([+−-]\s*\d+)", text
    )
    if signed:
        values = [int(item.replace("−", "-").replace(" ", "")) for item in signed.groups()]
        return min(values), max(values)
    directional = re.search(
        rf"{escaped}[^。；]{{0,8}}?(上升|增加|下降|减少)\s*(\d+)"
        rf"(?:\s*(?:到|至)\s*(\d+))?", text
    )
    if directional:
        direction, first, second = directional.groups()
        values = [int(first), int(second or first)]
        if direction in {"下降", "减少"}:
            values = [-item for item in values]
        return min(values), max(values)
    direct = re.search(rf"{escaped}\s*([+−-]\s*\d+)(?:\s*(?:到|至)\s*([+−-]\s*\d+))?", text)
    if direct:
        values = [
            int(item.replace("−", "-").replace(" ", ""))
            for item in direct.groups() if item is not None
        ]
        return min(values), max(values)
    window_match = re.search(rf"{escaped}([^。；]{{0,18}})", text)
    window = window_match.group(1) if window_match else ""
    if "重挫" in window:
        return (-30, -30)
    if any(word in window for word in ("大幅下降", "大跌", "骤降")):
        return (-20, -20)
    if any(word in window for word in ("下降", "下跌", "降低", "降")):
        return (-10, -10)
    if any(word in window for word in ("大幅上升", "大涨", "上冲")):
        return (20, 20)
    if any(word in window for word in ("上升", "上涨", "升", "涨")):
        return (10, 10)
    return None


def parse_flags(text: str, action: str) -> set[str]:
    result: set[str] = set()
    for match in re.finditer(rf"{action}旗标[：:]([^。；]+)", text):
        value = match.group(1)
        value = re.split(r"开启旗标|关闭旗标|后果叙事|若|连锁|签约户数|财政预算|民众|社会|政治|舆论|领导|廉政|环评", value)[0]
        # Mother-script prose after a Chinese comma explains the flag; flag
        # lists themselves use the enumeration comma ``、``.
        value = re.split(r"[，,]", value)[0]
        for item in re.split(r"[、，,]|以及|和", value):
            item = item.strip(" 「」『』。；")
            if item and len(item) <= 30:
                result.add(item)
    return result


def effects_from(text: str) -> dict:
    metric_deltas = {}
    ledger_deltas = {}
    for label, field in METRIC_NAMES.items():
        delta = parse_delta(text, label)
        if delta is not None and field not in metric_deltas:
            metric_deltas[field] = list(delta)
    for label, field in LEDGER_NAMES.items():
        delta = parse_delta(text, label)
        if delta is not None and field not in ledger_deltas:
            ledger_deltas[field] = list(delta)
    open_flags = parse_flags(text, "开启")
    close_flags = parse_flags(text, "关闭") - open_flags
    return {
        "metric_deltas": metric_deltas,
        "ledger_deltas": ledger_deltas,
        "open_flags": sorted(open_flags),
        "close_flags": sorted(close_flags),
        "state_assignments": {},
    }


def sorting_options(section: list[str], source_id: str, visible_text: dict[str, str]) -> list[dict]:
    expected_letters = {
        "DP2-08": "ABCD", "DP3-07": "ABCD",
        "DP4-02": "ABCDE", "DP5-12": "ABCDE", "DP6-07": "ABCDE",
    }
    letters = list(expected_letters.get(source_id, "")) or sorted(visible_text)
    if not 3 <= len(letters) <= 5:
        letters = list("ABCD")
    cleaned = [clean_line(item) for item in section]

    def named(prefix: str) -> str:
        return next((line for line in cleaned if line.startswith(prefix)), "")

    def positioned(letter: str, position: int) -> str:
        if source_id == "DP2-08":
            prefixes = {
                "A": "签约攻坚线结算", "B": "反腐查案线结算",
                "C": "环评真相线结算", "D": "民生维稳线结算",
            }
            line = named(prefixes[letter])
            if position not in {0, len(letters) - 1}:
                return ""
            marker = "排首位" if position == 0 else "排末位"
            part = next((item for item in line.split("。") if marker in item), "")
            return part + "。" if part else line
        start = next(
            (index for index, line in enumerate(cleaned)
             if re.match(rf"选项\s*{letter}(?:·|\s)", line)),
            -1,
        )
        if start < 0:
            return ""
        end = next(
            (index for index in range(start + 1, len(cleaned))
             if re.match(r"选项\s*[A-E](?:·|\s)", cleaned[index])),
            len(cleaned),
        )
        labels = {0: "位次一", 1: "位次二", 2: "位次三", 3: "末位"}
        label = labels[min(position, 3)]
        return next(
            (line for line in cleaned[start:end] if line.startswith(label)), ""
        )

    options = []
    for permutation in itertools.permutations(letters):
        selected: list[str] = []
        if source_id == "DP4-02":
            selected.append(named("先告村民结算") if permutation.index("A") < permutation.index("C") else named("先告企业结算"))
            selected.append(named("巡察组后置结算") if permutation.index("D") > permutation.index("C") else named("巡察组前置结算"))
        elif source_id == "DP5-12":
            selected.append(named("民生优先结算") if permutation.index("A") < permutation.index("E") else named("政治优先结算"))
            selected.append(named("上级押后结算") if permutation.index("E") > permutation.index("B") else named("上级前置结算"))
        elif source_id == "DP6-07":
            start = next((i for i, line in enumerate(cleaned) if line.startswith(f"首位为 {permutation[0]}")), -1)
            end = next((i for i in range(start + 1, len(cleaned)) if cleaned[i].startswith("首位为 ")), len(cleaned)) if start >= 0 else -1
            selected.extend(cleaned[start:end] if start >= 0 else [])
        else:
            selected.extend(positioned(letter, index) for index, letter in enumerate(permutation))
        settlement = "；".join(item for item in selected if item)
        consequence_match = re.search(r"后果叙事[：:]([^。]+)", settlement)
        if consequence_match:
            consequence = consequence_match.group(1).strip()
        else:
            source_sentence = next((item for item in selected if item), "做决定不花行动点。")
            first_sentence = source_sentence.split("。", 1)[0].strip()
            first_sentence = re.sub(r"^.*?结算[：:]\s*", "", first_sentence)
            consequence = f"{first_sentence}。" if "。" in source_sentence else first_sentence
        options.append({
            "option_id": "_".join(item.lower() for item in permutation),
            "text": " > ".join(permutation),
            "consequence": consequence,
            "effects": effects_from(settlement),
        })
    return options


def extract_options(section: list[str], source_id: str) -> list[dict]:
    visible_text: dict[str, str] = {}
    settlements: dict[str, str] = {}
    current_letter: str | None = None
    current_lines: list[str] = []
    in_code = True

    def flush() -> None:
        nonlocal current_letter, current_lines
        if current_letter and current_lines:
            # Preserve source-line clause boundaries.  A plain-space join lets
            # the flag parser swallow the following settlement or guard line.
            settlements.setdefault(current_letter, "；".join(current_lines))
        current_letter = None
        current_lines = []

    for raw in section:
        line = clean_line(raw)
        if raw.strip().startswith(":::玩家"):
            in_code = False
            flush()
            continue
        if raw.strip().startswith(":::代码"):
            in_code = True
            continue
        player = re.match(
            r"(?:选项\s*)?([A-E])(?:〔[^〕]+〕)?(?:[·.．、：:]|\s|　)+(.+)",
            line,
        )
        if player and "结算" not in line and len(player.group(2)) <= 180:
            visible_text.setdefault(player.group(1), player.group(2).strip())
        if not in_code:
            continue
        settlement = re.match(
            r"选项\s*([A-E])\s*(?:结算|后果)(?:·[^：:]+|〔[^〕]+〕)?[：:]?\s*(.*)",
            line,
        )
        first_place = re.match(r"首位为\s*([A-E])(?:（[^）]+）)?[：:]?\s*(.*)", line)
        code_option = re.match(r"选项\s*([A-E])(?:\s|　|[：:])+\s*(.*)", line)
        if settlement or first_place or code_option:
            flush()
            match = settlement or first_place or code_option
            current_letter = match.group(1)
            current_lines = [line]
            continue
        if current_letter:
            if re.match(r"(?:共同结算|判定系数|结局权重|研究记录|:::|##)", line):
                flush()
            elif line:
                current_lines.append(line)
    flush()
    if source_id in {"DP2-08", "DP3-07", "DP4-02", "DP5-12", "DP6-07"}:
        return sorting_options(section, source_id, visible_text)
    letters = sorted(set(settlements))
    if len(letters) < 2:
        letters = sorted(set(visible_text))
    if len(letters) < 2:
        letters = ["A", "B", "C", "D"]
    if source_id == "DP2-10":
        prefixes = {
            "A": "签约补偿项结算", "B": "民生安抚项结算",
            "C": "环评复检项结算", "D": "应急维稳项结算",
        }
        source_lines = [clean_line(item) for item in section]
        settlements = {
            letter: next(
                (line for line in source_lines if line.startswith(prefix)),
                "本决定不消耗行动点。",
            )
            for letter, prefix in prefixes.items()
        }
        letters = list("ABCD")
    options = []
    for letter in letters:
        settlement = settlements.get(letter, "做决定不花行动点。")
        consequence_match = re.search(r"后果叙事[：:]([^。]+)", settlement)
        if consequence_match:
            consequence = consequence_match.group(1).strip()
        else:
            first_sentence = settlement.split("。", 1)[0].strip()
            first_sentence = re.sub(r"^.*?结算[：:]\s*", "", first_sentence)
            if re.match(rf"^选项\s*{letter}(?:\s|　|[：:])", settlement) and "结算" not in settlement.split(" ", 1)[0]:
                consequence = visible_text.get(letter, first_sentence)
            else:
                consequence = f"{first_sentence}。" if "。" in settlement else first_sentence
        options.append({
            "option_id": letter.lower(),
            "text": visible_text.get(letter, f"选项{letter}"),
            "consequence": consequence,
            "effects": effects_from(settlement),
        })
    return options


def build_runtime_decisions(lines: list[str]) -> set[str]:
    ids = [f"DP{chapter}-{index:02d}" for chapter, count in DECISION_COUNTS.items()
           for index in range(1, count + 1)] + list(EVENT_IDS)
    positions = sorted((first_line_number(lines, item), item) for item in ids)
    documents = []
    extra_flags: set[str] = set()
    preserved = json.loads((PACKAGE / "decisions.json").read_text(encoding="utf-8"))
    preserved_by_id = {item["decision_id"]: item for item in preserved["decisions"]}
    special_ids = {
        "DP1-01": "dp1_01_taskforce_faction_map",
        "EV1-01": "ev1_01_reception_bag",
    }
    for index, (line_no, source_id) in enumerate(positions):
        decision_id = special_ids.get(source_id, source_id.lower().replace("-", "_"))
        if source_id in special_ids:
            document = preserved_by_id[decision_id]
        else:
            end = positions[index + 1][0] - 1 if index + 1 < len(positions) else len(lines)
            previous_line = positions[index - 1][0] if index else 1
            code_index = next(
                (
                    position
                    for position in range(line_no - 1, min(end, line_no + 120))
                    if re.search(
                        rf"(?:编号|节点编号)[^\n]*{re.escape(source_id)}\b",
                        lines[position],
                    )
                ),
                line_no - 1,
            )
            forward_players = [
                position
                for position in range(line_no - 1, code_index)
                if lines[position].strip().startswith(":::玩家")
            ]
            if forward_players:
                player_start = forward_players[-1]
            else:
                player_start = next(
                    (
                        position
                        for position in range(line_no - 2, previous_line - 2, -1)
                        if lines[position].strip().startswith(":::玩家")
                    ),
                    line_no - 1,
                )
            section = lines[player_start:end]
            heading_line = next(
                (
                    lines[position]
                    for position in range(line_no - 2, previous_line - 2, -1)
                    if lines[position].lstrip().startswith("#")
                ),
                source_id,
            )
            heading = clean_line(heading_line).lstrip("# ")
            title = heading.split("。", 1)[0]
            if title == source_id:
                title = f"{source_id}·编号决策"
            day = (
                EVENT_DAYS[source_id]
                if source_id in EVENT_DAYS
                else DECISION_DAY_BY_ID[source_id]
            )
            document = {
                "decision_id": decision_id,
                "story_day": day,
                "title": title,
                "prompt": "本决定不消耗行动点。",
                "action_point_cost": 0,
                "skippable": False,
                "options": extract_options(section, source_id),
            }
            if source_id in {"DP2-08", "DP3-07", "DP4-02", "DP5-12", "DP6-07"}:
                document["input_kind"] = "sorting"
                document["input_schema"] = {
                    "items": sorted({
                        part
                        for option in document["options"]
                        for part in option["option_id"].split("_")
                    })
                }
            if source_id == "DP2-10":
                document["input_kind"] = "allocation"
                document["input_schema"] = {
                    "total": 150,
                    "unit": "万元",
                    "fields": [
                        "signing_compensation",
                        "livelihood_support",
                        "environmental_retest",
                        "emergency_stability",
                    ],
                    "labels": {
                        "signing_compensation": "签约补偿",
                        "livelihood_support": "民生安抚",
                        "environmental_retest": "环评复检",
                        "emergency_stability": "应急维稳",
                    },
                }
                document["options"] = [{
                    "option_id": "submit",
                    "text": "分完为止，一分也不能剩。",
                    "consequence": "周转金投向已定",
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {},
                        "open_flags": ["周转金投向已定"],
                        "close_flags": [],
                    },
                }]
            if source_id == "DP5-04":
                document["required_flags"] = ["村账在手"]
            if source_id == "DP5-05":
                document["required_flags"] = ["村账已摊"]
            if source_id == "DP4-05":
                document["required_flags"] = ["周氏松口"]
            if source_id == "DP4-08":
                document["required_flags"] = ["掌握血铅"]
            if source_id == "DP2-07":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_b = next(item for item in document["options"] if item["option_id"] == "b")
                option_a["effects"]["ledger_deltas"] = {}
                option_a["effects"]["close_flags"] = []
                option_a["conditional_effects"] = [{
                    "required_flags": ["虚假签约"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"reported_signed_households": [-4, -2]},
                        "open_flags": [],
                        "close_flags": ["虚假签约"],
                    },
                }]
                option_b["effects"]["ledger_deltas"].pop("signed_households", None)
            if source_id == "DP5-06":
                by_id = {item["option_id"]: item for item in document["options"]}
                option_a = by_id["a"]
                option_a["effects"] = {
                    "metric_deltas": {}, "ledger_deltas": {},
                    "open_flags": [], "close_flags": [], "state_assignments": {},
                }
                option_a["conditional_effects"] = [
                    {
                        "required_flags": ["代签已查实"],
                        "forbidden_flags": ["宁德海已入账"],
                        "effects": {
                            "metric_deltas": {
                                "public_trust": [10, 10],
                                "social_stability": [10, 10],
                                "cadre_discontent": [10, 10],
                            },
                            "ledger_deltas": {"signed_households": [2, 2]},
                            "open_flags": ["宁德海已入账", "程序已正", "党员样板已立"],
                            "close_flags": [],
                        },
                    },
                    {
                        "forbidden_flags": ["代签已查实", "宁德海已入账"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {},
                            "open_flags": ["宁德海观望"], "close_flags": [],
                        },
                    },
                ]
                by_id["e"]["effects"]["ledger_deltas"] = {}
                by_id["e"]["effects"]["open_flags"] = ["宁德海观望"]
            if source_id == "DP4-05":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["effects"]["ledger_deltas"] = {}
                option_a["effects"]["open_flags"] = ["周大山归心", "祠堂地块批文已签发"]
                option_a["conditional_effects"] = [{
                    "forbidden_flags": ["周大山归心已入账"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"signed_households": [4, 4]},
                        "open_flags": ["周大山归心已入账"], "close_flags": [],
                    },
                }]
            if source_id == "DP4-06":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["effects"] = {
                    "metric_deltas": {}, "ledger_deltas": {},
                    "open_flags": [], "close_flags": [], "state_assignments": {},
                }
                option_a["conditional_effects"] = [
                    {
                        "required_flags": ["旧账缺口已坐实"],
                        "forbidden_flags": ["谭老六已入账", "谭老六被强压"],
                        "effects": {
                            "metric_deltas": {"public_trust": [5, 10], "media_pressure": [-10, -5]},
                            "ledger_deltas": {"signed_households": [3, 3]},
                            "open_flags": ["谭老六已安抚", "谭老六已入账"], "close_flags": [],
                        },
                    },
                    {
                        "forbidden_flags": ["旧账缺口已坐实", "谭老六已入账"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {},
                            "open_flags": ["谭老六被空口应付"], "close_flags": [],
                        },
                    },
                ]
            if source_id == "EV4-01":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["effects"]["ledger_deltas"].pop("signed_households", None)
                option_a["effects"]["open_flags"] = ["复查费县垫"]
                option_a["conditional_effects"] = [{
                    "forbidden_flags": ["袁桂兰已入账", "赔付换谅解在册"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"signed_households": [2, 2]},
                        "open_flags": ["袁桂兰已入账"], "close_flags": [],
                    },
                }]
            if source_id == "DP4-07":
                by_id = {item["option_id"]: item for item in document["options"]}
                for option_id in ("a", "b"):
                    option = by_id[option_id]
                    option["conditional_effects"] = [{
                        "required_flags": ["普查结果公开"],
                        "forbidden_flags": ["吴秀英已入账"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [6, 6]},
                            "open_flags": ["吴秀英已入账"], "close_flags": [],
                        },
                    }]
                option_e = by_id["e"]
                fallback = copy.deepcopy(by_id["c"]["effects"])
                fallback["open_flags"] = []
                fallback["close_flags"] = []
                option_e.setdefault("conditional_effects", []).append({
                    "replace_base": True,
                    "required_flags": ["门诊楼前被清场"],
                    "effects": fallback,
                })
            if source_id == "DP4-08":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["effects"]["ledger_deltas"].pop("signed_households", None)
                option_a["effects"]["open_flags"] = ["医疗兜底已启动", "三年前压检已认"]
                option_a["conditional_effects"] = [
                    {
                        "required_flags": ["吴秀英已入账", "谭老六已安抚"],
                        "forbidden_flags": ["何铁柱已入账", "杨波已入账", "杨波已被宏达绑定"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [6, 6]},
                            "open_flags": ["何铁柱已入账", "杨波已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["吴秀英已入账", "杨波已被宏达绑定"],
                        "forbidden_flags": ["何铁柱已入账", "杨波已入账"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [6, 6]},
                            "open_flags": ["何铁柱已入账", "杨波已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["吴秀英已入账", "杨波已入账"],
                        "forbidden_flags": ["何铁柱已入账"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [4, 4]},
                            "open_flags": ["何铁柱已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["吴秀英已入账"],
                        "forbidden_flags": [
                            "何铁柱已入账", "杨波已入账", "谭老六已安抚", "杨波已被宏达绑定",
                        ],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [4, 4]},
                            "open_flags": ["何铁柱已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["何铁柱已入账", "谭老六已安抚"],
                        "forbidden_flags": ["杨波已入账", "杨波已被宏达绑定"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [2, 2]},
                            "open_flags": ["杨波已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["何铁柱已入账", "杨波已被宏达绑定"],
                        "forbidden_flags": ["杨波已入账"],
                        "effects": {
                            "metric_deltas": {}, "ledger_deltas": {"signed_households": [2, 2]},
                            "open_flags": ["杨波已入账"], "close_flags": [],
                        },
                    },
                ]
            if source_id == "DP5-07":
                by_id = {item["option_id"]: item for item in document["options"]}
                for option_id, required_flag in {
                    "a": "党员样板已立", "b": "宁德海已入账", "e": "党员样板已立",
                }.items():
                    option = by_id[option_id]
                    option["effects"]["ledger_deltas"].pop("signed_households", None)
                    option["effects"]["open_flags"] = [
                        flag for flag in option["effects"]["open_flags"]
                        if flag != "马长顺已入账"
                    ]
                    option["conditional_effects"] = [{
                        "required_flags": [required_flag],
                        "forbidden_flags": ["马长顺已入账"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [3, 3]},
                            "open_flags": ["马长顺已入账"], "close_flags": [],
                        },
                    }]
                option_c = by_id["c"]
                option_c["effects"]["ledger_deltas"].pop("signed_households", None)
                option_c["effects"]["open_flags"] = ["差异化口子已开"]
                option_c["conditional_effects"] = [{
                    "forbidden_flags": ["马长顺已入账"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"signed_households": [3, 3]},
                        "open_flags": ["马长顺已入账"], "close_flags": [],
                    },
                }]
                option_d = by_id["d"]
                option_d["effects"]["ledger_deltas"] = {}
                option_d["effects"]["open_flags"] = ["一碗水端平", "马长顺待自然触发"]
            if source_id == "DP5-08":
                by_id = {item["option_id"]: item for item in document["options"]}
                option_a = by_id["a"]
                option_a["effects"]["open_flags"] = ["治疗兜底承诺"]
                option_a["effects"]["ledger_deltas"].pop("signed_households", None)
                option_a["conditional_effects"] = [{
                    "forbidden_flags": ["何铁柱已入账"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"signed_households": [4, 4]},
                        "open_flags": ["何铁柱已入账"], "close_flags": [],
                    },
                }]
                option_b = by_id["b"]
                option_b["effects"] = {
                    "metric_deltas": {}, "ledger_deltas": {},
                    "open_flags": [], "close_flags": [], "state_assignments": {},
                }
                option_b["conditional_effects"] = [
                    {
                        "required_flags": ["何铁柱欠你一个人情"],
                        "forbidden_flags": ["何铁柱已入账"],
                        "effects": {
                            "metric_deltas": {
                                "public_trust": [-5, -3], "political_credit": [2, 4]
                            },
                            "ledger_deltas": {"signed_households": [4, 4]},
                            "open_flags": ["人情已兑现", "何铁柱已入账"],
                            "close_flags": ["何铁柱欠你一个人情"],
                        },
                    },
                    {
                        "forbidden_flags": ["何铁柱欠你一个人情", "何铁柱已入账"],
                        "effects": {
                            "metric_deltas": {
                                "public_trust": [-9, -6], "social_stability": [-4, -2]
                            },
                            "ledger_deltas": {},
                            "open_flags": ["何铁柱已冷"], "close_flags": [],
                        },
                    },
                ]
            if source_id == "DP5-09":
                by_id = {item["option_id"]: item for item in document["options"]}
                for option_id in ("a", "b"):
                    option = by_id[option_id]
                    option["effects"]["ledger_deltas"].pop("signed_households", None)
                    option["effects"]["open_flags"] = [
                        flag for flag in option["effects"]["open_flags"]
                        if flag not in {"周大山归心已入账", "周大山补账已入账"}
                    ]
                    common_forbidden = ["周大山归心已入账"]
                    if option_id == "b":
                        common_forbidden.append("周大山被压价")
                    option["conditional_effects"] = [
                        {
                            "required_flags": ["周大山预付已入账"],
                            "forbidden_flags": common_forbidden,
                            "effects": {
                                "metric_deltas": {},
                                "ledger_deltas": {"signed_households": [4, 4]},
                                "open_flags": ["周大山归心已入账", "周大山补账已入账"],
                                "close_flags": [],
                            },
                        },
                        {
                            "forbidden_flags": [*common_forbidden, "周大山预付已入账"],
                            "effects": {
                                "metric_deltas": {},
                                "ledger_deltas": {"signed_households": [6, 6]},
                                "open_flags": ["周大山归心已入账", "周大山补账已入账"],
                                "close_flags": [],
                            },
                        },
                    ]
                option_b = by_id["b"]
                success_metrics = {
                    "public_trust": [3, 5], "integrity": [-8, -5],
                    "cadre_discontent": [2, 4],
                }
                option_b["effects"] = {
                    "metric_deltas": {}, "ledger_deltas": {},
                    "open_flags": [], "close_flags": [], "state_assignments": {},
                }
                option_b["conditional_effects"] = [
                    {
                        "required_flags": ["周大山预付已入账"],
                        "forbidden_flags": ["周大山归心已入账", "周大山被压价"],
                        "effects": {
                            "metric_deltas": success_metrics,
                            "ledger_deltas": {"signed_households": [4, 4]},
                            "open_flags": [
                                "周大山归心已入账", "周大山补账已入账",
                                "祠堂地块批文已签发", "用地手续有瑕疵",
                            ],
                            "close_flags": ["祠堂地块只有口头承诺"],
                        },
                    },
                    {
                        "forbidden_flags": [
                            "周大山归心已入账", "周大山被压价", "周大山预付已入账",
                        ],
                        "effects": {
                            "metric_deltas": success_metrics,
                            "ledger_deltas": {"signed_households": [6, 6]},
                            "open_flags": [
                                "周大山归心已入账", "周大山补账已入账",
                                "祠堂地块批文已签发", "用地手续有瑕疵",
                            ],
                            "close_flags": ["祠堂地块只有口头承诺"],
                        },
                    },
                    {
                        "required_flags": ["周大山被压价"],
                        "forbidden_flags": ["周大山归心已入账"],
                        "effects": {
                            "metric_deltas": {
                                "public_trust": [-8, -5], "social_stability": [-4, -2]
                            },
                            "ledger_deltas": {}, "open_flags": ["周大山已寒心"],
                            "close_flags": [],
                        },
                    },
                ]
            if source_id == "DP6-02":
                option_b = next(item for item in document["options"] if item["option_id"] == "b")
                option_b["effects"]["open_flags"] = ["样板签约"]
                option_b["effects"]["ledger_deltas"] = {}
                option_b["conditional_effects"] = [
                    {
                        "forbidden_flags": ["老倔头已入账", "差异化口子已开"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [1, 1]},
                            "open_flags": ["老倔头已入账"], "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["差异化口子已开", "进度榜已上墙"],
                        "forbidden_flags": ["老倔头已入账"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [1, 1]},
                            "open_flags": ["老倔头已入账"], "close_flags": [],
                        },
                    },
                ]
            if source_id == "DP6-03":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_c = next(item for item in document["options"] if item["option_id"] == "c")
                option_a["effects"]["open_flags"] = ["血铅补实"]
                option_a["effects"]["close_flags"] = []
                option_a["effects"]["ledger_deltas"] = {}
                # 守卫二等价于：未开差异化口子，或已上墙进度榜。拆成两个
                # 互斥分支，避免两项同时为真时重复加户。
                option_a["conditional_effects"] = [
                    {
                        "forbidden_flags": ["苗喜旺已入账", "差异化口子已开"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [1, 1]},
                            "open_flags": ["苗喜旺已入账"],
                            "close_flags": [],
                        },
                    },
                    {
                        "required_flags": ["差异化口子已开", "进度榜已上墙"],
                        "forbidden_flags": ["苗喜旺已入账"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [1, 1]},
                            "open_flags": ["苗喜旺已入账"],
                            "close_flags": [],
                        },
                    },
                ]
                option_c["effects"]["open_flags"] = ["环评被掩盖"]
                option_c["effects"]["state_assignments"] = {
                    "lead_roster_disposition": "己方封存"
                }
            if source_id == "EV6-01":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["effects"]["open_flags"] = ["俯身接怨"]
                option_a["effects"]["ledger_deltas"] = {}
                option_a["conditional_effects"] = [{
                    "forbidden_flags": ["宁德海已入账", "宁德海线已锁死"],
                    "effects": {
                        "metric_deltas": {},
                        "ledger_deltas": {"signed_households": [2, 2]},
                        "open_flags": ["宁德海已入账"], "close_flags": [],
                    },
                }]
            if source_id == "DP6-06":
                by_id = {item["option_id"]: item for item in document["options"]}
                option_a = by_id["a"]
                option_a["required_state_values"] = {
                    "lead_roster_disposition": "己方封存"
                }
                option_a["unavailable_reason"] = "名册不在你手上，你给不出你没有的东西。"
                option_a["effects"]["open_flags"] = ["记者结盟"]
                option_a["effects"]["close_flags"] = ["压制媒体"]
                option_a["effects"]["state_assignments"] = {
                    "lead_roster_disposition": "交给记者"
                }
                by_id["b"]["effects"]["open_flags"] = ["记者结盟"]
                by_id["b"]["effects"]["close_flags"] = ["压制媒体"]
                by_id["c"]["effects"]["open_flags"] = ["压制媒体", "面子优先"]
                by_id["c"]["effects"]["close_flags"] = ["记者结盟"]
                by_id["d"]["effects"]["open_flags"] = []
                by_id["d"]["effects"]["close_flags"] = []
                by_id["e"]["effects"]["open_flags"] = ["蒋崇岳知情", "面子优先"]
                by_id["e"]["effects"]["close_flags"] = ["记者结盟"]
            if source_id == "EV6-02":
                by_id = {item["option_id"]: item for item in document["options"]}
                option_a = by_id["a"]
                option_a["forbidden_flags"] = ["环评被掩盖"]
                option_a["availability_any"] = [
                    {"required_flags": ["血铅补实"]},
                    {"required_flags": ["毛坯据实"]},
                    {"forbidden_state_values": {
                        "lead_roster_disposition": ["未获取"]
                    }},
                ]
                option_a["unavailable_reason"] = (
                    "手里没有一份能公开的原始件，通稿一发就要被追问出处。"
                )
                option_a["effects"]["open_flags"] = ["环评揭穿", "舆情公开"]
                option_a["effects"]["close_flags"] = ["面子优先"]
                by_id["b"]["effects"]["open_flags"] = ["面子优先"]
                by_id["b"]["effects"]["close_flags"] = ["环评揭穿"]
                by_id["c"]["effects"]["open_flags"] = ["压制媒体", "面子优先"]
                by_id["c"]["effects"]["close_flags"] = ["环评揭穿"]
            if source_id == "DP6-07":
                by_option_id = {
                    option["option_id"]: option for option in document["options"]
                }
                for option in document["options"]:
                    if not option["option_id"].startswith("a_"):
                        continue
                    option["effects"]["ledger_deltas"] = {}
                    option["effects"]["open_flags"] = [
                        flag for flag in option["effects"]["open_flags"]
                        if flag != "邓守本已入账"
                    ]
                    option["conditional_effects"] = [{
                        "forbidden_flags": ["邓守本已入账"],
                        "effects": {
                            "metric_deltas": {},
                            "ledger_deltas": {"signed_households": [1, 1]},
                            "open_flags": ["邓守本已入账"], "close_flags": [],
                        },
                    }]
                # B（环评复评）只有在“环评揭穿”为真时才能成为实际首位；
                # 否则母稿要求忽略 B，改读次位。先把次位的完整效果复制为
                # 基线，再用 replace_base 条件分支切换为 B 的效果。
                b_template = next(
                    option for option in document["options"]
                    if option["option_id"].startswith("b_")
                )
                b_effects = copy.deepcopy(b_template["effects"])
                for option in document["options"]:
                    parts = option["option_id"].split("_")
                    if parts[0] != "b":
                        continue
                    fallback_letter = parts[1]
                    fallback = next(
                        candidate for candidate in document["options"]
                        if candidate["option_id"].startswith(f"{fallback_letter}_")
                    )
                    option["effects"] = copy.deepcopy(fallback["effects"])
                    option["conditional_effects"] = copy.deepcopy(
                        fallback.get("conditional_effects", [])
                    )
                    for branch in option["conditional_effects"]:
                        branch.setdefault("forbidden_flags", []).append("环评揭穿")
                    option["conditional_effects"].append({
                        "replace_base": True,
                        "required_flags": ["环评揭穿"],
                        "effects": b_effects,
                    })
            if source_id == "DP1-05":
                option_c = next(item for item in document["options"] if item["option_id"] == "c")
                half_effects = copy.deepcopy(option_c["effects"])
                half_effects["metric_deltas"] = {
                    key: [int(low / 2), int(high / 2)]
                    for key, (low, high) in half_effects["metric_deltas"].items()
                }
                option_c.setdefault("conditional_effects", []).append({
                    "replace_base": True,
                    "forbidden_flags": ["血铅疑云·初闻"],
                    "effects": half_effects,
                })
            if source_id == "EV1-02":
                by_id = {item["option_id"]: item for item in document["options"]}
                for option_id in ("a", "c"):
                    by_id[option_id]["maximum_ledger_values"] = {
                        "chapter_overtime_count": 2
                    }
                    by_id[option_id]["unavailable_reason"] = "连续加班已经耗尽现场处置余地。"
            if source_id == "DP2-02":
                document["presentation_blocks"] = [{
                    "block_id": "dp2_02_phone_pressure_variant",
                    "kind": "narration",
                    "text": "钱伟没有登门。电话由赵建国转进来，原本放在桌面的那条交易路径已经断了。",
                    "required_flags": ["与钱伟撕破脸"],
                }]
            if source_id == "DP2-03":
                document["early_day"] = 19
                document["early_required_flags"] = ["压下账目"]
            if source_id == "DP2-04":
                document["early_day"] = 21
                document["early_required_flags"] = ["已立项审计"]
                document["presentation_blocks"] = [{
                    "block_id": "dp2_04_audit_early_variant",
                    "kind": "narration",
                    "text": "审计已经立项，石文斌比原定时间早一天到访，并提醒你三份报告背后的原始口径正在被人收走。",
                    "required_flags": ["已立项审计"],
                }]
            if source_id == "DP4-08":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a["minimum_ledger_values"] = {"budget_remaining": 22}
                option_a["unavailable_reason"] = "当前财政预算不足以启动医疗兜底。"
            if source_id == "EV4-04":
                option_b = next(item for item in document["options"] if item["option_id"] == "b")
                option_b["forbidden_flags"] = ["围堰漫溢未处置"]
                option_b["unavailable_reason"] = "水还在漫，四十分钟收拾不完。"
            if source_id == "DP5-10":
                by_id = {item["option_id"]: item for item in document["options"]}
                attitudes = ["蒋崇岳背书", "蒋崇岳默许", "蒋崇岳弃保", "蒋崇岳否决"]
                option_a = by_id["a"]
                option_a["effects"]["open_flags"] = ["蒋崇岳知情"]
                option_a["effects"]["close_flags"] = []
                option_a["conditional_effects"] = [
                    {
                        "required_flags": ["掌握血铅", "旧账缺口已坐实"],
                        "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": ["蒋崇岳背书"], "close_flags": []},
                    },
                    {
                        "required_flags": ["掌握血铅"],
                        "forbidden_flags": ["旧账缺口已坐实"],
                        "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": ["蒋崇岳默许"], "close_flags": []},
                    },
                    {
                        "required_flags": ["旧账缺口已坐实"],
                        "forbidden_flags": ["掌握血铅"],
                        "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": ["蒋崇岳默许"], "close_flags": []},
                    },
                    {
                        "forbidden_flags": ["掌握血铅", "旧账缺口已坐实"],
                        "effects": {"metric_deltas": {"political_credit": [-4, -2]}, "ledger_deltas": {}, "open_flags": ["蒋崇岳弃保"], "close_flags": []},
                    },
                ]
                for option_id in ("b", "c", "d"):
                    by_id[option_id]["effects"]["open_flags"] = [
                        "蒋崇岳知情", "蒋崇岳弃保",
                        *(["私交已耗尽"] if option_id == "d" else []),
                    ]
                    by_id[option_id]["effects"]["close_flags"] = [
                        "蒋崇岳背书", "蒋崇岳默许", "蒋崇岳否决"
                    ]
                by_id["e"]["effects"]["open_flags"] = ["实情未上桌"]
                by_id["e"]["effects"]["close_flags"] = []
            if source_id == "DP5-06":
                by_id = {item["option_id"]: item for item in document["options"]}
                option_a = by_id["a"]
                option_a.setdefault("conditional_effects", []).extend([
                    {
                        "required_flags": ["代签已查实", "台账做平"],
                        "effects": {"metric_deltas": {"political_credit": [-10, -10]}, "ledger_deltas": {}, "open_flags": [], "close_flags": []},
                    },
                    {
                        "required_flags": ["代签已查实", "实情上报"],
                        "effects": {"metric_deltas": {"political_credit": [10, 10]}, "ledger_deltas": {}, "open_flags": [], "close_flags": []},
                    },
                    {
                        "required_flags": ["代签已查实", "党员档案在手"],
                        "effects": {"metric_deltas": {"public_trust": [10, 10]}, "ledger_deltas": {}, "open_flags": [], "close_flags": []},
                    },
                ])
                by_id["b"].setdefault("conditional_effects", []).append({
                    "required_flags": ["代签已查实"],
                    "effects": {"metric_deltas": {"public_trust": [-10, -10]}, "ledger_deltas": {}, "open_flags": ["宁德海被冒犯"], "close_flags": []},
                })
            if source_id == "DP5-07":
                by_id = {item["option_id"]: item for item in document["options"]}
                by_id["a"].setdefault("conditional_effects", []).append({
                    "required_flags": ["党员样板已立", "补偿口径已澄清"],
                    "effects": {"metric_deltas": {"social_stability": [10, 10]}, "ledger_deltas": {}, "open_flags": [], "close_flags": []},
                })
                option_b = by_id["b"]
                for branch in option_b.get("conditional_effects", []):
                    if branch.get("required_flags") == ["宁德海已入账"]:
                        branch.pop("required_flags")
                        branch["required_any_flags"] = ["宁德海已入账", "样板已下乡"]
                    if branch.get("forbidden_flags") == ["宁德海已入账", "马长顺已入账"]:
                        branch["forbidden_flags"] = ["宁德海已入账", "样板已下乡", "马长顺已入账"]
                option_c = by_id["c"]
                clean_c = copy.deepcopy(option_c["effects"])
                clean_c["metric_deltas"].pop("public_trust", None)
                clean_c["metric_deltas"]["integrity"] = [-18, -12]
                option_c.setdefault("conditional_effects", []).append({
                    "replace_base": True,
                    "required_flags": ["私许口头承诺"],
                    "effects": clean_c,
                })
                for option_id in ("a", "b", "e"):
                    by_id[option_id].setdefault("conditional_effects", []).append({
                        "required_flags": ["口径遭封"],
                        "effects": {"metric_deltas": {"public_trust": [-5, -5]}, "ledger_deltas": {}, "open_flags": [], "close_flags": []},
                    })
            if source_id == "DP5-09":
                option_a = next(item for item in document["options"] if item["option_id"] == "a")
                option_a.setdefault("conditional_effects", []).append({
                    "required_flags": ["周大山被压价"],
                    "effects": {
                        "metric_deltas": {"political_credit": [-5, -3]},
                        "ledger_deltas": {"budget_remaining": [-300, -200]},
                        "open_flags": [], "close_flags": [],
                    },
                })
            if source_id == "DP6-10":
                by_id = {item["option_id"]: item for item in document["options"]}
                attitudes = {"蒋崇岳背书", "蒋崇岳默许", "蒋崇岳弃保", "蒋崇岳否决"}
                for option in by_id.values():
                    option["effects"]["open_flags"] = [
                        flag for flag in option["effects"]["open_flags"] if flag not in attitudes
                    ]
                    option["effects"]["close_flags"] = [
                        flag for flag in option["effects"]["close_flags"] if flag not in attitudes
                    ]
        extra_flags.update(document.get("required_flags", []))
        extra_flags.update(document.get("required_any_flags", []))
        extra_flags.update(document.get("forbidden_flags", []))
        for option in document["options"]:
            option.update(OPTION_CONDITIONS.get((source_id, option["option_id"]), {}))
            option.setdefault("unavailable_reason", "条件不足")
            extra_flags.update(option.get("effects", {}).get("open_flags", []))
            extra_flags.update(option.get("effects", {}).get("close_flags", []))
            extra_flags.update(option.get("required_flags", []))
            extra_flags.update(option.get("required_any_flags", []))
            extra_flags.update(option.get("forbidden_flags", []))
            for branch in option.get("conditional_effects", []):
                extra_flags.update(branch.get("required_flags", []))
                extra_flags.update(branch.get("required_any_flags", []))
                extra_flags.update(branch.get("forbidden_flags", []))
                extra_flags.update(branch.get("effects", {}).get("open_flags", []))
                extra_flags.update(branch.get("effects", {}).get("close_flags", []))
            for clause in option.get("availability_any", []):
                extra_flags.update(clause.get("required_flags", []))
                extra_flags.update(clause.get("required_any_flags", []))
                extra_flags.update(clause.get("forbidden_flags", []))
        documents.append(document)
    by_decision = {item["decision_id"]: item for item in documents}
    ev3_followup = copy.deepcopy(by_decision["ev3_01"])
    ev3_followup["decision_id"] = "ev3_01_followup"
    ev3_followup["story_day"] = 43
    ev3_followup["title"] = "突发·血铅患儿曝光·次日重选"
    ev3_followup["prompt"] = "次日玩家须在选项A、B、C中重选一次，且此时各项代价均加重一档。"
    ev3_followup["required_flags"] = ["上交矛盾"]
    ev3_followup["options"] = [
        option for option in ev3_followup["options"] if option["option_id"] in {"a", "b", "c"}
    ]
    for option in ev3_followup["options"]:
        for key, values in option["effects"]["metric_deltas"].items():
            low, high = values
            if low < 0 or high < 0:
                option["effects"]["metric_deltas"][key] = [low - 5, high - 3]
        option["effects"].setdefault("close_flags", []).append("上交矛盾")
    documents.append(ev3_followup)

    dp5_04_recovery = copy.deepcopy(by_decision["dp5_04"])
    dp5_04_recovery["decision_id"] = "dp5_04_recovery"
    dp5_04_recovery["story_day"] = 69
    dp5_04_recovery["title"] = "周满仓·唯一一次重启"
    dp5_04_recovery["required_flags"] = ["周满仓重启已付费"]
    documents.append(dp5_04_recovery)
    dp5_05_recovery = copy.deepcopy(by_decision["dp5_05"])
    dp5_05_recovery["decision_id"] = "dp5_05_recovery"
    dp5_05_recovery["story_day"] = 69
    dp5_05_recovery["required_flags"] = ["村账已摊"]
    documents.append(dp5_05_recovery)
    extra_flags.add("周满仓重启已付费")
    documents.append({
        "decision_id": "dp4_roster_disposition",
        "story_day": 46,
        "title": "名册原件的下落",
        "prompt": "这一沓原件此刻还锁在你办公室的柜子里。报送的口径已经定了，原件本身还得有个下落。",
        "action_point_cost": 0,
        "skippable": False,
        "options": [
            {
                "option_id": "a", "text": "锁进你自己的保险柜，钥匙只你一个人有。",
                "consequence": "你把整摞原件塞进办公室的保险柜，密码转了两圈。",
                "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": [], "close_flags": [], "state_assignments": {"lead_roster_disposition": "己方封存"}},
            },
            {
                "option_id": "b", "text": "随案原件上交市里，走机要，交出去就不在你手上了。",
                "consequence": "原件装进机要袋，封条一压，第二天随件出了县界。",
                "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": [], "close_flags": [], "state_assignments": {"lead_roster_disposition": "呈交上级"}},
            },
            {
                "option_id": "c", "text": "留着这一份，给外头那条能见报的线备着。",
                "consequence": "你把牛皮纸袋重新扣好，压回抽屉最底下。",
                "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": ["名册预留记者"], "close_flags": [], "state_assignments": {"lead_roster_disposition": "己方封存"}},
            },
            {
                "option_id": "d", "text": "让这一摞带签名的原件从今往后不存在。",
                "consequence": "碎纸机响了很久。",
                "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": [], "close_flags": [], "state_assignments": {"lead_roster_disposition": "被销毁"}},
            },
            {
                "option_id": "e", "text": "连原件带袋子退回疾控，账面上你从没经手。",
                "consequence": "纸没进过你的柜子，账面上你从没经手过这份东西。",
                "effects": {"metric_deltas": {}, "ledger_deltas": {}, "open_flags": [], "close_flags": [], "state_assignments": {"lead_roster_disposition": "未获取"}},
            },
        ],
    })
    extra_flags.add("名册预留记者")
    assert len(documents) == 80
    dump("decisions.json", {"schema_version": 3, "decisions": documents})
    return extra_flags


def build_events() -> None:
    values = [
        ("EV1-01", 1, "廉政试炼·接风袋", "接风宴上的手提袋必须当场处置。"),
        ("EV1-02", 8, "渡口堵路", "渡口堵路风险进入处置窗口。"),
        ("EV1-03", 10, "深夜求救", "第十日夜里的求救抵达专班。"),
        ("EV2-01", 26, "县政府门口的来访者", "第26日突发危机自动触发。"),
        ("EV3-01", 42, "第四十二日夜间事件", "第42日夜间固定事件触发。"),
        ("event_d31_municipal_inspection_arrival", 31, "市委巡察组进驻", "市委巡察组由张立带队进驻云溪。"),
        ("event_d45_municipal_inspection_departure", 45, "市委巡察组撤离", "市委巡察组按期撤离云溪。"),
        ("EV4-01", 55, "县医院门前", "第55日县医院现场突发事件触发。"),
        ("EV4-02", 57, "深夜谈话", "第57日夜间突发事件触发。"),
        ("EV4-03", 58, "围堰漫了", "第58日夜围堰突发事件触发。"),
        ("EV4-04", 59, "先遣先到", "第59日清晨迎检先遣已经到场。"),
        ("event_d59_environmental_reception_arrival", 59, "环保迎检组进驻", "顾克明带领环保迎检组进驻。"),
        ("EV5-01", 65, "祖坟冲突", "祖坟被冒犯后的突发事件进入处置。"),
        ("EV5-02", 72, "门板上的复印件", "第72日开场突发事件自动触发。"),
        ("EV5-03", 75, "赵建国夜访", "第75日夜间突发事件无条件触发。"),
        ("EV6-01", 79, "患儿家属现场", "第79日患儿家属现场事件触发。"),
        ("EV6-02", 82, "二次舆情", "第82日二次舆情事件触发。"),
        ("event_d90_final_acceptance", 90, "最终验收", "张立以验收主检身份返回，最终验收开始。"),
    ]
    conditional = {"EV1-02", "EV1-03", "EV5-01"}
    conditions = {
        "EV1-02": {"required_any_flags": ["强势立威", "开口子许诺"]},
        "EV1-03": {"forbidden_event_ids": ["EV1-02"]},
        "EV5-01": {"required_flags": ["祖坟被冒犯"]},
    }
    dump("event_rules.json", {
        "schema_version": 2,
        "fixed_events": [
            ({
                "event_id": event_id,
                "story_day": day,
                "title": title,
                "visible_summary": summary,
                "trigger_type": "conditional" if event_id in conditional else "fixed",
            } | conditions.get(event_id, {}))
            for event_id, day, title, summary in values
        ],
    })


def build_flags(extra_flags: set[str]) -> None:
    path = PACKAGE / "flags.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    ending_flags = {
        "flag_accounts_exposed", "flag_sacrifice_zhao", "flag_bribe_accepted",
        "flag_truth_concealed", "flag_player_fallen", "flag_qian_secret_deal",
        "flag_eia_remediated", "flag_eia_exposed", "flag_eia_concealed",
        "flag_blood_lead_known", "flag_eia_cross_checked", "flag_violent_eviction",
        "flag_coercion", "flag_grave_conflict", "flag_false_signing",
        "flag_model_padding", "flag_two_million_smoothed", "flag_ledger_smoothed",
        "flag_truth_report", "flag_full_whitewash", "flag_collusion_line",
        "flag_conceal_report", "flag_wu_alliance", "flag_vulnerable_household_helped",
        "flag_model_signing", "flag_last_mile_success", "flag_extra_compensation",
        "flag_shell_truthfully_reported", "flag_blood_lead_remediated",
        "flag_listened_to_grievances", "flag_livelihood_first", "flag_wu_disappointed",
        "flag_petition_escalated", "flag_face_first", "flag_media_suppressed",
        "flag_clan_opposition", "flag_self_reflection", "flag_claim_credit",
        "flag_truthful_achievement", "flag_zhou_aligned", "flag_two_million_case_filed",
        "flag_old_accounts_to_inspection", "flag_team_grudge", "flag_team_support",
        "flag_team_detached", "flag_reporter_alliance", "flag_jiang_veto",
        "flag_jiang_abandons", "flag_jiang_endorses", "flag_jiang_acquiesces",
    }
    night_flags = {
        "攻守同盟已成", "钱赵生隙", "原件缺失", "攀比已成风",
        "已立项审计", "秘密摸底", "先告村民", "先告企业",
        "巡察组前置", "巡察组后置", "台账已对", "周家算账派拒谈",
    }
    document["schema_version"] = 2
    stale_tokens = {"关闭旗标", "开启旗标", "本选项", "本结算", "只走叙事"}
    # Do not merge the previous generated registry.  Earlier parser versions
    # could persist prose fragments forever even after their source was fixed.
    clean_registered = {
        flag for flag in document["registered_flags"]
        if flag.startswith("flag_") and not any(token in flag for token in stale_tokens)
    }
    document["registered_flags"] = sorted(
        clean_registered | ending_flags | night_flags | extra_flags
    )
    document["notes"] = "M2 登记运行时与十四条结局轴所需旗标。"
    dump("flags.json", document)


def build_map() -> None:
    locations = [
        ("loc_county_government", "云溪县政府", "县政府办公楼与专班中枢。", 1, [], ["EV2-01", "EV5-03"]),
        ("loc_liulin_village", "柳林村", "三十六户搬迁工作的核心现场。", 1, ["opp_d02_wu_xiuying_first_talk", "opp_d03_zhou_dashan_first_talk"], ["EV1-02", "EV1-03", "EV5-01", "EV5-02"]),
        ("loc_hongda_factory", "宏达化工旧厂区", "紧贴清江的锈红色旧厂区。", 3, [], ["EV4-03", "EV4-04"]),
        ("loc_ferry_town", "渡口镇政府", "镇级协调与首轮签约现场。", 1, [], ["EV1-02"]),
        ("loc_zhou_ancestral_hall", "周氏祠堂", "周氏宗族议事与祖坟线入口。", 31, [], ["EV5-01"]),
        ("loc_abandoned_grain_station", "废弃粮站", "刘三秘密接触玩家的地点。", 16, [], []),
        ("loc_county_hospital", "云溪县医院", "血铅体检和患儿善后现场。", 46, [], ["EV4-01", "EV6-01"]),
        ("loc_environment_station", "县环保站", "监测台账与原始材料入口。", 16, [], ["EV3-01", "EV6-02"]),
    ]
    dump("map_locations.json", {
        "schema_version": 1,
        "locations": [
            {
                "location_id": item[0],
                "name": item[1],
                "description": item[2],
                "unlock_day": item[3],
                "linked_opportunity_ids": item[4],
                "linked_event_ids": item[5],
            }
            for item in locations
        ],
    })


def build_endings(lines: list[str]) -> None:
    reached = ["压线", "宽裕", "全额"]
    rows = [
        ("铁窗之内", "悲剧", {"axis": {"D": "入局败露"}}, "A"),
        ("一票否决", "灰色", {"axis": {"V": "否决"}}, "A"),
        ("强手收场", "警示", {"axis": {"M": "暴力"}}, "A"),
        ("祠堂封门", "悲剧", {"flag": "掘坟结怨"}, "T"),
        ("掩耳盗铃", "警示", {"all": [{"flag": "样板充数"}, {"flag": "居功避重"}]}, "A"),
        ("全线溃败", "悲剧", {"axis": {"A": "溃败"}}, "C"),
        ("悲壮的失守", "悲剧", {"all": [{"axis": {"A": "差一两户"}}, {"axis": {"T": "揭而已治"}}]}, "P"),
        ("功亏一篑", "灰色", {"axis": {"A": "差一两户"}}, "Z"),
        ("串供过关", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"R": "串供口径"}}]}, "T"),
        ("粉饰太平", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"X": "假"}}]}, "T"),
        ("报喜不报忧", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis_in": {"R": ["瞒报", "全面粉饰"]}}]}, "T"),
        ("一手遮天", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"T": "知而捂"}}]}, "C"),
        ("弃车保帅", "灰色", {"all": [{"axis_in": {"A": reached}}, {"axis": {"C": "弃车保帅"}}]}, "P"),
        ("捂住的账", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"C": "捂住"}}]}, "P"),
        ("铁腕的代价", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"M": "施压"}}, {"axis_in": {"P": ["疏离", "离散"]}}]}, "J"),
        ("人走心散", "灰色", {"all": [{"axis_in": {"A": reached}}, {"axis": {"P": "离散"}}]}, "K"),
        ("独木难支", "灰色", {"all": [{"axis_in": {"A": reached}}, {"axis": {"K": "结怨"}}]}, "E"),
        ("记者的沉默", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"E": "被压制"}}]}, "T"),
        ("巡察组的档案", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"J": "立案"}}]}, "C"),
        ("无知者的功劳", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"T": "无知"}}]}, "F"),
        ("揭而未治", "警示", {"all": [{"axis_in": {"A": reached}}, {"axis": {"T": "揭而未治"}}]}, "V"),
        ("山河可鉴", "圆满", {"all": [{"axis_in": {"A": ["宽裕", "全额"]}}, {"axis": {"T": "揭而已治", "C": "端掉", "D": "干净", "M": "温和"}}]}, "P"),
        ("清白收官", "圆满", {"all": [{"axis_in": {"A": reached}}, {"axis": {"D": "干净", "T": "揭而已治"}}]}, "Z"),
        ("尘埃落定", "灰色", {"always": True}, "F"),
    ]
    expected_sub_counts = {
        "A": 5, "C": 4, "T": 4, "P": 4, "Z": 4,
        "J": 3, "K": 3, "E": 3, "F": 3, "V": 4,
    }

    def player_text_after(start: int) -> str:
        player = next(
            index for index in range(start + 1, len(lines))
            if lines[index].strip().startswith(":::玩家")
        )
        end = next(
            index for index in range(player + 1, len(lines))
            if lines[index].strip() == ":::"
        )
        return "\n".join(item.rstrip() for item in lines[player + 1:end]).strip()
    main = []
    sub = []
    for order, (name, tone, condition, axis) in enumerate(rows, 1):
        ending_id = f"ending_{order:02d}"
        main_source = next(
            index for index, line in enumerate(lines)
            if line.startswith(f"### 结局 #{order:02d} ")
        )
        sub_ids = []
        for index in range(expected_sub_counts[axis]):
            letter = chr(ord("a") + index)
            source_code = f"{order:02d}{letter}"
            sub_source = next(
                line_index for line_index, line in enumerate(lines)
                if re.match(rf"^####\s+{source_code}(?:\s|$)", line)
            )
            heading_title = re.sub(
                rf"^####\s+{source_code}\s*", "", lines[sub_source]
            ).strip()
            axis_value = heading_title.split("·")[-1].strip()
            sub_id = f"ending_{order:02d}{letter}"
            sub_ids.append(sub_id)
            sub.append({
                "sub_ending_id": sub_id,
                "main_ending_id": ending_id,
                "axis": axis,
                "axis_value": axis_value,
                "title": heading_title,
                "text": player_text_after(sub_source),
                "source_line": sub_source + 1,
            })
        main.append({
            "ending_id": ending_id,
            "order": order,
            "name": name,
            "tone": tone,
            "condition": condition,
            "free_axis": axis,
            "sub_ending_ids": sub_ids,
            "text": player_text_after(main_source),
            "source_line": main_source + 1,
        })
    assert len(main) == 24
    assert len(sub) == 95
    dump("ending_rules.json", {
        "schema_version": 2,
        "axis_order": ["A", "C", "D", "T", "M", "X", "R", "P", "F", "Z", "J", "K", "E", "V"],
        "main_endings": main,
        "sub_endings": sub,
        "appendices": [
            {"appendix_id": "appendix_lead_roster", "title": "血铅名册的去向", "source": "lead_roster_disposition"},
            {"appendix_id": "appendix_iron_box", "title": "那只铁盒的下落", "source": "iron_box_flags"},
            {"appendix_id": "appendix_rope", "title": "那根绳子的来路", "source": "coercion_flags"},
        ],
    })


def compute_package_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in PACKAGE.rglob("*") if item.is_file()):
        relative = path.relative_to(PACKAGE).as_posix()
        digest.update(relative.encode("utf-8"))
        if relative == "package_manifest.json":
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest.pop("content_hash", None)
            data = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        else:
            data = path.read_bytes()
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def build_manifest(source_bytes: bytes) -> None:
    manifest = json.loads((PACKAGE / "package_manifest.json").read_text(encoding="utf-8"))
    manifest.update({
        "package_version": "0.2.0-m2",
        "status": "published",
        "script_schema_version": 2,
        "rules_schema_version": 2,
        "source_sha256": f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        "notes": "M2 可运行包：D1-D90、事件、地图、复盘及 24/95 结局登记。",
        "content_hash": "pending",
    })
    dump("package_manifest.json", manifest)
    manifest["content_hash"] = compute_package_hash()
    dump("package_manifest.json", manifest)


def main() -> None:
    source_bytes = SOURCE.read_bytes()
    lines = source_bytes.decode("utf-8").splitlines()
    extra_flags = build_runtime_decisions(lines)
    build_story_beats(lines)
    build_npc_profiles(lines)
    build_facts_and_opportunities(lines)
    build_catalog(lines, source_bytes)
    build_events()
    build_flags(extra_flags)
    build_map()
    build_endings(lines)
    build_manifest(source_bytes)
    print("M2 package rebuilt")


if __name__ == "__main__":
    main()
