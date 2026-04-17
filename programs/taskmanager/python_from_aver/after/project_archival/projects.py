"""Project lifecycle: create projects, manage membership, archive/un-archive.

Every mutation routes through permission checks, so callers cannot bypass
ownership rules. Membership mutations additionally require the project to be
un-archived; archival toggles themselves bypass that check (un-archival *must*
work on an archived project).
"""
from __future__ import annotations

from dataclasses import replace

from models import Project, User
from validation import can_modify_project, can_toggle_archive, ensure_not_archived


def create_project(id: str, name: str, owner: User) -> Project:
    """Create a new project with the given owner as sole member."""
    return Project(
        id=id,
        name=name,
        owner_id=owner.id,
        member_ids=(owner.id,),
        archived=False,
    )


def add_member(actor: User, p: Project, member_id: str) -> Project:
    """Add a user to the project. Raises PermissionError if actor lacks authority."""
    if not can_modify_project(actor, p):
        raise PermissionError(f"Only the owner or an Admin can modify project {p.id}")
    ensure_not_archived(p)
    if member_id in p.member_ids:
        return p
    return replace(p, member_ids=(*p.member_ids, member_id))


def remove_member(actor: User, p: Project, member_id: str) -> Project:
    """Remove a user from the project. Owner cannot be removed."""
    if not can_modify_project(actor, p):
        raise PermissionError(f"Only the owner or an Admin can modify project {p.id}")
    ensure_not_archived(p)
    if member_id == p.owner_id:
        raise ValueError("Cannot remove the project owner")
    remaining = tuple(mid for mid in p.member_ids if mid != member_id)
    return replace(p, member_ids=remaining)


def archive_project(actor: User, p: Project) -> Project:
    """Mark the project as archived. Only the owner or an Admin may do so.

    Archiving an already-archived project is a no-op.
    """
    if not can_toggle_archive(actor, p):
        raise PermissionError(
            f"Only the owner or an Admin can archive project {p.id}"
        )
    if p.archived:
        return p
    return replace(p, archived=True)


def unarchive_project(actor: User, p: Project) -> Project:
    """Restore the project to its active state. Only the owner or an Admin may do so.

    Un-archiving an already-active project is a no-op.
    """
    if not can_toggle_archive(actor, p):
        raise PermissionError(
            f"Only the owner or an Admin can un-archive project {p.id}"
        )
    if not p.archived:
        return p
    return replace(p, archived=False)
