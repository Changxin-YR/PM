"""Organization identity event types; these events have no project scope."""
INVITED = 'org.account.invited'
ENROLLED = 'org.account.enrolled'
INVITE_REVOKED = 'org.account.invite_revoked'
STATUS_CHANGED = 'org.account.status_changed'
ORGANIZATION_EVENTS = frozenset({INVITED, ENROLLED, INVITE_REVOKED, STATUS_CHANGED})
