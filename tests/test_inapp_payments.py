"""Actual FastAPI routes + PostgreSQL, with an explicit fake Paystack boundary.
Requires an isolated DATABASE_URL. Never use production/payment credentials.
"""
import copy, hashlib, hmac, json, uuid
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
import main

@pytest.fixture
def ctx(monkeypatch):
    assert main.engine.url.host in ('127.0.0.1','localhost')
    assert main.PAYSTACK_SECRET_KEY == 'sk_test_isolated_mock_only'
    suffix=uuid.uuid4().hex[:10]
    db=main.SessionLocal()
    business=main.BusinessProfile(business_code='PAY-'+suffix,company_name='Payment Test',email=f'{suffix}@example.com')
    db.add(business);db.flush()
    sub=main.BusinessSubscription(business_id=business.id,plan='starter',billing_interval='monthly',status='pending_payment_method',card_verified=False)
    db.add(sub);db.flush()
    tokens={}
    for role in ('admin','manager','staff'):
        user=main.User(business_id=business.id,username=role+suffix,password=main.hash_password('Test-only-password-42!'),role=role,email=f'{role}{suffix}@example.com',phone='+2348000000000')
        db.add(user);db.flush();tokens[role]=main.issue_token(user,db)
    db.commit()
    provider={}; calls=[]
    def request(method,path,json_body=None,timeout=15):
        calls.append((method,path,copy.deepcopy(json_body)))
        if path=='/transaction/initialize':
            ref=json_body['reference'];provider[ref]={**copy.deepcopy(json_body),'id':int(uuid.uuid4().hex[:12],16),'status':'pending',
                'customer':{'email':json_body['email'],'customer_code':'CUS_'+suffix},
                'authorization':{'authorization_code':'AUTH_mock','reusable':True,'channel':'card','last4':'4242','brand':'visa','exp_month':'08','exp_year':'2029'}}
            return {'status':True,'data':{'reference':ref,'access_code':'test_access_'+suffix,'authorization_url':'https://checkout.paystack.com/test_'+suffix}}
        if path.startswith('/transaction/verify/'):
            return {'status':True,'data':copy.deepcopy(provider[path.rsplit('/',1)[-1]])}
        if path=='/refund':return {'status':True,'data':{'id':'refund-'+suffix,'status':'pending'}}
        if path.startswith('/refund/'):return {'status':True,'data':{'id':'refund-'+suffix,'status':'processed'}}
        if path=='/customer':return {'status':True,'data':{'customer_code':'CUS_'+suffix}}
        raise AssertionError('Unexpected provider path: '+path)
    monkeypatch.setattr(main,'paystack_request',request)
    client=TestClient(main.app)
    class Context: pass
    c=Context();c.db=db;c.business=business;c.sub=sub;c.provider=provider;c.calls=calls;c.client=client;c.tokens=tokens
    c.headers=lambda role='admin',key=None: {'Authorization':'Bearer '+tokens[role],'Idempotency-Key':key or uuid.uuid4().hex}
    c.init=lambda **values:client.post('/subscription/checkout',headers=c.headers(),json={'plan':'starter','billing_interval':'monthly',**values})
    c.confirm=lambda ref:client.post('/subscription/checkout/confirm',headers=c.headers(),json={'reference':ref})
    yield c
    db.close();client.close()

def test_initialize_authoritative_and_idempotent(ctx):
    key=uuid.uuid4().hex;headers=ctx.headers(key=key)
    first=ctx.client.post('/subscription/checkout',headers=headers,json={'plan':'starter','billing_interval':'annual','amount':1,'currency':'USD'})
    assert first.status_code==200,first.text
    retry=ctx.client.post('/subscription/checkout',headers=headers,json={'plan':'starter','billing_interval':'annual','amount':999})
    assert retry.status_code==200,retry.text
    assert retry.json()['reference']==first.json()['reference']
    assert len([c for c in ctx.calls if c[1]=='/transaction/initialize'])==1
    assert first.json()['amount_kobo']==main.PLAN_CONFIG['starter']['annual_price']*100
    assert first.json()['currency']=='NGN'
    assert 'sk_test' not in first.text and 'AUTH_' not in first.text
    assert ctx.sub.status=='pending_payment_method'

@pytest.mark.parametrize('role',['manager','staff'])
@pytest.mark.parametrize('route',['/subscription/checkout','/subscription/trial/init','/subscription/payment-method/init','/subscription/checkout/confirm','/subscription/payment-method/confirm'])
def test_rbac(ctx,role,route):
    response=ctx.client.post(route,headers=ctx.headers(role),json={'plan':'starter','billing_interval':'monthly','reference':'unknown'})
    assert response.status_code==403,response.text
    assert not ctx.calls

def test_unauthenticated(ctx):
    assert ctx.client.post('/subscription/checkout',json={'plan':'starter'}).status_code==401

@pytest.mark.parametrize('field,value',[('amount',1),('currency','USD'),('reference','wrong'),('id',None)])
def test_verification_mismatch(ctx,field,value):
    init=ctx.init();assert init.status_code==200,init.text
    ref=init.json()['reference'];ctx.provider[ref]['status']='success';ctx.provider[ref][field]=value
    response=ctx.confirm(ref);assert response.status_code==409,response.text
    ctx.db.refresh(ctx.sub);assert ctx.sub.status=='pending_payment_method'

def test_pending_not_success_and_duplicate_success(ctx):
    ref=ctx.init().json()['reference']
    response=ctx.confirm(ref);assert response.status_code==202,response.text
    ctx.db.refresh(ctx.sub);assert ctx.sub.status=='pending_payment_method'
    ctx.provider[ref]['status']='success'
    response=ctx.confirm(ref);assert response.status_code==200,response.text
    ctx.db.refresh(ctx.sub);end=ctx.sub.current_period_end
    assert ctx.sub.card_last4=='4242' and ctx.sub.card_exp_year=='2029'
    assert ctx.confirm(ref).json()['already_processed'] is True
    ctx.db.refresh(ctx.sub);assert ctx.sub.current_period_end==end
    assert ctx.db.query(main.AuditLog).filter_by(business_id=ctx.business.id,action='SUBSCRIPTION_ACTIVATED').count()==1

def test_cross_tenant_and_invalid_reference(ctx):
    assert ctx.confirm('not-owned').status_code==404
    row=main.PaymentRecord(business_id=ctx.business.id,subscription_id=ctx.sub.id,plan='starter',billing_interval='monthly',amount_kobo=100,currency='NGN',paystack_reference='other-'+uuid.uuid4().hex)
    ctx.db.add(row);ctx.db.commit()
    user=ctx.db.query(main.User).filter_by(business_id=ctx.business.id,role='admin').one()
    other=main.BusinessProfile(business_code=uuid.uuid4().hex,company_name='Other',email='other@example.com');ctx.db.add(other);ctx.db.flush()
    user.business_id=other.id;ctx.db.commit()
    token=main.issue_token(user,ctx.db)
    assert ctx.client.post('/subscription/checkout/confirm',headers={'Authorization':'Bearer '+token},json={'reference':row.paystack_reference}).status_code==404

def test_webhook_duplicate_and_signature(ctx):
    ref=ctx.init().json()['reference'];ctx.provider[ref]['status']='success'
    raw=json.dumps({'event':'charge.success','data':ctx.provider[ref]}).encode()
    assert ctx.client.post('/webhooks/paystack',content=raw).status_code==401
    sig=hmac.new(main.PAYSTACK_SECRET_KEY.encode(),raw,hashlib.sha512).hexdigest()
    headers={'x-paystack-signature':sig,'content-type':'application/json'}
    first=ctx.client.post('/webhooks/paystack',content=raw,headers=headers)
    assert first.status_code==200,first.text
    assert ctx.client.post('/webhooks/paystack',content=raw,headers=headers).json()['status']=='already_processed'
    assert ctx.confirm(ref).json()['already_processed'] is True

def test_change_method_preserves_subscription_and_refund(ctx):
    ctx.sub.status='active';ctx.sub.current_period_start=datetime.utcnow();ctx.sub.current_period_end=datetime.utcnow()+timedelta(days=30)
    ctx.sub.card_verified=True;ctx.sub.card_last4='1111';ctx.sub.paystack_authorization_code='AUTH_old';ctx.db.commit()
    end=ctx.sub.current_period_end
    response=ctx.client.post('/subscription/payment-method/init',headers=ctx.headers());assert response.status_code==200,response.text
    ref=response.json()['reference'];ctx.provider[ref]['status']='success'
    response=ctx.client.post('/subscription/payment-method/confirm',headers=ctx.headers(),json={'reference':ref})
    assert response.status_code==200,response.text
    assert response.json()['refund_status']=='pending'
    ctx.db.refresh(ctx.sub);assert ctx.sub.card_last4=='4242' and ctx.sub.current_period_end==end and ctx.sub.status=='active'
    again=ctx.client.post('/subscription/payment-method/confirm',headers=ctx.headers(),json={'reference':ref})
    assert again.json()['refund_status']=='succeeded'
    assert len([c for c in ctx.calls if c[:2]==('POST','/refund')])==1

def test_trial_pending_and_success(ctx):
    response=ctx.client.post('/subscription/trial/init',headers=ctx.headers(),json={'plan':'starter','billing_interval':'monthly'})
    assert response.status_code==200,response.text
    ref=response.json()['reference']
    assert ctx.client.post('/subscription/trial/confirm',headers=ctx.headers(),json={'reference':ref}).status_code==202
    ctx.provider[ref]['status']='success'
    response=ctx.client.post('/subscription/trial/confirm',headers=ctx.headers(),json={'reference':ref})
    assert response.status_code==200,response.text
    assert response.json()['status']=='trialing'
    ctx.db.refresh(ctx.sub);end=ctx.sub.trial_end_at
    again=ctx.client.post('/subscription/trial/confirm',headers=ctx.headers(),json={'reference':ref})
    ctx.db.refresh(ctx.sub);assert ctx.sub.trial_end_at==end

def test_onboarding(ctx,monkeypatch):
    monkeypatch.setattr(main,'_supabase_email_confirmed',lambda email:True)
    response=ctx.client.post('/onboarding/payment/init',headers={'Idempotency-Key':uuid.uuid4().hex},json={'email':ctx.business.email,'plan':'starter','billing_interval':'annual'})
    assert response.status_code==200,response.text
    ref=response.json()['reference'];ctx.provider[ref]['status']='success'
    response=ctx.client.post('/onboarding/payment/confirm',json={'reference':ref})
    assert response.status_code==200,response.text
    assert response.json()['status']=='verified'
    assert 'AUTH_' not in response.text

def test_headers_and_return(ctx):
    response=ctx.client.get('/')
    csp=response.headers['content-security-policy']
    assert 'https://js.paystack.co' in csp and 'frame-src https://checkout.paystack.com' in csp
    assert 'default-src *' not in csp and response.headers['x-frame-options']=='DENY'
    assert ctx.client.get('/payments/return?reference=%3Cscript%3E').status_code==400
    response=ctx.client.get('/payments/return?reference=cauldra_abc')
    assert response.headers['cache-control']=='no-store' and 'cauldra://payment-return' in response.text
