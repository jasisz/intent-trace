"""Entry-point demo + smoke tests wiring users, a project, and a task through the full API."""
from __future__ import annotations

from models import Priority, Project, Role, Status, Task, User
from projects import add_member, create_project, remove_member
from tasks import assign_task, create_task, set_priority, set_status, unassign_task
from validation import (
    can_assign,
    can_assign_priority,
    can_modify_project,
    can_transition_to,
    is_admin,
    is_member,
    is_owner_or_admin,
)


def _smoke_tests() -> None:
    alice = User(id="u1", name="Alice", role=Role.ADMIN)
    bob = User(id="u2", name="Bob", role=Role.MEMBER)
    carol = User(id="u3", name="Carol", role=Role.VIEWER)
    # Dave is a non-admin project owner; he has write access but is not Admin,
    # so he must not be allowed to assign Critical tasks.
    dave = User(id="u4", name="Dave", role=Role.MEMBER)

    assert is_admin(alice)
    assert not is_admin(bob)

    project = create_project("p1", "Launch", alice)
    assert project.member_ids == ("u1",)

    project = add_member(alice, project, "u2")
    assert "u2" in project.member_ids

    try:
        add_member(carol, project, "u3")
        raise AssertionError("Viewer should not be able to add members")
    except PermissionError:
        pass

    # ----- Priority basics -----
    task = create_task("t1", "p1", "Write docs", alice)
    assert task.status is Status.TODO
    assert task.assignee_id is None
    # Default priority for existing-style creation is Medium.
    assert task.priority is Priority.MEDIUM

    # Priority appears in the task's repr so callers that display it see it.
    assert "priority=" in repr(task)
    assert "medium" in repr(task).lower()

    try:
        assign_task(alice, project, task, carol)
        raise AssertionError("Non-member should not be assignable")
    except PermissionError:
        pass

    task = assign_task(alice, project, task, bob)
    assert task.assignee_id == "u2"

    task = set_status(alice, project, task, Status.IN_PROGRESS)
    assert task.status is Status.IN_PROGRESS

    task = set_status(alice, project, task, Status.DONE)
    assert task.status is Status.DONE

    try:
        set_status(alice, project, task, Status.TODO)
        raise AssertionError("Cannot transition out of Done")
    except ValueError:
        pass

    # ----- Priority assignment rules -----
    # Admin can assign a Critical task.
    critical_task = create_task(
        "t2", "p1", "Ship hotfix", alice, priority=Priority.CRITICAL
    )
    assert critical_task.priority is Priority.CRITICAL
    critical_task = assign_task(alice, project, critical_task, bob)
    assert critical_task.assignee_id == "u2"

    # A non-admin project owner may assign Low/Medium/High but NOT Critical.
    dave_project = create_project("p2", "Dave's repo", dave)
    dave_project = add_member(dave, dave_project, "u2")  # bob joins as member
    # High is fine for a non-admin owner.
    high_task = create_task(
        "t3", "p2", "Tune perf", dave, priority=Priority.HIGH
    )
    high_task = assign_task(dave, dave_project, high_task, bob)
    assert high_task.assignee_id == "u2"
    assert high_task.priority is Priority.HIGH

    # Critical is rejected even though Dave owns the project.
    dave_critical = create_task(
        "t4", "p2", "Emergency", dave, priority=Priority.CRITICAL
    )
    try:
        assign_task(dave, dave_project, dave_critical, bob)
        raise AssertionError(
            "Non-admin project owner should not assign Critical tasks"
        )
    except PermissionError:
        pass

    # Predicate itself enforces the same rule.
    assert not can_assign_priority(dave, dave_project, bob, Priority.CRITICAL)
    assert can_assign_priority(dave, dave_project, bob, Priority.HIGH)
    assert can_assign_priority(alice, project, bob, Priority.CRITICAL)
    # Non-member assignees still rejected regardless of priority.
    assert not can_assign_priority(alice, project, carol, Priority.LOW)

    # Raising a task to Critical still requires an Admin to then assign it.
    escalated = create_task("t5", "p2", "Escalate me", dave)
    escalated = set_priority(dave, dave_project, escalated, Priority.CRITICAL)
    assert escalated.priority is Priority.CRITICAL
    try:
        assign_task(dave, dave_project, escalated, bob)
        raise AssertionError("Escalated-to-Critical still rejects non-admin owner")
    except PermissionError:
        pass

    # ----- Original project-membership invariants -----
    try:
        remove_member(alice, project, "u1")
        raise AssertionError("Owner cannot be removed")
    except ValueError:
        pass

    project = remove_member(alice, project, "u2")
    assert "u2" not in project.member_ids

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
