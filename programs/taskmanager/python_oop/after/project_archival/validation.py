"""Cross-cutting permission and transition rules consumed by domain methods."""
from __future__ import annotations

from models import (
    InvalidTransitionError,
    PermissionDeniedError,
    Project,
    ProjectArchivedError,
    Status,
    User,
)


class Permissions:
    """Authorization predicates and their raising-equivalents for domain operations.

    Two-layer model: `require_project_modify` covers ownership/admin authority
    (archive, unarchive). `require_project_write` adds the archival predicate
    on top and is the gate for any state-changing operation on the project or
    its tasks.
    """

    @classmethod
    def can_modify_project(cls, actor: User, project: Project) -> bool:
        return project.can_modify(actor)

    @classmethod
    def can_write_project(cls, actor: User, project: Project) -> bool:
        """Write access = modify authority AND project not archived."""
        return project.can_modify(actor) and not project.is_archived

    @classmethod
    def can_assign(cls, actor: User, project: Project, assignee: User) -> bool:
        """Assign requires project write access and membership of the assignee."""
        if not cls.can_write_project(actor, project):
            return False
        return project.is_member(assignee.id)

    @classmethod
    def require_project_modify(cls, actor: User, project: Project) -> None:
        """Ownership/admin gate, independent of archival state."""
        if not cls.can_modify_project(actor, project):
            raise PermissionDeniedError(
                f"Only the owner or an Admin can modify project {project.id}"
            )

    @classmethod
    def require_project_active(cls, project: Project) -> None:
        """Archival predicate; composed into every write-path guard."""
        if project.is_archived:
            raise ProjectArchivedError(
                f"Project {project.id} is archived and cannot be modified"
            )

    @classmethod
    def require_project_write(cls, actor: User, project: Project) -> None:
        """Gate for any mutation on the project or its tasks."""
        cls.require_project_modify(actor, project)
        cls.require_project_active(project)

    @classmethod
    def require_assign(cls, actor: User, project: Project, assignee: User) -> None:
        """Distinguish write-access failures from non-member assignees."""
        cls.require_project_write(actor, project)
        if not project.is_member(assignee.id):
            raise PermissionDeniedError(
                f"User {assignee.id} is not a member of project {project.id} "
                f"and cannot be assigned"
            )


class Transitions:
    """Task status transition rules, indexed by the current status."""

    _ALLOWED: dict[Status, frozenset[Status]] = {
        Status.TODO: frozenset({Status.IN_PROGRESS, Status.BLOCKED}),
        Status.IN_PROGRESS: frozenset({Status.DONE, Status.BLOCKED, Status.TODO}),
        Status.BLOCKED: frozenset({Status.IN_PROGRESS, Status.TODO}),
        Status.DONE: frozenset(),
    }

    @classmethod
    def is_allowed(cls, current: Status, next_status: Status) -> bool:
        return next_status in cls._ALLOWED[current]

    @classmethod
    def require(cls, current: Status, next_status: Status) -> None:
        if not cls.is_allowed(current, next_status):
            raise InvalidTransitionError(
                f"Invalid transition from {current.value} to {next_status.value}"
            )
