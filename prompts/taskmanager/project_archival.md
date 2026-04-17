# Change request: project archival

Projects can be archived. An archived project is read-only: no new tasks, no task status changes, no membership changes. Queries about the project still work (you can still fetch it, see members, see tasks).

Add the ability to archive and un-archive a project (only the owner or an Admin can do either). Attempts to mutate an archived project must fail with a clear error; attempts to mutate its tasks must also fail, even from users who normally have write access.

The existing permission checks should compose naturally with the archival check — don't duplicate logic across operations. The validation module is the right place for the archival predicate.
