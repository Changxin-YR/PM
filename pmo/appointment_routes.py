from uuid import UUID

from fastapi import APIRouter

from . import appointments as service
from .access import Auth, Key, Revision
from .appointments import InboxItem, Invitation, MemberProposal, Proposal
from .contracts import Result, Strict

router=APIRouter(prefix='/api/v1')


@router.post('/projects/{projectId}/role-invitations',operation_id='pmo_role_assignment_propose',response_model_exclude_none=True)
def propose(projectId:UUID,command:Proposal,authorization:Auth,key:Key,expected:Revision)->Result[Invitation]:
    return service.propose(authorization,projectId,int(expected),key,command)


@router.post('/projects/{projectId}/member-invitations', operation_id='human_member_assignment_propose',
             openapi_extra={'x-actorTypes': ['human']}, response_model_exclude_none=True)
def propose_member(projectId: UUID, command: MemberProposal, authorization: Auth, key: Key, expected: Revision) -> Result[Invitation]:
    return service.propose(authorization, projectId, int(expected), key, command, extended=True)


@router.get('/me/role-invitations',operation_id='human_my_role_invitations')
def inbox(authorization:Auth)->Result[list[InboxItem]]:
    return service.inbox(authorization)


@router.get('/me/appointment-queue',operation_id='human_appointment_queue')
def queue(authorization:Auth)->Result[list[InboxItem]]:
    return service.inbox(authorization,for_issue=True)


@router.post('/role-invitations/{invitationId}/issue',operation_id='human_issue_role_invitation')
def issue(invitationId:UUID,authorization:Auth,key:Key,expected:Revision,command:Strict|None=None)->Result[Invitation]:
    return service.respond(authorization,invitationId,int(expected),key,'issue')


@router.post('/role-invitations/{invitationId}/accept',operation_id='human_accept_role_assignment')
def accept(invitationId:UUID,authorization:Auth,key:Key,expected:Revision,command:Strict|None=None)->Result[Invitation]:
    return service.respond(authorization,invitationId,int(expected),key,'accept')


@router.post('/role-invitations/{invitationId}/decline',operation_id='human_decline_role_assignment')
def decline(invitationId:UUID,authorization:Auth,key:Key,expected:Revision,command:Strict|None=None)->Result[Invitation]:
    return service.respond(authorization,invitationId,int(expected),key,'decline')
