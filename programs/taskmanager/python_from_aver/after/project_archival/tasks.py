"""Task lifecycle: creation, assignment, status transitions.

Every mutation consults validation for permission and transition rules and
calls :func:`ensure_not_archived` so that tasks belonging to an archived
project are frozen alongside the project itself. Tasks contains no policy of
its own — only the orchestration of checks and updates.
"""
from __future__ import annotations

from dataclasses import replace

from models import Project, Status, Task, User
from validation import (
    can_assign,
    can_modify_project,
    can_transition_to,
    ensure_not_archived,
)


def create_task(
    id: str, project: Project, title: str, creator: User
) -> Task:
    """Create a Todo task in the given project, authored by creator.

    The project is passed in (not just its id) so that the archival guard and
    the creator's write access can be enforced at the boundary.
    """
    if not can_modify_project(creator, project):
        raise PermissionError(
            f"Actor {creator.id} cannot create tasks in project {project.id}"
        )
    ensure_not_archived(project)
    return Task(
        id=id,
        project_id=project.id,
        title=title,
        status=Status.TODO,
        assignee_id=None,
        created_by_user_id=creator.id,
    )


def assign_task(actor: User, project: Project, task: Task, assignee: User) -> Task:
    """Assign a task to assignee. Raises PermissionError on authority failure."""
    if not can_assign(actor, project, assignee):
        raise PermissionError(
            f"Actor {actor.id} cannot assign tasks in project {project.id} to {assignee.id}"
        )
    ensure_not_archived(project)
    return replace(task, assignee_id=assignee.id)


def unassign_task(actor: User, project: Project, task: Task) -> Task:
    """Remove the current assignee. Actor must have project write access."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot modify tasks in project {project.id}"
        )
    ensure_not_archived(project)
    return replace(task, assignee_id=None)


def set_status(actor: User, project: Project, task: Task, next_status: Status) -> Task:
    """Move a task to a new status. Permission + transition rule enforced."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot change task status in project {project.id}"
        )
    ensure_not_archived(project)
    if not can_transition_to(task.status, next_status):
        raise ValueError(
            f"Invalid transition from {task.status.value} to {next_status.value}"
        )
    return replace(task, status=next_status)
