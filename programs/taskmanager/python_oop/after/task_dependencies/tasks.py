"""Task-facing service layer: thin facade over Task aggregate methods."""
from __future__ import annotations

from typing import TYPE_CHECKING

from models import Project, Status, Task, User

if TYPE_CHECKING:
    from collections.abc import Iterable


class TaskService:
    """Stateless coordinator for task-level operations.

    Mirrors ProjectService in shape: every call delegates into the aggregate
    and returns the mutated task for convenience.
    """

    @staticmethod
    def create(
        task_id: str,
        project_id: str,
        title: str,
        creator: User,
        dependencies: Iterable[Task] | None = None,
    ) -> Task:
        return Task.create(task_id, project_id, title, creator, dependencies)

    @staticmethod
    def assign(actor: User, project: Project, task: Task, assignee: User) -> Task:
        task.assign(actor, project, assignee)
        return task

    @staticmethod
    def unassign(actor: User, project: Project, task: Task) -> Task:
        task.unassign(actor, project)
        return task

    @staticmethod
    def add_dependency(
        actor: User, project: Project, task: Task, dependency: Task
    ) -> Task:
        task.add_dependency(actor, project, dependency)
        return task

    @staticmethod
    def set_status(
        actor: User,
        project: Project,
        task: Task,
        next_status: Status,
        dependency_tasks: Iterable[Task] | None = None,
    ) -> Task:
        task.set_status(actor, project, next_status, dependency_tasks)
        return task
