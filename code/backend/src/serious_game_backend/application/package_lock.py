from __future__ import annotations

from serious_game_backend.application.ports import ScriptPackageRepository
from serious_game_backend.domain.errors import SessionContentUnavailableError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


CONTENT_UNAVAILABLE_REASON = "该进度锁定的剧本内容已不在当前版本中，暂时无法打开。"


def locked_package_access(
    packages: ScriptPackageRepository,
    session: GameSession,
) -> tuple[ScriptPackage | None, dict]:
    package = packages.get(session.package_id)
    content_available = bool(
        package
        and package.package_version == session.package_version
        and package.content_hash == session.package_content_hash
    )
    if not content_available:
        return package, {
            "mode": "content_unavailable",
            "content_available": False,
            "review_available": False,
            "loadable": False,
            "unavailable_reason": CONTENT_UNAVAILABLE_REASON,
        }
    review_only = package is not None and package.status == "retired"
    return package, {
        "mode": "review_only" if review_only else "playable",
        "content_available": True,
        "review_available": True,
        "loadable": True,
        "unavailable_reason": None,
    }


def require_locked_package(
    packages: ScriptPackageRepository,
    session: GameSession,
) -> ScriptPackage:
    package, access = locked_package_access(packages, session)
    if not access["content_available"]:
        raise SessionContentUnavailableError(
            access["unavailable_reason"],
            details=access,
        )
    assert package is not None
    return package
