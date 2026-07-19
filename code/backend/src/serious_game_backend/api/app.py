from __future__ import annotations

from contextvars import ContextVar
import secrets

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from serious_game_backend.api.schemas import (
    ActionRequest,
    ConsentSignRequest,
    ConsentWithdrawRequest,
    EndDayRequest,
    LoginRequest,
    RegisterRequest,
    ExportRequestBody,
    GovernancePurposeBody,
    RetentionRunBody,
    SubjectRequestBody,
    StartSessionRequest,
)
from serious_game_backend.application.package_lock import require_locked_package
from serious_game_backend.bootstrap import Container, build_container
from serious_game_backend.config import Settings
from serious_game_backend.domain.errors import DomainError, NotFoundError
from serious_game_backend.domain.errors import (
    AuthenticationRequiredError,
    RegistrationDisabledError,
)
from serious_game_backend.domain.identity import PERMISSION_PLAY, PLAYER, Principal
from serious_game_backend.domain.identity import PERMISSION_OPERATE


_principal_context: ContextVar[Principal | None] = ContextVar(
    "serious_game_principal", default=None
)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    effective_settings = settings or Settings.from_env()
    runtime = container or build_container(effective_settings)
    app = FastAPI(
        title="浊流之下·清江搬迁记后端",
        version="0.1.0",
        description="独立权威游戏运行时；不包含剧本生成器或游戏前端。",
    )
    app.state.container = runtime
    authentication_enabled = (
        effective_settings.environment == "production"
        or effective_settings.auth_required
    )

    def error_response(exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return error_response(exc)

    @app.middleware("http")
    async def production_authentication(request: Request, call_next):
        if not authentication_enabled:
            return await call_next(request)
        public_paths = {
            "/health/live", "/health/ready", "/api/auth/login", "/api/auth/register",
            "/docs", "/openapi.json", "/redoc",
        }
        if request.url.path in public_paths:
            return await call_next(request)
        try:
            principal = runtime.auth.authenticate(
                request.cookies.get(effective_settings.auth_cookie_name)
            )
            if request.url.path.startswith("/api/game"):
                runtime.auth.require(principal, PERMISSION_PLAY)
            if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                runtime.auth.verify_csrf(
                    principal, request.headers.get("X-CSRF-Token")
                )
        except DomainError as exc:
            return error_response(exc)
        context_token = _principal_context.set(principal)
        try:
            return await call_next(request)
        finally:
            _principal_context.reset(context_token)

    def current_account_id(x_account_id: str | None = Header(default=None)) -> str:
        if authentication_enabled:
            principal = _principal_context.get()
            if principal is None:
                raise AuthenticationRequiredError("缺少可信登录身份")
            return principal.account_id
        value = (x_account_id or "").strip()
        if not value:
            raise DomainError("沙盒请求必须提供 X-Account-ID")
        return value

    def privileged_principal() -> Principal:
        principal = _principal_context.get()
        if principal is None:
            raise AuthenticationRequiredError("治理接口仅接受正式 Cookie 登录身份")
        return principal

    def command_gate(session, package) -> dict:
        pending = session.pending_decision is not None
        beat = package.story_day(session.game_state.story_day)
        active = session.status.value == "active"
        allow_actions = beat is None or beat.allow_actions
        allow_end_day = (
            beat is None
            or (
                beat.allow_end_day
                and beat.end_day_requires_flags.issubset(session.flags)
            )
        )
        action_blocked_reason = None
        if not active:
            action_blocked_reason = "本局已经结束"
        elif pending:
            action_blocked_reason = "必须先处理当前决策"
        elif not allow_actions:
            action_blocked_reason = "当前剧情节点不开放自主行动"
        return {
            "can_choose": active and pending,
            "can_act": active and not pending and allow_actions,
            "can_end_day": active and not pending and allow_end_day,
            "action_blocked_reason": action_blocked_reason,
        }

    @app.get("/health/live")
    async def live() -> dict:
        return {"status": "ok", "service": "serious-game-backend"}

    @app.get("/health/ready")
    async def ready() -> dict:
        package = runtime.packages.get(effective_settings.default_package_id)
        return {
            "status": "ready" if package else "not_ready",
            "default_package_id": effective_settings.default_package_id,
            "llm_provider": effective_settings.role_llm_provider,
            "llm_model": effective_settings.role_llm_model,
            "repository": effective_settings.repository,
            "authentication_required": authentication_enabled,
            "self_registration": effective_settings.allow_self_registration,
        }

    @app.post("/api/auth/login")
    async def login(body: LoginRequest, response: Response) -> dict:
        raw_token, csrf_token, principal, expires_at = runtime.auth.login(
            body.username, body.password
        )
        return set_login_cookie(
            response, raw_token, csrf_token, principal, expires_at
        )

    def set_login_cookie(
        response: Response, raw_token: str, csrf_token: str,
        principal: Principal, expires_at: str,
    ) -> dict:
        response.set_cookie(
            key=effective_settings.auth_cookie_name,
            value=raw_token,
            httponly=True,
            secure=effective_settings.auth_cookie_secure,
            samesite="lax",
            max_age=effective_settings.auth_session_ttl_seconds,
            path="/",
        )
        return {
            "account_id": principal.account_id,
            "roles": sorted(principal.roles),
            "csrf_token": csrf_token,
            "expires_at": expires_at,
        }

    @app.post("/api/auth/register", status_code=201)
    async def register(body: RegisterRequest, response: Response) -> dict:
        if not effective_settings.allow_self_registration:
            raise RegistrationDisabledError("当前环境未开放自助注册")
        account = runtime.auth.create_account(
            account_id=f"acct_{secrets.token_hex(16)}",
            username=body.username,
            password=body.password,
            roles=frozenset({PLAYER}),
        )
        raw_token, csrf_token, principal, expires_at = runtime.auth.login(
            account.username, body.password
        )
        return set_login_cookie(
            response, raw_token, csrf_token, principal, expires_at
        )

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response) -> Response:
        runtime.auth.logout(request.cookies.get(effective_settings.auth_cookie_name))
        response.delete_cookie(
            effective_settings.auth_cookie_name,
            path="/",
            secure=effective_settings.auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        response.status_code = 204
        return response

    @app.get("/api/auth/me")
    async def auth_me(x_account_id: str | None = Header(default=None)) -> dict:
        account_id = current_account_id(x_account_id)
        principal = _principal_context.get()
        return {
            "account_id": account_id,
            "roles": sorted(principal.roles) if principal else ["sandbox"],
        }

    @app.get("/api/consent/current")
    async def current_consent(
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        record = runtime.consents.latest(account_id)
        return {
            "required_version": effective_settings.consent_version,
            "document_hash": effective_settings.consent_document_hash,
            "model_provider": effective_settings.consent_model_provider,
            "processing_region": effective_settings.consent_processing_region,
            "record": ({
                "consent_record_id": record.consent_record_id,
                "consent_version": record.consent_version,
                "scopes": sorted(record.scopes),
                "signed_at": record.signed_at,
                "withdrawn_at": record.withdrawn_at,
            } if record else None),
        }

    @app.post("/api/consent")
    async def sign_consent(
        body: ConsentSignRequest,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        record = runtime.consents.sign(
            account_id=account_id,
            consent_version=body.consent_version,
            scopes=frozenset(body.scopes),
        )
        return {
            "consent_record_id": record.consent_record_id,
            "consent_version": record.consent_version,
            "scopes": sorted(record.scopes),
            "signed_at": record.signed_at,
        }

    @app.post("/api/consent/withdraw")
    async def withdraw_consent(
        body: ConsentWithdrawRequest,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        record = runtime.consents.withdraw(account_id=account_id, reason=body.reason)
        return {
            "consent_record_id": record.consent_record_id,
            "withdrawn_at": record.withdrawn_at,
        }

    @app.post("/api/privacy/requests", status_code=202)
    async def create_subject_request(
        body: SubjectRequestBody,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        request = runtime.governance.request_subject_action(
            current_account_id(x_account_id), body.request_type, body.reason
        )
        return {"request_id": request.request_id, "status": request.status,
                "request_type": request.request_type, "created_at": request.created_at}

    @app.post("/api/admin/privacy/requests/{request_id}/process")
    async def process_subject_request(request_id: str, body: GovernancePurposeBody) -> dict:
        result = runtime.governance.process_subject_action(
            privileged_principal(), request_id, purpose=body.purpose
        )
        return {"request_id": result.request_id, "status": result.status,
                "completed_at": result.completed_at, "result": result.result}

    @app.post("/api/admin/research/exports", status_code=202)
    async def request_research_export(body: ExportRequestBody) -> dict:
        job = runtime.governance.request_export(
            privileged_principal(), purpose=body.purpose,
            fields=tuple(body.fields), conditions=body.conditions,
            minimum_cell_size=body.minimum_cell_size,
        )
        return {"export_job_id": job.export_job_id, "status": job.status}

    @app.post("/api/admin/research/exports/{export_job_id}/approve")
    async def approve_research_export(export_job_id: str, body: GovernancePurposeBody) -> dict:
        job = runtime.governance.approve_export(
            privileged_principal(), export_job_id, purpose=body.purpose
        )
        return {"export_job_id": job.export_job_id, "status": job.status,
                "approved_by": job.approved_by}

    @app.post("/api/admin/research/exports/{export_job_id}/materialize")
    async def materialize_research_export(export_job_id: str, body: GovernancePurposeBody) -> dict:
        return runtime.governance.materialize_export(
            privileged_principal(), export_job_id, purpose=body.purpose
        )

    @app.post("/api/admin/retention/run")
    async def run_retention(body: RetentionRunBody) -> dict:
        result = runtime.governance.apply_retention(
            privileged_principal(), cutoff_at=body.cutoff_at,
            policy_version=body.policy_version, purpose=body.purpose,
        )
        return result.__dict__ if hasattr(result, "__dict__") else {
            "policy_version": result.policy_version, "cutoff_at": result.cutoff_at,
            "raw_research_text_removed": result.raw_research_text_removed,
            "auth_sessions_removed": result.auth_sessions_removed,
        }

    @app.post("/api/admin/research/outbox/drain")
    async def drain_research_outbox(limit: int = Query(default=100, ge=1, le=1000)) -> dict:
        principal = privileged_principal()
        runtime.auth.require(principal, PERMISSION_OPERATE)
        return {"dispatched": runtime.research_outbox.drain(limit)}

    @app.post("/api/game/session", status_code=201)
    async def start_session(
        body: StartSessionRequest,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        package_id = body.package_id or effective_settings.default_package_id
        session = runtime.game_sessions.start_session(
            account_id=account_id,
            package_id=package_id,
            client_request_id=body.client_request_id,
            origin_id=body.origin_id,
        )
        package = require_locked_package(runtime.packages, session)
        return runtime.projector.project(session, package)

    @app.get("/api/game/origins")
    async def list_origins(
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        current_account_id(x_account_id)
        package = runtime.packages.get(effective_settings.default_package_id)
        if package is None:
            raise NotFoundError("默认剧本包不存在")
        return {
            "package_id": package.package_id,
            "origins": [
                {
                    "origin_id": item.origin_id,
                    "title": item.title,
                    "description": item.description,
                }
                for item in package.origins.values()
            ],
        }

    @app.get("/api/game/package/validation")
    async def get_package_validation(
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        current_account_id(x_account_id)
        package = runtime.packages.get(effective_settings.default_package_id)
        if package is None:
            raise NotFoundError("默认剧本包不存在")
        return runtime.package_validation.build_report(package)

    @app.get("/api/game/session/latest-active")
    async def get_latest_active(
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.latest_active(account_id)
        package = require_locked_package(runtime.packages, session)
        return runtime.projector.project(session, package)

    @app.get("/api/game/session/{session_id}")
    async def get_session(session_id: str, x_account_id: str | None = Header(default=None)) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        return runtime.projector.project(session, package)

    @app.get("/api/game/session/{session_id}/view")
    async def get_terminal_view(
        session_id: str,
        after: int = Query(default=0, ge=0),
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        gate = command_gate(session, package)
        return {
            "state": runtime.projector.project(session, package),
            "feed": runtime.story_flow.feed_since(session, after),
            "commands": {
                "can_choose": gate["can_choose"],
                "can_act": gate["can_act"],
                "can_end_day": gate["can_end_day"],
                "can_talk": gate["can_act"] and bool(
                    runtime.opportunities.list_available(session, package)
                ),
            },
        }

    @app.get("/api/game/session/{session_id}/feed")
    async def get_narrative_feed(
        session_id: str,
        after: int = Query(default=0, ge=0),
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        require_locked_package(runtime.packages, session)
        return {
            "state_version": session.state_version,
            **runtime.story_flow.feed_since(session, after),
        }

    @app.get("/api/game/session/{session_id}/knowledge")
    async def get_known_knowledge(
        session_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        facts = [
            package.facts[fact_id]
            for fact_id in sorted(session.known_fact_ids)
            if fact_id in package.facts
        ]
        return {
            "state_version": session.state_version,
            "known_fact_ids": sorted(session.known_fact_ids),
            "facts": [
                {
                    "fact_id": item.fact_id,
                    "title": item.title,
                    "text": item.text,
                    "category": item.category,
                }
                for item in facts
            ],
            "clues": [],
            "evidence": [],
        }

    @app.get("/api/game/session/{session_id}/map")
    async def get_map(
        session_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        return runtime.map_service.build(session, package)

    @app.get("/api/game/session/{session_id}/review")
    async def get_review(
        session_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        return runtime.review_service.build(session, package)

    @app.get("/api/game/session/{session_id}/actions")
    async def list_actions(
        session_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        state = session.game_state
        tier = package.action_cost_tier(state.story_day)
        gate = command_gate(session, package)
        available_opportunities = (
            runtime.opportunities.list_available(session, package)
            if gate["can_act"]
            else ()
        )
        result = []
        for rule in package.action_rules.values():
            cost = rule.cost_for(tier)
            opportunity_ids = [
                item.opportunity_id
                for item in available_opportunities
                if item.action_id == rule.action_id
            ]
            available = (
                gate["can_act"]
                and bool(opportunity_ids)
                and state.action_points >= cost
            )
            reason = gate["action_blocked_reason"]
            if reason is None and not opportunity_ids:
                reason = "当前没有可用行动入口"
            elif (
                reason is None
                and rule.daily_cap is not None
                and state.daily_action_counts.get(rule.action_id, 0) >= rule.daily_cap
            ):
                available, reason = False, "今日次数已用尽"
            elif reason is None and rule.half_day and state.half_day_action_used:
                available, reason = False, "今日半日行程已占用"
            elif reason is None and rule.hard_force and state.fatigue >= 75:
                available, reason = False, "当前状态不能执行"
            elif (
                reason is None
                and rule.precondition_flags_any
                and not any(
                    flag in session.flags for flag in rule.precondition_flags_any
                )
            ):
                available, reason = False, "前置条件尚未满足"
            elif reason is None and state.action_points < cost:
                reason = "行动点不足"
            result.append({
                "action_id": rule.action_id,
                "name": rule.name,
                "category": rule.category,
                "cost_action_points": cost,
                "available": available,
                "unavailable_reason": reason,
                "opportunity_ids": opportunity_ids,
            })
        return {"state_version": session.state_version, "cost_tier": tier.value, "actions": result}

    @app.get("/api/game/session/{session_id}/opportunities")
    async def list_opportunities(
        session_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        session = runtime.game_sessions.get_owned(session_id, account_id)
        package = require_locked_package(runtime.packages, session)
        gate = command_gate(session, package)
        values = (
            runtime.opportunities.list_available(session, package)
            if gate["can_act"]
            else ()
        )
        tier = package.action_cost_tier(session.game_state.story_day)
        return {
            "state_version": session.state_version,
            "blocked_reason": gate["action_blocked_reason"],
            "opportunities": [
                {
                    "opportunity_id": item.opportunity_id,
                    "npc_id": item.npc_id,
                    "entry_type": item.entry_type,
                    "action_id": item.action_id,
                    "cost_action_points": package.action_rules[item.action_id].cost_for(tier),
                }
                for item in values
            ],
        }

    @app.post("/api/game/session/{session_id}/action")
    async def execute_action(
        session_id: str,
        body: ActionRequest,
        x_account_id: str | None = Header(default=None),
    ):
        account_id = current_account_id(x_account_id)
        result = runtime.actions.execute(
            account_id=account_id,
            session_id=session_id,
            command=body.to_command(),
        )
        if result.get("status") == "processing":
            return JSONResponse(status_code=202, content=result)
        return result

    @app.post("/api/game/session/{session_id}/end-day")
    async def end_day(
        session_id: str,
        body: EndDayRequest,
        x_account_id: str | None = Header(default=None),
    ):
        account_id = current_account_id(x_account_id)
        result = runtime.end_days.end_day(
            account_id=account_id,
            session_id=session_id,
            client_action_id=body.client_action_id,
            state_version=body.state_version,
            active_rest=body.active_rest,
        )
        if result.get("status") == "processing":
            return JSONResponse(status_code=202, content=result)
        return result

    @app.get("/api/game/session/{session_id}/operations/{client_action_id}")
    async def get_operation(
        session_id: str,
        client_action_id: str,
        x_account_id: str | None = Header(default=None),
    ) -> dict:
        account_id = current_account_id(x_account_id)
        runtime.game_sessions.get_owned(session_id, account_id)
        operation = runtime.operations.get(account_id, session_id, client_action_id)
        if operation is None:
            raise NotFoundError("操作不存在")
        return {
            "operation_id": operation.operation_id,
            "status": operation.status.value,
            "attempt_count": operation.attempt_count,
            "response": operation.response,
            "error": operation.error,
        }

    return app
