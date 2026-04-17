# Change request: task dependencies

Tasks can depend on other tasks. A task with dependencies cannot transition to `Done` until all its dependencies are `Done`.

Add the ability to declare dependencies when creating a task, or to add them later (same permission rules as other task mutations). A task can depend on multiple other tasks. Depending on tasks from a different project should not be allowed.

The transition rule in validation should reflect the new constraint: `setStatus`/`set_status` moving a task to `Done` must check that all dependencies are complete. Since validation needs to know dependency state, the dependency check needs to be given enough information at the call site to do its job.

If dependencies are not all done, fail with an error that says which dependencies are still blocking.
