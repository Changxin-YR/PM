"""用同一个园区报修项目演练从立项到归档的角色协作。"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scripts import pilot_local as local
from scripts.pilot_r6 import run
from scripts.pilot_r6_case_receipts import clarify, receive

CASE = ('R6-PARK-001', '东湖园区设备报修与回单试点', 'mobile')
CATEGORIES = ('development', 'implementation', 'management')
BASIS = ('研发签到与接口联调清单', '巡检班组现场实施登记', '交付协调与交接会议记录')


def evidence(item, label, kind, resource, description, document=None):
    if item.done(label+'-核验'):
        return item.steps[label+'-核验']['data']['evidenceRef']
    if document is None:
        image = Image.new('RGB', (760, 210), 'white')
        face = ImageFont.truetype(str(Path('C:/Windows/Fonts/msyh.ttc')), 22)
        ImageDraw.Draw(image).multiline_text((24, 24), '东湖园区设备报修与回单试点\n' + description + '\n虚拟项目演练原件', fill='#263848', font=face, spacing=10)
        source = BytesIO()
        image.save(source, format='PNG')
        file = ('园区核查记录.png', source.getvalue(), 'image/png')
    else:
        file = ('双口径对账单.json', json.dumps(document, ensure_ascii=False).encode(), 'application/json')
    rev = item.get('pm', item.path+'/evidence')['collectionRevision']
    row = item.write(label+'-上传', 'pm', item.path+'/evidence', revision=rev,
        uploads={'files': {'file':file}, 'data': {'linkedResourceType':kind,'linkedResourceId':resource,'description':description}})
    checked = item.write(label+'-核验','review2','evidence/'+row['evidenceId']+'/verify',
        {'reason':'逐项核对本次演练原件、对应工单和文件摘要。','sha256':row['sha256']},row['revision'])
    return checked['evidenceRef']


def export_weekly(item, index):
    report = item.steps[f'第{index}期周报发布']['data']
    export = item.get('pm', f"reports/{report['reportId']}/weekly-export")
    content = export['html'].encode('utf-8')
    assert sha256(content).hexdigest() == export['sha256']
    receipt = item.evidence.get('隔离收件', {}).get(str(index))
    if receipt:
        assert export['sha256'] == receipt['原件摘要']
    archive = local.ROOT / '.local/r6-evidence' / f'园区试点第{index}期周报.html'
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(content)


def reports(item):
    for index, parts in enumerate((('20.00','14.00','4.00'),('20.00','15.00','5.00')), 1):
        when = item.base - timedelta(days=7 if index==1 else 0)
        period=when.strftime('%G-W%V')
        total = format(sum(map(Decimal,parts)), '.2f')
        text = '接单和回传接口联调完成，地下车库回单待补传。' if index==1 else '补传后逐单核对三张离线回单，维修班组与服务台共同签收。'
        fields = {'milestone_progress':[{'summary':text}],'effort_person_days':total,
            'effort_breakdown':[{'category':c,'personDays':v,'basis':b} for c,v,b in zip(CATEGORIES,parts,BASIS,strict=True)],
            'requirement_progress':[{'summary':'已逐单核对接单、回传及签收要求。'}],
            'risks_and_blockers':[{'summary':'地下车库网络不稳定，回单通过本地留存与恢复后补传处理。'}]}
        label=f'第{index}期周报'
        row=item.write(label,'pm',item.path+'/reports',{'reportType':'weekly','period':period,'structuredFields':fields,
            'narrative':f'{text}本期工时为{total}人天，按签到记录独立核对。'})
        path='reports/'+row['reportId']
        final=item.write(label+'发布','pm',path+'/confirm',revision=row['revision'])
        for category, amount in zip(CATEGORIES,parts,strict=True):
            posting=item.write(label+category+'申报','pm',path+'/effort-posting',{'category':category,'period':when.strftime('%Y-%m'),'expectedAmount':amount},final['revision'])
            item.write(label+category+'核验','finance','cost-postings/'+posting['postingId']+'/verify',
                {'occurredAt':when.isoformat(),'reason':'财务独立比对岗位签到和本期交接清单。'})
        assert item.get('pm',path+'/effort-posting')['candidatePersonDays']=='0.00'
        export_weekly(item, index)
    daily_fields={k:v for k,v in fields.items() if k!='effort_breakdown'} | {'effort_person_days':'3.00'}
    item.write('当班日报','pm',item.path+'/reports',{'reportType':'daily','period':item.base.date().isoformat(),'structuredFields':daily_fields,
        'narrative':'三位维修人员各用一人天完成弱网回单抽查，班组长留存核查清单。'})


def execution(item):
    for kind, amount, source in [('procurement','39000.00','采购终端签收单'),('travel','9000.00','现场差旅明细'),
                                 ('cloud','20000.00','试点云服务结算单'),('allocation','10000.00','园区内部费用分摊单')]:
        posting=item.write('登记'+kind,'pm',item.path+'/cost-postings/categorized',{'type':'money','category':kind,'amount':amount,
            'period':item.base.strftime('%Y-%m'),'sourceRef':source})
        item.write('核验'+kind,'finance','cost-postings/'+posting['postingId']+'/verify',{'reason':'财务逐行核对试点结算资料和发生时点。','occurredAt':item.base.isoformat()})
    m=item.steps['milestone-2']['data']
    change=item.write('现场联调窗口顺延','pm',item.path+'/changes',dict(targetType='milestone',targetId=m['milestoneId'],
        before={'plannedDate':m['plannedDate']},after={'plannedDate':(item.base.date()+timedelta(days=15)).isoformat()},reason='物业腾空地下车库的检修窗口延后一天。'),m['revision'])
    item.write('独立审批联调窗口','sponsor','changes/'+change['changeId']+'/approve',{'rationale':'核对物业停工通知和班组排班，批准调整一天。'})
    for m in item.get('pm',item.path+'/milestones')['items']:
        mid=m['milestoneId']; proof=evidence(item,'里程碑'+mid,'milestone',mid,m['name']+'：班组现场核对并签收。')
        item.write('完成'+mid,'pm','milestones/'+mid+'/complete',{'actualDate':item.base.date().isoformat(),'evidenceRef':proof,'reason':'对照验收点逐项核查园区试点交付物。'},m['revision'])
    for r in item.get('pm',item.path+'/requirements',params={'pageSize':100})['items']:
        rid=r['requirementId'];proof=evidence(item,'需求'+rid,'requirement',rid,r['desc']+'：维修班组核对场景通过。')
        for action,role in [('deliver','pm'),('accept','pm'),('customer-confirm','customer')]:
            r=item.write(rid+action,role,'requirements/'+rid+'/'+action,{'evidenceRef':proof},r['revision'])
    condition=item.steps['feasibility-decision']['data']['tasks'][0]
    item.write('完成离线回单抽查','pm','tasks/'+condition['taskId'],{'fieldsTask':{'status':'done'}},condition['revision'],method='PATCH')
    risk=item.steps['risk']['data'];rid=risk['riskId']
    proof=evidence(item,'弱网风险签收','risk',rid,'三张离线回单均补传成功，班组逐笔查收。')
    item.write('弱网风险关闭','pm','risks/'+rid+'/close',{'resolutionEvidenceRef':proof},expected=403,code='INDEPENDENT_REVIEWER_REQUIRED')
    item.write('独立复核关闭风险','review1','risks/'+rid+'/close',{'resolutionEvidenceRef':proof})
    forecast_revision=item.steps['forecast-classified']['data']['revision']
    item.write('七项剩余预测核对','pm',item.path+'/cost-categories/forecast',{'amounts':dict.fromkeys(('procurement','travel','cloud','allocation',*CATEGORIES),'0.00'),'reason':'工单交付后逐项核对，已无剩余采购和工作量。'},forecast_revision,method='PUT')


def transition(item, stage):
    req=item.write('申请'+stage,'pm',item.path+'/transition-requests',{'toStage':stage,'reason':'已按园区试点验收清单核对当前阶段资料。'},item.current()['revision'])
    if stage!='archived':
        item.write('执行'+stage,'pm',item.path+'/transitions',{'toStage':stage,'approvalRequestId':req['requestId']},req['revision'])
    return req


def closing(item):
    transition(item,'delivering')
    if not item.done('独立双口径对账'):
        state=item.get('reconcile',item.path+'/cost-reconciliations')
        cutoff=item.evidence.setdefault('对账时点',datetime.now(UTC).isoformat());local.save(item.file,item.evidence)
        document={'schemaVersion':1,'projectId':item.pid,'coveredThrough':cutoff,'lines':[
            {'type':'money','sourceRef':'园区采购及费用结算总表','netActual':'78000.00'},
            {'type':'effort','sourceRef':'两期班组三岗位签到汇总','netActual':'78.00'}]}
        proof=evidence(item,'园区收尾总账','project',item.pid,'园区两期试点结算及工时对账单',document)
        item.write('独立双口径对账','reconcile',item.path+'/cost-reconciliations',{'costHash':state['costHash'],'evidenceRefs':[proof]},state['revision'])
    path=item.submit('结项送审','closing')
    if not item.done('结项通过'):
        item.decide('结项通过',path,'pass')
    calibration=item.path+'/review-calibration'
    refs=[row['data']['evidenceRef'] for key,row in item.steps.items() if key.endswith('-核验') and row.get('data',{}).get('evidenceRef')]
    for dimension in item.get('review1',calibration)['dimensions']:
        item.write('按签收原件反校'+dimension,'review1',calibration+'/results/'+dimension,{'status':'evaluated','score':'4.00',
            'reasoning':'对照立项冻结评分与交付签收，试点收益和风险控制达到预期。','evidenceRefs':[refs[0]]},method='PUT')
    transition(item,'retrospective')
    retro=item.path+'/retrospective'
    answers=['采购和费用合计七万八千元，两期合计七十八人天，均在批准预算内。',
        '地下车库检修窗口延后一天，发起正式变更并获独立审批。',
        '把离线回单的本地留存、重传和签收做成逐单清单。',
        '下个园区上线前按弱网场景组织现场演练，指定服务台逐单核查。']
    item.write('复盘四问','pm',retro,{'answers':answers},item.get('pm',retro)['revision'],method='PUT')
    lesson=item.write('起草交付教训','pm',item.path+'/lessons',{'category':'现场交付','what':answers[2],'rootCause':'地下车库断网时回单回传延迟。','advice':answers[3]})
    item.write('独立确认教训','review1','lessons/'+lesson['lessonId']+'/confirm',{},lesson['revision'])
    approval=transition(item,'archived')
    item.write('独立归档裁决','review1','approvals/'+approval['requestId']+'/decide',{'decision':'approve','rationale':'核对双口径对账、客户签收和已确认复盘教训，批准试点归档。'},approval['revision'])
    assert item.current()['stage']=='archived'


def exercise(item):
    if item.current()['stage'] == 'archived':
        for index in (1, 2):
            export_weekly(item, index)
        return
    reports(item)
    clarify(item, evidence)
    receive(item)
    execution(item)
    closing(item)


if __name__=='__main__':
    run(cases=(CASE,),after_planning=exercise)
