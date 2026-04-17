"""Pure permission predicates for the task-manager.

Every cross-cutting check that operations consult lives here, so that
business rules are kept out of the operation bodies and can be reviewed
on their own. Transition rules also know about task dependencies:
moving a task to Done requires every dependency task to be Done.

Design decisions:

* Permission checks live here as named pure functions rather than as
  inline snippets in the operation bodies or as methods on the record
  classes. Keeping each rule as a top-level function makes it reviewable
  in one place; inlining the same logic into every operation would spread
  the same policy across several files and make it easy for drift to
  sneak in. Alternatives considered: inline checks at each call site,
  class methods on the record types.

* Dependency resolution is performed at the call site, not inside
  validation. Tasks module knows how to resolve dependency ids to Task
  records (or how to hand the caller's ``tasks_by_id`` map through);
  validation only decides whether the tasks it receives are Done. Keeping
  validation pure and side-effect free means it never has to reach for a
  store or fetch extra records, which keeps the predicates easy to test
  and easy to reason about. Alternatives considered: fetch dependency
  tasks inside validation, store dependency completion status on the
  task itself.
"""
from __future__ import annotations

from collections.abc import Mapping

from models import Project, Role, Status, Task, User


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


def unsatisfied_dependencies(
    task: Task, tasks_by_id: Mapping[str, Task]
) -> tuple[str, ...]:
    """Return the ids of dependencies that are not yet Done.

    Missing dependency ids (not present in the given map) are reported as
    unsatisfied too — a caller that does not know about them cannot prove
    they are complete.
    """
    blocking: list[str] = []
    for dep_id in task.depends_on_ids:
        dep = tasks_by_id.get(dep_id)
        if dep is None or dep.status is not Status.DONE:
            blocking.append(dep_id)
    return tuple(blocking)
