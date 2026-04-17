"""Task lifecycle: creation, assignment, status transitions.

Every mutation consults validation for permission and transition rules;
tasks contains no policy of its own — only the orchestration of checks
and updates.
"""
from __future__ import annotations

from dataclasses import replace

from models import Priority, Project, Status, Task, User
from validation import (
    can_assign_priority,
    can_modify_project,
    can_transition_to,
)


def create_task(
    id: str,
    project_id: str,
    title: str,
    creator: User,
    priority: Priority = Priority.MEDIUM,
) -> Task:
    """Create a Todo task in the given project, authored by creator.

    Priority defaults to Medium when not specified — existing call sites keep
    working unchanged, and the default is a sensible middle-of-the-road choice.
    """
    return Task(
        id=id,
        project_id=project_id,
        title=title,
        status=Status.TODO,
        assignee_id=None,
        created_by_user_id=creator.id,
        priority=priority,
    )


def set_priority(
    actor: User, project: Project, task: Task, priority: Priority
) -> Task:
    """Change a task's priority. Requires project write access."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot change task priority in project {project.id}"
        )
    return replace(task, priority=priority)


def assign_task(actor: User, project: Project, task: Task, assignee: User) -> Task:
    """Assign a task to assignee.

    Permission is delegated to ``can_assign_priority`` so that the
    Critical-only-Admin rule lives entirely in validation, not here.
    """
    if not can_assign_priority(actor, project, assignee, task.priority):
        raise PermissionError(
            f"Actor {actor.id} cannot assign {task.priority.value} tasks "
            f"in project {project.id} to {assignee.id}"
        )
    return replace(task, assignee_id=assignee.id)


def unassign_task(actor: User, project: Project, task: Task) -> Task:
    """Remove the current assignee. Actor must have project write access."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot modify tasks in project {project.id}"
        )
    return replace(task, assignee_id=None)


def set_status(actor: User, project: Project, task: Task, next_status: Status) -> Task:
    """Move a task to a new status. Permission + transition rule enforced."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot change task status in project {project.id}"
        )
    if not can_transition_to(task.status, next_status):
        raise ValueError(
            f"Invalid transition from {task.status.value} to {next_status.value}"
        )
    return replace(task, status=next_status)
