"""Entry-point demo + smoke tests wiring users, a project, and a task through the full API."""
from __future__ import annotations

from models import Project, Role, Status, Task, User
from projects import add_member, create_project, remove_member
from tasks import assign_task, create_task, set_status, unassign_task
from validation import (
    can_assign,
    can_modify_project,
    can_transition_to,
    is_admin,
    is_member,
    is_owner_or_admin,
)


def _smoke_tests() -> None:
    """Run a representative sequence of API calls to check the module wires together."""
    alice = User(id="u1", name="Alice", role=Role.ADMIN)
    bob = User(id="u2", name="Bob", role=Role.MEMBER)
    carol = User(id="u3", name="Carol", role=Role.VIEWER)

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

    task = create_task("t1", "p1", "Write docs", alice)
    assert task.status is Status.TODO
    assert task.assignee_id is None

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
