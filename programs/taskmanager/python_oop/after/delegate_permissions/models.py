"""Domain entities, enums, and exception hierarchy for the task manager."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class Role(Enum):
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Status(Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class TaskManagerError(Exception):
    """Base class for every domain-level error raised by the task manager."""


class PermissionDeniedError(TaskManagerError):
    """Raised when an actor attempts an operation they are not authorized for."""


class InvalidTransitionError(TaskManagerError):
    """Raised when a task status change is not allowed from its current state."""


class InvalidMemberError(TaskManagerError):
    """Raised when membership rules are violated (e.g. removing the owner)."""


class InvalidDelegationError(TaskManagerError):
    """Raised when a delegation grant or revoke violates project rules."""


@dataclass(frozen=True)
class User:
    """Immutable user identity with a single role."""

    id: str
    name: str
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


@dataclass
class Project:
    """Mutable project aggregate: owns its membership list and enforces ownership rules."""

    id: str
    name: str
    owner_id: str
    member_ids: list[str] = field(default_factory=list)
    delegate_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, project_id: str, name: str, owner: User) -> Project:
        """Factory that seeds the project with the owner as its sole member."""
        return cls(
            id=project_id,
            name=name,
            owner_id=owner.id,
            member_ids=[owner.id],
            delegate_ids=[],
        )

    def is_member(self, user_id: str) -> bool:
        return user_id in self.member_ids

    def is_owner(self, user_id: str) -> bool:
        return user_id == self.owner_id

    def is_delegate(self, user_id: str) -> bool:
        return user_id in self.delegate_ids

    def can_modify(self, actor: User) -> bool:
        """True when the actor is owner, a global admin, or a current delegate."""
        return self.is_owner(actor.id) or actor.is_admin or self.is_delegate(actor.id)

    def add_member(self, actor: User, member_id: str) -> None:
        from validation import Permissions

        Permissions.require_project_modify(actor, self)
        if member_id in self.member_ids:
            return
        self.member_ids.append(member_id)

    def remove_member(self, actor: User, member_id: str) -> None:
        from validation import Permissions

        Permissions.require_project_modify(actor, self)
        if member_id == self.owner_id:
            raise InvalidMemberError("Cannot remove the project owner")
        self.member_ids = [mid for mid in self.member_ids if mid != member_id]

    def grant_delegation(self, actor: User, delegate_id: str) -> None:
        """Authorize delegate_id to act with owner-level project rights."""
        from validation import Permissions

        Permissions.require_project_modify(actor, self)
        if delegate_id == self.owner_id:
            raise InvalidDelegationError(
                "Owner already has full rights; cannot be a delegate"
            )
        if delegate_id in self.delegate_ids:
            return
        self.delegate_ids.append(delegate_id)

    def revoke_delegation(self, actor: User, delegate_id: str) -> None:
        """Remove delegate_id from the delegation list, if present."""
        from validation import Permissions

        Permissions.require_project_modify(actor, self)
        self.delegate_ids = [did for did in self.delegate_ids if did != delegate_id]

    def members(self) -> Iterable[str]:
        return tuple(self.member_ids)

    def delegates(self) -> Iterable[str]:
        return tuple(self.delegate_ids)


@dataclass
class Task:
    """Mutable task with status transitions and assignment, gated on project permissions."""

    id: str
    project_id: str
    title: str
    status: Status
    assignee_id: str | None
    created_by_user_id: str

    @classmethod
    def create(cls, task_id: str, project_id: str, title: str, creator: User) -> Task:
        """Build a fresh Todo task authored by creator, with no assignee."""
        return cls(
            id=task_id,
            project_id=project_id,
            title=title,
            status=Status.TODO,
            assignee_id=None,
            created_by_user_id=creator.id,
        )

    @property
    def is_assigned(self) -> bool:
        return self.assignee_id is not None

    @property
    def is_terminal(self) -> bool:
        return self.status is Status.DONE

    def assign(self, actor: User, project: Project, assignee: User) -> None:
        from validation import Permissions

        if project.id != self.project_id:
            raise InvalidMemberError(
                f"Task {self.id} does not belong to project {project.id}"
            )
        Permissions.require_assign(actor, project, assignee)
        self.assignee_id = assignee.id

    def unassign(self, actor: User, project: Project) -> None:
        from validation import Permissions

        if project.id != self.project_id:
            raise InvalidMemberError(
                f"Task {self.id} does not belong to project {project.id}"
            )
        Permissions.require_project_modify(actor, project)
        self.assignee_id = None

    def set_status(self, actor: User, project: Project, next_status: Status) -> None:
        """Atomically validate actor permissions and the status transition, then apply it."""
        from validation import Permissions, Transitions

        if project.id != self.project_id:
            raise InvalidMemberError(
                f"Task {self.id} does not belong to project {project.id}"
            )
        Permissions.require_project_modify(actor, project)
        if not Transitions.is_allowed(self.status, next_status):
            raise InvalidTransitionError(
                f"Invalid transition from {self.status.value} to {next_status.value}"
            )
        self.status = next_status
