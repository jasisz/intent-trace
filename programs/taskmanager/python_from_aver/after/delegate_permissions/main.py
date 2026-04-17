"""Entry-point demo + smoke tests wiring users, a project, and a task through the full API."""
from __future__ import annotations

from models import Project, Role, Status, Task, User
from projects import (
    add_member,
    create_project,
    grant_delegation,
    remove_member,
    revoke_delegation,
)
from tasks import assign_task, create_task, set_status, unassign_task
from validation import (
    can_assign,
    can_modify_project,
    can_transition_to,
    is_admin,
    is_delegated,
    is_member,
    is_owner_or_admin,
)


def _smoke_tests() -> None:
    alice = User(id="u1", name="Alice", role=Role.ADMIN)
    bob = User(id="u2", name="Bob", role=Role.MEMBER)
    carol = User(id="u3", name="Carol", role=Role.VIEWER)
    dave = User(id="u4", name="Dave", role=Role.MEMBER)

    assert is_admin(alice)
    assert not is_admin(bob)

    project = create_project("p1", "Launch", alice)
    assert project.member_ids == ("u1",)
    assert project.delegate_ids == ()

    project = add_member(alice, project, "u2")
    assert "u2" in project.member_ids

    project = add_member(alice, project, "u4")
    assert "u4" in project.member_ids

    try:
        add_member(carol, project, "u3")
        raise AssertionError("Viewer should not be able to add members")
    except PermissionError:
        pass

    # Bob is a plain member — without delegation he cannot modify the project.
    assert not can_modify_project(bob, project)
    try:
        add_member(bob, project, "u3")
        raise AssertionError("Non-delegated member should not be able to add members")
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

    # --- Delegation: Alice (owner) delegates her authority to Bob while away.
    project = grant_delegation(alice, project, bob.id)
    assert bob.id in project.delegate_ids
    assert is_delegated(bob, project)
    assert can_modify_project(bob, project)

    # Delegated Bob can now do owner-gated work: add a member and move task state.
    project = add_member(bob, project, "u5")
    assert "u5" in project.member_ids

    task = set_status(bob, project, task, Status.DONE)
    assert task.status is Status.DONE

    # Delegation also lifts the owner-or-Admin gate for assignment.
    task2 = create_task("t2", "p1", "Ship release", alice)
    task2 = assign_task(bob, project, task2, dave)
    assert task2.assignee_id == "u4"

    # A plain non-delegated member (Dave) is still rejected.
    assert not is_delegated(dave, project)
    try:
        add_member(dave, project, "u6")
        raise AssertionError("Non-delegated member should be rejected")
    except PermissionError:
        pass

    # Granting delegation is itself restricted to owner/Admin — a delegate
    # cannot grant further delegations.
    try:
        grant_delegation(bob, project, dave.id)
        raise AssertionError("Delegates must not be able to grant delegations")
    except PermissionError:
        pass

    # Idempotent re-grant: granting Bob again changes nothing.
    same_project = grant_delegation(alice, project, bob.id)
    assert same_project.delegate_ids == project.delegate_ids

    # Granting to the owner is a no-op (owner already has authority).
    same_project = grant_delegation(alice, project, alice.id)
    assert alice.id not in same_project.delegate_ids

    # --- Revocation: Alice revokes Bob's delegation; Bob loses authority.
    project = revoke_delegation(alice, project, bob.id)
    assert bob.id not in project.delegate_ids
    assert not is_delegated(bob, project)
    assert not can_modify_project(bob, project)

    try:
        add_member(bob, project, "u7")
        raise AssertionError("Revoked delegate should no longer modify project")
    except PermissionError:
        pass

    try:
        assign_task(bob, project, task2, dave)
        raise AssertionError("Revoked delegate should not be able to assign")
    except PermissionError:
        pass

    # Revoking a non-existent delegation is idempotent.
    same_project = revoke_delegation(alice, project, "u99")
    assert same_project.delegate_ids == project.delegate_ids

    try:
        remove_member(alice, project, "u1")
        raise AssertionError("Owner cannot be removed")
    except ValueError:
        pass

    project = remove_member(alice, project, "u2")
    assert "u2" not in project.member_ids

    # Cannot transition out of Done.
    try:
        set_status(alice, project, task, Status.TODO)
        raise AssertionError("Cannot transition out of Done")
    except ValueError:
        pass

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
