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
    dave = User(id="u4", name="Dave", role=Role.MEMBER)

    assert alice.is_admin
    assert not bob.is_admin

    # Ownership requires a non-admin owner so we can exercise delegation without
    # the Admin shortcut masking the delegate path.
    project = ProjectService.create("p1", "Launch", bob)
    assert tuple(project.members()) == ("u2",)
    assert project.is_owner("u2")
    assert project.is_member("u2")
    assert tuple(project.delegates()) == ()

    ProjectService.add_member(bob, project, "u3")
    ProjectService.add_member(bob, project, "u4")
    assert project.is_member("u3")
    assert project.is_member("u4")

    try:
        ProjectService.add_member(carol, project, "u3")
        raise AssertionError("Viewer should not be able to add members")
    except PermissionDeniedError:
        pass

    assert Permissions.can_modify_project(bob, project)
    assert Permissions.can_modify_project(alice, project)  # Admin override
    assert not Permissions.can_modify_project(carol, project)
    assert not Permissions.can_modify_project(dave, project)

    task = TaskService.create("t1", "p1", "Write docs", bob)
    assert task.status is Status.TODO
    assert task.assignee_id is None
    assert not task.is_assigned
    assert not task.is_terminal

    # --- Delegation grant flow ---------------------------------------------
    # Non-delegated, non-owner, non-admin user is rejected up front.
    try:
        TaskService.set_status(dave, project, task, Status.IN_PROGRESS)
        raise AssertionError("Non-delegated member cannot modify project")
    except PermissionDeniedError:
        pass

    # A non-owner/non-admin cannot grant delegations either.
    try:
        ProjectService.grant_delegation(carol, project, "u4")
        raise AssertionError("Viewer should not be able to grant delegation")
    except PermissionDeniedError:
        pass

    ProjectService.grant_delegation(bob, project, "u4")
    assert project.is_delegate("u4")
    assert tuple(project.delegates()) == ("u4",)
    assert Permissions.can_modify_project(dave, project)

    # Idempotent: granting the same delegate again is a no-op.
    ProjectService.grant_delegation(bob, project, "u4")
    assert tuple(project.delegates()) == ("u4",)

    # Multiple simultaneous delegations are allowed.
    ProjectService.grant_delegation(bob, project, "u3")
    assert project.is_delegate("u3")
    assert set(project.delegates()) == {"u3", "u4"}

    # Delegate Dave now performs an owner-gated operation.
    TaskService.set_status(dave, project, task, Status.IN_PROGRESS)
    assert task.status is Status.IN_PROGRESS

    # Delegate Dave may also assign, since delegation grants full modify rights.
    TaskService.assign(dave, project, task, bob)
    assert task.assignee_id == "u2"

    TaskService.unassign(dave, project, task)
    assert task.assignee_id is None
    TaskService.assign(bob, project, task, bob)

    # --- Delegation revoke flow --------------------------------------------
    ProjectService.revoke_delegation(bob, project, "u4")
    assert not project.is_delegate("u4")
    assert tuple(project.delegates()) == ("u3",)

    # Revoked user is rejected again, just like before the grant.
    try:
        TaskService.set_status(dave, project, task, Status.DONE)
        raise AssertionError("Revoked delegate cannot modify project")
    except PermissionDeniedError:
        pass

    # Revoking an unknown user is a no-op, not an error.
    ProjectService.revoke_delegation(bob, project, "u999")
    assert tuple(project.delegates()) == ("u3",)

    # The remaining delegate (Carol) still has rights.
    TaskService.set_status(carol, project, task, Status.DONE)
    assert task.status is Status.DONE
    assert task.is_terminal

    assert not Transitions.is_allowed(Status.DONE, Status.TODO)

    try:
        TaskService.set_status(bob, project, task, Status.TODO)
        raise AssertionError("Cannot transition out of Done")
    except InvalidTransitionError:
        pass

    try:
        ProjectService.remove_member(bob, project, "u2")
        raise AssertionError("Owner cannot be removed")
    except InvalidMemberError:
        pass

    # Admin override still works regardless of delegation state.
    ProjectService.revoke_delegation(alice, project, "u3")
    assert tuple(project.delegates()) == ()

    ProjectService.remove_member(alice, project, "u3")
    assert not project.is_member("u3")

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
