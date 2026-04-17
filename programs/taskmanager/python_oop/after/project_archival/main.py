"""Entry point wiring the OOP task-manager API through smoke tests."""
from __future__ import annotations

from models import (
    InvalidMemberError,
    InvalidTransitionError,
    PermissionDeniedError,
    ProjectArchivedError,
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

    # --- Archival ---
    archive_project = ProjectService.create("p2", "Archive demo", alice)
    ProjectService.add_member(alice, archive_project, "u2")
    live_task = TaskService.create("t2", "p2", "Active work", alice)
    TaskService.assign(alice, archive_project, live_task, bob)

    # Only owner/admin can archive.
    try:
        ProjectService.archive(carol, archive_project)
        raise AssertionError("Viewer should not be able to archive")
    except PermissionDeniedError:
        pass
    assert not archive_project.is_archived

    # Owner archives successfully; state flips.
    ProjectService.archive(alice, archive_project)
    assert archive_project.is_archived

    # Queries still work on archived projects.
    assert archive_project.is_member("u2")
    assert tuple(archive_project.members()) == ("u1", "u2")
    assert archive_project.is_owner("u1")

    # Every mutation path rejects archived project.
    try:
        ProjectService.add_member(alice, archive_project, "u3")
        raise AssertionError("Cannot add members to archived project")
    except ProjectArchivedError:
        pass

    try:
        ProjectService.remove_member(alice, archive_project, "u2")
        raise AssertionError("Cannot remove members from archived project")
    except ProjectArchivedError:
        pass

    try:
        TaskService.set_status(alice, archive_project, live_task, Status.IN_PROGRESS)
        raise AssertionError("Cannot change task status on archived project")
    except ProjectArchivedError:
        pass

    try:
        TaskService.unassign(alice, archive_project, live_task)
        raise AssertionError("Cannot unassign on archived project")
    except ProjectArchivedError:
        pass

    try:
        TaskService.assign(alice, archive_project, live_task, alice)
        raise AssertionError("Cannot reassign on archived project")
    except ProjectArchivedError:
        pass

    # Predicate layer still reports modify authority (archival is separate axis).
    assert Permissions.can_modify_project(alice, archive_project)
    assert not Permissions.can_write_project(alice, archive_project)
    assert not Permissions.can_assign(alice, archive_project, bob)

    # Non-owner, non-admin still cannot unarchive.
    try:
        ProjectService.unarchive(bob, archive_project)
        raise AssertionError("Member should not be able to unarchive")
    except PermissionDeniedError:
        pass

    # Owner unarchives; writes resume.
    ProjectService.unarchive(alice, archive_project)
    assert not archive_project.is_archived
    TaskService.set_status(alice, archive_project, live_task, Status.IN_PROGRESS)
    assert live_task.status is Status.IN_PROGRESS

    # Archive/unarchive are idempotent.
    ProjectService.archive(alice, archive_project)
    ProjectService.archive(alice, archive_project)
    assert archive_project.is_archived
    ProjectService.unarchive(alice, archive_project)
    ProjectService.unarchive(alice, archive_project)
    assert not archive_project.is_archived

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
