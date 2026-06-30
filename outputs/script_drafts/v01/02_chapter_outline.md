# 剧本制作包：章节规划

## 结局可达性验证

### 结局 ending_01：安居乐业：治理现代化的样本（类型: good）

- **需要的最终变量状态:** `signed` >= 34, `social_stability` >= 80, `public_trust` >= 70, `political_credit` >= 80, `budget` >= 1000
- **必须携带的 flag:** `flag_style_consultation`, `flag_transparency_high`, `flag_quality_strict`, `flag_crisis_communicate`, `flag_env_investigate`, `flag_vulnerable_support`, `flag_final_precision`, `flag_acceptance_proactive`, `flag_media_proactive`
- **必须避免的 flag:** `flag_style_authoritarian`, `flag_style_transactional`, `flag_cover_up`
- **在大纲中如何达成:**
  - **累积变量:** 
    - Ch01: 选择“公开透明动员”和“尊重乡贤意见”可提升 `public_trust` 和 `social_stability`。
    - Ch02: 选择“严格质检”和“解决弱势群体困难”可提升 `political_credit` 和 `public_trust`，虽消耗 `budget` 但避免后期危机。
    - Ch03: 选择“主动沟通媒体”和“依法调解冲突”可控制 `media_pressure` 并维持 `social_stability`。
    - Ch04: 选择“精准核算”和“完善后续服务”可确保 `signed` 达标且 `budget` 不崩盘。
  - **设置必须 flag:** 
    - Ch01_1A (协商动员) -> `flag_style_consultation`
    - Ch01_2B (咨询马阿訇) -> `flag_respect_customs` (辅助)
    - Ch02_1A (严格质检) -> `flag_quality_strict`
    - Ch02_2A (兜底保障) -> `flag_vulnerable_support`
    - Ch03_1A (主动公开) -> `flag_transparency_high`
    - Ch03_2A (依法调解) -> `flag_crisis_communicate`
    - Ch04_1A (精准核查) -> `flag_final_precision`
  - **避免禁止 flag:** 
    - 避免 Ch01_1C (行政强推) -> `flag_style_authoritarian`
    - 避免 Ch02_1B (放松监管) -> `flag_cover_up`
    - 避免 Ch03_1B (压制舆情) -> `flag_suppress_media`
- **是否存在矛盾？** 无。协商型路径虽然耗时（可能影响 `days_left` 效率），但通过高信任度降低签约阻力，反而能加速后期签约，且避免返工成本，逻辑自洽。

### 结局 ending_02：平稳过渡：带着遗憾的完成（类型: neutral）

- **需要的最终变量状态:** `signed` >= 30, `social_stability` >= 50, `political_credit` >= 50。不满足 Good 结局的高信任和高信用要求。
- **必须携带的 flag:** `flag_style_transactional` 或 `flag_compromise_partial`
- **必须避免的 flag:** `flag_style_authoritarian` (导致坏结局)
- **在大纲中如何达成:**
  - **累积变量:** 
    - Ch01: 选择“重点突破精英” (`flag_elite_focus`)，快速提升 `signed` 但 `public_trust` 增长有限。
    - Ch02: 选择“资源交换” (`flag_resource_swap`)，用额外预算换取签约，`budget` 下降较快，`political_credit` 持平。
    - Ch03: 选择“冷处理舆情” (`flag_ignore_minor_complaints`)，`media_pressure` 小幅上升但未爆发，`social_stability` 维持在及格线。
    - Ch04: 选择“基本验收” (`flag_basic_acceptance`)，完成任务但遗留小问题。
  - **设置必须 flag:** 
    - Ch01_1B (利益诱导) -> `flag_style_transactional`
    - Ch02_2B (常规补偿) -> `flag_compromise_partial`
  - **避免禁止 flag:** 
    - 避免 Ch03_2C (激化矛盾) -> `flag_escalate_conflict`
- **是否存在矛盾？** 无。交易型路径以资源换时间，虽牺牲公平性和长期信任，但能在截止日前凑够户数，符合 Neutral 结局定义。

### 结局 ending_03：失控边缘：压力的反噬（类型: bad）

- **需要的最终变量状态:** `social_stability` < 30 OR `political_credit` < 40 OR `days_left` = 0 且 `signed` < 25 OR `media_pressure` > 90。
- **必须携带的 flag:** `flag_style_authoritarian` 或 `flag_cover_up` 或 `flag_escalate_conflict`
- **必须避免的 flag:** `flag_style_consultation` (通常会避免坏结局)
- **在大纲中如何达成:**
  - **累积变量:** 
    - Ch01: 选择“行政强推” (`flag_style_authoritarian`)，`social_stability` 大幅下降，`public_trust` 暴跌。
    - Ch02: 选择“掩盖质量问题” (`flag_cover_up`)，`env_clue` 或 `media_pressure` 飙升，`political_credit` 下降。
    - Ch03: 选择“压制上访/对抗媒体” (`flag_suppress_media`)，触发 `media_pressure` > 90 或 `social_stability` < 30。
    - Ch04: 此时已无法挽回，任务失败或被问责。
  - **设置必须 flag:** 
    - Ch01_1C (强制命令) -> `flag_style_authoritarian`
    - Ch02_1B (偷工减料默许) -> `flag_cover_up`
    - Ch03_1C (封锁消息) -> `flag_suppress_media`
  - **避免禁止 flag:** 无特定避免，只要不走协商或稳健路线即可。
- **是否存在矛盾？** 无。威权型路径在初期可能提升 `signed` 速度，但极易触发群体事件和舆情反噬，导致系统崩溃，符合 Bad 结局逻辑。

## 第 1 章：压力传导与破局初探
- **chapter_id:** ch01
- **day_range:** 第1-20天
- **core_task:** 确立治理风格，打破村民观望僵局，完成首批关键户签约。
- **main_question:** 在上级高压指标与村民普遍抵触之间，玩家选择何种策略启动搬迁工作？
- **learning_goals:** 理解“压力型体制”下的初始决策困境；识别熟人社会中的关键意见领袖（KOL）作用。
- **scene_brief:** 
  - **场景:** 镇党委会办公室（压抑、电话铃声不断）、青石村村委会大院（嘈杂、村民聚集）。
  - **氛围:** 焦虑、紧迫。赵国强书记不断施压，村民议论纷纷。
- **info_nodes:**
  - **node_id:** info_ch01_policy
    - **玩家获得的信息:** 县里下达的“一票否决”考核指标；全镇剩余90天倒计时；“10+5+N”补偿政策细则。
    - **解锁条件:** 开局自动获取。
  - **node_id:** info_ch01_village_map
    - **玩家获得的信息:** 青石村户籍地图，标注了张德贵（精英）、李桂兰（弱势）、赵小虎（网红）等关键户位置及关系网。
    - **解锁条件:** 与刘大山对话后获取。
- **decision_framework:**
  - **决策点 1：动员策略选择**
    - **选项 A（标签: 协商共治）:** 召开村民代表会，公开政策，邀请马阿訇和张德贵参与讨论。
      - **核心逻辑:** 赋予村民知情权和参与感，建立信任基础。
      - **主要变量影响:** `public_trust` +10, `social_stability` +5, `days_left` -5 (耗时)。
      - **NPC 状态变化:** 马阿訇 trust +10, 张德贵 attitude +10, 赵国强 anxiety +5 (嫌慢)。
      - **设置的 flag:** `flag_style_consultation`, `flag_respect_customs`。
      - **解锁/关闭内容:** 解锁 Ch02 中马阿訇的协助选项；关闭 Ch01 快速签约捷径。
      - **教学反馈重点:** 协商民主在基层治理中的合法性构建作用。
    - **选项 B（标签: 利益诱导）:** 私下接触张德贵等精英，承诺额外产业扶持，要求其带头签约。
      - **核心逻辑:** 利用精英影响力带动从众心理，效率较高但存在公平性质疑。
      - **主要变量影响:** `signed` +3 (精英及其亲属), `budget` -200, `public_trust` -5 (若消息泄露)。
      - **NPC 状态变化:** 张德贵 trust +15, 赵小虎 attitude -10 (怀疑暗箱操作)。
      - **设置的 flag:** `flag_style_transactional`, `flag_elite_focus`。
      - **解锁/关闭内容:** 解锁 Ch02 中与周老板的产业配套谈判；增加 Ch03 舆情风险。
      - **教学反馈重点:** 精英俘获风险与政策执行的公平性困境。
    - **选项 C（标签: 行政强推）:** 召开动员大会，宣布纪律，对拒不签约者暗示采取断水断电等措施。
      - **核心逻辑:** 依靠行政权威施压，短期见效快，但激化矛盾。
      - **主要变量影响:** `social_stability` -15, `public_trust` -15, `signed` +1 (仅极少数畏惧者)。
      - **NPC 状态变化:** 赵国强 satisfaction +10, 刘大山 anxiety +20, 李桂兰 attitude -20。
      - **设置的 flag:** `flag_style_authoritarian`, `flag_coercion_start`。
      - **解锁/关闭内容:** 解锁 Ch03 的维稳高压选项；关闭 Ch02 中温和调解的可能性。
      - **教学反馈重点:** “刚性稳定”的脆弱性与权力依赖症的后果。
  
  - **决策点 2：首户突破对象**
    - **选项 A（标签: 攻克精英）:** 集中资源说服张德贵签约。
      - **核心逻辑:** 擒贼先擒王，利用其示范效应。
      - **主要变量影响:** 若 Ch01_1A/B，则 `signed` +5 (带动族人)；若 Ch01_1C，则 `signed` +1 (张德贵可能抵制)。
      - **NPC 状态变化:** 张德贵 trust +/- 取决于前序选择。
      - **设置的 flag:** `flag_first_blood_elite`。
    - **选项 B（标签: 关怀弱势）:** 优先解决李桂兰的养老顾虑，帮助其签约。
      - **核心逻辑:** 展现政府温情，树立道德标杆，软化舆论。
      - **主要变量影响:** `public_trust` +10, `signed` +1, `budget` -50 (养老服务购买)。
      - **NPC 状态变化:** 李桂兰 trust +20, 马阿訇 attitude +10。
      - **设置的 flag:** `flag_first_blood_vulnerable`。
      - **解锁/关闭内容:** 解锁 Ch02 中李桂兰孙子的正面宣传。
    - **选项 C（标签: 随机入户）:** 按名单顺序，碰到谁做谁工作。
      - **核心逻辑:** 缺乏策略，效率低下。
      - **主要变量影响:** `signed` +0~1, `days_left` -5。
      - **NPC 状态变化:** 无显著变化。
      - **设置的 flag:** `flag_no_strategy`。

- **variables_in_focus:** [`public_trust`, `social_stability`, `signed`]
- **flag_design:** 
  - `flag_style_consultation/transactional/authoritarian`: 决定后续章节的 NPC 互动基调和可用选项。
  - `flag_first_blood_elite/vulnerable`: 影响初期口碑传播方向。
- **npc_state_plan:** 
  - **赵国强:** 焦虑值维持高位，根据玩家签约进度调整态度。
  - **刘大山:** 观察玩家风格，若玩家强硬则消极怠工，若玩家尊重则积极配合。
  - **张德贵:** 成为关键变量，其态度直接影响一组村民的签约率。
- **production_notes:** 
  - UI 需突出显示“剩余天数”和“全县排名”的压力感。
  - 音效：党委会上的电话铃声、会议室的沉默尴尬音；村大院的嘈杂人声。

## 第 2 章：深水区博弈与资源约束
- **chapter_id:** ch02
- **day_range:** 第21-45天
- **core_task:** 处理典型疑难个案，平衡财政预算与工程质量，应对初步舆情苗头。
- **main_question:** 当遭遇利益硬骨头和质量隐患时，玩家如何权衡短期进度与长期风险？
- **learning_goals:** 掌握利益协调机制；理解财政约束下的政策弹性；识别工程腐败与质量风险。
- **scene_brief:** 
  - **场景:** 安置点建筑工地（尘土飞扬、机器轰鸣）、孙寡妇家（昏暗、贫困）、镇财政所。
  - **氛围:** 焦灼、利益纠葛。周老板的催促、孙梅的哭诉、王秀英的算盘声。
- **info_nodes:**
  - **node_id:** info_ch02_budget_alert
    - **玩家获得的信息:** 预算消耗过快，若按当前标准，后期资金可能不足。
    - **解锁条件:** 与王秀英对话。
  - **node_id:** info_ch02_quality_report
    - **玩家获得的信息:** 匿名举报安置点墙体材料标号不足。
    - **解锁条件:** 陈会计提示或玩家主动调查。
- **decision_framework:**
  - **决策点 1：安置点质量危机**
    - **选项 A（标签: 严格质检）:** 责令停工整改，追究周老板责任，重新检测。
      - **核心逻辑:** 坚守底线，保障群众利益，但延误工期，得罪承建商。
      - **主要变量影响:** `political_credit` +10, `days_left` -10, `budget` -100 (复检费), 周老板 attitude -20。
      - **NPC 状态变化:** 吴记者 attitude +10 (若知晓), 村民 public_trust +5。
      - **设置的 flag:** `flag_quality_strict`。
      - **解锁/关闭内容:** 解锁 Ch04 的优质验收加分；关闭 Ch03 中周老板的配合选项。
      - **教学反馈重点:** 依法行政与工程质量的红线意识。
    - **选项 B（标签: 默许通融）:** 要求周老板“注意一点”，继续施工，赶工期。
      - **核心逻辑:** 进度优先，赌不会出事，或与周老板形成利益共同体。
      - **主要变量影响:** `days_left` +5 (赶工), `budget` +0, `env_clue` +20 (隐患积累), `political_credit` -10 (若发现)。
      - **NPC 状态变化:** 周老板 trust +15, 陈会计 anxiety +10。
      - **设置的 flag:** `flag_cover_up`, `flag_quality_risk`。
      - **解锁/关闭内容:** 解锁 Ch03 的舆情爆发事件；关闭 Ch04 的优质评价。
      - **教学反馈重点:** 治理中的机会主义行为及其潜在灾难性后果。
  
  - **决策点 2：弱势群体孙梅的诉求**
    - **选项 A（标签: 兜底保障）:** 协调民政、残联，落实低保衔接和特殊补助，安排志愿者帮扶。
      - **核心逻辑:** 精准施策，体现政策温度，解决后顾之忧。
      - **主要变量影响:** `signed` +1, `public_trust` +10, `budget` -50, `social_stability` +5。
      - **NPC 状态变化:** 孙梅 trust +30, 马阿訇 attitude +5。
      - **设置的 flag:** `flag_vulnerable_support`。
      - **解锁/关闭内容:** 解锁孙梅作为正面案例的宣传。
      - **教学反馈重点:** 社会保障网在政策执行中的托底作用。
    - **选项 B（标签: 常规补偿）:** 按统一标准补偿，告知其不符合额外补助条件。
      - **核心逻辑:** 坚持原则，避免攀比，但可能被指责冷漠。
      - **主要变量影响:** `signed` +0, `social_stability` -5, `media_pressure` +5 (若其上访)。
      - **NPC 状态变化:** 孙梅 attitude -10, 赵小虎 attitude -5。
      - **设置的 flag:** `flag_rigid_execution`。
      - **解锁/关闭内容:** 增加 Ch03 上访概率。
      - **教学反馈重点:** 政策刚性与个体困境的张力。
    - **选项 C（标签: 临时安抚）:** 自掏腰包或挪用小额经费给予一次性慰问，不解决根本问题。
      - **核心逻辑:** 花钱买平安，短期有效，长期隐患。
      - **主要变量影响:** `signed` +1 (暂时), `budget` -10 (违规风险), `political_credit` -5。
      - **NPC 状态变化:** 孙梅 trust +5 (短暂), 陈会计 anxiety +15。
      - **设置的 flag:** `flag_temporary_fix`。
      - **解锁/关闭内容:** 可能在 Ch04 引发审计问题。

- **variables_in_focus:** [`budget`, `political_credit`, `env_clue`]
- **flag_design:** 
  - `flag_quality_strict/cover_up`: 决定 Ch03 是否爆发质量舆情。
  - `flag_vulnerable_support`: 影响 Ch04 的群众满意度评价。
- **npc_state_plan:** 
  - **周老板:** 若玩家严格，则转为对抗或消极配合；若玩家默许，则变得傲慢。
  - **王秀英:** 对玩家的预算使用进行严格监控，若违规则拒绝签字。
  - **赵小虎:** 开始关注安置点质量和孙梅待遇，准备直播素材。
- **production_notes:** 
  - 视觉：工地现场的粗糙细节 vs 财政报表的红字警示。
  - 交互：模拟审批单的签字流程，若预算不足则按钮变灰。

## 第 3 章：舆情风暴与危机应对
- **chapter_id:** ch03
- **day_range:** 第46-70天
- **core_task:** 应对突发群体性事件或网络舆情，化解信任危机，稳住基本盘。
- **main_question:** 当矛盾激化、舆情发酵时，玩家选择压制、回避还是公开透明地解决？
- **learning_goals:** 掌握舆情应对策略；理解“依法抗争”的逻辑；锻炼危机沟通能力。
- **scene_brief:** 
  - **场景:** 镇信访接待室（拥挤、情绪激动）、网络直播间（手机屏幕、弹幕滚动）、镇政府会议室。
  - **氛围:** 紧张、混乱。警笛声、键盘敲击声、争吵声。
- **info_nodes:**
  - **node_id:** info_ch03_viral_video
    - **玩家获得的信息:** 赵小虎或不明身份者发布的视频，指控搬迁不公或质量差，点击量飙升。
    - **解锁条件:** `media_pressure` > 50 或 Ch02 埋下隐患。
  - **node_id:** info_ch03_petition_letter
    - **玩家获得的信息:** 部分村民联名上访信，要求重新核定面积。
    - **解锁条件:** `social_stability` < 60。
- **decision_framework:**
  - **决策点 1：舆情应对策略**
    - **选项 A（标签: 主动公开）:** 召开新闻发布会/直播回应，邀请吴记者和村民代表现场质询，公布数据。
      - **核心逻辑:** 真相是最好的公关，重建信任。
      - **主要变量影响:** `media_pressure` -20, `public_trust` +10, `political_credit` +5。
      - **NPC 状态变化:** 吴记者 attitude +15, 赵小虎 attitude +10 (若数据真实)。
      - **设置的 flag:** `flag_transparency_high`, `flag_crisis_communicate`。
      - **解锁/关闭内容:** 解锁 W 记者的正面报道；关闭谣言传播路径。
      - **教学反馈重点:** 全媒体时代的政府信息公开与舆情引导。
    - **选项 B（标签: 冷处理/删帖）:** 联系网信部门删帖，警告赵小虎，内部消化矛盾。
      - **核心逻辑:** 控制信息流，防止扩散，但易引发次生舆情。
      - **主要变量影响:** `media_pressure` +10 (反弹), `public_trust` -10, `social_stability` -5。
      - **NPC 状态变化:** 赵小虎 attitude -20, 吴记者 attitude -10。
      - **设置的 flag:** `flag_suppress_media`。
      - **解锁/关闭内容:** 触发 Ch04 的上级问责风险；关闭公众沟通渠道。
      - **教学反馈重点:** “堵不如疏”的舆情治理教训。
    - **选项 C（标签: 转移视线）:** 制造其他热点，或指责村民无理取闹。
      - **核心逻辑:** 污名化抗争者，推卸责任。
      - **主要变量影响:** `social_stability` -15, `political_credit` -10, `media_pressure` +20。
      - **NPC 状态变化:** 全体村民 attitude -10, 赵国强 anxiety +20。
      - **设置的 flag:** `flag_blame_victims`。
      - **解锁/关闭内容:** 极大概率触发 Bad 结局。

  - **决策点 2：群体事件处置**
    - **选项 A（标签: 依法调解）:** 引入司法所、马阿訇组成调解团，逐户核实诉求，合法合规解决。
      - **核心逻辑:** 法治思维与乡土智慧结合，实质性解决问题。
      - **主要变量影响:** `social_stability` +10, `signed` +2 (问题解决后), `days_left` -5。
      - **NPC 状态变化:** 马阿訇 trust +15, 刘大山 trust +10。
      - **设置的 flag:** `flag_legal_mediation`。
      - **解锁/关闭内容:** 为 Ch04 收官奠定稳定基础。
      - **教学反馈重点:** 多元纠纷解决机制在基层的应用。
    - **选项 B（标签: 强力维稳）:** 调动警力震慑，带走带头闹事者。
      - **核心逻辑:** 恢复秩序优先，但加剧对立。
      - **主要变量影响:** `social_stability` -20 (表面平静), `public_trust` -20, `political_credit` -10。
      - **NPC 状态变化:** 赵国强 satisfaction +5 (短期), 村民 fear +20。
      - **设置的 flag:** `flag_force_suppression`。
      - **解锁/关闭内容:** 埋下 Ch04 爆发更大冲突的种子。
      - **教学反馈重点:** 维稳思维的局限性与合法性流失。

- **variables_in_focus:** [`media_pressure`, `social_stability`, `public_trust`]
- **flag_design:** 
  - `flag_transparency_high/suppress_media`: 决定最终舆情的走向。
  - `flag_legal_mediation/force_suppression`: 决定社会稳定的真实性。
- **npc_state_plan:** 
  - **赵小虎:** 若玩家透明，则转为监督合作者；若玩家压制，则成为死敌。
  - **吴记者:** 根据玩家表现决定报道基调（正面典型 vs 深度调查）。
  - **马阿訇:** 在危机时刻发挥关键的缓冲和调停作用。
- **production_notes:** 
  - UI：模拟手机屏幕，显示短视频播放量和评论区弹幕，弹幕内容随 `media_pressure` 变化。
  - 音效：心跳声、警报声、键盘敲击声混合，营造紧迫感。

## 第 4 章：收官结算与未来展望
- **chapter_id:** ch04
- **day_range:** 第71-90天
- **core_task:** 完成剩余户数签约，进行最终验收，处理遗留问题，迎接考核。
- **main_question:** 在最后冲刺阶段，玩家如何确保成果稳固，并为后续治理留下空间？
- **learning_goals:** 理解政策闭环管理；反思治理绩效的可持续性；体验不同结局的因果反馈。
- **scene_brief:** 
  - **场景:** 搬迁新居社区（崭新但略显空旷）、镇档案室（堆积如山的文件）、县委考核组会议室。
  - **氛围:** 疲惫、期待、尘埃落定。
- **info_nodes:**
  - **node_id:** info_ch04_final_count
    - **玩家获得的信息:** 当前签约户数、预算余额、舆情指数、上级考核初步反馈。
    - **解锁条件:** 章节开始。
- **decision_framework:**
  - **决策点 1：最后攻坚策略**
    - **选项 A（标签: 精准服务）:** 对剩余未签约户进行一对一帮扶，解决具体困难（如就业、子女入学）。
      - **核心逻辑:** 以人为本，彻底消除顾虑。
      - **主要变量影响:** `signed` +剩余户数 (若信任度高), `budget` -剩余可用部分, `public_trust` +10。
      - **NPC 状态变化:** 村民 gratitude +20。
      - **设置的 flag:** `flag_final_precision`。
      - **解锁/关闭内容:** 导向 Good 结局的关键。
    - **选项 B（标签: 指标凑数）:** 动员村干部亲戚朋友挂靠户口签约，或放宽审核标准。
      - **核心逻辑:** 弄虚作假，完成任务。
      - **主要变量影响:** `signed` +5 (虚假), `political_credit` -20 (若审计发现), `social_stability` -10。
      - **NPC 状态变化:** 郑科长 attitude -10, 陈会计 anxiety +20。
      - **设置的 flag:** `flag_fraud_signing`。
      - **解锁/关闭内容:** 导向 Bad 或 Neutral 结局（取决于是否被发现）。
    - **选项 C（标签: 放弃尾户）:** 承认无法完成100%，重点做好已搬迁户的服务。
      - **核心逻辑:** 实事求是，接受不完美。
      - **主要变量影响:** `signed` 不变, `political_credit` -5 (未满分), `social_stability` +5 (避免冲突)。
      - **NPC 状态变化:** 赵国强 disappointment +10。
      - **设置的 flag:** `flag_realistic_completion`。
      - **解锁/关闭内容:** 导向 Neutral 结局。

  - **决策点 2：迎检与总结**
    - **选项 A（标签: 展示亮点）:** 整理典型案例，邀请媒体和上级参观，展示治理创新。
      - **核心逻辑:** 积极营销治理成果。
      - **主要变量影响:** `political_credit` +10, `media_pressure` -10。
      - **NPC 状态变化:** 赵国强 satisfaction +20, 吴记者 attitude +10。
      - **设置的 flag:** `flag_media_proactive`。
      - **解锁/关闭内容:** 强化 Good 结局的晋升路径。
    - **选项 B（标签: 低调过关）:** 提交标准报告，不惹眼，不出错。
      - **核心逻辑:** 保守行事。
      - **主要变量影响:** 无显著变化。
      - **NPC 状态变化:** 郑科长 neutral。
      - **设置的 flag:** `flag_low_profile`。
      - **解锁/关闭内容:** 维持现状。

- **variables_in_focus:** [`signed`, `political_credit`, `budget`]
- **flag_design:** 
  - `flag_final_precision/fraud_signing`: 决定最终签约数的真实性和合法性。
  - `flag_media_proactive`: 影响最终的政治评价。
- **npc_state_plan:** 
  - **赵国强:** 根据最终结果决定对玩家的评价（推荐提拔 vs 批评）。
  - **郑科长:** 进行最终数据审核，若发现造假则触发问责。
  - **村民:** 根据搬迁体验给出最终满意度评价。
- **production_notes:** 
  - 结局动画：根据变量组合播放不同的蒙太奇片段（如：村民在新居的笑脸 vs 上访队伍 vs 玩家被谈话）。
  - 数据面板：最终展示所有变量的雷达图，对比初始状态。

## Flag 全局规划表

| flag_id | 创建于 | 作用（解锁什么 / 关闭什么） | 参与哪个结局 |
|---|---|---|---|
| flag_style_consultation | ch01_1A | 解锁 Ch02/03 中的协商选项；提升 NPC 信任 | ending_01 |
| flag_style_transactional | ch01_1B | 解锁 Ch02 中的资源交换选项；增加舆情风险 | ending_02 |
| flag_style_authoritarian | ch01_1C | 解锁 Ch03 中的维稳选项；大幅降低信任 | ending_03 |
| flag_quality_strict | ch02_1A | 避免 Ch03 质量舆情；提升政治信用 | ending_01 |
| flag_cover_up | ch02_1B | 触发 Ch03 质量危机；降低政治信用 | ending_03 |
| flag_vulnerable_support | ch02_2A | 提升群众信任；解锁正面案例 | ending_01 |
| flag_transparency_high | ch03_1A | 降低舆情压力；解锁媒体支持 | ending_01 |
| flag_suppress_media | ch03_1B | 增加舆情反弹风险；降低信任 | ending_03 |
| flag_legal_mediation | ch03_2A | 提升社会稳定；解决实质矛盾 | ending_01 |
| flag_force_suppression | ch03_2B | 降低社会稳定；埋下冲突隐患 | ending_03 |
| flag_final_precision | ch04_1A | 确保签约真实性；提升满意度 | ending_01 |
| flag_fraud_signing | ch04_1B | 触发审计风险；可能导致问责 | ending_03 |

## 分支与状态追踪总表

| choice_id | 所属章节 | 变量变化方向 | NPC 状态变化 | 新增/移除 flag | 解锁内容 | 关闭内容 | 长期影响 | 关联结局 |
|---|---|---|---|---|---|---|---|---|
| ch01_1A | ch01 | public_trust +10, social_stability +5 | 马阿訇 trust +10, 张德贵 attitude +10 | flag_style_consultation | Ch02 马阿訇协助 | Ch01 快速签约 | 奠定信任基础，后期签约阻力小 | ending_01 |
| ch01_1C | ch01 | social_stability -15, public_trust -15 | 赵国强 satisfaction +10, 李桂兰 attitude -20 | flag_style_authoritarian | Ch03 维稳选项 | Ch02 温和调解 | 矛盾积累，易引发群体事件 | ending_03 |
| ch02_1A | ch02 | political_credit +10, days_left -10 | 周老板 attitude -20, 吴记者 attitude +10 | flag_quality_strict | Ch04 优质验收 | Ch03 质量危机 | 避免重大舆情，提升政绩含金量 | ending_01 |
| ch02_1B | ch02 | env_clue +20, political_credit -10 | 周老板 trust +15, 陈会计 anxiety +10 | flag_cover_up | Ch03 舆情爆发 | Ch04 优质评价 | 埋雷，后期可能爆炸 | ending_03 |
| ch03_1A | ch03 | media_pressure -20, public_trust +10 | 吴记者 attitude +15, 赵小虎 attitude +10 | flag_transparency_high | 正面报道 | 谣言传播 | 化解危机，转危为机 | ending_01 |
| ch03_1B | ch03 | media_pressure +10, public_trust -10 | 赵小虎 attitude -20, 吴记者 attitude -10 | flag_suppress_media | 上级问责风险 | 公众沟通 | 舆情反弹，信任崩塌 | ending_03 |
| ch04_1A | ch04 | signed +剩余, public_trust +10 | 村民 gratitude +20 | flag_final_precision | Good 结局路径 | 虚假签约 | 圆满完成，口碑极佳 | ending_01 |
| ch04_1B | ch04 | signed +5 (虚), political_credit -20 | 郑科长 attitude -10, 陈会计 anxiety +20 | flag_fraud_signing | 审计风险 | 真实服务 | 东窗事发，身败名裂 | ending_03 |

## 制作备注
- **关键场景清单:** 
  1. 镇党委会（压力源）
  2. 青石村大院（民意场）
  3. 安置点工地（利益链）
  4. 网络直播间/信访室（危机点）
- **重要道具或文件:** 
  - 《生态搬迁补偿方案》（核心规则）
  - 《入户民情日志》（记录 NPC 状态）
  - 《资金审批单》（预算约束具象化）
  - 《舆情监测报告》（动态风险提示）
- **UI 表现建议:** 
  - 采用写实风格，界面模拟基层办公系统（OA）与微信聊天界面结合。
  - 关键数值（如预算、天数、舆情热度）需醒目显示，并用颜色预警（红/黄/绿）。
  - NPC 头像应随情绪状态（信任、焦虑、愤怒）有细微表情变化。
- **音效和氛围建议:** 
  - 背景音乐随 `social_stability` 和 `media_pressure` 变化：平静时使用西北民谣吉他，紧张时使用急促鼓点和电子噪音。
  - 加入环境音：风声（山区）、施工声（工地）、争吵声（信访）、键盘声（舆情）。
- **仍需人工补充的问题:** 
  1. NPC 对话需聘请当地顾问审核，确保口音和俚语地道（如“咋整”、“弄啥咧”等西北方言特征）。
  2. 政策术语（如“10+5+N”）的具体内容需根据最新甘肃省文件细化，确保专业性。
  3. 变量变化的具体数值需经过多轮测试平衡，避免某条路径过于简单或困难。
  4. 需审查所有剧情分支，确保不出现违背民族政策或宗教禁忌的内容（特别是涉及马阿訇的剧情）。
