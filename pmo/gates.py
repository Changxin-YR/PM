from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import ValidationError

from . import schema_v1 as db
from .change_intents import snapshot
from .charter_models import Fields
from .charters import COLUMNS
from .contracts import RULE_VERSION, Strict
from .costs import cost_basis, latest_plan, zero_budget_claim
from .evidence import verified_link
from .identity import Failure, explicit_grants, visible
from .projects import fingerprint
from .requirement_baselines import active_assignment, active_member, s as signatures
from .requirement_disputes import effective as effective_disputes
from .requirement_disputes import source as dispute_source
from .scope_quality import metrics

Gate = Literal['charter', 'feasibility', 'closing']
ENTRY = {'charter': 'drafting', 'feasibility': 'designing', 'closing': 'delivering'}
REVIEW = {gate: f'{gate}_review' for gate in ENTRY}


class Gap(Strict):
    code: str
    message: str
    sourceRefs: list[str]


class Check(Strict):
    projectId: UUID
    gate: Gate
    eligible: bool
    missing: list[Gap]
    ruleVersion: str = RULE_VERSION
    snapshotHash: str


def lock_project(c, a, pid, *, write=False):
    p = db.projects
    row = c.execute(sa.select(p, visible(a).label('materials_visible')).where(
        p.c.id == pid, visible(a, include_initiator=True)).with_for_update(read=not write)).mappings().one_or_none()
    if row is None:
        raise Failure(404, 'RESOURCE_NOT_FOUND')
    if a.kind == 'agent':
        g = explicit_grants.c
        grant = c.scalar(sa.select(g.project_id).where(g.credential_id == a.credential,
                         g.project_id == pid, g.granted_by == a.account).with_for_update(read=True))
        if not grant:
            raise Failure(403, 'EXPLICIT_PROJECT_GRANT_REQUIRED', project_id=pid)
    return row


def submitter(c, p, person: UUID, gate: Gate) -> bool:
    if gate == 'charter' and person == p.initiator_id and active_member(c, p, person, 'initiator'):
        return True
    return p.pm_id == person and active_member(c, p, person, 'pm')


def roster(c, p):
    m, n, u = db.project_members, db.persons, db.user_accounts
    excluded = [i for i in (p.initiator_id, p.pm_id, p.sponsor_id) if i is not None]
    return list(c.execute(sa.select(m.c.person_id, m.c.id, m.c.accepted_at).join(n, n.c.id == m.c.person_id).join(u, u.c.id == n.c.user_id).where(
        m.c.project_id == p.id, m.c.role == 'reviewer', m.c.assignment_status == 'accepted', m.c.accepted_at.is_not(None), m.c.revoked_at.is_(None),
        m.c.person_id.not_in(excluded), n.c.org_id == p.org_id, n.c.is_active, u.c.is_active,
        sa.or_(u.c.locked_until.is_(None), u.c.locked_until <= sa.func.now())).order_by(m.c.person_id).with_for_update(read=True, of=(m, n))).mappings())


@dataclass
class Materials:
    content: dict
    refs: list[str] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)

    def reject(self, code, message, refs=()):
        self.gaps.append(Gap(code=code, message=message, sourceRefs=list(refs)[:100]))

    def include(self, section, kind, rows):
        self.content[section] = [snapshot(row) for row in rows]
        refs = [f'{kind}:{row.id}@{row.revision}' for row in rows]
        self.refs.extend(refs)
        return refs


def _charter_value(value):
    if isinstance(value, str):
        return ' '.join(value.split())
    if isinstance(value, list):
        return [{key: ' '.join(text.split()) for key, text in risk.items()} for risk in value]
    return value


def charter_refiling(c, p, charter, out):
    reopened = c.execute(sa.text('''SELECT t.approval_request_id, i.reason, d.id AS rejection_id,
            snap.review_id, snap.snapshot_hash, snap.materials
        FROM project_stage_transitions t
        LEFT JOIN approval_requests q ON q.id=t.approval_request_id AND q.project_id=t.project_id
            AND q.action='project_stage_transition' AND q.status='applied'
        LEFT JOIN project_transition_intents i ON i.approval_id=q.id AND i.from_stage=t.from_stage AND i.to_stage=t.to_stage
        LEFT JOIN decisions d ON d.id::text=i.basis->'rejection'->>'id' AND d.project_id=t.project_id AND d.option='reject'
        LEFT JOIN reviews r ON r.id=d.review_id AND r.project_id=t.project_id AND r.type='charter'
        LEFT JOIN review_decision_snapshots decision ON decision.decision_id=d.id AND decision.review_id=r.id
        LEFT JOIN review_submission_snapshots snap ON snap.review_id=r.id AND snap.snapshot_hash=decision.material_hash
        WHERE t.project_id=:project AND t.from_stage='rejected' AND t.to_stage='drafting'
        ORDER BY t.to_revision DESC LIMIT 1'''), {'project': p.id}).mappings().one_or_none()
    if reopened is None:
        return
    if (not reopened.reason or not reopened.materials
            or fingerprint(reopened.materials) != reopened.snapshot_hash or len(reopened.materials.get('charter', [])) != 1):
        out.reject('CHARTER_REFILING_BASIS_REQUIRED', '重报需要可核验的原评审材料和重提说明。')
        return
    previous, = reopened.materials['charter']
    prior_ref = f"charter:{previous['id']}@{previous['revision']}"
    refs = [f'approval:{reopened.approval_request_id}', f'decision:{reopened.rejection_id}',
            f'review-snapshot:{reopened.review_id}', prior_ref]
    revised = snapshot(charter) if charter is not None and str(charter.id) == previous['id'] else {}
    changed = [
        key for key, column in COLUMNS.items()
        if _charter_value(previous[column]) != _charter_value(revised[column])
    ] if revised and revised['revision'] > previous['revision'] else []
    if not changed:
        out.reject('CHARTER_REFILING_CHANGES_REQUIRED', '请根据重提说明更新立项卡实质内容后再送审。', refs)
        return
    out.content['charterRefiling'] = {'previousCharterRef': prior_ref,
        'currentCharterRef': f'charter:{charter.id}@{charter.revision}', 'changedFields': changed,
        'rejectionId': str(reopened.rejection_id), 'resubmissionId': str(reopened.approval_request_id),
        'reason': reopened.reason}
    out.refs.extend(refs)


def charter_material(c, a, p, out):
    t = db.charters
    row = c.execute(sa.select(t).where(t.c.project_id == p.id)).mappings().one_or_none()
    refs = out.include('charter', 'charter', [row] if row else [])
    if row is None:
        out.reject('CHARTER_MISSING', '缺少立项卡。')
    else:
        data = {key: row[column] for key, column in COLUMNS.items()}
        for key in ('roughCost', 'roughEffort'):
            if data[key] is not None:
                data[key] = format(data[key], '.2f')
        for key, value in data.items():
            try:
                Fields.model_validate({key: value})
            except ValidationError:
                out.reject('CHARTER_FIELD_REQUIRED', f'立项卡字段缺失或无效：{key}。', refs)
    charter_refiling(c, p, row, out)
    from .review_calibration import freeze
    freeze(c, a, p, row, out)


def rows_in(c, table, pid):
    return list(c.execute(sa.select(table).where(table.c.project_id == pid).order_by(table.c.id)).mappings())


def signed_baseline(c, pid):
    customer = c.execute(sa.select(signatures).where(signatures.c.project_id == pid,
        signatures.c.signer_role == 'client_representative')).mappings().one_or_none()
    if not customer or fingerprint([str(pid), customer.snapshot]) != customer.content_hash:
        return []
    pm = c.execute(sa.select(signatures).where(signatures.c.project_id == pid,
        signatures.c.signer_role == 'pm', signatures.c.content_hash == customer.content_hash,
        signatures.c.signer_id != customer.signer_id, signatures.c.revision < customer.revision)
        .order_by(signatures.c.revision.desc()).limit(1)).mappings().one_or_none()
    return [pm, customer] if pm and pm.snapshot == customer.snapshot else []


def charter_strengthening(c, a, p, out):
    from .policy import review_action, selection
    from .review_responses import checked_evidence, latest, source_ref, view

    policy = selection(c, p, review_action('feasibility'))
    formal = bool(policy and 'synthetic' not in policy[0])
    rows = c.execute(sa.text('''WITH approved_charter AS (
        SELECT r.id, r.revision, s.transitioned_at FROM project_stage_transitions s
        JOIN reviews r ON r.id=s.review_id AND r.project_id=s.project_id
        JOIN review_decision_snapshots ds ON ds.review_id=r.id AND ds.request_id=s.request_id
        JOIN decisions d ON d.id=ds.decision_id AND d.review_id=r.id AND d.project_id=s.project_id
        WHERE s.project_id=:pid AND s.to_revision<=:revision AND s.from_stage='charter_review'
          AND s.to_stage='designing' AND r.type='charter' AND r.status='decided'
          AND r.verdict=d.option AND d.option IN ('pass','conditional_pass')
        ORDER BY s.to_revision DESC LIMIT 1
    )
    SELECT t.id,t.revision,t.title,t.status,t.completed_at,r.transitioned_at,r.id AS review_id,
           r.revision AS review_revision,b.item_key,b.evidence_required
    FROM approved_charter r JOIN review_strengthening_tasks b ON b.review_id=r.id
    JOIN tasks t ON t.id=b.task_id AND t.project_id=:pid AND t.source_type='review' AND t.source_id=r.id
    WHERE b.item_key LIKE 'dimension:%' OR b.item_key LIKE 'condition:%'
    ORDER BY b.item_key'''), {'pid': p.id, 'revision': p.revision}).mappings()
    for row in rows:
        refs = [f'task:{row.id}@{row.revision}', f'review:{row.review_id}@{row.review_revision}']
        out.refs.extend(refs)
        valid = row.status == 'done' and row.completed_at and row.completed_at >= row.transitioned_at
        if formal or row.evidence_required is not None:
            response = latest(c, row.id)
            try:
                valid = valid and response and response.submitted_at == row.completed_at and (
                    not row.evidence_required or bool(response.evidence))
                valid = valid and checked_evidence(c, a, p, [item['ref'] for item in response.evidence],
                    persisted=True) == response.evidence
            except Failure:
                valid = False
            if valid:
                material = view(response, row.review_id, row.item_key).model_dump(mode='json')
                out.content.setdefault('reinforcementResponses', []).append(material | {'evidence': response.evidence})
                out.refs.extend([source_ref(response), *material['evidenceRefs']])
        if not valid:
            out.reject('CHARTER_STRENGTHENING_RESPONSE_REQUIRED',
                       f'立项补强缺少当前有效的书面回应：{row.title}。', refs)


def planning_material(c, a, p, gate, out):
    if gate == 'feasibility':
        charter_strengthening(c, a, p, out)
    milestones = [m for m in rows_in(c, db.milestones, p.id) if not m.is_cancelled]
    ms_refs = out.include('milestones', 'milestone', milestones)
    if gate == 'feasibility' and not 3 <= len(milestones) <= 8:
        out.reject('MILESTONE_COUNT', '需有 3 至 8 个有效里程碑。', ms_refs)
    if gate == 'closing' and not milestones:
        out.reject('COMPLETION_EVIDENCE_REQUIRED', '缺少可核验的有效里程碑。')
    reqs = rows_in(c, db.requirements, p.id)
    out.include('requirements', 'requirement', reqs)
    quality, req_refs = metrics(c, p.id)
    out.refs.extend(req_refs)
    out.content['requirementMetrics'] = quality.model_dump(mode='json')
    signed = signed_baseline(c, p.id)
    out.content['baselineSignatures'] = [snapshot(i) for i in signed]
    out.refs.extend(f'requirement-baseline:{p.id}@{i.revision}' for i in signed)
    if not signed or quality.gaps or not quality.activeCount or any(not r.planned_confirm_at or not r.baseline_version for r in reqs if r.status != 'cancelled'):
        out.reject('SIGNED_REQUIREMENTS_REQUIRED', '需至少一条有效的已共同签署需求，且确认日期及基线版本完整。', req_refs)
    if quality.frozenCount:
        out.reject('PENDING_SCOPE_CHANGE', '存在待审范围变更。', req_refs)
    plan = c.execute(latest_plan(p.id)).mappings().one_or_none()
    plan_ref = [f'cost-plan:{p.id}@{plan.revision}'] if plan else []
    out.refs.extend(plan_ref)
    out.content['costPlan'] = snapshot(plan) if plan else None
    risks = rows_in(c, db.risks, p.id)
    risk_refs = out.include('risks', 'risk', risks)
    if gate == 'feasibility':
        from .category_costs import actual_refs, actuals, budget_ref, clock, forecast_ref, freeze_material, required_for_project

        draft, forecast = freeze_material(c, p)
        if draft is None and required_for_project(c, p):
            out.reject('CATEGORY_BUDGET_REQUIRED', '按政策填写完整类别预算后再送审。', plan_ref)
        if draft is not None:
            category_refs = [budget_ref(p.id, draft)]
            if forecast is not None:
                category_refs.append(forecast_ref(p.id, forecast))
            at = clock(c)
            _, posted_refs = actuals(c, cost_basis(c, p.id, at), at)
            active_refs = actual_refs(posted_refs)
            out.refs.extend(category_refs)
            out.refs.extend(active_refs)
            out.content['categoryPlan'] = {'budget': snapshot(draft), 'forecast': snapshot(forecast) if forecast else None,
                                           'actualSourceRefs': active_refs}
            if forecast is None or forecast.budget_revision != draft.revision:
                out.reject('CATEGORY_FORECAST_REQUIRED', '按当前类别预算填写完整剩余预测。', category_refs)
        for kind in ('money', 'effort'):
            if not plan or plan[kind + '_budget'] is None:
                out.reject(f'{kind.upper()}_BUDGET_REQUIRED', '需填写金额和人天预算草稿。', plan_ref)
        if not risks or any(not r.response.strip() for r in risks):
            out.reject('RISK_REGISTER_REQUIRED', '需登记项目风险并填写应对措施。', risk_refs)
        return
    disputes = effective_disputes(c, p.id)
    if disputes:
        out.include('requirementDisputes', 'requirement-dispute', disputes)
        open_refs = [dispute_source(row) for row in disputes if row.withdrawn_at is None]
        if open_refs:
            out.reject('CUSTOMER_DISPUTE_OPEN', '仍有针对当前有效需求的正式客户争议。', open_refs)
    if quality.activeP0 != quality.confirmedP0 or not quality.activeWeight or 10 * quality.confirmedWeight < 9 * quality.activeWeight:
        from .requirement_exceptions import gate_check
        exception = gate_check(c, a, p)
        if exception is None:
            out.reject('CUSTOMER_ACCEPTANCE_REQUIRED', '有效 P0 需全部确认，且加权满足率至少为 90%，或有具体需求的客户书面例外。', req_refs)
        else:
            out.content['requirementException'] = exception.snapshot
            out.refs.extend(exception.source_refs)
    for r in reqs:
        if r.status == 'customer_confirmed':
            evidence(c, a, out, r, 'requirement')
    for m in milestones:
        if m.actual_date is None:
            out.reject('MILESTONE_NOT_COMPLETED', '仍有未完成的里程碑。', [f'milestone:{m.id}@{m.revision}'])
        else:
            evidence(c, a, out, m, 'milestone')
    from .risk_resolution import resolve_risk
    for r in (r for r in risks if r.is_blocking):
        receipt = resolve_risk(c, a, p, r)
        if receipt is None:
            out.reject('BLOCKING_RISK_UNRESOLVED', '阻塞风险尚未关闭或未有可核验的接收确认。',
                       [f'risk:{r.id}@{r.revision}'])
        else:
            out.content.setdefault('riskResolutions', []).append(snapshot(receipt))
            out.refs += [f'risk-resolution:{receipt.id}', f'evidence:{receipt.evidence_id}']
    closing_costs(c, a, p, out, plan)


def evidence(c, a, out, row, kind):
    try:
        proof = verified_link(c, a, row.evidence_ref or '', row.project_id, kind, row.id, shared=True)
    except Failure:
        out.reject('COMPLETION_EVIDENCE_REQUIRED', '完成或客户确认缺少有效凭据。', [f'{kind}:{row.id}@{row.revision}'])
        return
    if kind == 'requirement':
        signoff = c.execute(sa.text('''SELECT * FROM requirement_transitions WHERE requirement_id=:id AND revision=:rev
            AND baseline_version=:version AND status='customer_confirmed' AND actor_type='human' AND evidence_id=:proof'''),
            dict(id=row.id, rev=row.revision, version=row.baseline_version, proof=proof.id)).mappings().one_or_none()
        if signoff is None:
            out.reject('CUSTOMER_CONFIRMATION_RECEIPT_REQUIRED', '缺少绑定当前需求版本及证据的人工客户确认。',
                [f'requirement:{row.id}@{row.revision}'])
        else:
            out.content.setdefault('confirmationReceipts', []).append(snapshot(signoff))
    out.refs.append(f'evidence:{proof.id}')
    out.content.setdefault('evidence', []).append({
        'evidenceId': str(proof.id), 'sha256': proof.sha256, 'revision': proof.revision,
        'verifiedBy': str(proof.verified_by), 'verifiedAt': proof.verified_at.isoformat(),
    })


def closing_costs(c, a, p, out, plan):
    from .cost_reconciliation import current_reconciliation
    budgets = [r for r in rows_in(c, db.cost_entries, p.id) if r.is_current_baseline]
    budget_refs = out.include('budgets', 'cost-entry', budgets)
    postings = rows_in(c, db.cost_postings, p.id)
    now = c.scalar(sa.select(sa.func.statement_timestamp()))
    verified = [r for r in postings if r.status == 'verified' and r.verified_at and r.occurred_at and r.occurred_at <= now]
    out.content['costPostings'] = [snapshot(r) for r in postings]
    out.content['occurredVerifiedPostingIds'] = [str(r.id) for r in verified]
    post_refs = [f'cost-posting:{r.id}' for r in postings]
    out.refs.extend(post_refs)
    if len(verified) != len(postings):
        out.reject('UNVERIFIED_OR_FUTURE_POSTINGS', '仍有未核验或尚未发生的凭证。', post_refs)
    reconciled = current_reconciliation(c, a, p)
    if not reconciled:
        out.reject('COST_RECONCILIATION_REQUIRED', '缺少当前有效的正式对账记录。逐笔核验不等同于项目已对账。', post_refs)
    else:
        receipt, proof_refs = reconciled
        out.content['costReconciliation'] = {k: v for k, v in snapshot(receipt).items()
            if k in ('id', 'revision', 'receipt_hash', 'cost_hash', 'signed_by', 'signed_at', 'covered_through')}
        out.refs.extend(proof_refs)
    zero_basis = None
    for kind in ('money', 'effort'):
        lines = [r for r in budgets if r.type == kind]
        total = sum((r.budget for r in lines), Decimal(0))
        actual = sum((r.amount_signed for r in verified if r.type == kind), Decimal(0))
        remaining = plan[kind + '_remaining'] if plan else None
        if remaining is None:
            remaining = sum((r.forecast_remaining for r in lines), Decimal(0))
        declared = None
        if lines and total == 0:
            if zero_basis is None:
                zero_basis = cost_basis(c, p.id, now)
            if zero_basis.budget_refs(kind) and zero_basis.amounts(kind)[0] == 0:
                declared = zero_budget_claim(c, zero_basis, kind, now)
            if declared:
                out.content.setdefault('zeroBudgetDeclarations', {})[kind] = snapshot(declared)
                out.refs.append(f'zero-budget:{declared.id}')
        if not lines or (total == 0 and declared is None):
            out.reject(f'{kind.upper()}_CLOSING_BUDGET_UNVERIFIED', '缺少有效预算或可核验的零预算声明。', budget_refs)
        elif actual < 0 or abs(actual - total) * 5 > total:
            out.reject(f'{kind.upper()}_CLOSING_VARIANCE', '结项最终偏差绝对值需不超过 20%。', [*budget_refs, *post_refs])
        if remaining != 0:
            unit = 'CNY' if kind == 'money' else '人天'
            out.reject(f'{kind.upper()}_FORECAST_NOT_ZERO',
                f'结项剩余预测为 {remaining:.2f} {unit}；真实后续承诺须逐项列明，现有例外范围不能放行。', budget_refs)


def evaluate(c, a, p, gate: Gate, *, pending=False):
    out = Materials({'gate': gate, 'ruleVersion': RULE_VERSION, 'project': {k: v for k, v in snapshot(p).items()
        if k in ('id', 'org_id', 'code', 'name', 'description', 'initiator_id', 'pm_id', 'sponsor_id')}})
    if p.stage != (REVIEW[gate] if pending else ENTRY[gate]):
        out.reject('GATE_STAGE_MISMATCH', '项目当前阶段不能进入此关口。')
        return Check(projectId=p.id, gate=gate, eligible=False, missing=out.gaps, snapshotHash=fingerprint(out.content)), out
    for role in ('pm', 'sponsor'):
        person = p[role + '_id']
        assignment = active_assignment(c, p, person, role) if person else None
        out.content[role + 'Assignment'] = snapshot(assignment) if assignment else None
        if assignment:
            out.refs.append(f'project-member:{assignment.id}')
        else:
            out.reject(role.upper() + '_ASSIGNMENT_REQUIRED', '须有本人接受且仍有效的项目经理及 Sponsor 任命。')
    members = roster(c, p)
    out.content['reviewers'] = [snapshot(m) for m in members]
    out.refs.extend(f'project-member:{m.id}' for m in members)
    if not members:
        out.reject('INDEPENDENT_REVIEWER_REQUIRED', '缺少有效的独立评审人。')
    if gate == 'charter':
        charter_material(c, a, p, out)
    else:
        planning_material(c, a, p, gate, out)
    if len(out.gaps) > 100:
        out.gaps[99:] = [Gap(code='ADDITIONAL_GAPS', message=f'另有 {len(out.gaps)-99} 项缺口。', sourceRefs=[])]
    return Check(projectId=p.id, gate=gate, eligible=not out.gaps, missing=out.gaps, snapshotHash=fingerprint(out.content)), out
