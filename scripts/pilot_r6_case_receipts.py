"""园区演练的冲突澄清和隔离收件回执。"""
from datetime import timedelta
from hashlib import sha256
from time import monotonic, sleep
from uuid import UUID

import sqlalchemy as sa
from scripts import pilot_local as local
from pmo import policy_admin, weekly_delivery as delivery


def clarify(item, evidence):
    for i, (value, date) in enumerate((('两张',item.base-timedelta(days=2)),('三张',item.base-timedelta(days=1))),1):
        text=f'地下车库东侧的待补传回单为{value}。'
        row=item.write(f'补传盘点日报{i}','pm',item.path+'/reports',{'reportType':'daily','period':date.date().isoformat(),
            'structuredFields':{'milestone_progress':[{'summary':'核对物业维修回单清单。'}],'effort_person_days':'1.00',
            'requirement_progress':[{'summary':'抽查离线登记与补传。'}],'risks_and_blockers':[{'summary':text}]},'narrative':text})
        path='reports/'+row['reportId']; ref=f"report:{row['reportId']}@{row['revision']}"
        fact=item.write(f'补传盘点事实{i}','pm',path+'/facts/proposals',{'subject':'地下车库东侧首班离线回单','predicate':'交接时未同步数量',
            'typedValue':value,'quote':text,'asOf':(item.base-timedelta(days=2)).isoformat(),'sourceRef':ref},row['revision'])
        item.write(f'确认盘点日报{i}','pm',path+'/confirm',revision=row['revision'])
        proof=evidence(item,f'盘点签收原件{i}','report',row['reportId'],text)
        verified=item.write(f'凭签收原件核对事实{i}','review1','facts/'+fact['factId']+'/verify',{'trust':'high','evidenceRef':proof,
            'reason':'核对签收清单首行的地点、班次和数量，逐笔匹配原件。'},fact['revision'])
    assert verified['validationStatus']=='disputed'
    settled=item.write('凭原始签收单澄清数量','pm','facts/'+verified['factId']+'/clarify',{'reason':'早班漏登一张纸质回单，交接清册复点为三张。',
        'correctedSourceRef':proof},verified['revision'])
    assert settled['validationStatus']=='verified'


def receive(item):
    assert local.R6_PREVIEW and local.DB_PORT==55456 and item.code=='R6-PARK-001'
    sponsor=item.team['sponsor'].get('auth/me').raise_for_status().json()
    original={'项目':item.pid,'收件单位':'东湖园区物业服务台','渠道':'试点隔离收件箱','样例签署人':sponsor['personId'],'用途':'接收试点正式周报'}
    name='park-recipient-approval.json'; saved=local.read(name)
    if saved is None: local.save(name,original)
    elif saved!=original: raise ValueError('收件授权原件与当前项目不一致')
    digest=sha256(local.local_file(name).read_bytes()).hexdigest()
    with item.pool.begin() as c:
        policy_admin.protected_owner(c)
        policy_admin.authenticate_operator(c,UUID(sponsor['orgId']),UUID(sponsor['personId']),local.read('r6-logins.json')['m-sponsor'])
        # 此处登记隔离收件人。周报入队、投递和收件回执交给应用处理。
        a=delivery.authorizations
        authorization=c.scalar(sa.select(a.c.id).where(a.c.project_id==UUID(item.pid),a.c.approval_hash==digest))
        if authorization is None:
            authorization=c.scalar(a.insert().values(project_id=UUID(item.pid),recipient_key='east-lake-service',display_name=original['收件单位'],
                channel='synthetic_inbox',approved_by=UUID(sponsor['personId']),approval_hash=digest,expires_at=item.base+timedelta(days=30)).returning(a.c.id))
    for n in (1,2):
        row=item.steps[f'第{n}期周报发布']['data'];path='reports/'+row['reportId'];file=item.get('pm',path+'/weekly-export')
        delivered=item.write(f'第{n}期正式周报送达','pm',path+'/delivery',{'authorizationId':str(authorization),'reportHash':row['contentHash'],
            'attachmentHash':file['sha256']},row['revision'])
        deadline=monotonic()+45
        while True:
            state=next(d for d in item.get('pm',path+'/delivery')['deliveries'] if d['deliveryId']==delivered['deliveryId'])
            if state['status']!='pending' or monotonic()>=deadline: break
            sleep(.5)
        if state['status']!='delivered':raise RuntimeError(f"第{n}期隔离收件 {state['status']} {state.get('errorCode')}")
        assert state['attachmentHash']==file['sha256'] and state['externalReceiptVerified'] is False
        item.evidence.setdefault('隔离收件',{})[str(n)]={'回执':state['deliveryId'],'时间':state['channelReceivedAt'],'原件摘要':state['attachmentHash']}
        local.save(item.file,item.evidence)
