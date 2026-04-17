"""Pure permission predicates for the task-manager.

Every cross-cutting check that operations consult lives here, so that
business rules are kept out of the operation bodies and can be reviewed
on their own.

Archival is modelled as a separate predicate (``is_archived``) plus a small
guard (``ensure_not_archived``). Mutating operations call both the existing
permission predicate *and* the guard; the guard centralises the error message
so the check is not duplicated across modules.
"""
from __future__ import annotations

from models import Project, Role, Status, User


def is_admin(u: User) -> bool:
    return u.role is Role.ADMIN


def is_member(p: Project, user_id: str) -> bool:
    return user_id in p.member_ids


def is_owner_or_admin(u: User, p: Project) -> bool:
    return u.id == p.owner_id or is_admin(u)


def is_archived(p: Project) -> bool:
    """Return True if the project is in the archived (read-only) state."""
    return p.archived


def can_assign(actor: User, project: Project, assignee: User) -> bool:
    """Assigning a task: actor must have write access AND assignee must be a member."""
    if not is_owner_or_admin(actor, project):
        return False
    return is_member(project, assignee.id)


def can_modify_project(actor: User, project: Project) -> bool:
    """Only owner or Admin may change project membership or metadata.

    This predicate answers only *who* may mutate the project. Whether the
    project is currently mutable (i.e. not archived) is a separate concern,
    enforced by :func:`ensure_not_archived` at the call site. Keeping the two
    checks distinct lets callers surface precise error messages.
    """
    return is_owner_or_admin(actor, project)


def can_toggle_archive(actor: User, project: Project) -> bool:
    """Only the owner or an Admin can archive or un-archive a project.

    Note: the actor *can* run this operation on an already-archived project —
    that is how un-archival works. We deliberately do not consult
    :func:`is_archived` here.
    """
    return is_owner_or_admin(actor, project)


def ensure_not_archived(project: Project) -> None:
    """Raise ``ValueError`` if the project is archived.

    Called by every mutating operation (project membership changes, task
    creation, task assignment, task status transitions) so the archival rule
    is expressed exactly once.
    """
    if is_archived(project):
        raise ValueError(
            f"Project {project.id} is archived and cannot be modified; "
            "un-archive it first"
        )


_ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.TODO: frozenset({Status.IN_PROGRESS, Status.BLOCKED}),
    Status.IN_PROGRESS: frozenset({Status.DONE, Status.BLOCKED, Status.TODO}),
    Status.BLOCKED: frozenset({Status.IN_PROGRESS, Status.TODO}),
    Status.DONE: frozenset(),
}


def can_transition_to(current: Status, next_status: Status) -> bool:
    """Allowed Task status transitions."""
    return next_status in _ALLOWED_TRANSITIONS[current]
