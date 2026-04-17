"""Task lifecycle: creation, assignment, status transitions, dependencies.

Every mutation consults validation for permission and transition rules;
tasks contains no policy of its own — only the orchestration of checks
and updates. Dependencies must point at tasks in the same project, and
a task can only reach Done when every dependency is Done.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from models import Project, Status, Task, User
from validation import (
    can_assign,
    can_modify_project,
    can_transition_to,
    unsatisfied_dependencies,
)


def _validate_dependency_targets(
    project: Project, dep_ids: Iterable[str], tasks_by_id: Mapping[str, Task]
) -> tuple[str, ...]:
    """Normalise dep ids and verify each points at a task in the same project.

    Duplicates are collapsed while preserving order.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for dep_id in dep_ids:
        if dep_id in seen:
            continue
        seen.add(dep_id)
        dep = tasks_by_id.get(dep_id)
        if dep is None:
            raise ValueError(f"Unknown dependency task {dep_id}")
        if dep.project_id != project.id:
            raise ValueError(
                f"Dependency {dep_id} belongs to project {dep.project_id}, "
                f"not {project.id}"
            )
        ordered.append(dep_id)
    return tuple(ordered)


def create_task(
    id: str,
    project: Project,
    title: str,
    creator: User,
    depends_on_ids: Iterable[str] = (),
    tasks_by_id: Mapping[str, Task] | None = None,
) -> Task:
    """Create a Todo task in the given project, authored by creator.

    If dependencies are supplied, each must already exist in the same
    project; pass ``tasks_by_id`` so the check can resolve them.
    """
    dep_ids = tuple(depends_on_ids)
    if dep_ids:
        if tasks_by_id is None:
            raise ValueError(
                "tasks_by_id is required when creating a task with dependencies"
            )
        if id in tasks_by_id:
            # Avoid the degenerate case where a task depends on itself by id.
            raise ValueError(f"Task id {id} already exists")
        dep_ids = _validate_dependency_targets(project, dep_ids, tasks_by_id)
    return Task(
        id=id,
        project_id=project.id,
        title=title,
        status=Status.TODO,
        assignee_id=None,
        created_by_user_id=creator.id,
        depends_on_ids=dep_ids,
    )


def assign_task(actor: User, project: Project, task: Task, assignee: User) -> Task:
    """Assign a task to assignee. Raises PermissionError on authority failure."""
    if not can_assign(actor, project, assignee):
        raise PermissionError(
            f"Actor {actor.id} cannot assign tasks in project {project.id} to {assignee.id}"
        )
    return replace(task, assignee_id=assignee.id)


def unassign_task(actor: User, project: Project, task: Task) -> Task:
    """Remove the current assignee. Actor must have project write access."""
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot modify tasks in project {project.id}"
        )
    return replace(task, assignee_id=None)


def add_dependencies(
    actor: User,
    project: Project,
    task: Task,
    new_dep_ids: Iterable[str],
    tasks_by_id: Mapping[str, Task],
) -> Task:
    """Declare additional dependencies for ``task``.

    Same permission rules as other task mutations; targets must live in the
    same project. Already-declared dependencies are silently de-duplicated,
    and a task may not depend on itself.
    """
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot modify tasks in project {project.id}"
        )
    if task.project_id != project.id:
        raise ValueError(
            f"Task {task.id} belongs to project {task.project_id}, not {project.id}"
        )
    incoming = _validate_dependency_targets(project, new_dep_ids, tasks_by_id)
    if task.id in incoming:
        raise ValueError(f"Task {task.id} cannot depend on itself")
    existing = set(task.depends_on_ids)
    added = tuple(dep_id for dep_id in incoming if dep_id not in existing)
    if not added:
        return task
    return replace(task, depends_on_ids=(*task.depends_on_ids, *added))


def set_status(
    actor: User,
    project: Project,
    task: Task,
    next_status: Status,
    tasks_by_id: Mapping[str, Task] | None = None,
) -> Task:
    """Move a task to a new status. Permission + transition rule enforced.

    To transition into Done, the caller must pass ``tasks_by_id`` so the
    dependency check can verify every prerequisite is already Done.
    """
    if not can_modify_project(actor, project):
        raise PermissionError(
            f"Actor {actor.id} cannot change task status in project {project.id}"
        )
    if not can_transition_to(task.status, next_status):
        raise ValueError(
            f"Invalid transition from {task.status.value} to {next_status.value}"
        )
    if next_status is Status.DONE and task.depends_on_ids:
        if tasks_by_id is None:
            raise ValueError(
                f"Cannot move task {task.id} to done without dependency state; "
                "pass tasks_by_id"
            )
        blocking = unsatisfied_dependencies(task, tasks_by_id)
        if blocking:
            joined = ", ".join(blocking)
            raise ValueError(
                f"Cannot move task {task.id} to done: blocked by {joined}"
            )
    return replace(task, status=next_status)
