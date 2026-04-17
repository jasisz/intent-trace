# Change request: categorize rejection reasons

Currently, rejection reasons are free-form strings. That makes it hard to analyze rejections systematically: reports "rejected because policy" and "rejected - policy violation" look identical to a human but different to aggregation code.

Change rejection reasons to a closed set of categories, with an optional free-form note attached to each. The categories should cover the common classes: budget-exceeded, missing-documentation, policy-violation, duplicate-submission, and "other" with mandatory note.

`rejectReport` / `reject_report` must now take a categorized reason instead of a raw string. The audit trail should continue to record rejections in a readable form (the category plus the note if present).
