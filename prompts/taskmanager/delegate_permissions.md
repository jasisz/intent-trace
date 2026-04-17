# Change request: delegate project permissions

A project owner needs to be able to temporarily delegate their project-modification authority to another user — for example, when going on vacation. While a delegation is active, the delegated user can perform any operation that normally requires owner-or-Admin rights on that specific project.

Add a way to grant a delegation and to revoke it. Multiple simultaneous delegations per project are fine. The permission check that asks "can this user modify this project" should transparently accept a delegated user as well as the real owner.

Users who are neither owner, Admin, nor delegated should continue to be rejected as before. The delegation list is a property of the project, not of the user.
