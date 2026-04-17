"""Core shared types for the task-manager domain.

Admin/Member/Viewer roles govern what operations are permitted.
Tasks move through Todo → InProgress → Done, with Blocked as an off-path state.

A Project tracks its active permission delegations: users to whom the owner has
temporarily extended owner-level project-modification authority.

Design decisions:

* Delegations live as a list of user ids on the Project record, not as a
  parallel record type and not as a field on the User. Delegations are
  scoped to a single project (an owner going on vacation delegates rights
  on that project, not globally), so the natural home is the Project
  record. Storing the delegated user ids as a closed list keeps
  permission checks a simple membership test and keeps revocation a list
  removal. Alternatives considered: delegation list on User, a separate
  Delegation record.
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
    delegate_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Task:
    id: str
    project_id: str
    title: str
    status: Status
    assignee_id: str | None
    created_by_user_id: str
