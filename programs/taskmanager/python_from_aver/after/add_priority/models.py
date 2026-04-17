"""Core shared types for the task-manager domain.

Admin/Member/Viewer roles govern what operations are permitted.
Tasks move through Todo → InProgress → Done, with Blocked as an off-path state.
Tasks also carry a Priority (Low/Medium/High/Critical); Critical tasks have
stricter assignment rules (see validation).

Design decisions:

* Priority lives on the Task record itself as a closed enum of four levels,
  not on a parallel record and not as a free-form integer. Priority is a
  property of the work item — it influences assignment rules and any
  display of the task — so it belongs on Task, and a closed sum of four
  levels makes pattern-matching exhaustive and assignment-policy code
  auditable. Alternatives considered: integer priority field, separate
  Priority record per task.
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
