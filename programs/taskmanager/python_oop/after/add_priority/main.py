"""Entry point wiring the OOP task-manager API through smoke tests."""
from __future__ import annotations

from models import (
    DEFAULT_PRIORITY,
    InvalidMemberError,
    InvalidTransitionError,
    PermissionDeniedError,
    Priority,
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
    # Default priority sanity
    assert task.priority is DEFAULT_PRIORITY
    assert task.priority is Priority.MEDIUM
    assert not task.is_critical
    assert "priority=medium" in task.describe()

    try:
        TaskService.assign(alice, project, task, carol)
        raise AssertionError("Non-member should not be assignable")
    except PermissionDeniedError:
        pass

    TaskService.assign(alice, project, task, bob)
    assert task.assignee_id == "u2"
    assert task.is_assigned
    assert "assignee=u2" in task.describe()

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

    # --- Priority-specific tests -------------------------------------------------

    # Explicit low-priority task, assignment behaves as before.
    low_task = TaskService.create("t2", "p1", "Tidy up", alice, Priority.LOW)
    assert low_task.priority is Priority.LOW
    TaskService.assign(alice, project, low_task, bob)
    assert low_task.assignee_id == "u2"

    # High-priority task; still only requires owner-or-admin, so Bob-as-owner
    # scenario is covered by a separate project where Bob owns it.
    bob_project = ProjectService.create("p2", "Bob's", bob)
    ProjectService.add_member(bob, bob_project, "u4")
    high_task = TaskService.create("t3", "p2", "Ship beta", bob, Priority.HIGH)
    assert high_task.priority is Priority.HIGH
    TaskService.assign(bob, bob_project, high_task, dave)
    assert high_task.assignee_id == "u4"

    # Critical task, created in Alice's project. Bob is a member but not admin,
    # so even with full project access in his own project, in Alice's project
    # he is not an owner, and Critical requires Admin globally.
    crit_task = TaskService.create(
        "t4", "p1", "Fix outage", alice, Priority.CRITICAL
    )
    assert crit_task.priority is Priority.CRITICAL
    assert crit_task.is_critical
    assert "priority=critical" in crit_task.describe()

    # Admin can assign Critical tasks to members.
    TaskService.assign(alice, project, crit_task, bob)
    assert crit_task.assignee_id == "u2"

    # Predicate exposed from validation is the source of truth for the rule.
    assert Permissions.requires_admin_priority(Priority.CRITICAL)
    assert not Permissions.requires_admin_priority(Priority.HIGH)
    assert not Permissions.requires_admin_priority(Priority.MEDIUM)
    assert not Permissions.requires_admin_priority(Priority.LOW)

    # Non-admin owner cannot assign a Critical task in their own project.
    bob_crit = TaskService.create(
        "t5", "p2", "Fix critical", bob, Priority.CRITICAL
    )
    try:
        TaskService.assign(bob, bob_project, bob_crit, dave)
        raise AssertionError("Non-admin owner cannot assign Critical tasks")
    except PermissionDeniedError:
        pass

    # can_assign matches require_assign on the Critical rule.
    assert not Permissions.can_assign(bob, bob_project, dave, Priority.CRITICAL)
    assert Permissions.can_assign(bob, bob_project, dave, Priority.HIGH)
    assert Permissions.can_assign(alice, project, bob, Priority.CRITICAL)

    # Admin promotes a task to Critical, then bumps assignment rule kicks in.
    promoted = TaskService.create("t6", "p2", "Promote me", bob, Priority.LOW)
    # Bob can raise priority while he still owns the project.
    TaskService.set_priority(bob, bob_project, promoted, Priority.CRITICAL)
    assert promoted.priority is Priority.CRITICAL
    # But then Bob cannot assign it because it became Critical.
    try:
        TaskService.assign(bob, bob_project, promoted, dave)
        raise AssertionError("Critical assignment must be Admin-only")
    except PermissionDeniedError:
        pass

    # Non-member still can't be assigned, even by Admin, for a Critical task.
    try:
        TaskService.assign(alice, project, crit_task, carol)
        raise AssertionError("Non-member cannot be assigned regardless of priority")
    except PermissionDeniedError:
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
