"""Project-facing service layer: thin facade over Project aggregate methods."""
from __future__ import annotations

from models import Project, User


class ProjectService:
    """Stateless coordinator for project-level operations.

    Exists so callers can depend on a service object rather than reaching into
    the aggregate directly; every method simply delegates into Project.
    """

    @staticmethod
    def create(project_id: str, name: str, owner: User) -> Project:
        return Project.create(project_id, name, owner)

    @staticmethod
    def add_member(actor: User, project: Project, member_id: str) -> Project:
        """Mutate project in place, returning the same instance for call chaining."""
        project.add_member(actor, member_id)
        return project

    @staticmethod
    def remove_member(actor: User, project: Project, member_id: str) -> Project:
        project.remove_member(actor, member_id)
        return project

    @staticmethod
    def archive(actor: User, project: Project) -> Project:
        project.archive(actor)
        return project

    @staticmethod
    def unarchive(actor: User, project: Project) -> Project:
        project.unarchive(actor)
        return project
