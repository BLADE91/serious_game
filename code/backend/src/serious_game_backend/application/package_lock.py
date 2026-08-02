from __future__ import annotations

from serious_game_backend.application.ports import ScriptPackageRepository
from serious_game_backend.domain.errors import SessionContentUnavailableError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


def require_locked_package(
    packages: ScriptPackageRepository,
    session: GameSession,
) -> ScriptPackage:
    package = packages.get(session.package_id)
    if package is None:
        raise SessionContentUnavailableError("游戏锁定的剧本包不存在")
    if (
        package.package_version != session.package_version
        or package.content_hash != session.package_content_hash
    ):
        raise SessionContentUnavailableError(
            "游戏锁定的剧本包版本或内容哈希不匹配",
            details={
                "package_id": session.package_id,
                "expected_version": session.package_version,
                "actual_version": package.package_version,
                "expected_hash": session.package_content_hash,
                "actual_hash": package.content_hash,
            },
        )
    return package
