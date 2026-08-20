from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.application.gameplay_governance_service import (
    GameplayGovernanceService,
)
from serious_game_backend.application.scripted_delta_resolver import (
    ScriptedDeltaResolver,
)
from serious_game_backend.application.scripted_effect_service import (
    ScriptedEffectService,
)
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.domain.errors import ActionUnavailableError
from serious_game_backend.domain.llm import GovernanceLLMResult
from serious_game_backend.domain.story import ScriptedEffects
from serious_game_backend.infrastructure.repositories.codec import (
    decode_session,
    encode_session,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class GameplayGovernanceTests(unittest.TestCase):
    def test_contract_numeric_terms_require_field_context(self) -> None:
        text = "付款日：D10；搬离日：D10；交房日：D10。"
        self.assertFalse(
            GameplayGovernanceService._contract_term_is_present(
                text, "cash_amount", 10
            )
        )
        self.assertFalse(
            GameplayGovernanceService._contract_term_is_present(
                "违约金10万元。" + text, "cash_amount", 10
            )
        )
        self.assertTrue(
            GameplayGovernanceService._contract_term_is_present(
                "现金补偿10万元。" + text, "cash_amount", 10
            )
        )
        for negated_text in (
            "非现金补偿10万元。",
            "非现金补偿款10万元。",
            "不支付现金10万元。",
            "本合同不另行支付现金10万元。",
            "无需支付现金10万元。",
            "不含现金补偿10万元。",
        ):
            with self.subTest(negated_text=negated_text):
                self.assertFalse(
                    GameplayGovernanceService._contract_term_is_present(
                        negated_text + text, "cash_amount", 10
                    )
                )
        self.assertFalse(
            GameplayGovernanceService._contract_term_is_present(
                "搬离日：D10。", "payment_day", 10
            )
        )
        self.assertTrue(
            GameplayGovernanceService._contract_term_is_present(
                "付款日：D10。", "payment_day", 10
            )
        )

    def setUp(self) -> None:
        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            repository="memory",
            role_llm_provider="fake",
        )
        self.runtime = build_container(settings)
        self.client = TestClient(create_app(settings, self.runtime))
        self.headers = {"X-Account-ID": "acct_gameplay_governance"}
        response = self.client.post(
            "/api/game/session",
            headers=self.headers,
            json={"client_request_id": "governance-session-0001"},
        )
        self.assertEqual(201, response.status_code, response.text)
        self.state = response.json()
        self.session_id = self.state["session_id"]

    def _post(self, path: str, payload: dict, expected: int = 200) -> dict:
        response = self.client.post(
            f"/api/game/session/{self.session_id}{path}",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(expected, response.status_code, response.text)
        return response.json()

    def _put(self, path: str, payload: dict, expected: int = 200) -> dict:
        response = self.client.put(
            f"/api/game/session/{self.session_id}{path}",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(expected, response.status_code, response.text)
        return response.json()

    def _resolve_opening(self) -> None:
        pending = self.state["pending_decision"]
        response = self._post("/action", {
            "input_mode": "decision",
            "client_action_id": "governance-opening-0001",
            "state_version": self.state["state_version"],
            "decision_id": pending["decision_id"],
            "option_id": pending["option_ids"][0],
        })
        self.state = response["visible_state"]

    def test_repeated_state_get_is_pure_and_does_not_persist_demand_sync(
        self,
    ) -> None:
        stored = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        demand_id = "demand_zheng_xiangdong"
        stored.npc_demand_states[demand_id] = {
            "npc_id": "npc_zheng_xiangdong",
            "status": "unknown",
            "updated_day": stored.game_state.story_day,
            "history": [],
        }
        version = stored.state_version
        self.runtime.sessions.save(stored, expected_version=version)
        before = encode_session(stored)

        first = self.client.get(
            f"/api/game/session/{self.session_id}", headers=self.headers
        )
        second = self.client.get(
            f"/api/game/session/{self.session_id}", headers=self.headers
        )
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertNotIn(
            demand_id,
            {item["demand_id"] for item in first.json()["npc_demands"]},
        )
        after = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(before, encode_session(after))
        self.assertEqual(version, after.state_version)

    def _create_authorization_document(
        self, *, deadline_day: int = 10
    ) -> dict:
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "leadership_meeting",
            "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            "lead_npc_id": "npc_feng_jingzhi",
            "topic": "专项房源授权",
            "archive_ids": ["archive:doc_compensation_policy_v1"],
            "proposed_document_type": "implementation_notice",
        }, expected=201)
        meeting = started["meeting"]
        turn = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "请核对专项房源授权上限和公示边界。",
            },
        )
        return self._post(
            f"/governance/meetings/{meeting['meeting_id']}/resolve",
            {
                "state_version": turn["state_version"],
                "adopt": True,
                "resolution": {
                    "decision": "授权最多配置两套首批安置房",
                    "target_scope": "专项安置家庭",
                    "resources": {"housing_d1_120": 2},
                    "resource_mode": "authorization_ceiling",
                    "responsible_ids": [
                        "npc_feng_jingzhi", "npc_zhao_jianguo",
                    ],
                    "deadline_day": deadline_day,
                    "public_scope": ["全村36户"],
                    "document_title": "专项房源授权实施通知",
                },
            },
        )

    def test_fixed_identity_permissions_actions_and_initial_policy(self) -> None:
        self.assertEqual("mayor", self.state["story"]["origin"]["origin_id"])
        origins = self.client.get("/api/game/origins", headers=self.headers).json()
        self.assertFalse(origins["selection_required"])
        self.assertEqual([], origins["origins"])

        actions = self.client.get(
            f"/api/game/session/{self.session_id}/actions",
            headers=self.headers,
        ).json()["actions"]
        self.assertEqual(
            {
                "household_visit",
                "cadre_interview",
                "leadership_meeting",
                "inspect_archives",
            },
            {item["action_id"] for item in actions},
        )
        overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        self.assertEqual({"1.1", "1.2", "1.3", "1.4", "1.5"},
                         set(overview["permissions"]))
        policy = next(
            item for item in overview["documents"]
            if item["document_id"] == "doc_compensation_policy_v1"
        )
        self.assertEqual("published", policy["status"])
        self.assertIn("全村36户", policy["public_scope"])
        self.assertEqual(36, sum(
            item["capacity"] for item in overview["resources"]["resource_pools"]
            if item["category"] == "housing"
        ))

    def test_archive_inspection_returns_content_and_supports_persisted_reread(
        self,
    ) -> None:
        self._resolve_opening()
        archive_id = "archive_project_brief"
        before = self.client.get(
            (
                f"/api/game/session/{self.session_id}/governance/archives/"
                f"{archive_id}"
            ),
            headers=self.headers,
        )
        self.assertEqual(409, before.status_code, before.text)

        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "inspect_archives",
            "archive_ids": [archive_id],
        }, expected=201)

        self.assertEqual(1, started["cost_action_points"])
        self.assertEqual("completed", started["action"]["status"])
        self.assertEqual(archive_id, started["archives"][0]["archive_id"])
        self.assertTrue(started["archives"][0]["content"])
        self.assertIn(1, started["archives"][0]["read_at_days"])

        reread = self.client.get(
            (
                f"/api/game/session/{self.session_id}/governance/archives/"
                f"{archive_id}"
            ),
            headers=self.headers,
        )
        self.assertEqual(200, reread.status_code, reread.text)
        self.assertEqual(
            started["archives"][0]["content"],
            reread.json()["archive"]["content"],
        )
        stored = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertEqual(7, stored.game_state.action_points)
        self.assertEqual([1], stored.archive_records[archive_id].read_at_days)

    def test_governance_npcs_unlock_with_story_progress(self) -> None:
        def visible_ids(catalog: str) -> set[str]:
            overview = self.client.get(
                f"/api/game/session/{self.session_id}/governance",
                headers=self.headers,
            ).json()
            return {
                item["target_id"]
                for item in overview["target_catalogs"][catalog]
            }

        initial_meeting_ids = visible_ids("meeting_participants")
        self.assertNotIn("npc_zhou_dashan", initial_meeting_ids)
        self.assertIn("npc_zhao_jianguo", initial_meeting_ids)
        self.assertIn("npc_feng_jingzhi", initial_meeting_ids)
        self.assertNotIn("npc_sun_qiang", initial_meeting_ids)
        self.assertNotIn("npc_zhang_li", initial_meeting_ids)
        self.assertNotIn("npc_he_tiezhu", initial_meeting_ids)

        self._resolve_opening()
        blocked = self.client.post(
            f"/api/game/session/{self.session_id}/governance/actions",
            headers=self.headers,
            json={
                "state_version": self.state["state_version"],
                "action_kind": "cadre_interview",
                "target_ids": ["npc_sun_qiang"],
                "topic": "提前接触",
            },
        )
        self.assertEqual(409, blocked.status_code, blocked.text)

        ordinary_attendee = self.client.post(
            f"/api/game/session/{self.session_id}/governance/actions",
            headers=self.headers,
            json={
                "state_version": self.state["state_version"],
                "action_kind": "leadership_meeting",
                "target_ids": ["npc_zhou_dashan", "npc_zhao_jianguo"],
                "lead_npc_id": "npc_zhao_jianguo",
                "topic": "测试不合规参会名单",
            },
        )
        self.assertEqual(409, ordinary_attendee.status_code, ordinary_attendee.text)

        missing_lead = self.client.post(
            f"/api/game/session/{self.session_id}/governance/actions",
            headers=self.headers,
            json={
                "state_version": self.state["state_version"],
                "action_kind": "leadership_meeting",
                "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
                "topic": "测试缺少分管领导",
            },
        )
        self.assertEqual(409, missing_lead.status_code, missing_lead.text)

        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        session.game_state = replace(
            session.game_state,
            story_day=7,
            days_left=84,
        )
        self.runtime.sessions.save(
            session, expected_version=session.state_version
        )
        progressed_ids = visible_ids("meeting_participants")
        self.assertIn("npc_sun_qiang", progressed_ids)
        self.assertNotIn("npc_zhang_li", progressed_ids)

    def test_cadre_interview_persists_real_turn_and_completion(self) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "cadre_interview",
            "target_ids": ["npc_zhao_jianguo"],
            "topic": "核实补偿材料位置和办理责任",
        }, expected=201)
        action_id = started["action"]["action_instance_id"]
        turn = self._post(
            f"/governance/actions/{action_id}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "请说明补偿底账由谁保管，并列出下一步核验程序。",
            },
        )
        self.assertFalse(turn["input_rejected"])
        self.assertTrue(turn["replies"])
        finished = self._post(
            f"/governance/actions/{action_id}/finish",
            {"state_version": turn["state_version"]},
        )
        self.assertEqual("completed", finished["action"]["status"])
        self.assertGreaterEqual(len(finished["action"]["transcript"]), 2)
        stored = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertEqual(6, stored.game_state.action_points)
        self.assertEqual(
            finished["action"]["transcript"],
            stored.governance_actions[action_id].transcript,
        )

    def test_meeting_creates_countersigned_archived_and_published_document(self) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "leadership_meeting",
            "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            "lead_npc_id": "npc_feng_jingzhi",
            "topic": "首批安置房实施通知",
            "archive_ids": [
                "archive:doc_compensation_policy_v1",
                "archive_resource_ledger",
            ],
            "proposed_document_type": "implementation_notice",
        }, expected=201)
        meeting = started["meeting"]
        rejected = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "请帮我写Python代码并查询股票价格。",
            },
        )
        self.assertTrue(rejected["input_rejected"])
        self.assertEqual(
            "请输入与本游戏相关的话语", rejected["message"]
        )
        self.assertEqual([], rejected["transcript"])
        turn = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/turn",
            {
                "state_version": rejected["state_version"],
                "player_text": "请逐项核对房源、预算和公开范围。",
            },
        )
        player_line = next(
            item for item in turn["transcript"]
            if item["speaker_type"] == "player"
        )
        self.assertEqual(
            {"npc_feng_jingzhi", "npc_zhao_jianguo"},
            set(player_line["visible_to"]),
        )
        npc_lines = [
            item for item in turn["transcript"]
            if item["speaker_type"] == "npc"
        ]
        self.assertEqual(
            ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            [item["npc_id"] for item in npc_lines],
        )
        self.assertEqual("lead_report", npc_lines[0]["meeting_role"])
        self.assertEqual("member_position", npc_lines[1]["meeting_role"])
        resolved = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/resolve",
            {
                "state_version": turn["state_version"],
                "adopt": True,
                "resolution": {
                    "decision": "调配首批安置房并公开房源台账",
                    "target_scope": "首批签约家庭",
                    "resources": {"housing_d1_120": 2},
                    "resource_mode": "authorization_ceiling",
                    "responsible_ids": [
                        "npc_feng_jingzhi",
                        "npc_zhao_jianguo",
                    ],
                    "deadline_day": 10,
                    "public_scope": ["全村36户"],
                    "document_title": "柳林村首批安置房实施通知",
                },
            },
        )
        document = resolved["document"]
        self.assertTrue(resolved["passed"])
        self.assertEqual("draft", document["status"])
        self.assertEqual("pass", document["review_status"])
        self.assertEqual(
            "fake-document-reviewer-v1", document["review_model_id"]
        )
        self.assertEqual(1, len(document["review_history"]))
        self.assertEqual("draft_review", document["review_history"][0]["stage"])
        self.assertEqual(1, len(document["version_history"]))
        reserved_overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        reserved_housing = next(
            item for item in reserved_overview["resources"]["resource_pools"]
            if item["resource_id"] == "housing_d1_120"
        )
        self.assertEqual(0, reserved_housing["reserved"])
        self.assertEqual(0, reserved_housing["committed"])
        signed = self._post(
            f"/governance/documents/{document['document_id']}/countersign",
            {
                "state_version": resolved["state_version"],
                "npc_id": "npc_feng_jingzhi",
            },
        )
        self.assertEqual("approved", signed["document"]["status"])
        issued = self._post(
            f"/governance/documents/{document['document_id']}/issue",
            {"state_version": signed["state_version"]},
        )
        self.assertEqual("issued", issued["document"]["status"])
        persisted = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        restored = decode_session(encode_session(persisted))
        restored_document = restored.administrative_documents[
            document["document_id"]
        ]
        self.assertEqual("pass", restored_document.review_status)
        self.assertEqual(1, len(restored_document.review_history))
        self.assertEqual(1, len(restored_document.version_history))
        issued_overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        issued_housing = next(
            item for item in issued_overview["resources"]["resource_pools"]
            if item["resource_id"] == "housing_d1_120"
        )
        self.assertEqual(0, issued_housing["reserved"])
        self.assertEqual(0, issued_housing["committed"])
        issued_document = next(
            item for item in issued_overview["documents"]
            if item["document_id"] == document["document_id"]
        )
        self.assertEqual(
            {
                "authorized": 2,
                "drawn": 0,
                "remaining": 2,
            },
            issued_document["authorization_status"]["housing_d1_120"],
        )
        published = self._post(
            f"/governance/documents/{document['document_id']}/publish",
            {
                "state_version": issued["state_version"],
                "scope": ["全村36户"],
            },
        )
        self.assertEqual("published", published["document"]["status"])
        self.assertEqual(0, published["cost_action_points"])

        overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        archive = next(
            item for item in overview["archives"]
            if item["source_id"] == document["document_id"]
        )
        self.assertEqual("public", archive["confidentiality"])
        self.assertEqual("E3", archive["evidence_level"])

    def test_meeting_authorization_ceiling_does_not_consume_resources(self) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "leadership_meeting",
            "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            "lead_npc_id": "npc_feng_jingzhi",
            "topic": "房源授权上限",
            "archive_ids": ["archive:doc_compensation_policy_v1"],
            "proposed_document_type": "implementation_notice",
        }, expected=201)
        meeting = started["meeting"]
        turn = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "只讨论授权上限，不提前锁定具体房源。",
            },
        )
        resolved = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/resolve",
            {
                "state_version": turn["state_version"],
                "adopt": True,
                "resolution": {
                    "decision": "授权县政府在上限内另行逐户配置",
                    "target_scope": "全村36户",
                    "resources": {"housing_d1_120": 2},
                    "resource_mode": "authorization_ceiling",
                    "responsible_ids": [
                        "npc_feng_jingzhi", "npc_zhao_jianguo",
                    ],
                    "deadline_day": 10,
                    "public_scope": ["全村36户"],
                    "document_title": "房源配置授权上限通知",
                },
            },
        )
        self.assertEqual(
            {"housing_d1_120": 2},
            resolved["meeting"]["resolution"][
                "resource_authorization_limits"
            ],
        )
        overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        housing = next(
            item for item in overview["resources"]["resource_pools"]
            if item["resource_id"] == "housing_d1_120"
        )
        self.assertEqual(0, housing["reserved"])
        self.assertEqual(0, housing["committed"])

    def test_meeting_rejects_document_resource_reservation_mode(self) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "leadership_meeting",
            "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            "lead_npc_id": "npc_feng_jingzhi",
            "topic": "专项房源安排",
            "archive_ids": ["archive:doc_compensation_policy_v1"],
            "proposed_document_type": "implementation_notice",
        }, expected=201)
        meeting = started["meeting"]
        turn = self._post(
            f"/governance/meetings/{meeting['meeting_id']}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "讨论文件授权和后续逐户执行。",
            },
        )
        response = self.client.post(
            (
                f"/api/game/session/{self.session_id}/governance/"
                f"meetings/{meeting['meeting_id']}/resolve"
            ),
            headers=self.headers,
            json={
                "state_version": turn["state_version"],
                "adopt": True,
                "resolution": {
                    "decision": "预留两套房源",
                    "target_scope": "专项安置家庭",
                    "resources": {"housing_d1_120": 2},
                    "resource_mode": "reserve",
                    "responsible_ids": [
                        "npc_feng_jingzhi", "npc_zhao_jianguo",
                    ],
                    "deadline_day": 10,
                    "public_scope": ["全村36户"],
                    "document_title": "专项房源通知",
                },
            },
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn("只能形成资源授权上限", response.text)

    def test_document_review_agent_repairs_unstructured_promises(self) -> None:
        self._resolve_opening()
        resolved = self._create_authorization_document()
        document = resolved["document"]
        response = self.client.put(
            (
                f"/api/game/session/{self.session_id}/governance/"
                f"documents/{document['document_id']}"
            ),
            headers=self.headers,
            json={
                "state_version": resolved["state_version"],
                "content": (
                    document["content"]
                    + "\n另行追加housing_d1_140一套，并增加999万元。"
                ),
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        repaired = response.json()["document"]
        self.assertEqual("pass", repaired["review_status"])
        self.assertNotIn("housing_d1_140", repaired["content"])
        self.assertNotIn("999", repaired["content"])
        self.assertEqual(3, repaired["version"])
        self.assertEqual(
            ["needs_revision", "pass"],
            [
                item["status"]
                for item in repaired["review_history"][-2:]
            ],
        )
        self.assertEqual(
            "document_revision_agent",
            repaired["version_history"][-1]["created_by"],
        )
        self.assertIn(
            "DOC-AUDIT-DETERMINISTIC-001",
            repaired["revision_history"][-1]["addressed_issue_ids"],
        )

    def test_invalid_model_document_is_repaired_from_resolution(self) -> None:
        delegate = self.runtime.gameplay_governance._gateway

        class ExtraPromiseGateway:
            def __getattr__(self, name):
                return getattr(delegate, name)

            def run_governance_task(self, context):
                result = delegate.run_governance_task(context)
                if context.task != "draft_document":
                    return result
                return GovernanceLLMResult(
                    task=result.task,
                    data={
                        **result.data,
                        "document_text": (
                            str(result.data["document_text"])
                            + "\n另行追加999万元。"
                        ),
                    },
                    model_id=result.model_id,
                )

        self.runtime.gameplay_governance._gateway = ExtraPromiseGateway()
        self._resolve_opening()
        resolved = self._create_authorization_document()

        self.assertTrue(resolved["passed"])
        self.assertNotIn("999", resolved["document"]["content"])
        self.assertIn(
            "housing_d1_120=2", resolved["document"]["content"]
        )
        self.assertEqual("pass", resolved["document"]["review_status"])
        self.assertGreaterEqual(
            len(resolved["document"]["review_history"]), 2
        )
        self.assertEqual(
            "document_revision_agent",
            resolved["document"]["version_history"][-1]["created_by"],
        )

    def test_unrelated_household_input_has_no_side_effect_and_can_cancel(
        self,
    ) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "household_visit",
            "target_ids": ["npc_zhou_dashan"],
            "topic": "逐户合同",
        }, expected=201)
        action_id = started["action"]["action_instance_id"]
        rejected = self._post(
            f"/governance/actions/{action_id}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "请写一段代码模拟签约合同。",
            },
        )
        self.assertTrue(rejected["input_rejected"])
        self.assertIsNone(rejected["contract_batch_proposal"])
        self.assertEqual([], rejected["acquired_archive_ids"])
        overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        action = next(
            item for item in overview["governance_actions"]
            if item["action_instance_id"] == action_id
        )
        self.assertEqual([], action["transcript"])
        cancelled = self._post(
            f"/governance/actions/{action_id}/cancel",
            {"state_version": rejected["state_version"]},
        )
        self.assertEqual("cancelled", cancelled["action"]["status"])

    def test_household_and_meeting_turns_stream_npc_deltas(self) -> None:
        self._resolve_opening()
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "household_visit",
            "target_ids": ["npc_zhou_dashan"],
            "topic": "核实搬迁诉求",
        }, expected=201)
        action_id = started["action"]["action_instance_id"]
        with self.client.stream(
            "POST",
            (
                f"/api/game/session/{self.session_id}/governance/actions/"
                f"{action_id}/turn/stream"
            ),
            headers=self.headers,
            json={
                "state_version": started["state_version"],
                "player_text": "请说明住房和补偿方面最需要解决的问题。",
            },
        ) as response:
            self.assertEqual(200, response.status_code)
            events = [json.loads(line) for line in response.iter_lines() if line]
        self.assertEqual("stream_start", events[0]["type"])
        self.assertEqual("complete", events[-1]["type"])
        self.assertEqual(1, sum(item["type"] == "npc_start" for item in events))
        deltas = [item["delta"] for item in events if item["type"] == "npc_delta"]
        self.assertGreater(len(deltas), 1)
        result = events[-1]["result"]
        self.assertEqual(result["replies"][0]["text"], "".join(deltas))

        finished = self._post(
            f"/governance/actions/{action_id}/finish",
            {"state_version": result["state_version"]},
        )
        meeting_started = self._post("/governance/actions", {
            "state_version": finished["state_version"],
            "action_kind": "leadership_meeting",
            "target_ids": ["npc_feng_jingzhi", "npc_zhao_jianguo"],
            "lead_npc_id": "npc_feng_jingzhi",
            "topic": "明确补偿公开程序",
        }, expected=201)
        meeting = meeting_started["meeting"]
        with self.client.stream(
            "POST",
            (
                f"/api/game/session/{self.session_id}/governance/meetings/"
                f"{meeting['meeting_id']}/turn/stream"
            ),
            headers=self.headers,
            json={
                "state_version": meeting_started["state_version"],
                "player_text": "请分别说明责任分工和七日内可公开的材料。",
                "addressed_npc_id": None,
            },
        ) as response:
            self.assertEqual(200, response.status_code)
            meeting_events = [
                json.loads(line) for line in response.iter_lines() if line
            ]
        self.assertEqual(2, sum(
            item["type"] == "npc_start" for item in meeting_events
        ))
        self.assertEqual(2, sum(
            item["type"] == "npc_end" for item in meeting_events
        ))
        speaker_events = [
            (item["type"], item.get("npc_id"))
            for item in meeting_events
            if item["type"] in {
                "npc_thinking_start", "npc_thinking_end", "npc_start", "npc_end"
            }
        ]
        expected_order = ["npc_feng_jingzhi", "npc_zhao_jianguo"]
        self.assertEqual(
            [
                event
                for npc_id in expected_order
                for event in (
                    ("npc_thinking_start", npc_id),
                    ("npc_thinking_end", npc_id),
                    ("npc_start", npc_id),
                    ("npc_end", npc_id),
                )
            ],
            speaker_events,
        )
        self.assertEqual("complete", meeting_events[-1]["type"])

    def test_representative_request_creates_independent_contracts_and_settles_resources(self) -> None:
        self._resolve_opening()
        authorization = self._create_authorization_document()
        authorization_document = authorization["document"]
        countersigned = self._post(
            (
                f"/governance/documents/"
                f"{authorization_document['document_id']}/countersign"
            ),
            {
                "state_version": authorization["state_version"],
                "npc_id": "npc_feng_jingzhi",
            },
        )
        issued_authorization = self._post(
            (
                f"/governance/documents/"
                f"{authorization_document['document_id']}/issue"
            ),
            {"state_version": countersigned["state_version"]},
        )
        self.state["state_version"] = issued_authorization["state_version"]
        started = self._post("/governance/actions", {
            "state_version": self.state["state_version"],
            "action_kind": "household_visit",
            "target_ids": ["npc_zhou_dashan"],
            "topic": "逐户合同",
        }, expected=201)
        action_id = started["action"]["action_instance_id"]
        turn = self._post(
            f"/governance/actions/{action_id}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "我明确向你和你代表的每一户分别发起合同，请逐户签约。",
            },
        )
        proposal = turn["contract_batch_proposal"]
        self.assertEqual(6, len(proposal["household_ids"]))
        confirmed = self._post(
            f"/governance/contract-batches/{proposal['batch_id']}/confirm",
            {
                "state_version": turn["state_version"],
                "confirmed": True,
            },
        )
        contracts = confirmed["contracts"]
        self.assertEqual(6, len(contracts))
        self.assertEqual(6, len({
            item["signatory_name"] for item in contracts
        }))

        contract = next(
            item for item in contracts if item["household_id"] == "ZDS-01"
        )
        detail = self.client.get(
            (
                f"/api/game/session/{self.session_id}/governance/"
                f"contracts/{contract['contract_id']}"
            ),
            headers=self.headers,
        )
        self.assertEqual(200, detail.status_code)
        self.assertEqual(
            contract["contract_id"], detail.json()["contract"]["contract_id"]
        )
        term_payload = {
            "policy_document_id": "doc_compensation_policy_v1",
            "cash_amount": 100,
            "budget_envelope": "property_land",
            "housing_resource_id": "housing_d1_120",
            "service_allocations": {},
            "payment_day": 2,
            "move_out_day": 20,
            "housing_delivery_day": 20,
            "transition_months": 12,
            "public_window_reward": True,
            "approval_document_ids": [
                authorization_document["document_id"]
            ],
            "authorization_confirmed": False,
            "real_unit_viewed": False,
            "ledger_disclosed": False,
            "old_case_resolved": False,
            "prior_payment_verified": False,
        }
        first_terms = self._put(
            f"/governance/contracts/{contract['contract_id']}/terms",
            {
                "state_version": confirmed["state_version"],
                **term_payload,
            },
        )
        self.assertEqual("pass", first_terms["contract"]["audit_status"])
        self.assertEqual(
            "fake-contract-auditor-v1",
            first_terms["contract"]["audit_model_id"],
        )
        first_review = self._post(
            f"/governance/contracts/{contract['contract_id']}/review",
            {"state_version": first_terms["state_version"]},
        )
        self.assertEqual(
            "explanation_requested", first_review["contract"]["status"]
        )
        self.assertEqual(
            "explain",
            first_review["contract"]["review_history"][0]["decision"],
        )
        term_payload["service_allocations"] = {
            "grave_relocation_service": 1
        }
        terms = self._put(
            f"/governance/contracts/{contract['contract_id']}/terms",
            {
                "state_version": first_review["state_version"],
                **term_payload,
            },
        )
        unauthorized = self._put(
            f"/governance/contracts/{contract['contract_id']}/text",
            {
                "state_version": terms["state_version"],
                "text": (
                    terms["contract"]["contract_text"]
                    + "\n除上述补偿外，再额外支付100万元专项补助。"
                ),
            },
        )
        self.assertEqual("reject", unauthorized["contract"]["audit_status"])
        issue = unauthorized["contract"]["audit_result"]["issues"][0]
        self.assertIn("专项补助", issue["text_quote"])
        self.assertTrue(issue["message"])
        self.assertTrue(issue["suggestion"])
        blocked_review = self.client.post(
            (
                f"/api/game/session/{self.session_id}/governance/"
                f"contracts/{contract['contract_id']}/review"
            ),
            headers=self.headers,
            json={"state_version": unauthorized["state_version"]},
        )
        self.assertEqual(409, blocked_review.status_code)
        self.assertIn("专业审校尚未通过", blocked_review.text)
        repaired = self._put(
            f"/governance/contracts/{contract['contract_id']}/text",
            {
                "state_version": unauthorized["state_version"],
                "text": terms["contract"]["contract_text"],
            },
        )
        self.assertEqual("pass", repaired["contract"]["audit_status"])
        review = self._post(
            f"/governance/contracts/{contract['contract_id']}/review",
            {"state_version": repaired["state_version"]},
        )
        self.assertEqual("accepted", review["contract"]["status"])
        self.assertEqual(2, len(review["contract"]["review_history"]))
        self.assertEqual(
            "explain",
            review["contract"]["review_history"][0]["decision"],
        )
        self.assertEqual(
            "accept",
            review["contract"]["review_history"][1]["decision"],
        )
        persisted = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        restored = decode_session(encode_session(persisted))
        self.assertEqual(
            2,
            len(
                restored.household_contracts[
                    contract["contract_id"]
                ].review_history
            ),
        )
        self.assertEqual(
            "预占至D3，尚未支付",
            review["contract"]["resource_hold_status"],
        )
        reviewed_session = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertEqual(7800, reviewed_session.game_state.budget_remaining)
        with self.assertRaises(ActionUnavailableError):
            ScriptedEffectService(ScriptedDeltaResolver()).apply(
                reviewed_session,
                self.runtime.packages.get("pkg_gameplay_v2"),
                ScriptedEffects(
                    ledger_deltas={"budget_remaining": (-7750, -7750)}
                ),
                source_id="test-player-choice",
                resource_authority="player_choice",
                resource_reference="test-choice-id",
            )
        reserved_overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        authorization_status = next(
            item for item in reserved_overview["documents"]
            if item["document_id"] == authorization_document["document_id"]
        )["authorization_status"]["housing_d1_120"]
        self.assertEqual(
            {"authorized": 2, "drawn": 1, "remaining": 1},
            authorization_status,
        )
        reserved_housing = next(
            item for item in reserved_overview["resources"]["resource_pools"]
            if item["resource_id"] == "housing_d1_120"
        )
        self.assertEqual(1, reserved_housing["reserved"])
        self.assertEqual(0, reserved_housing["committed"])
        self.assertEqual(
            "预占至D3，尚未支付",
            next(
                item["display_status"]
                for item in reserved_overview["resources"][
                    "active_reservations"
                ]
                if item["resource_id"] == "housing_d1_120"
            ),
        )
        signed = self._post(
            f"/governance/contracts/{contract['contract_id']}/sign",
            {
                "state_version": review["state_version"],
                "confirmed": True,
            },
        )
        self.assertTrue(signed["signed"])
        self.assertEqual(
            "已签署并占用资源，尚未支付",
            signed["contract"]["resource_hold_status"],
        )
        self.assertEqual(
            1,
            signed["visible_state"]["ledger"]["signed_households"]["signed"],
        )
        overview = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        housing = next(
            item for item in overview["resources"]["resource_pools"]
            if item["resource_id"] == "housing_d1_120"
        )
        self.assertEqual(1, housing["used"])
        self.assertEqual(0, housing["reserved"])
        self.assertEqual(1, housing["committed"])
        self.assertEqual(
            100,
            overview["resources"]["budget_envelopes"]["property_land"]["used"],
        )
        contract_entries = [
            item for item in overview["resource_ledger"]
            if item["source_id"] == contract["contract_id"]
        ]
        self.assertTrue(any(
            item["change_kind"] == "reservation"
            and item["source_type"] == "contract_review"
            for item in contract_entries
        ))
        self.assertTrue(any(
            item["change_kind"] == "ledger_commitment"
            and item["source_type"] == "signed_contract"
            and item["resource_id"] == "budget_committed"
            for item in contract_entries
        ))

    def test_tan_contract_batch_requires_story_unlock(self) -> None:
        self._resolve_opening()
        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        session.pending_decision = None
        session.game_state = replace(
            session.game_state,
            story_day=53,
            days_left=38,
            action_points=8,
        )
        self.runtime.sessions.save(
            session, expected_version=session.state_version
        )
        started = self._post("/governance/actions", {
            "state_version": session.state_version,
            "action_kind": "household_visit",
            "target_ids": ["npc_tan_laoliu"],
            "topic": "逐户合同",
        }, expected=201)
        action_id = started["action"]["action_instance_id"]
        blocked = self._post(
            f"/governance/actions/{action_id}/turn",
            {
                "state_version": started["state_version"],
                "player_text": "我明确向你和你代表的每一户分别发起合同。",
            },
        )
        self.assertIsNone(blocked["contract_batch_proposal"])

        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        session.flags.update({
            "谭老六愿意进入拟约",
            "谭老六核心矛盾已缓解",
            "谭老六合同批次可发起",
        })
        self.runtime.sessions.save(
            session, expected_version=session.state_version
        )
        unlocked = self._post(
            f"/governance/actions/{action_id}/turn",
            {
                "state_version": blocked["state_version"],
                "player_text": "现在正式向你和你代表的每一户分别发起签约合同。",
            },
        )
        self.assertIsNotNone(unlocked["contract_batch_proposal"])
        self.assertEqual(
            3,
            len(unlocked["contract_batch_proposal"]["household_ids"]),
        )

    def test_all_13_groups_can_complete_36_individual_contracts(self) -> None:
        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        package = self.runtime.packages.get(session.package_id)
        assert package is not None
        session.pending_decision = None
        session.game_state = replace(
            session.game_state,
            story_day=60,
            days_left=31,
            action_points=8,
        )
        session.flags.update(
            str(value)
            for value in package.governance_config.get(
                "contract_batch_gate_flags", {}
            ).values()
        )
        session.flags.update(
            household.signing_lock_flag
            for household in package.households
            if household.signing_lock_flag
        )
        batches = []
        for representative_id in package.governance_config[
            "household_representative_npc_ids"
        ]:
            batch = self.runtime.gameplay_governance._detect_and_create_contract_batch(
                session,
                package,
                representative_id,
                "我明确向你和你代表的每一户分别发起签约合同。",
            )
            self.assertIsNotNone(batch, representative_id)
            batches.append(batch)
        self.runtime.sessions.save(
            session, expected_version=session.state_version
        )

        contracts = []
        state_version = session.state_version
        for batch in batches:
            confirmed = self._post(
                f"/governance/contract-batches/{batch.batch_id}/confirm",
                {"state_version": state_version, "confirmed": True},
            )
            state_version = confirmed["state_version"]
            contracts.extend(confirmed["contracts"])
        self.assertEqual(13, len(batches))
        self.assertEqual(36, len(contracts))
        self.assertEqual(36, len({item["signatory_name"] for item in contracts}))

        pools = {
            str(item["resource_id"]): {
                **item,
                "remaining": int(item["capacity"]),
            }
            for item in package.governance_config["resource_pools"]
        }
        households = {
            item.household_id: item for item in package.households
        }
        required_area = {2: 80, 3: 100, 4: 120, 5: 140}

        def allocate_housing(household) -> str:
            area = required_area[household.resettlement_population]
            needs_accessible = "low_floor" in household.resettlement_preference
            candidates = [
                item
                for item in pools.values()
                if item["category"] == "housing"
                and item["remaining"] > 0
                and int(item["attributes"]["area_m2"]) >= area
                and (
                    not needs_accessible
                    or bool(item["attributes"].get("accessible"))
                )
            ]
            candidates.sort(key=lambda item: (
                int(item["attributes"]["area_m2"]),
                bool(item["attributes"].get("accessible")),
                int(item["available_day"]),
            ))
            self.assertTrue(candidates, household.household_id)
            selected = candidates[0]
            selected["remaining"] -= 1
            return str(selected["resource_id"])

        for contract in contracts:
            household = households[contract["household_id"]]
            allocations = {}
            if household.grave_or_shrine_profile not in {
                "none", "clan_follower", "clan_accounting",
            }:
                allocations["grave_relocation_service"] = 1
            if household.medical_tags:
                allocations["lead_recheck_slot"] = 1
            if "school_continuity" in household.employment_startup_tags:
                allocations["school_transition_seat"] = 1
            term_sheet = {
                "policy_document_id": "doc_compensation_policy_v1",
                "cash_amount": self.runtime.gameplay_governance._standard_cash(
                    package,
                    household,
                    months=0,
                    reward=False,
                ),
                "budget_envelope": "property_land",
                "housing_resource_id": allocate_housing(household),
                "service_allocations": allocations,
                "payment_day": 60,
                "move_out_day": 60,
                "housing_delivery_day": 60,
                "transition_months": 0,
                "public_window_reward": False,
                "approval_document_ids": [],
                "authorization_confirmed": True,
                "real_unit_viewed": True,
                "ledger_disclosed": True,
                "old_case_resolved": True,
                "prior_payment_verified": True,
            }
            drafted = self._put(
                f"/governance/contracts/{contract['contract_id']}/terms",
                {"state_version": state_version, **term_sheet},
            )
            self.assertEqual(
                "pass",
                drafted["contract"]["audit_status"],
                msg=(
                    f"{contract['household_id']}: "
                    f"{drafted['contract']['audit_result']}"
                ),
            )
            reviewed = self._post(
                f"/governance/contracts/{contract['contract_id']}/review",
                {"state_version": drafted["state_version"]},
            )
            self.assertEqual("accepted", reviewed["contract"]["status"])
            signed = self._post(
                f"/governance/contracts/{contract['contract_id']}/sign",
                {
                    "state_version": reviewed["state_version"],
                    "confirmed": True,
                },
            )
            self.assertEqual("signed", signed["contract"]["status"])
            state_version = signed["state_version"]

        completed = self.runtime.sessions.get_owned(
            self.session_id, "acct_gameplay_governance"
        )
        self.assertEqual(36, completed.game_state.signed_households)
        self.assertEqual(
            36,
            sum(
                item.quantity
                for item in completed.resource_reservations
                if item.resource_id.startswith("housing_")
                and item.status == "committed"
            ),
        )
        self.assertEqual(
            5,
            sum(
                item.quantity
                for item in completed.resource_reservations
                if item.resource_id == "grave_relocation_service"
                and item.status == "committed"
            ),
        )


if __name__ == "__main__":
    unittest.main()
