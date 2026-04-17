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


class InvalidDependencyError(TaskManagerError):
    """Raised when a dependency declaration is structurally invalid."""


class DependenciesNotSatisfiedError(TaskManagerError):
    """Raised when completing a task is blocked by incomplete dependencies."""


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

    @classmethod
    def create(cls, project_id: str, name: str, owner: User) -> Project:
        """Factory that seeds the project with the owner as its sole member."""
        return cls(
            id=project_id,
            name=name,
            owner_id=owner.id,
            member_ids=[owner.id],
        )

    def is_member(self, user_id: str) -> bool:
        return user_id in self.member_ids

    def is_owner(self, user_id: str) -> bool:
        return user_id == self.owner_id

    def can_modify(self, actor: User) -> bool:
        """True when the actor is either project owner or a global admin."""
        return self.is_owner(actor.id) or actor.is_admin

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

    def members(self) -> Iterable[str]:
        return tuple(self.member_ids)


@dataclass
class Task:
    """Mutable task with status transitions, assignment, and dependency tracking."""

    id: str
    project_id: str
    title: str
    status: Status
    assignee_id: str | None
    created_by_user_id: str
    dependency_ids: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        task_id: str,
        project_id: str,
        title: str,
        creator: User,
        dependencies: Iterable[Task] | None = None,
    ) -> Task:
        """Build a fresh Todo task; optionally declare dependencies at creation time."""
        dep_ids: list[str] = []
        if dependencies is not None:
            for dep in dependencies:
                if dep.project_id != project_id:
                    raise InvalidDependencyError(
                        f"Dependency {dep.id} belongs to project "
                        f"{dep.project_id}, not {project_id}"
                    )
                if dep.id == task_id:
                    raise InvalidDependencyError(
                        f"Task {task_id} cannot depend on itself"
                    )
                if dep.id not in dep_ids:
                    dep_ids.append(dep.id)
        return cls(
            id=task_id,
            project_id=project_id,
            title=title,
            status=Status.TODO,
            assignee_id=None,
            created_by_user_id=creator.id,
            dependency_ids=dep_ids,
        )

    @property
    def is_assigned(self) -> bool:
        return self.assignee_id is not None

    @property
    def is_terminal(self) -> bool:
        return self.status is Status.DONE

    @property
    def has_dependencies(self) -> bool:
        return bool(self.dependency_ids)

    def depends_on(self, task_id: str) -> bool:
        return task_id in self.dependency_ids

    def dependencies(self) -> Iterable[str]:
        return tuple(self.dependency_ids)

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

    def add_dependency(
        self, actor: User, project: Project, dependency: Task
    ) -> None:
        """Declare a new dependency; same permission rules as other task mutations."""
        from validation import Permissions

        if project.id != self.project_id:
            raise InvalidMemberError(
                f"Task {self.id} does not belong to project {project.id}"
            )
        if dependency.project_id != self.project_id:
            raise InvalidDependencyError(
                f"Dependency {dependency.id} belongs to project "
                f"{dependency.project_id}, not {self.project_id}"
            )
        if dependency.id == self.id:
            raise InvalidDependencyError(
                f"Task {self.id} cannot depend on itself"
            )
        Permissions.require_project_modify(actor, project)
        if dependency.id in self.dependency_ids:
            return
        self.dependency_ids.append(dependency.id)

    def set_status(
        self,
        actor: User,
        project: Project,
        next_status: Status,
        dependency_tasks: Iterable[Task] | None = None,
    ) -> None:
        """Validate actor permissions, the transition, and (on Done) dependencies."""
        from validation import Permissions, Transitions

        if project.id != self.project_id:
            raise InvalidMemberError(
                f"Task {self.id} does not belong to project {project.id}"
            )
        Permissions.require_project_modify(actor, project)
        Transitions.require(self.status, next_status)
        if next_status is Status.DONE:
            Transitions.require_dependencies_done(self, dependency_tasks)
        self.status = next_status
