# Change request: task priorities with assignment rules

Add priority levels to tasks: Low, Medium, High, Critical.

Priority affects assignment rules — specifically, Critical-priority tasks can only be assigned by users with Admin role (project ownership alone is not enough). Lower priorities keep the existing rule: owner or Admin + assignee-is-member.

Priority is also displayed in any task representation that callers might consume.

Existing tasks need a sensible default priority. The validation module should expose whatever new predicate is needed for the Critical-only-Admin rule so that `tasks` doesn't invent its own policy.
