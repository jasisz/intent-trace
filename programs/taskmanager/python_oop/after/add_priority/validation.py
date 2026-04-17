"""Cross-cutting permission and transition rules consumed by domain methods."""
from __future__ import annotations

from models import (
    DEFAULT_PRIORITY,
    InvalidTransitionError,
    PermissionDeniedError,
    Priority,
    Project,
    Status,
    User,
)


class Permissions:
    """Authorization predicates and their raising-equivalents for domain operations."""

    @classmethod
    def can_modify_project(cls, actor: User, project: Project) -> bool:
        return project.can_modify(actor)

    @classmethod
    def requires_admin_priority(cls, priority: Priority) -> bool:
        """Critical priority is Admin-only; other priorities follow the owner-or-admin rule."""
        return priority is Priority.CRITICAL

    @classmethod
    def is_admin_only_assignment(
        cls, actor: User, project: Project, priority: Priority
    ) -> bool:
        return cls.requires_admin_priority(priority) and not actor.is_admin

    @classmethod
    def can_assign(
        cls,
        actor: User,
        project: Project,
        assignee: User,
        priority: Priority = DEFAULT_PRIORITY,
    ) -> bool:
        """Assign requires project write access, assignee membership, and Admin for Critical."""
        if cls.is_admin_only_assignment(actor, project, priority):
            return False
        if not project.can_modify(actor):
            return False
        return project.is_member(assignee.id)

    @classmethod
    def require_project_modify(cls, actor: User, project: Project) -> None:
        if not cls.can_modify_project(actor, project):
            raise PermissionDeniedError(
                f"Only the owner or an Admin can modify project {project.id}"
            )

    @classmethod
    def require_assign(
        cls,
        actor: User,
        project: Project,
        assignee: User,
        priority: Priority = DEFAULT_PRIORITY,
    ) -> None:
        """Distinguish between missing admin rights, lacking write access, and non-member assignee."""
        if cls.is_admin_only_assignment(actor, project, priority):
            raise PermissionDeniedError(
                f"Only an Admin can assign {priority.value}-priority tasks "
                f"in project {project.id}"
            )
        if not project.can_modify(actor):
            raise PermissionDeniedError(
                f"Actor {actor.id} cannot assign tasks in project {project.id}"
            )
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
