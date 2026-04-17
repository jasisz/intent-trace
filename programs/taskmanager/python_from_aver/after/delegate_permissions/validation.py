"""Pure permission predicates for the task-manager.

Every cross-cutting check that operations consult lives here, so that
business rules are kept out of the operation bodies and can be reviewed
on their own.

Delegation note: when an owner grants a delegation, the delegated user
gains the same project-modification authority as the owner for the
duration of that delegation. `can_modify_project` is the single gate all
owner-or-Admin-gated project operations flow through, so accepting
delegates there transparently extends every such operation.
"""
from __future__ import annotations

from models import Project, Role, Status, User


def is_admin(u: User) -> bool:
    return u.role is Role.ADMIN


def is_member(p: Project, user_id: str) -> bool:
    return user_id in p.member_ids


def is_owner_or_admin(u: User, p: Project) -> bool:
    return u.id == p.owner_id or is_admin(u)


def is_delegated(u: User, p: Project) -> bool:
    """True if u currently holds a delegation from the owner of p."""
    return u.id in p.delegate_ids


def can_modify_project(actor: User, project: Project) -> bool:
    """Owner, Admin, or an active delegate may change project state."""
    return is_owner_or_admin(actor, project) or is_delegated(actor, project)


def can_assign(actor: User, project: Project, assignee: User) -> bool:
    """Assigning a task: actor must have write access AND assignee must be a member."""
    if not can_modify_project(actor, project):
        return False
    return is_member(project, assignee.id)


_ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.TODO: frozenset({Status.IN_PROGRESS, Status.BLOCKED}),
    Status.IN_PROGRESS: frozenset({Status.DONE, Status.BLOCKED, Status.TODO}),
    Status.BLOCKED: frozenset({Status.IN_PROGRESS, Status.TODO}),
    Status.DONE: frozenset(),
}


def can_transition_to(current: Status, next_status: Status) -> bool:
    """Allowed Task status transitions."""
    return next_status in _ALLOWED_TRANSITIONS[current]
