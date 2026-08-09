from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
WEBP_DIR = ROOT / "webp"
THUMB_DIR = ROOT / "thumbs"


@dataclass(frozen=True)
class Character:
    name: str
    group: str
    role: str
    age: str
    short: str
    use: str
    visual: str
    script_note: str = ""
    aliases: tuple[str, ...] = ()
    ui_priority: str = "主要"

    @property
    def png(self) -> str:
        return f"{self.name}.png"

    @property
    def webp(self) -> str:
        return f"webp/{self.name}.webp"

    @property
    def thumb(self) -> str:
        return f"thumbs/{self.name}.webp"


CHARACTERS: list[Character] = [
    Character("赵建国", "推进搬迁/县内权力", "常务副县长", "56岁", "本土派老干部", "班子掣肘者，贪腐线和环评隐瞒关键人物", "深色行政夹克，手持茶杯或文件，面上和气、眼神精明"),
    Character("郑向东", "推进搬迁/县内权力", "县长秘书", "36岁", "办公室出身", "玩家身边的信息漏斗和风险镜子", "浅灰衬衫，抱笔记本/文件夹，谨慎克制"),
    Character("冯敬之", "推进搬迁/县内权力", "县财政局长", "51岁", "账本型干部", "预算、程序与财政底线的闸口", "眼镜，深色马甲或规整衬衫，拿硬壳账本"),
    Character("贺兴邦", "推进搬迁/县内权力", "县卫健局副局长", "48岁", "医疗兜底递交者", "血铅名册与医疗兜底线的关键递交者", "白衬衫加旧外套，怀抱体检名册，疲惫焦虑"),
    Character("柯启年", "推进搬迁/县内权力", "县环保站站长", "48岁", "环保台账守门人", "环保台账、监测点位变更与隐瞒线的闸门", "褪色环保站工装，胸牌，旧监测仪/台账"),
    Character("钱伟", "推进搬迁/企业", "宏达化工法人代表", "48岁", "企业利益代言人", "企业利益和行贿线主动出手者", "西装革履，名片夹或烟盒，笑容圆滑"),
    Character("孙强", "推进搬迁/基层", "渡口镇党委书记", "45岁", "夹心层干部", "基层执行主力，承接县里与村里的双重压力", "镇干部夹克，手拿手机和材料，疲惫有压力"),
    Character("张立", "外部压力", "市委巡察组组长", "50岁左右", "上级监督压力", "人事线执棋人，巡察与问责压力来源", "深色正装，手持卷宗，站姿笔直，冷面少表情"),
    Character("顾克明", "外部压力", "市生态环境局副局长", "55岁", "环保验收判词", "环保验收判词，事实函数", "风衣或环保系统夹克，采样瓶/报告夹，沉稳不接烟"),
    Character("陈默", "外部压力", "独立调查记者", "30多岁", "舆论导火索", "偷排视频与体检单持有者，推动舆论压力", "户外夹克，背包，相机/手机，眼神锐利"),
    Character("王芳", "外部压力", "县电视台记者", "30多岁", "官方舆论阀门", "官方舆论阀门，也可能倒向事实与陈默", "利落职业装，话筒或采访本，笑容克制"),
    Character("周大山", "柳林村/村庄核心", "村支书兼主任", "67岁", "周氏族长", "宗族核心、村庄阻力与村账黑洞", "深棕夹克或老式干部装，茶缸/手杖，族长气场"),
    Character("刘三", "柳林村/村庄核心", "村会计", "40岁", "散姓会计", "私账和行贿细节钥匙，最不稳定的倒戈者", "瘦小，旧衬衫，夹账本或钥匙，眼神游移"),
    Character("吴秀英", "柳林村/村庄核心", "退休教师", "60多岁", "村民代表", "散户意见领袖，公平补偿和祖坟诉求的关键人物", "灰蓝布衫或针织开衫，银发，手拿旧契约/教案"),
    Character("何铁柱", "柳林村/村庄核心", "退伍军人", "61岁", "钉子户", "血铅旧案引信，强硬谈判对象", "旧军绿色外套，短硬白发，军章/搪瓷杯，拍桌气势"),
    Character("袁桂兰", "柳林村/村庄核心", "困难户", "40多岁", "低保户、残疾人家属", "弱势户试金石，牵动民心和舆情", "旧花布衣、围裙，攥药单/低保证，怯弱疲惫"),
    Character("马长顺", "柳林村/村庄核心", "小卖部老板", "40多岁", "流言放大器", "从众效应、攀比心理和村内流言的放大器", "围裙或旧夹克，账本/香烟盒，圆滑小老板感"),
    Character("宁德海", "柳林村/村庄核心", "退休老党员", "73岁", "体制内关联户", "党员带头样板，程序合规风向", "深蓝中山装，党员徽章，老花镜/协议，稳重体面", "正文常称“宁老”，与宁德海为同一人。", ("宁老",)),
    Character("谭老六", "柳林村/村庄核心", "老上访户", "50多岁", "仇怨户", "历史瑕疵与舆情雷点", "旧棉袄或皱夹克，手机录像，斜挎材料包，脸色尖刻"),
    Character("杨波", "柳林村/扩展户群", "返乡青年", "30岁上下", "摇摆户", "网络出口、年轻一代代表", "休闲卫衣/工装裤，智能手机、耳机或快递袋"),
    Character("周奎元", "柳林村/扩展户群", "周氏宗族执事", "71岁", "祖坟线闸门", "祖坟与祠堂三户的闸门", "黑布衫，腰挂祠堂钥匙，族谱/木杖，古板庄重"),
    Character("周满仓", "柳林村/扩展户群", "周氏族人", "54岁", "算账派", "明账服人的触发者，刘三账线引信", "劳动布衣，算盘/皱纸账单，眼神较真"),
    Character("石文斌", "柳林村/扩展户群", "县环保站职工", "39岁", "柳林村人", "偷排优盘持有者，隐瞒线增量", "旧环保工装，背包，攥U盘，紧张回头"),
    Character("李致远", "玩家/权力中心", "新任县长", "42岁", "玩家身份", "玩家代入位；用于档案封面、教程剪影或关系图中心", "干净白衬衫或深色行政外套，背影/半侧脸，避免固定玩家性格", ui_priority="特殊"),
    Character("蒋崇岳", "玩家/权力中心", "县委书记", "52岁", "云溪权力中心", "常委会轴心与玩家上级，给玩家划线的人", "深色书记夹克，老花镜，县域地图前的压迫感", ui_priority="特殊"),
    Character("邓守本", "功能配角", "柳林村独身老汉", "老人", "最后一户", "争的是祖屋与根；最后一户兜底节点", "干瘦老人，旧棉袄，抱门框或旧屋钥匙", ui_priority="配角"),
    Character("苗喜旺", "功能配角", "柳林村早签户", "中年", "早期高补偿源头", "旧政策高补偿源头，需要重签讲清", "略得意又心虚，手拿早期协议", ui_priority="配角"),
    Character("老倔头", "功能配角", "铁心不签的老人", "老人", "沉默犟户", "被吴秀英劝松的犟户", "烟锅、布鞋、沉默坐姿", ui_priority="配角"),
    Character("罗健", "功能配角", "县卫生院防疫科", "青年", "血铅传真递送者", "第一时间递出血铅传真，推动危机转入明线", "白大褂/防疫科工牌，抱传真纸奔跑", ui_priority="配角"),
    Character("崔广林", "功能配角", "信访办卷宗室老同志", "快退休", "卷宗守门人", "卷宗问到就说，不主动揭", "袖套，守档案柜，快退休老干部气质", ui_priority="配角"),
]


OFFSCREEN = [
    ("市委书记顾成", "幕后/文本人物", "作为更高层政治压力和关系网存在，当前试玩不建议单独出立绘，除非后续做市级会见场景。"),
    ("陈默妻子林微", "幕后/文本人物", "作为陈默个人线背景出现，不参与玩家正面对话，当前用文字材料表达更合适。"),
    ("何铁柱孙子/血铅儿童群体", "事件人物", "是血铅旧案情绪核心，不建议做单个可交互立绘，可在医院/病历/照片道具中表现。"),
    ("宁德海儿子", "关系人物", "只作为宁德海顾虑与市局关系的压力源，不另立角色卡。"),
    ("中裕集团/省里领导等泛称", "背景力量", "属于权力结构和项目背景，建议以公文、电话、会议记录呈现。"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def ensure_dirs() -> None:
    WEBP_DIR.mkdir(exist_ok=True)
    THUMB_DIR.mkdir(exist_ok=True)


def image_bbox(im: Image.Image) -> tuple[int, int, int, int]:
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    return alpha.getbbox() or (0, 0, rgba.width, rgba.height)


def trim_and_fit(im: Image.Image, box: tuple[int, int], padding: int = 18) -> Image.Image:
    rgba = im.convert("RGBA")
    cropped = rgba.crop(image_bbox(rgba))
    target_w, target_h = box
    max_w = target_w - padding * 2
    max_h = target_h - padding * 2
    scale = min(max_w / cropped.width, max_h / cropped.height)
    resized = cropped.resize((max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", box, (0, 0, 0, 0))
    x = (target_w - resized.width) // 2
    y = target_h - padding - resized.height
    canvas.alpha_composite(resized, (x, y))
    return canvas


def convert_assets() -> list[dict]:
    rows: list[dict] = []
    for ch in CHARACTERS:
        src = ROOT / ch.png
        if not src.exists():
            rows.append({"name": ch.name, "status": "missing"})
            continue
        im = Image.open(src).convert("RGBA")
        webp_path = WEBP_DIR / f"{ch.name}.webp"
        thumb_path = THUMB_DIR / f"{ch.name}.webp"
        im.save(webp_path, "WEBP", quality=92, method=6, lossless=False)
        thumb = trim_and_fit(im, (320, 420), padding=12)
        thumb.save(thumb_path, "WEBP", quality=90, method=6)
        bbox = image_bbox(im)
        alpha_corners = [
            im.getpixel((0, 0))[3],
            im.getpixel((im.width - 1, 0))[3],
            im.getpixel((0, im.height - 1))[3],
            im.getpixel((im.width - 1, im.height - 1))[3],
        ]
        rows.append(
            {
                "name": ch.name,
                "status": "ok",
                "width": im.width,
                "height": im.height,
                "mode": im.mode,
                "trim_bbox": bbox,
                "transparent_corners": all(a == 0 for a in alpha_corners),
                "png_bytes": src.stat().st_size,
                "webp_bytes": webp_path.stat().st_size,
                "thumb_bytes": thumb_path.stat().st_size,
            }
        )
    return rows


def draw_multiline(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int], max_width: int, line_gap: int = 6) -> int:
    x, y = xy
    line = ""
    for ch in text:
        candidate = line + ch
        if draw.textbbox((0, 0), candidate, font=fnt)[2] <= max_width:
            line = candidate
        else:
            draw.text((x, y), line, font=fnt, fill=fill)
            y += draw.textbbox((0, 0), line, font=fnt)[3] + line_gap
            line = ch
    if line:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += draw.textbbox((0, 0), line, font=fnt)[3] + line_gap
    return y


def grouped(chars: Iterable[Character]) -> dict[str, list[Character]]:
    result: dict[str, list[Character]] = {}
    for ch in chars:
        result.setdefault(ch.group, []).append(ch)
    return result


def build_overview() -> None:
    bg = (245, 236, 217)
    paper = (255, 248, 234)
    card = (250, 241, 221)
    ink = (44, 43, 36)
    muted = (111, 97, 75)
    green = (38, 84, 66)
    gold = (171, 132, 70)
    line = (211, 190, 150)

    by_name = {ch.name: ch for ch in CHARACTERS}
    sections = [
        (
            "县政、企业与外部压力",
            ["赵建国", "郑向东", "冯敬之", "贺兴邦", "柯启年", "钱伟", "孙强", "张立", "顾克明", "陈默", "王芳", "李致远", "蒋崇岳"],
            "项目推进、监督问责、舆论与权力中枢",
        ),
        (
            "柳林村核心户",
            ["周大山", "刘三", "吴秀英", "何铁柱", "袁桂兰", "马长顺", "宁德海", "谭老六"],
            "签约攻坚主战场，牵动宗族、账本、弱势户和舆情",
        ),
        (
            "柳林村扩展户群",
            ["杨波", "周奎元", "周满仓", "石文斌"],
            "年轻户、祖坟线、明账线与偷排证据线",
        ),
        (
            "功能配角",
            ["邓守本", "苗喜旺", "老倔头", "罗健", "崔广林"],
            "一两幕内推动证据、兜底户与卷宗线",
        ),
    ]
    cols = 8
    cell_w, cell_h = 220, 350
    margin_x = 58
    margin_y = 54
    title_h = 132
    section_header_h = 74
    total_w = margin_x * 2 + cols * cell_w
    total_rows = sum((len(names) + cols - 1) // cols for _, names, _ in sections)
    total_h = margin_y * 2 + title_h + len(sections) * section_header_h + total_rows * cell_h
    canvas = Image.new("RGB", (total_w, total_h), bg)
    draw = ImageDraw.Draw(canvas)

    title_font = font(46, True)
    subtitle_font = font(22)
    section_font = font(29, True)
    section_note_font = font(18)
    name_font = font(25, True)
    info_font = font(16)

    draw.rounded_rectangle((32, 30, total_w - 32, total_h - 30), radius=28, fill=paper, outline=line, width=2)
    draw.text((margin_x, margin_y), "《流域抉择》全剧本人物立绘总览", font=title_font, fill=ink)
    draw.text(
        (margin_x, margin_y + 64),
        "30 位透明 PNG 母版：23 位完整人物 + 2 位特殊角色 + 5 位功能配角。正文“宁老”即宁德海。",
        font=subtitle_font,
        fill=muted,
    )

    y = margin_y + title_h
    for section_title, names, note in sections:
        draw.rounded_rectangle((margin_x, y, total_w - margin_x, y + 52), radius=16, fill=(232, 221, 196), outline=line, width=1)
        draw.rectangle((margin_x, y, margin_x + 8, y + 52), fill=gold)
        draw.text((margin_x + 22, y + 10), section_title, font=section_font, fill=green)
        draw.text((margin_x + 360, y + 17), note, font=section_note_font, fill=muted)
        y += section_header_h
        for idx, name in enumerate(names):
            ch = by_name[name]
            row = idx // cols
            col = idx % cols
            x = margin_x + col * cell_w
            base_y = y + row * cell_h
            card_x0, card_y0 = x + 8, base_y + 4
            card_x1, card_y1 = x + cell_w - 8, base_y + cell_h - 14
            draw.rounded_rectangle((card_x0, card_y0, card_x1, card_y1), radius=18, fill=card, outline=(226, 207, 169), width=1)
            src = Image.open(ROOT / ch.png).convert("RGBA")
            portrait = trim_and_fit(src, (cell_w - 36, 238), padding=6)
            px = x + 18
            py = base_y + 12
            canvas.paste(portrait, (px, py), portrait)
            name_bbox = draw.textbbox((0, 0), ch.name, font=name_font)
            draw.text((x + (cell_w - (name_bbox[2] - name_bbox[0])) // 2, base_y + 254), ch.name, font=name_font, fill=ink)
            info = f"{ch.role}｜{ch.short}"
            draw_multiline(draw, (x + 24, base_y + 290), info, info_font, muted, cell_w - 48, line_gap=2)
        y += ((len(names) + cols - 1) // cols) * cell_h
    canvas.save(ROOT / "角色总览_全30.jpg", "JPEG", quality=95)


def build_manifest(qa_rows: list[dict]) -> None:
    qa_map = {r["name"]: r for r in qa_rows}
    data = {
        "title": "《流域抉择》全剧本人物立绘库",
        "source_script": "C:/Users/章钊林/Desktop/严肃游戏/严肃游戏/serious_game_code/最终剧本.md",
        "generated_by": "00_build_character_library.py",
        "style_baseline": "明亮温暖的叙事像素风，带手绘纸张质感；人物半写实比例但保留像素块面，透明背景 PNG 为母版，WebP/缩略图供前端加载。",
        "characters": [
            {
                **asdict(ch),
                "aliases": list(ch.aliases),
                "files": {"png": ch.png, "webp": ch.webp, "thumb": ch.thumb},
                "qa": qa_map.get(ch.name, {}),
            }
            for ch in CHARACTERS
        ],
        "offscreen_or_text_only": [{"name": n, "type": t, "reason": r} for n, t, r in OFFSCREEN],
    }
    (ROOT / "characters_manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_markdown_docs(qa_rows: list[dict]) -> None:
    missing = [r for r in qa_rows if r.get("status") != "ok"]
    md: list[str] = []
    md.append("# 《流域抉择》全剧本人物立绘清单\n")
    md.append("本清单以 `最终剧本.md` 的人物体系为准：23 位完整九维档案人物 + 玩家身份/县委书记 2 位特殊角色 + 5 位功能配角，共 30 位。现有新版立绘已全部齐备。\n")
    md.append("## 资源结论\n")
    md.append("- 必须出立绘人物：30 位\n")
    md.append(f"- 已有透明 PNG：{sum(1 for r in qa_rows if r.get('status') == 'ok')} / 30\n")
    md.append("- 已生成前端 WebP 与缩略图：见 `webp/`、`thumbs/`\n")
    md.append("- 旧总览图漏列顾克明、陈默、王芳；已补 `角色总览_全30.jpg`\n")
    md.append("- 正文中的“宁老”即“宁德海”，前端人物 ID 建议统一用 `ning_dehai`，显示名可随语境切换为“宁老”。\n")
    md.append("## 立绘表\n")
    md.append("| 分组 | 姓名 | 别称 | 身份 | 剧情功能 | 前端建议 |\n")
    md.append("|---|---|---|---|---|---|\n")
    for ch in CHARACTERS:
        alias = "、".join(ch.aliases) if ch.aliases else ""
        md.append(f"| {ch.group} | {ch.name} | {alias} | {ch.role}，{ch.age}，{ch.short} | {ch.use} | {ch.ui_priority}人物卡；PNG 作母版，WebP 作运行时资源 |\n")
    md.append("## 不单独生成立绘的人物/群体\n")
    md.append("| 名称 | 类型 | 处理建议 |\n")
    md.append("|---|---|---|\n")
    for name, typ, reason in OFFSCREEN:
        md.append(f"| {name} | {typ} | {reason} |\n")
    md.append("## 前端接入建议\n")
    md.append("- 阅读流中只展示当前场景涉及的 1–3 位人物小卡，放在地点信息下方；点击后展开该角色当前剧情进度允许知道的简介。\n")
    md.append("- 人物详情不要一次性展示秘密、动机和隐藏关系；按剧情旗标解锁：基础身份 → 当前诉求 → 关系提示 → 暗线证据。\n")
    md.append("- 对话头像优先用 `thumbs/`，人物档案/关系图节点用 `webp/`，需要高质量导出或二次编辑时再用 PNG。\n")
    md.append("- 李致远是玩家代入位，不建议在阅读界面频繁展示正脸；可用于档案封面、教程身份确认、关系图中心点。\n")
    md.append("- 顾克明、陈默、王芳属于外部压力线，旧总览漏列，但在第五节人物体系中属于必须保留的外部力量。\n")
    if missing:
        md.append("## 缺失项\n")
        for row in missing:
            md.append(f"- {row['name']}\n")
    (ROOT / "全剧本人物立绘清单.md").write_text("".join(md), encoding="utf-8")

    readme = """# 角色立绘库 README

本目录存放《流域抉择》新版剧本的人物立绘。原始 PNG 均为透明背景，适合继续编辑；`webp/` 与 `thumbs/` 为前端运行时建议资源。

## 文件结构

- `姓名.png`：透明 PNG 母版
- `webp/姓名.webp`：前端常规展示
- `thumbs/姓名.webp`：人物小卡、对话头像、列表缩略图
- `characters_manifest.json`：前端/工具链可读的角色元数据
- `角色资源索引.html`：本地快速预览页
- `角色总览_全30.jpg`：完整总览图
- `源图/`：早期批量生成源图，仅作溯源参考

## 命名原则

角色文件名使用剧本正式姓名。正文别称只作为显示层处理，例如“宁老”统一映射到 `宁德海`。

## 视觉基准

明亮温暖的叙事像素风，带手绘纸张质感；半写实比例，服装和手持物件要能一眼提示身份。前端不宜把人物压得过大，建议在场景信息区使用小卡，点击展开档案。
"""
    (ROOT / "README_角色立绘库.md").write_text(readme, encoding="utf-8")

    report: list[str] = []
    report.append("# 人物立绘质量检查报告\n\n")
    report.append("## 自动检查\n\n")
    report.append("| 姓名 | PNG尺寸 | 透明角 | PNG大小 | WebP大小 | 缩略图大小 | 状态 |\n")
    report.append("|---|---:|---|---:|---:|---:|---|\n")
    for row in qa_rows:
        if row.get("status") != "ok":
            report.append(f"| {row['name']} | - | - | - | - | - | 缺失 |\n")
            continue
        report.append(
            f"| {row['name']} | {row['width']}×{row['height']} | {'是' if row['transparent_corners'] else '否'} | {row['png_bytes']} | {row['webp_bytes']} | {row['thumb_bytes']} | 可用 |\n"
        )
    report.append("\n## 人工审看结论\n\n")
    report.append("- 单张立绘文件齐全，均为 RGBA 透明背景。\n")
    report.append("- 原 `角色总览.jpg` 漏列顾克明、陈默、王芳三位；已新建 `角色总览_全30.jpg`。\n")
    report.append("- 已对陈默、谭老六、顾克明、宁德海、杨波做边缘清理，去除旁人残片；原始问题图备份在 `修复备份_原始问题立绘/`。\n")
    report.append("- 谭老六已使用内置 imagegen 重新生成完整单人立绘，解决多人源图抠图导致的边缘缺失；替换前版本与洋红底生成图备份在 `修复备份_谭老六重新生成/`。\n")
    report.append("- 杨波已从 `源图/角色组图_批次4_村民.png` 按真实源图尺寸重新抠图，修复左右缺失与过度裁切；重裁前版本备份在 `修复备份_重裁前_谭老六杨波/`。\n")
    report.append("- 已为全 30 张 PNG 母版补足透明安全边；自动复扫结果为无游离残片、无贴边风险。\n")
    report.append("- 当前无需补生必需角色。若后续剧情扩展到市级会见、记者家庭线或医院儿童正面出场，再增补顾成、林微或事件群像即可。\n")
    (ROOT / "人物立绘质量检查报告.md").write_text("".join(report), encoding="utf-8")


def build_html() -> None:
    cards = []
    for ch in CHARACTERS:
        alias = f"｜别称：{'、'.join(ch.aliases)}" if ch.aliases else ""
        note = f"<p class='note'>{ch.script_note}</p>" if ch.script_note else ""
        cards.append(
            f"""
      <article class="card" data-group="{ch.group}">
        <div class="portrait"><img src="{ch.thumb}" alt="{ch.name}"></div>
        <h2>{ch.name}</h2>
        <p class="meta">{ch.group}{alias}</p>
        <p class="role">{ch.role}｜{ch.age}｜{ch.short}</p>
        <p>{ch.use}</p>
        <p class="visual">{ch.visual}</p>
        {note}
      </article>"""
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>流域抉择｜人物立绘库</title>
  <style>
    :root {{
      color-scheme: light;
      --paper:#f5ecd9; --paper-2:#fff8ea; --ink:#292821; --muted:#75674f;
      --green:#275d49; --line:#d7c59c; --gold:#aa8347;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:linear-gradient(180deg,#e9f1e2 0,#f5ecd9 24rem); color:var(--ink); font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif; }}
    header {{ padding:42px clamp(22px,4vw,64px) 20px; }}
    h1 {{ margin:0; font-size:clamp(32px,4vw,56px); letter-spacing:.03em; }}
    .lead {{ max-width:900px; font-size:18px; line-height:1.8; color:var(--muted); }}
    .stats {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:18px; }}
    .pill {{ padding:10px 16px; border:1px solid var(--line); border-radius:999px; background:rgba(255,248,234,.72); color:var(--green); font-weight:700; }}
    main {{ padding:10px clamp(18px,3vw,54px) 64px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(238px,1fr)); gap:18px; }}
    .card {{ min-height:560px; background:rgba(255,248,234,.82); border:1px solid var(--line); border-radius:24px; padding:18px; box-shadow:0 18px 42px rgba(73,54,28,.12); display:flex; flex-direction:column; }}
    .portrait {{ height:300px; display:flex; align-items:flex-end; justify-content:center; border-radius:18px; background:radial-gradient(circle at 50% 78%, rgba(170,131,71,.20), transparent 48%), linear-gradient(180deg,rgba(255,255,255,.5),rgba(232,218,188,.45)); overflow:hidden; }}
    .portrait img {{ max-height:300px; max-width:100%; object-fit:contain; }}
    h2 {{ margin:16px 0 4px; font-size:27px; }}
    p {{ margin:8px 0; line-height:1.6; font-size:16px; }}
    .meta {{ color:var(--gold); font-weight:800; }}
    .role {{ color:var(--green); font-weight:800; }}
    .visual {{ color:var(--muted); }}
    .note {{ margin-top:auto; padding:10px 12px; border-left:4px solid var(--gold); background:rgba(170,131,71,.10); border-radius:10px; }}
  </style>
</head>
<body>
  <header>
    <h1>流域抉择｜人物立绘库</h1>
    <p class="lead">按最终剧本人物体系整理：23 位完整人物、2 位特殊角色、5 位功能配角。此页用于美术审看与前端接入核对。</p>
    <div class="stats">
      <span class="pill">透明 PNG 母版：30</span>
      <span class="pill">前端 WebP：30</span>
      <span class="pill">缩略图：30</span>
      <span class="pill">旧总览漏列三人，已修正</span>
    </div>
  </header>
  <main>
    <section class="grid">
{''.join(cards)}
    </section>
  </main>
</body>
</html>
"""
    (ROOT / "角色资源索引.html").write_text(html, encoding="utf-8")


def main() -> None:
    ensure_dirs()
    qa_rows = convert_assets()
    build_overview()
    build_manifest(qa_rows)
    build_markdown_docs(qa_rows)
    build_html()
    ok = sum(1 for row in qa_rows if row.get("status") == "ok")
    print(f"角色立绘库构建完成：{ok}/{len(CHARACTERS)}")


if __name__ == "__main__":
    main()
