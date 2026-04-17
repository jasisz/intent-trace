"""Entry-point demo + smoke tests wiring users, a project, and tasks with
dependencies through the full API.
"""
from __future__ import annotations

from models import Project, Role, Status, Task, User
from projects import add_member, create_project, remove_member
from tasks import add_dependencies, assign_task, create_task, set_status, unassign_task
from validation import (
    can_assign,
    can_modify_project,
    can_transition_to,
    is_admin,
    is_member,
    is_owner_or_admin,
    unsatisfied_dependencies,
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

    tasks: dict[str, Task] = {}

    task = create_task("t1", project, "Write docs", alice)
    tasks[task.id] = task
    assert task.status is Status.TODO
    assert task.assignee_id is None
    assert task.depends_on_ids == ()

    try:
        assign_task(alice, project, task, carol)
        raise AssertionError("Non-member should not be assignable")
    except PermissionError:
        pass

    task = assign_task(alice, project, task, bob)
    tasks[task.id] = task
    assert task.assignee_id == "u2"

    task = set_status(alice, project, task, Status.IN_PROGRESS)
    tasks[task.id] = task
    assert task.status is Status.IN_PROGRESS

    # A task with no deps reaches Done without needing tasks_by_id.
    task = set_status(alice, project, task, Status.DONE)
    tasks[task.id] = task
    assert task.status is Status.DONE

    try:
        set_status(alice, project, task, Status.TODO)
        raise AssertionError("Cannot transition out of Done")
    except ValueError:
        pass

    # Dependencies: t2 depends on t1 (done), t3 depends on t1 + t2.
    t2 = create_task("t2", project, "Ship docs", alice, depends_on_ids=("t1",), tasks_by_id=tasks)
    tasks[t2.id] = t2
    assert t2.depends_on_ids == ("t1",)

    t3 = create_task(
        "t3",
        project,
        "Announce",
        alice,
        depends_on_ids=("t1", "t2"),
        tasks_by_id=tasks,
    )
    tasks[t3.id] = t3
    assert t3.depends_on_ids == ("t1", "t2")

    # Unknown dep id is rejected at creation.
    try:
        create_task("t_bad", project, "x", alice, depends_on_ids=("ghost",), tasks_by_id=tasks)
        raise AssertionError("Unknown dependency should be rejected")
    except ValueError:
        pass

    # Cross-project dependency is rejected.
    other_project = create_project("p2", "Other", alice)
    other_task = create_task("o1", other_project, "Other task", alice)
    tasks[other_task.id] = other_task
    try:
        create_task(
            "t_bad", project, "x", alice, depends_on_ids=("o1",), tasks_by_id=tasks
        )
        raise AssertionError("Cross-project dependency should be rejected")
    except ValueError:
        pass

    # t2 cannot move to Done without tasks_by_id available (deps must be checkable).
    t2 = set_status(alice, project, t2, Status.IN_PROGRESS)
    tasks[t2.id] = t2
    try:
        set_status(alice, project, t2, Status.DONE)
        raise AssertionError("Must require tasks_by_id for dep check")
    except ValueError:
        pass

    # t2 deps (t1) are Done -> transition succeeds.
    t2 = set_status(alice, project, t2, Status.DONE, tasks_by_id=tasks)
    tasks[t2.id] = t2
    assert t2.status is Status.DONE

    # t3 still Todo; try moving straight to IP then Done. t3 deps (t1, t2) are Done,
    # so it should succeed.
    t3 = set_status(alice, project, t3, Status.IN_PROGRESS)
    tasks[t3.id] = t3
    t3 = set_status(alice, project, t3, Status.DONE, tasks_by_id=tasks)
    tasks[t3.id] = t3
    assert t3.status is Status.DONE

    # Dep that isn't done blocks the transition and the error names the blocker.
    t4 = create_task("t4", project, "Plan v2", alice)
    tasks[t4.id] = t4
    t5 = create_task(
        "t5", project, "Execute v2", alice, depends_on_ids=("t4",), tasks_by_id=tasks
    )
    tasks[t5.id] = t5
    t5 = set_status(alice, project, t5, Status.IN_PROGRESS)
    tasks[t5.id] = t5
    try:
        set_status(alice, project, t5, Status.DONE, tasks_by_id=tasks)
        raise AssertionError("Unfinished dep must block Done")
    except ValueError as err:
        assert "t4" in str(err), f"error should mention blocker: {err!s}"

    assert unsatisfied_dependencies(t5, tasks) == ("t4",)

    # add_dependencies: same permission rules, same cross-project rules.
    t4 = set_status(alice, project, t4, Status.IN_PROGRESS)
    tasks[t4.id] = t4
    t6 = create_task("t6", project, "Retro", alice)
    tasks[t6.id] = t6

    try:
        add_dependencies(carol, project, t6, ("t4",), tasks)
        raise AssertionError("Viewer cannot add dependencies")
    except PermissionError:
        pass

    t6 = add_dependencies(alice, project, t6, ("t4",), tasks)
    tasks[t6.id] = t6
    assert t6.depends_on_ids == ("t4",)

    # Duplicate deps are collapsed, order preserved.
    t6 = add_dependencies(alice, project, t6, ("t4", "t5"), tasks)
    tasks[t6.id] = t6
    assert t6.depends_on_ids == ("t4", "t5")

    # Self-dep rejected.
    try:
        add_dependencies(alice, project, t6, ("t6",), tasks)
        raise AssertionError("Self-dependency must be rejected")
    except ValueError:
        pass

    # Cross-project add rejected.
    try:
        add_dependencies(alice, project, t6, ("o1",), tasks)
        raise AssertionError("Cross-project add_dependencies must be rejected")
    except ValueError:
        pass

    # Moving t6 to Done while t4 is still InProgress should fail with both blockers.
    t6 = set_status(alice, project, t6, Status.IN_PROGRESS)
    tasks[t6.id] = t6
    try:
        set_status(alice, project, t6, Status.DONE, tasks_by_id=tasks)
        raise AssertionError("Two unfinished deps must block Done")
    except ValueError as err:
        msg = str(err)
        assert "t4" in msg and "t5" in msg, f"error should list blockers: {msg}"

    # Finish t4, t5 -> t6 can complete.
    t4 = set_status(alice, project, t4, Status.DONE, tasks_by_id=tasks)
    tasks[t4.id] = t4
    t5 = set_status(alice, project, t5, Status.DONE, tasks_by_id=tasks)
    tasks[t5.id] = t5
    t6 = set_status(alice, project, t6, Status.DONE, tasks_by_id=tasks)
    tasks[t6.id] = t6
    assert t6.status is Status.DONE

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
