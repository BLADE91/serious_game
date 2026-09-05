"""Export all maintained frames and their branch predicates for editorial review."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "code/backend/content/packages/pkg_gameplay_v3"
OUT = ROOT / "output/问题反馈第二批-2026-09-05"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    beats = json.loads((PACKAGE / "story_beats.json").read_text(encoding="utf-8"))["beats"]
    decisions = json.loads((PACKAGE / "decisions.json").read_text(encoding="utf-8"))["decisions"]
    events = json.loads((PACKAGE / "event_rules.json").read_text(encoding="utf-8"))["fixed_events"]
    rows = []
    md = ["# 逐日逐幕核查清单", "", "核查版本：3.5.13-feedback-reading-actions，2026-09-05。",
        "", "本清单覆盖90日的开场、决策铺垫、保留后果及夜间正文。条件列登记触发前提；回归测试逐块验证满足/不满足条件时是否进入正文流，事件另测触发、跳过和重复调用。",
        "", "这是内容与运行条件核查，不是声称所有组合均由真实模型逐帧试玩。浏览器重点复核签约入口、提示可读性、多人历史切换及两个窗口宽度；结局路线使用测试模型通过正式API执行。", ""]
    for beat in beats:
        day = beat["story_day"]
        md += [f"## 第{day}日", "", "| 环节/内容ID | 背景 | 条件 | 正文开头 |", "|---|---|---|---|"]
        blocks = [("开场", b) for b in beat["opening_blocks"]]
        blocks += [("决策 " + d["decision_id"], b) for d in decisions if d["story_day"] == day
                   for b in d["presentation_blocks"] + d["followup_blocks"]]
        blocks += [("夜间", b) for b in beat["night_blocks"]]
        if not blocks:
            md.append("| 自由工作日 | 根据当前现场 | 必办事件优先 | 无额外填充页，正常显示自由行动提示 |")
        for phase, b in blocks:
            predicates = {k: b[k] for k in ("origin_ids", "required_flags", "required_any_flags", "forbidden_flags") if b.get(k)}
            condition = json.dumps(predicates, ensure_ascii=False) if predicates else "无额外条件"
            text = b["text"].replace("\n", " ").replace("|", "／")
            md.append(f"| {phase} / `{b['block_id']}` | {b['scene_id']} | {condition} | {text[:110]}{'…' if len(text)>110 else ''} |")
            rows.append({"day": day, "phase": phase, **b, "review_scope": "text_location_time_background_and_visibility"})
        for event in events:
            if event["story_day"] == day:
                predicates = {k: v for k, v in event.items() if k in {"required_flags", "required_any_flags", "forbidden_flags", "forbidden_event_ids"}}
                md += ["", f"事件 `{event['event_id']}`：{event['title']}。" + (json.dumps(predicates, ensure_ascii=False) if predicates else "固定发生；重复触发不重复入队。")]
        md.append("")
    summary = {"days": len(beats), "frames": len(rows), "conditional_frames": sum(any(r.get(k) for k in ("origin_ids", "required_flags", "required_any_flags", "forbidden_flags")) for r in rows), "events": len(events)}
    (OUT / "逐日逐幕核查清单.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "场景核查.json").write_text(json.dumps({"summary": summary, "frames": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
