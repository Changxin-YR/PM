from datetime import UTC,datetime,timedelta
from hashlib import sha256
from secrets import token_urlsafe
from .request_context import correlation_id
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from . import schema_v1 as db
from .db import engine
from .session_schema import attempts,sessions
from .identity import Failure

hasher=PasswordHasher()
dummy=hasher.hash(token_urlsafe(32))

def digest(value): return sha256(value.encode()).hexdigest()

def throttle(c,key,limit,now):
    bucket=digest(key)
    c.execute(insert(attempts).values(bucket=bucket,started_at=now,attempt_count=0).on_conflict_do_nothing())
    r=c.execute(sa.select(attempts).where(attempts.c.bucket==bucket).with_for_update()).mappings().one()
    fresh=r.started_at<now-timedelta(minutes=10)
    if not fresh and r.attempt_count>=limit: return False
    c.execute(attempts.update().where(attempts.c.bucket==bucket).values(started_at=now if fresh else r.started_at,attempt_count=1 if fresh else r.attempt_count+1))
    return True

def login(username,password,ip):
    now=datetime.now(UTC)
    with engine().begin() as c:
        # Commit consumed attempts even when credentials are rejected.
        if not throttle(c,'ip:'+ip,100,now) or not throttle(c,'user:'+username,12,now): return None,429
        u,p,o=db.user_accounts,db.persons,db.organizations
        row=c.execute(sa.select(u,p.c.org_id).select_from(u.join(p,p.c.user_id==u.c.id).join(o,p.c.org_id==o.c.id)).where(u.c.username==username,u.c.is_active,p.c.is_active,o.c.status=='active',sa.or_(u.c.locked_until.is_(None),u.c.locked_until<=sa.func.now())).with_for_update(of=u)).mappings().one_or_none()
        try: valid=hasher.verify(row.password_hash if row else dummy,password)
        except (VerifyMismatchError,InvalidHashError,VerificationError): valid=False
        if not row or not valid:
            if row: c.execute(db.audit_logs.insert().values(action='auth_login',actor_type='human',actor_id=row.id,org_id=row.org_id,result='denied',error_code='UNAUTHORIZED',request_id=correlation_id()))
            return None,401
        token,csrf=token_urlsafe(40),token_urlsafe(32)
        c.execute(sessions.insert().values(token_hash=digest(token),csrf_hash=digest(csrf),account_id=row.id,expires_at=now+timedelta(hours=8)))
        c.execute(u.update().where(u.c.id==row.id).values(last_login_at=now))
        c.execute(db.audit_logs.insert().values(action='auth_login',actor_type='human',actor_id=row.id,org_id=row.org_id,result='success',request_id=correlation_id()))
        return (token,csrf),200

def signout(c,a,rid,cookie):
    if a.kind!='human': raise Failure(403,'FORBIDDEN')
    c.execute(sessions.update().where(sessions.c.token_hash==digest(cookie)).values(revoked_at=sa.func.now()))
    c.execute(db.audit_logs.insert().values(action='auth_logout',actor_type='human',actor_id=a.account,org_id=a.org,result='success',request_id=rid))
