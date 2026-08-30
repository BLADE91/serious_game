from __future__ import annotations

import argparse
import json
from pathlib import Path

from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


MECHANISM_LABELS = {
    "decision": "剧情决策",
    "archive": "档案调查",
    "conversation": "人物会谈",
    "governance_action": "治理行动",
    "meeting": "班子会议",
    "contract": "合同签约",
    "document": "红头文件",
    "forced_night_conversation": "夜间强制会谈",
}


def _operation_summary(mechanism: str, operation: dict) -> str:
    if mechanism == "decision":
        return f"{operation.get('decision_id')} → {operation.get('option_id')}"
    if mechanism == "archive":
        facts = "、".join(map(str, operation.get("new_fact_ids", ()))) or "无新增事实"
        return f"查阅 {operation.get('archive_id')}；取得 {facts}"
    if mechanism == "conversation":
        return (
            f"与 {operation.get('npc_id')} 会谈（{operation.get('opportunity_id') or '自由会谈'}），"
            f"状态 {operation.get('completion_status')}"
        )
    if mechanism == "governance_action":
        targets = "、".join(map(str, operation.get("target_ids", ()))) or "无指定对象"
        return f"{operation.get('action_kind')}；对象 {targets}；状态 {operation.get('status')}"
    if mechanism == "meeting":
        participants = "、".join(map(str, operation.get("participant_ids", ())))
        return (
            f"议题“{operation.get('topic')}”；参会 {participants}；"
            f"拟制 {operation.get('proposed_document_type') or '无'}"
        )
    if mechanism == "contract":
        return f"{operation.get('household_id')} 合同状态 {operation.get('status')}"
    if mechanism == "document":
        countersigns = "、".join(map(str, operation.get("countersigned_by", ()))) or "无"
        return (
            f"{operation.get('document_type')}《{operation.get('title')}》；"
            f"状态 {operation.get('status')}；会签 {countersigns}"
        )
    if mechanism == "forced_night_conversation":
        participants = "、".join(map(str, operation.get("participant_ids", ())))
        return f"议题“{operation.get('agenda')}”；参与者 {participants}；状态 {operation.get('phase')}"
    return json.dumps(operation, ensure_ascii=False, sort_keys=True)


def build_ending_operation_markdown(routes: list[dict]) -> str:
    """Render every witnessed ending as a legal player-operation guide."""

    lines = [
        "# 全部结局合法达成操作记录",
        "",
        "> 本记录由同一轮真实 API 路线证据生成。只记录玩家可通过正式游戏接口完成的操作，不含改库、改旗标或伪造存档。",
        "",
    ]
    for route in routes:
        record = route.get("operation_record")
        if not isinstance(record, dict):
            raise AssertionError("route evidence is missing operation_record")
        lines.extend([
            f"## {record.get('route_id')}",
            "",
            f"- 主结局：`{record.get('actual_main_ending_id')}`",
            f"- 子结局：`{record.get('actual_sub_ending_id')}`",
            f"- 身份来源：`{record.get('origin_id')}`",
            "",
            "| 时间 | 机制 | 玩家操作与结果 |",
            "|---|---|---|",
        ])
        for item in record.get("operation_sequence", ()):
            mechanism = str(item.get("mechanism", ""))
            summary = _operation_summary(mechanism, dict(item.get("operation", {})))
            summary = summary.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| D{item.get('story_day')} | {MECHANISM_LABELS.get(mechanism, mechanism)} | {summary} |"
            )
        effects = dict(record.get("mechanism_effects", {}))
        investigation = dict(effects.get("investigation", {}))
        contracts = dict(effects.get("contracts", {}))
        documents = dict(effects.get("red_head_documents", {}))
        conversations = dict(effects.get("conversations", {}))
        governance = dict(effects.get("governance", {}))
        lines.extend([
            "",
            "### 机制对本结局的实际影响",
            "",
            f"- 调查：查阅 {investigation.get('archive_read_count', 0)} 份档案，最终掌握 {len(investigation.get('known_fact_ids', ()))} 项事实，用于解锁证据型决策、追问和会议依据。",
            f"- 会谈：完成 {conversations.get('completed_count', 0)} 场普通会谈、{conversations.get('forced_night_count', 0)} 场强制夜谈；其作用是取得人物信息、推动诉求并留下可被后续识别的承诺。",
            f"- 治理与决策：执行 {governance.get('action_count', 0)} 项治理行动、作出 {governance.get('decision_count', 0)} 次剧情决策，直接塑造结局轴。",
            f"- 合同：正式签约户为 {('、'.join(map(str, contracts.get('signed_households', ()))) or '无')}，签约成功即计入进度。",
            f"- 红头文件：形成并公开 {documents.get('published_count', 0)} 份；会议决议、会签人与公开范围见上表，用于把调查和班子意见转成正式治理依据。",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _single_summary(stage: Path) -> dict:
    matches = sorted(stage.rglob("summary.json"))
    if len(matches) != 1:
        raise AssertionError(
            f"{stage.name} must contain exactly one summary.json, got {len(matches)}"
        )
    return json.loads(matches[0].read_text(encoding="utf-8"))


def build_summary(run_dir: Path) -> dict:
    capabilities = json.loads((
        run_dir / "capabilities" / "capability-matrix.json"
    ).read_text(encoding="utf-8"))
    roles = json.loads((run_dir / "roles" / "role-matrix.json").read_text(encoding="utf-8"))
    failures = json.loads((run_dir / "failures" / "failure-matrix.json").read_text(encoding="utf-8"))
    features = _single_summary(run_dir / "features")
    routes = _single_summary(run_dir / "routes")
    night = _single_summary(run_dir / "night")
    browser = json.loads((run_dir / "browser" / "summary.json").read_text(encoding="utf-8"))
    package = FileScriptPackageLoader().load(
        Path(__file__).resolve().parents[1]
        / "content" / "packages" / "pkg_gameplay_v3"
    )
    blockers: list[str] = []
    checks = {
        "capability_fake_calls": int(capabilities.get("fake_calls", 0)),
        "role_fake_calls": int(roles.get("fake_calls", 0)),
        "failure_fake_calls": int(failures.get("fake_calls", 0)),
        "feature_fake_calls": int(features.get("fake_calls", 0)),
        "route_fake_calls": int(routes.get("fake_calls", 0)),
        "night_fake_calls": int(night.get("fake_calls", 0)),
        "main_endings": int(routes.get("main_ending_count", 0)),
        "sub_endings": int(routes.get("sub_ending_count", 0)),
        "route_profiles": int(routes.get("profile_count", 0)),
        "night_cases": len(night.get("cases", ())),
        "browser_passed": int(browser.get("passed", 0)),
    }
    if any(value for key, value in checks.items() if key.endswith("fake_calls")):
        blockers.append("fake_calls")
    for key, expected in (("main_endings", 24), ("sub_endings", 95), ("route_profiles", 95), ("night_cases", 24)):
        if checks[key] != expected:
            blockers.append(key)
    if browser.get("status") != "passed" or browser.get("real_e2e_enabled") is not True:
        blockers.append("browser")
    provenance_path = run_dir / "provenance.json"
    provenance = (
        json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance_path.is_file() else {}
    )
    if provenance.get("workspace_stable") is not True:
        blockers.append("workspace_provenance")
    return {
        "status": "passed" if not blockers else "blocked",
        "publishable": not blockers,
        "release_blockers": sorted(set(blockers)),
        "v3_package_version": package.package_version,
        "v3_content_hash": package.content_hash,
        "checks": checks,
        "provenance": provenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build_summary(args.run_dir.resolve())
    routes_summary = _single_summary(args.run_dir.resolve() / "routes")
    routes = list(routes_summary.get("routes", ()))
    operation_records = [item["operation_record"] for item in routes]
    (args.run_dir / "ending-operation-records.json").write_text(
        json.dumps(operation_records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.run_dir / "ending-operation-records.md").write_text(
        build_ending_operation_markdown(routes), encoding="utf-8"
    )
    (args.run_dir / "release-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["publishable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
