"""Entry point wiring the OOP task-manager API through smoke tests."""
from __future__ import annotations

from models import (
    InvalidMemberError,
    InvalidTransitionError,
    PermissionDeniedError,
    Role,
    Status,
    User,
)
from projects import ProjectService
from tasks import TaskService
from validation import Permissions, Transitions


def _smoke_tests() -> None:
    alice = User(id="u1", name="Alice", role=Role.ADMIN)
    bob = User(id="u2", name="Bob", role=Role.MEMBER)
    carol = User(id="u3", name="Carol", role=Role.VIEWER)

    assert alice.is_admin
    assert not bob.is_admin

    project = ProjectService.create("p1", "Launch", alice)
    assert tuple(project.members()) == ("u1",)
    assert project.is_owner("u1")
    assert project.is_member("u1")

    ProjectService.add_member(alice, project, "u2")
    assert project.is_member("u2")

    try:
        ProjectService.add_member(carol, project, "u3")
        raise AssertionError("Viewer should not be able to add members")
    except PermissionDeniedError:
        pass

    assert Permissions.can_modify_project(alice, project)
    assert not Permissions.can_modify_project(carol, project)

    task = TaskService.create("t1", "p1", "Write docs", alice)
    assert task.status is Status.TODO
    assert task.assignee_id is None
    assert not task.is_assigned
    assert not task.is_terminal

    try:
        TaskService.assign(alice, project, task, carol)
        raise AssertionError("Non-member should not be assignable")
    except PermissionDeniedError:
        pass

    TaskService.assign(alice, project, task, bob)
    assert task.assignee_id == "u2"
    assert task.is_assigned

    TaskService.unassign(alice, project, task)
    assert task.assignee_id is None
    TaskService.assign(alice, project, task, bob)

    TaskService.set_status(alice, project, task, Status.IN_PROGRESS)
    assert task.status is Status.IN_PROGRESS

    TaskService.set_status(alice, project, task, Status.DONE)
    assert task.status is Status.DONE
    assert task.is_terminal

    assert not Transitions.is_allowed(Status.DONE, Status.TODO)

    try:
        TaskService.set_status(alice, project, task, Status.TODO)
        raise AssertionError("Cannot transition out of Done")
    except InvalidTransitionError:
        pass

    try:
        ProjectService.remove_member(alice, project, "u1")
        raise AssertionError("Owner cannot be removed")
    except InvalidMemberError:
        pass

    ProjectService.remove_member(alice, project, "u2")
    assert not project.is_member("u2")

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
