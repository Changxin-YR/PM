"""One-time bootstrap for a fresh installation; no role assignment on projects."""
import argparse
from getpass import getpass
from uuid import uuid4
import sqlalchemy as sa
from .db import engine
from . import schema_v1 as db
from .sessions import hasher

def bootstrap(connection, organization, username, name, password):
    if not (0<len(organization)<=160 and 0<len(username)<=120 and 0<len(name)<=120 and 14<=len(password)<=256):
        raise ValueError('Invalid identity fields or password length (14-256 required)')
    connection.exec_driver_sql('LOCK TABLE organizations, user_accounts IN EXCLUSIVE MODE')
    for table in (db.organizations, db.user_accounts):
        if connection.scalar(sa.select(table.c.id).limit(1)):
            raise ValueError('Bootstrap requires an empty installation')
    org=connection.scalar(db.organizations.insert().values(name=organization,status='active').returning(db.organizations.c.id))
    user=connection.scalar(db.user_accounts.insert().values(username=username,password_hash=hasher.hash(password),is_active=True).returning(db.user_accounts.c.id))
    person=uuid4()
    connection.execute(db.persons.insert().values(id=person,user_id=user,name=name,created_by=person,org_id=org,is_active=True))
    connection.execute(db.user_roles.insert().values(user_id=user,role='system_admin'))
    connection.execute(db.audit_logs.insert().values(actor_type='human',actor_id=user,org_id=org,action='bootstrap',result='success',request_id=str(uuid4())))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for arg in ('organization','username','name'): parser.add_argument('--'+arg,required=True)
    args=parser.parse_args()
    password=getpass('Initial password (14+ characters): ')
    if password!=getpass('Confirm password: '): raise SystemExit('Password mismatch')
    with engine().begin() as c: bootstrap(c,args.organization,args.username,args.name,password)
    print('Initialized. No project governance roles were assigned.')

if __name__=='__main__': main()
