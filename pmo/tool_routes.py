"""Only contract operations backed by a live implementation belong here."""
ROUTES = {
    'pmo_charter_save': ('PUT', '/projects/{projectId}/charter'),
    'pmo_project_list': ('GET', '/projects'),
    'pmo_project_get': ('GET', '/projects/{projectId}'),
    'pmo_project_create': ('POST', '/projects'),
    'pmo_project_draft_update': ('PATCH', '/projects/{projectId}'),
    'pmo_milestone_list': ('GET', '/projects/{projectId}/milestones'),
    'pmo_milestone_create': ('POST', '/projects/{projectId}/milestones'),
    'pmo_milestone_draft_update': ('PATCH', '/milestones/{milestoneId}'),
    'pmo_requirement_list': ('GET', '/projects/{projectId}/requirements'),
    'pmo_requirement_create': ('POST', '/projects/{projectId}/requirements'),
    'pmo_requirement_draft_update': ('PATCH', '/requirements/{requirementId}'),
}
