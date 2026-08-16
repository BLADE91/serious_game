from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from serious_game_backend.application.action_cost_policy import (
    political_credit_block_reason,
    quote_cost,
)
from serious_game_backend.application.gameplay_governance_service import (
    GameplayGovernanceService,
)
from serious_game_backend.application.decision_tradeoff_policy import (
    player_facing_metric_deltas,
)
from serious_game_backend.application.npc_demand_service import NPCDemandService
from serious_game_backend.application.progress_broadcast_policy import (
    progress_broadcast,
)
from serious_game_backend.application.story_clock_service import StoryClockService
from serious_game_backend.application.story_flow_service import StoryFlowService
from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.domain.errors import ActionUnavailableError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.domain.gameplay_governance import (
    ContractVersion,
    HouseholdContract,
    ResourceReservation,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryGameSessionRepository,
    InMemoryScriptPackageRepository,
)
from serious_game_backend.infrastructure.repositories.codec import (
    decode_session,
    encode_session,
)
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


PACKAGE_DIR = (
    Path(__file__).resolve().parents[1]
    / "content" / "packages" / "pkg_gameplay_v2"
)


@pytest.fixture(scope="module")
def package():
    return FileScriptPackageLoader().load(PACKAGE_DIR)


def new_session(package, **state_changes) -> GameSession:
    state = GameState.new_game(package.initial_state)
    if state_changes:
        state = replace(state, **state_changes)
    return GameSession(
        session_id="sess_demands",
        account_id="account",
        package_id=package.package_id,
        package_version=package.package_version,
        package_content_hash=package.content_hash,
        random_seed="seed",
        game_state=state,
        origin_id="technical",
    )


def demand_service(session, package):
    sessions = InMemoryGameSessionRepository()
    sessions.create(session)
    service = GameplayGovernanceService(
        sessions,
        InMemoryScriptPackageRepository([package]),
        None,
        None,
        VisibleStateProjector(),
        None,
    )
    return service, sessions


def test_progress_broadcast_only_appears_every_ten_days(package):
    assert progress_broadcast(new_session(package, story_day=9, days_left=81)) is None
    broadcast = progress_broadcast(new_session(
        package,
        story_day=10,
        days_left=80,
        signed_households=4,
    ))
    assert broadcast is not None
    assert broadcast["broadcast_id"] == "progress_d10"
    assert broadcast["tone"] == "encouraging"
    assert broadcast["progress"]["expected"] == 4


def test_progress_broadcast_turns_stern_when_governance_is_slipping(package):
    session = new_session(
        package,
        story_day=20,
        days_left=70,
        signed_households=1,
        public_trust=35,
        social_stability=30,
        media_pressure=70,
        fatigue=75,
    )
    session.npc_demand_states["broken_promise"] = {"status": "breached"}
    broadcast = progress_broadcast(session)
    assert broadcast is not None
    assert broadcast["tone"] == "stern"
    assert "会议纪要" in broadcast["message"]
    assert any("疲惫不能折算" in item for item in broadcast["signals"])
    assert any("承诺违约" in item for item in broadcast["signals"])


def test_progress_broadcast_uses_wry_nudge_for_a_small_delay(package):
    broadcast = progress_broadcast(new_session(
        package,
        story_day=10,
        days_left=80,
        signed_households=2,
    ))
    assert broadcast is not None
    assert broadcast["tone"] == "wry"
    assert "稳步推进" in broadcast["headline"]
    assert "行李不会" in broadcast["message"]


def test_visible_state_projects_the_current_progress_broadcast(package):
    session = new_session(
        package,
        story_day=30,
        days_left=60,
        signed_households=8,
    )
    visible = VisibleStateProjector().project(session, package)
    assert visible["progress_broadcast"]["story_day"] == 30


def test_every_npc_has_one_machine_readable_core_demand(package):
    assert len(package.npc_profiles) == 29
    assert len(package.npc_demands) == 29
    assert {item.npc_id for item in package.npc_demands} == {
        item.npc_id for item in package.npc_profiles
    }
    assert len({item.demand_id for item in package.npc_demands}) == 29
    for demand in package.npc_demands:
        assert all((
            demand.demand_id, demand.npc_id, demand.title, demand.category,
            demand.description, demand.legal_disposition,
        ))
        assert isinstance(demand.discover, dict)
        assert isinstance(demand.commit, dict)
        assert isinstance(demand.satisfy, dict)
        assert isinstance(demand.consequences, dict)


def test_illegal_demands_are_resolved_by_lawful_refusal(package):
    dispositions = {
        item.npc_id: item.legal_disposition for item in package.npc_demands
    }
    assert dispositions["npc_qian_wei"] == "lawfully_refuse"
    assert dispositions["npc_zhou_dashan"] == "lawfully_refuse"
    assert dispositions["npc_zhao_jianguo"] == "lawfully_refuse"
    assert NPCDemandService.allowed_transitions(
        "acknowledged", "lawfully_refuse"
    ) == ["lawfully_refused"]


def test_all_states_exist_but_undiscovered_demands_are_not_projected(package):
    session = new_session(package)
    NPCDemandService.initialize(session, package)
    assert len(session.npc_demand_states) == 29
    public = NPCDemandService.public(session, package)
    assert public
    assert len(public) < 29
    assert all(item["status"] != "unknown" for item in public)
    visible_npcs = GameplayGovernanceService._visible_governance_npc_ids(
        session, package
    )
    assert {item["npc_id"] for item in public}.issubset(visible_npcs)
    assert "demand_zhang_li" not in {item["demand_id"] for item in public}
    assert all(
        item["allowed_transitions"] == []
        for item in public
        if item["status"] == "discovered"
    )


def test_formal_contact_confirms_demand_before_disposition(package):
    session = new_session(package)
    NPCDemandService.initialize(session, package)
    demand = next(
        item for item in package.npc_demands if item.npc_id == "npc_wu_xiuying"
    )
    assert session.npc_demand_states[demand.demand_id]["status"] == "discovered"

    session.logs.append({
        "type": "conversation_started",
        "npc_id": demand.npc_id,
        "story_day": 1,
    })
    NPCDemandService.sync(session, package)

    assert session.npc_demand_states[demand.demand_id]["status"] == "acknowledged"
    public = next(
        item for item in NPCDemandService.public(session, package)
        if item["demand_id"] == demand.demand_id
    )
    assert public["allowed_transitions"] == ["committed"]


def test_legacy_save_defaults_to_empty_demand_state_and_can_be_initialized(package):
    session = new_session(package)
    payload = encode_session(session)
    payload.pop("npc_demand_states")
    restored = decode_session(payload)
    assert restored.npc_demand_states == {}
    NPCDemandService.initialize(restored, package)
    assert len(restored.npc_demand_states) == 29


def test_demand_resource_pool_cannot_be_overbooked(package):
    session = new_session(package)
    NPCDemandService.initialize(session, package)
    for index in range(2):
        session.resource_reservations.append(ResourceReservation(
            reservation_id=f"existing_{index}",
            owner_type="contract",
            owner_id=f"contract_{index}",
            resource_id="housing_d1_100_accessible",
            quantity=1,
            status="committed",
            reserved_day=1,
        ))
    demand = next(
        item for item in package.npc_demands
        if item.demand_id == "demand_yuan_guilan"
    )
    service = GameplayGovernanceService.__new__(GameplayGovernanceService)
    with pytest.raises(ActionUnavailableError):
        service._commit_demand_resources(session, package, demand)
    assert not any(
        item.owner_type == "npc_demand" for item in session.resource_reservations
    )


def test_committed_demand_cannot_be_falsely_delivered_and_consequence_is_once(
    package,
):
    demand = next(
        item for item in package.npc_demands
        if item.demand_id == "demand_zheng_xiangdong"
    )
    session = new_session(package)
    session.npc_demand_states[demand.demand_id] = {
        "npc_id": demand.npc_id,
        "status": "committed",
        "updated_day": 1,
        "history": [],
    }
    session.resource_reservations.append(ResourceReservation(
        reservation_id="demand_legal_review",
        owner_type="npc_demand",
        owner_id=demand.demand_id,
        resource_id="legal_review_slot",
        quantity=1,
        status="committed",
        reserved_day=1,
        committed_day=1,
    ))
    service, sessions = demand_service(session, package)
    version = session.state_version

    with pytest.raises(ActionUnavailableError, match="履约事实"):
        service.dispose_npc_demand(
            account_id=session.account_id,
            session_id=session.session_id,
            state_version=version,
            demand_id=demand.demand_id,
            transition="satisfied",
        )
    stored = sessions.get_owned(session.session_id, session.account_id)
    assert stored is not None
    assert stored.npc_demand_states[demand.demand_id]["status"] == "committed"
    public = next(
        item for item in NPCDemandService.public(stored, package)
        if item["demand_id"] == demand.demand_id
    )
    assert public["allowed_transitions"] == ["breached"]

    stored.game_state = replace(stored.game_state, story_day=2, days_left=88)
    sessions.save(stored, expected_version=version)
    result = service.dispose_npc_demand(
        account_id=session.account_id,
        session_id=session.session_id,
        state_version=version,
        demand_id=demand.demand_id,
        transition="satisfied",
    )
    assert result["demand"]["status"] == "satisfied"
    after = sessions.get_owned(session.session_id, session.account_id)
    assert after is not None
    credit = after.game_state.political_credit
    assert NPCDemandService.apply_consequences(
        after, demand, "satisfied"
    ) == {}
    assert after.game_state.political_credit == credit


def test_visible_projection_is_idempotent_and_does_not_sync_demands(package):
    session = new_session(package)
    before = encode_session(session)
    projector = VisibleStateProjector()

    first = projector.project(session, package)
    second = projector.project(session, package)

    assert first == second
    assert encode_session(session) == before
    assert session.state_version == before["state_version"]
    assert session.npc_demand_states == {}


def test_flag_and_expiry_consequences_are_applied_exactly_once(package):
    source = next(
        item for item in package.npc_demands
        if item.demand_id == "demand_zheng_xiangdong"
    )
    flagged = replace(source, satisfy={"required_flags": ["proof_ready"]})
    flagged_package = replace(
        package,
        npc_demands=tuple(
            flagged if item.demand_id == flagged.demand_id else item
            for item in package.npc_demands
        ),
    )
    session = new_session(flagged_package)
    session.npc_demand_states[flagged.demand_id] = {
        "npc_id": flagged.npc_id,
        "status": "committed",
        "updated_day": 1,
        "history": [],
    }
    session.flags.add("proof_ready")
    before_credit = session.game_state.political_credit
    NPCDemandService.sync(session, flagged_package)
    NPCDemandService.sync(session, flagged_package)
    assert session.game_state.political_credit == before_credit + 3

    expiring = next(
        item for item in package.npc_demands
        if item.demand_id == "demand_shi_wenbin"
    )
    expired_session = new_session(package, story_day=59, days_left=31)
    expired_session.npc_demand_states[expiring.demand_id] = {
        "npc_id": expiring.npc_id,
        "status": "committed",
        "updated_day": 58,
        "history": [],
    }
    before_credit = expired_session.game_state.political_credit
    NPCDemandService.sync(expired_session, package)
    NPCDemandService.sync(expired_session, package)
    assert expired_session.game_state.political_credit == before_credit - 8


def test_capacity_one_demand_reservation_transfers_to_signed_contract(package):
    config = deepcopy(package.governance_config)
    for pool in config["resource_pools"]:
        if pool["resource_id"] == "housing_d1_100":
            pool["capacity"] = 1
    limited_package = replace(package, governance_config=config)
    demand = next(
        item for item in limited_package.npc_demands
        if item.demand_id == "demand_lao_juetou"
    )
    session = new_session(limited_package, story_day=46, days_left=44)
    session.npc_demand_states[demand.demand_id] = {
        "npc_id": demand.npc_id,
        "status": "committed",
        "updated_day": 46,
        "history": [],
    }
    service = GameplayGovernanceService.__new__(GameplayGovernanceService)
    service._commit_demand_resources(session, limited_package, demand)
    contract = HouseholdContract(
        contract_id="contract_capacity_one",
        batch_id="batch_capacity_one",
        household_id="hh_capacity_one",
        signatory_name="老倔头",
        signatory_npc_id=demand.npc_id,
        created_day=46,
        status="accepted",
        term_sheet={
            "budget_envelope": "housing_delivery",
            "cash_amount": 1,
            "housing_resource_id": "housing_d1_100",
            "service_allocations": {},
            "payment_day": 46,
            "move_out_day": 47,
            "housing_delivery_day": 47,
            "public_window_reward": False,
            "policy_document_id": "doc_compensation_policy_v1",
            "approval_document_ids": [],
        },
        versions=[ContractVersion(
            version=1,
            text="老倔头逐户安置合同",
            term_hash="term-hash",
            text_hash="text-hash",
            created_by="test",
        )],
        current_version=1,
    )
    session.household_contracts[contract.contract_id] = contract
    service._reserve_contract_resources(session, limited_package, contract)
    active_housing = [
        item for item in session.resource_reservations
        if item.resource_id == "housing_d1_100"
        and item.status in {"reserved", "committed", "delivered"}
    ]
    assert sum(item.quantity for item in active_housing) == 1
    assert active_housing[0].owner_id == contract.contract_id
    service._validate_contract_reservations(session, contract)

    wired, sessions = demand_service(session, limited_package)
    signed = wired.sign_contract(
        account_id=session.account_id,
        session_id=session.session_id,
        state_version=session.state_version,
        contract_id=contract.contract_id,
        confirmed=True,
    )
    assert signed["signed"] is True
    stored = sessions.get_owned(session.session_id, session.account_id)
    assert stored is not None
    assert stored.npc_demand_states[demand.demand_id]["status"] == "satisfied"
    assert sum(
        item.quantity for item in stored.resource_reservations
        if item.resource_id == "housing_d1_100"
        and item.status in {"reserved", "committed", "delivered"}
    ) == 1


def test_cost_policy_preserves_final_script_fixed_prices(package):
    trust = new_session(package, public_trust=40)
    trust_quote = quote_cost(trust, "home_visit", 7)
    assert trust_quote.friction == 0
    assert trust_quote.final_cost == 7
    media = new_session(package, media_pressure=61)
    assert quote_cost(media, "contact_reporter", 2).final_cost == 2
    cadre = new_session(package, cadre_discontent=61)
    assert quote_cost(cadre, "leadership_meeting", 3).final_cost == 3
    credit = new_session(package, political_credit=20)
    assert political_credit_block_reason(credit, "initiate_accountability") is None
    assert package.action_rules["forced_clearance"].cost_for(
        package.action_cost_tier(90)
    ) == 8


def test_confirmed_demand_does_not_change_fixed_action_price(package):
    session = new_session(package)
    NPCDemandService.initialize(session, package)
    demand = next(
        item for item in package.npc_demands if item.npc_id == "npc_wu_xiuying"
    )
    session.npc_demand_states[demand.demand_id]["status"] = "acknowledged"

    untargeted = quote_cost(session, "home_visit", 2)
    matching = quote_cost(
        session,
        "home_visit",
        2,
        target_npc_ids=("npc_wu_xiuying",),
    )
    other_person = quote_cost(
        session,
        "home_visit",
        2,
        target_npc_ids=("npc_zhou_dashan",),
    )

    assert untargeted.discount == 0
    assert matching.discount == 0
    assert matching.final_cost == 2
    assert other_person.discount == 0


def test_key_character_demands_match_their_story_roles(package):
    resources = {
        item.npc_id: {
            value["resource_id"] for value in item.commit.get("resources", ())
        }
        for item in package.npc_demands
    }
    assert resources["npc_zhou_mancang"] == {"audit_slot"}
    assert resources["npc_ma_changshun"] == {"business_restart_package"}
    assert resources["npc_ning_dehai"] == {"legal_review_slot"}
    assert resources["npc_yang_bo"] == {
        "startup_interest_slot", "broadband_transition_slot",
        "school_transition_seat",
    }
    assert resources["npc_lao_juetou"] == {"housing_d1_100"}
    assert resources["npc_miao_xiwang"] == {
        "lead_recheck_slot", "child_assessment_slot",
    }
    assert resources["npc_deng_shouben"] == {
        "housing_d1_80", "elder_support_slot",
    }


def test_low_stability_does_not_secretly_change_next_day_cap(package):
    class Events:
        @staticmethod
        def trigger_fixed_events(_session, _package):
            return []

    session = new_session(package, social_stability=30)
    StoryClockService(Events()).end_day(session, package)
    assert session.game_state.daily_action_point_cap == 8
    assert not any(
        item.get("type") == "low_stability_action_cap" for item in session.logs
    )


def test_zero_ap_forced_decision_has_no_deadlock_and_preview_hides_internal_fields(package):
    session = new_session(package, action_points=0)
    StoryFlowService()._present_decision_id(session, package, "dp2_08")
    visible = VisibleStateProjector().project(session, package)
    options = visible["pending_decision"]["options"]
    assert options
    assert all("tradeoff_preview" not in item for item in options)
    serialized = json.dumps(options, ensure_ascii=False)
    assert "flag_" not in serialized
    assert "open_flags" not in serialized
    assert "state_assignments" not in serialized


def test_attached_and_collective_decisions_do_not_charge_again(package):
    assert package.decisions["dp1_03"].cost_source == "attached"
    assert package.decisions["ev1_01_reception_bag"].cost_source == "interrupt"
    assert package.decisions["dp2_08"].cost_source == "desk"
    assert package.decisions["dp1_04"].cost_source == "collective"
    assert package.decisions["dp1_04"].action_point_cost == 0


def test_every_decision_option_has_visible_gain_and_cost_preview(package):
    harmful_when_positive = {"media_pressure", "cadre_discontent"}
    for decision in package.decisions.values():
        for option in decision.options:
            deltas = player_facing_metric_deltas(option)
            assert deltas or option.effects.ledger_deltas
            if deltas:
                valences = set()
                for field, (minimum, maximum) in deltas.items():
                    positive = minimum > 0 and maximum > 0
                    beneficial = positive != (field in harmful_when_positive)
                    valences.add("benefit" if beneficial else "cost")
                assert valences == {"benefit", "cost"}
