"""Core shared types for the task-manager domain.

Admin/Member/Viewer roles govern what operations are permitted.
Tasks move through Todo → InProgress → Done, with Blocked as an off-path state.
Tasks may declare dependencies on other tasks in the same project; a task
cannot reach Done until every dependency is Done.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(Enum):
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Status(Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: Role


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    owner_id: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class Task:
    id: str
    project_id: str
    title: str
    status: Status
    assignee_id: str | None
    created_by_user_id: str
    depends_on_ids: tuple[str, ...] = field(default_factory=tuple)
