"""Entry point wiring the OOP task-manager API through smoke tests."""
from __future__ import annotations

from models import (
    DependenciesNotSatisfiedError,
    InvalidDependencyError,
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
    assert not task.has_dependencies

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

    # Dependency smoke tests --------------------------------------------------
    dep_a = TaskService.create("t2", "p1", "Design", alice)
    dep_b = TaskService.create("t3", "p1", "Prototype", alice)
    feature = TaskService.create(
        "t4", "p1", "Ship feature", alice, dependencies=[dep_a, dep_b]
    )
    assert tuple(feature.dependencies()) == ("t2", "t3")
    assert feature.has_dependencies
    assert feature.depends_on("t2")
    assert feature.depends_on("t3")
    assert not feature.depends_on("t1")

    TaskService.set_status(alice, project, feature, Status.IN_PROGRESS)

    # Moving feature to Done must fail while both deps are still TODO.
    try:
        TaskService.set_status(
            alice, project, feature, Status.DONE, dependency_tasks=[dep_a, dep_b]
        )
        raise AssertionError("Done should be blocked by incomplete dependencies")
    except DependenciesNotSatisfiedError as exc:
        msg = str(exc)
        assert "t2" in msg and "t3" in msg

    # Finish one dependency; still blocked by the other.
    TaskService.set_status(alice, project, dep_a, Status.IN_PROGRESS)
    TaskService.set_status(alice, project, dep_a, Status.DONE)
    assert dep_a.is_terminal

    try:
        TaskService.set_status(
            alice, project, feature, Status.DONE, dependency_tasks=[dep_a, dep_b]
        )
        raise AssertionError("Done should still be blocked by t3")
    except DependenciesNotSatisfiedError as exc:
        msg = str(exc)
        assert "t3" in msg
        assert "t2" not in msg

    # Dependency-state omission on a task with deps must fail loudly.
    try:
        TaskService.set_status(alice, project, feature, Status.DONE)
        raise AssertionError("Missing dependency snapshot should be rejected")
    except InvalidDependencyError:
        pass

    # Finish the last dependency -> feature can now complete.
    TaskService.set_status(alice, project, dep_b, Status.IN_PROGRESS)
    TaskService.set_status(alice, project, dep_b, Status.DONE)
    assert Transitions.dependencies_satisfied(feature, [dep_a, dep_b])
    TaskService.set_status(
        alice, project, feature, Status.DONE, dependency_tasks=[dep_a, dep_b]
    )
    assert feature.is_terminal

    # Add-dependency path: permission + cross-project guard.
    other_project = ProjectService.create("p2", "Other", alice)
    foreign_task = TaskService.create("t5", "p2", "Foreign", alice)
    new_task = TaskService.create("t6", "p1", "Later", alice)

    try:
        TaskService.add_dependency(alice, project, new_task, foreign_task)
        raise AssertionError("Cross-project dependencies should be rejected")
    except InvalidDependencyError:
        pass
    assert not new_task.has_dependencies

    try:
        TaskService.add_dependency(carol, project, new_task, dep_a)
        raise AssertionError("Viewer should not add dependencies")
    except PermissionDeniedError:
        pass
    assert not new_task.has_dependencies

    TaskService.add_dependency(alice, project, new_task, dep_a)
    assert new_task.depends_on("t2")
    # Idempotent: re-adding the same dep is a no-op.
    TaskService.add_dependency(alice, project, new_task, dep_a)
    assert tuple(new_task.dependencies()) == ("t2",)

    # Self-dependency is always rejected.
    try:
        TaskService.add_dependency(alice, project, new_task, new_task)
        raise AssertionError("Self-dependency should be rejected")
    except InvalidDependencyError:
        pass

    # Task without deps can still reach Done without passing dependency_tasks.
    TaskService.set_status(alice, project, new_task, Status.IN_PROGRESS)
    # new_task now depends on dep_a (Done), so we must provide its state.
    TaskService.set_status(
        alice, project, new_task, Status.DONE, dependency_tasks=[dep_a]
    )
    assert new_task.is_terminal

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
