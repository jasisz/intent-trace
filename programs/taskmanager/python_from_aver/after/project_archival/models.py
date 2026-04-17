"""Core shared types for the task-manager domain.

Admin/Member/Viewer roles govern what operations are permitted.
Tasks move through Todo → InProgress → Done, with Blocked as an off-path state.
Projects may be archived: an archived project is read-only and rejects any
mutation (membership changes, new tasks, task state changes) until it is
un-archived again.

Design decisions:

* Archival is a single Bool field on the Project record, not a new stage
  in a project-state enum and not a separate archive record. Archival is
  a binary lifecycle flag: a project is either active or archived, and
  archiving/unarchiving is symmetric. A bool keeps the Project record a
  single source of truth and lets permission predicates compose the check
  into existing rules without a parallel type. Alternatives considered:
  a Project state enum, a separate Archive record referencing the
  project.
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
    archived: bool = field(default=False)


@dataclass(frozen=True)
class Task:
    id: str
    project_id: str
    title: str
    status: Status
    assignee_id: str | None
    created_by_user_id: str
