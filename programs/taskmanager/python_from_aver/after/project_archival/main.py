"""Entry-point demo + smoke tests wiring users, a project, and a task through the full API."""
from __future__ import annotations

from models import Role, Status, User
from projects import (
    add_member,
    archive_project,
    create_project,
    remove_member,
    unarchive_project,
)
from tasks import assign_task, create_task, set_status, unassign_task
from validation import (
    can_assign,
    can_modify_project,
    can_toggle_archive,
    can_transition_to,
    is_admin,
    is_archived,
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
    assert not project.archived
    assert not is_archived(project)

    project = add_member(alice, project, "u2")
    assert "u2" in project.member_ids

    try:
        add_member(carol, project, "u3")
        raise AssertionError("Viewer should not be able to add members")
    except PermissionError:
        pass

    task = create_task("t1", project, "Write docs", alice)
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

    # --- Archival -------------------------------------------------------

    # Non-owner / non-admin cannot archive.
    try:
        archive_project(carol, project)
        raise AssertionError("Viewer should not be able to archive")
    except PermissionError:
        pass

    # Bob is a Member but not the owner — still cannot archive.
    project = add_member(alice, project, "u2")
    try:
        archive_project(bob, project)
        raise AssertionError("Non-owner member should not be able to archive")
    except PermissionError:
        pass

    # Predicate surface behaves for a live project.
    assert can_toggle_archive(alice, project)
    assert not can_toggle_archive(carol, project)

    # Owner archives the project.
    project = archive_project(alice, project)
    assert project.archived
    assert is_archived(project)

    # Archiving is idempotent.
    again = archive_project(alice, project)
    assert again is project or again.archived

    # Reads still work on archived projects (membership is visible).
    assert is_member(project, "u1")
    assert is_member(project, "u2")
    assert is_owner_or_admin(alice, project)

    # Mutations on an archived project are rejected with a clear ValueError,
    # even when the actor would normally have write access.
    try:
        add_member(alice, project, "u3")
        raise AssertionError("Cannot add members to an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    try:
        remove_member(alice, project, "u2")
        raise AssertionError("Cannot remove members from an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    try:
        create_task("t2", project, "Polish", alice)
        raise AssertionError("Cannot create tasks in an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    try:
        assign_task(alice, project, task, bob)
        raise AssertionError("Cannot reassign tasks in an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    try:
        unassign_task(alice, project, task)
        raise AssertionError("Cannot unassign tasks in an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    # The Done → anything transition is still blocked independently; make a
    # fresh Todo-ish task-ish assertion instead: we observe that even a valid
    # transition is refused while archived.
    fresh = task  # currently Done, but that's fine — archival guard fires first
    try:
        set_status(alice, project, fresh, Status.IN_PROGRESS)
        raise AssertionError("Cannot change task status in an archived project")
    except ValueError as e:
        assert "archived" in str(e)

    # can_* predicates still report the underlying authority truthfully —
    # archival is enforced by ensure_not_archived at the call site, not by
    # silently flipping the permission predicates.
    assert can_modify_project(alice, project)
    assert can_assign(alice, project, bob)
    assert can_transition_to(Status.TODO, Status.IN_PROGRESS)

    # Non-owner / non-admin cannot un-archive either.
    try:
        unarchive_project(carol, project)
        raise AssertionError("Viewer should not be able to un-archive")
    except PermissionError:
        pass

    # Owner un-archives → mutations work again.
    project = unarchive_project(alice, project)
    assert not project.archived

    # Un-archive is idempotent.
    project = unarchive_project(alice, project)
    assert not project.archived

    project = add_member(alice, project, "u3")
    assert "u3" in project.member_ids

    # Admin (not owner) can also archive / un-archive.
    dave_admin = User(id="u4", name="Dave", role=Role.ADMIN)
    project = archive_project(dave_admin, project)
    assert project.archived
    project = unarchive_project(dave_admin, project)
    assert not project.archived

    print("smoke tests passed")


if __name__ == "__main__":
    _smoke_tests()
