"""Core shared types for the task-manager domain.

Admin/Member/Viewer roles govern what operations are permitted.
Tasks move through Todo → InProgress → Done, with Blocked as an off-path state.
Tasks also carry a Priority (Low/Medium/High/Critical); Critical tasks have
stricter assignment rules (see validation).
"""
from __future__ import annotations

from dataclasses import dataclass
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


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


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
    priority: Priority = Priority.MEDIUM
