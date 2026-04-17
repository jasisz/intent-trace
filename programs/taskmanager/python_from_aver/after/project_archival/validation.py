"""Pure permission predicates for the task-manager.

Every cross-cutting check that operations consult lives here, so that
business rules are kept out of the operation bodies and can be reviewed
on their own.

Archival is modelled as a separate predicate (``is_archived``) plus a small
guard (``ensure_not_archived``). Mutating operations call both the existing
permission predicate *and* the guard; the guard centralises the error message
so the check is not duplicated across modules.

Design decisions:

* Permission checks live here as named pure functions rather than as
  inline snippets in the operation bodies or as methods on the record
  classes. Keeping each rule as a top-level function makes it reviewable
  in one place; inlining the same logic into every operation would spread
  the same policy across several files and make it easy for drift to
  sneak in. Alternatives considered: inline checks at each call site,
  class methods on the record types.

* Archival is composed into mutations through a single reusable piece
  (the ``ensure_not_archived`` guard) rather than by repeating an
  ``if is_archived(p): ...`` branch in every operation. Archival adds one
  universal requirement to every mutation: the project must be active.
  Introducing a single point of enforcement means every project-modifying
  operation picks up the rule without being individually edited, and
  ``can_modify_project`` / ``can_toggle_archive`` keep their narrower
  meanings so archive/unarchive themselves can still run on an archived
  project. Alternatives considered: repeat the archival check in each
  operation, branch on archival inside each permission predicate.
"""
from __future__ import annotations

from models import Project, Role, Status, User


def is_admin(u: User) -> bool:
    """True if the user has Admin role."""
    return u.role is Role.ADMIN


def is_member(p: Project, user_id: str) -> bool:
    """True if user_id is listed in the project's member_ids."""
    return user_id in p.member_ids


def is_owner_or_admin(u: User, p: Project) -> bool:
    """True if the user owns the project or has Admin role on the system."""
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
