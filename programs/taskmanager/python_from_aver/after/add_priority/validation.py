"""Pure permission predicates for the task-manager.

Every cross-cutting check that operations consult lives here, so that
business rules are kept out of the operation bodies and can be reviewed
on their own. This module also owns the Critical-only-Admin assignment
rule so that tasks does not invent its own policy.

Design decisions:

* Permission checks live here as named pure functions rather than as
  inline snippets in the operation bodies or as methods on the record
  classes. Keeping each rule as a top-level function makes it reviewable
  in one place; inlining the same logic into every operation would spread
  the same policy across several files and make it easy for drift to
  sneak in. Alternatives considered: inline checks at each call site,
  class methods on the record types.

* The Critical-only-Admin rule is exposed as a single predicate
  (``can_assign_priority``) that tasks calls through. Critical tasks are
  escalations whose assignment must be gated by a system-wide authority,
  not by project ownership alone; keeping the rule behind one predicate
  leaves tasks free of priority policy and keeps all priority-aware rules
  in validation where they can be reviewed together. Alternatives
  considered: inline the role check in ``assign_task``, duplicate the
  role check at each call site.
"""
from __future__ import annotations

from models import Priority, Project, Role, Status, User


def is_admin(u: User) -> bool:
    """True if the user has Admin role."""
    return u.role is Role.ADMIN


def is_member(p: Project, user_id: str) -> bool:
    """True if user_id is listed in the project's member_ids."""
    return user_id in p.member_ids


def is_owner_or_admin(u: User, p: Project) -> bool:
    """True if the user owns the project or has Admin role on the system."""
    return u.id == p.owner_id or is_admin(u)


def can_assign(actor: User, project: Project, assignee: User) -> bool:
    """Assigning a task: actor must have write access AND assignee must be a member."""
    if not is_owner_or_admin(actor, project):
        return False
    return is_member(project, assignee.id)


def can_assign_priority(
    actor: User, project: Project, assignee: User, priority: Priority
) -> bool:
    """Assigning a task of the given priority.

    Builds on ``can_assign`` (write access + assignee is a member), and adds
    the Critical-only-Admin rule: Critical-priority tasks can be assigned only
    by users with Admin role — project ownership alone is not enough.
    """
    if not can_assign(actor, project, assignee):
        return False
    if priority is Priority.CRITICAL and not is_admin(actor):
        return False
    return True


def can_modify_project(actor: User, project: Project) -> bool:
    """Only owner or Admin may change project membership or metadata."""
    return is_owner_or_admin(actor, project)


_ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.TODO: frozenset({Status.IN_PROGRESS, Status.BLOCKED}),
    Status.IN_PROGRESS: frozenset({Status.DONE, Status.BLOCKED, Status.TODO}),
    Status.BLOCKED: frozenset({Status.IN_PROGRESS, Status.TODO}),
    Status.DONE: frozenset(),
}


def can_transition_to(current: Status, next_status: Status) -> bool:
    """Allowed Task status transitions."""
    return next_status in _ALLOWED_TRANSITIONS[current]
