"""Project lifecycle: create projects, manage membership and delegations.

Every mutation routes through permission checks, so callers cannot bypass
ownership rules.

Delegation note: granting and revoking a delegation is itself an
owner-level capability and deliberately does *not* accept delegates as
actors — otherwise a delegate could extend or cement their own
authority. Regular project mutations (membership, tasks, status) do
accept delegates, via `can_modify_project`.
"""
from __future__ import annotations

from dataclasses import replace

from models import Project, User
from validation import can_modify_project, is_owner_or_admin


def create_project(id: str, name: str, owner: User) -> Project:
    """Create a new project with the given owner as sole member and no delegates."""
    return Project(
        id=id,
        name=name,
        owner_id=owner.id,
        member_ids=(owner.id,),
        delegate_ids=(),
    )


def add_member(actor: User, p: Project, member_id: str) -> Project:
    """Add a user to the project. Raises PermissionError if actor lacks authority."""
    if not can_modify_project(actor, p):
        raise PermissionError(f"Only the owner or an Admin can modify project {p.id}")
    if member_id in p.member_ids:
        return p
    return replace(p, member_ids=(*p.member_ids, member_id))


def remove_member(actor: User, p: Project, member_id: str) -> Project:
    """Remove a user from the project. Owner cannot be removed."""
    if not can_modify_project(actor, p):
        raise PermissionError(f"Only the owner or an Admin can modify project {p.id}")
    if member_id == p.owner_id:
        raise ValueError("Cannot remove the project owner")
    remaining = tuple(mid for mid in p.member_ids if mid != member_id)
    return replace(p, member_ids=remaining)


def grant_delegation(actor: User, p: Project, delegate_id: str) -> Project:
    """Grant `delegate_id` owner-equivalent authority over project p.

    Only the real owner or an Admin may grant a delegation; delegates
    cannot self-propagate. Granting to the owner is a no-op (the owner
    already has that authority). Re-granting an existing delegation is
    idempotent.
    """
    if not is_owner_or_admin(actor, p):
        raise PermissionError(
            f"Only the owner or an Admin can grant delegations on project {p.id}"
        )
    if delegate_id == p.owner_id:
        return p
    if delegate_id in p.delegate_ids:
        return p
    return replace(p, delegate_ids=(*p.delegate_ids, delegate_id))


def revoke_delegation(actor: User, p: Project, delegate_id: str) -> Project:
    """Revoke an active delegation. Idempotent if no such delegation exists."""
    if not is_owner_or_admin(actor, p):
        raise PermissionError(
            f"Only the owner or an Admin can revoke delegations on project {p.id}"
        )
    remaining = tuple(did for did in p.delegate_ids if did != delegate_id)
    return replace(p, delegate_ids=remaining)
