"""Focused endpoint tests on disposable SQLite, mocked providers.

These execute FastAPI handlers and SQLAlchemy persistence; they do NOT prove
PostgreSQL row-lock behavior or real Supabase/Paystack delivery.
"""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['SUPPLY_AI_SKIP_DB_STARTUP_CHECK'] = 'true'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://test:test@127.0.0.1:65432/cauldra_test'
os.environ['SUPPLY_AI_SECRET_KEY'] = 'isolated-test-secret-012345678901234567890123456789'
os.environ['SUPPLY_AI_CORS_ORIGINS'] = 'https://localhost,capacitor://localhost'
import unittest, secrets, base64, hashlib
from unittest.mock import patch
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
import main

class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        main.Base.metadata.create_all(self.engine,tables=[main.OnboardingEmailChallenge.__table__,main.OnboardingAuthorization.__table__,main.AuthFailure.__table__])
        self.Session=sessionmaker(bind=self.engine)
        def db():
            with self.Session() as session: yield session
        main.app.dependency_overrides[main.get_db]=db
        self.codes={};self.sends=[];self.tokens={};self.revoked=[]
        def provider(path,body,params=None):
            if path=='otp':
                self.sends.append((body,params));return {}
            expected=self.codes.get(body['auth_code'])
            actual=base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'].encode()).digest()).decode().rstrip('=')
            if not expected or expected[0]!=actual: raise HTTPException(400,'Invalid code')
            self.codes.pop(body['auth_code'])
            return {'user':{'email':expected[1],'email_confirmed_at':'2026-09-08T00:00:00Z'},'refresh_token':'never-return-to-browser'}
        def token_proof(token):
            # Stands in for Supabase's GET /auth/v1/user: validates the link session.
            if token not in self.tokens: raise HTTPException(400,'The verification link could not be processed. Please request a new email.')
            email,issued=self.tokens[token]
            return {'email':email,'email_confirmed_at':'2026-09-19T00:00:00Z'},issued
        self.patches=[patch.object(main,'_onboarding_provider_post',side_effect=provider),
            patch.object(main,'_onboarding_email_token_proof',side_effect=token_proof),
            patch.object(main,'_onboarding_revoke_link_session',side_effect=lambda t:self.revoked.append(t)),
            patch.object(main,'_supabase_email_confirmed',side_effect=AssertionError('Global confirmation must not be queried')),
            patch.object(main,'SUPPLY_AI_FRONTEND_URL','https://web.example.com'),
            patch.object(main,'SUPABASE_EMAIL_REDIRECT_URL','https://web.example.com'),
            patch.object(main,'ONBOARDING_EMAIL_CALLBACK_BASE_URL','https://api.example.com'),
            patch.object(main,'PAYSTACK_SECRET_KEY','test-provider-secret'),
            patch.object(main,'initialize_owned_checkout',side_effect=lambda db,req,row,email,meta:{'reference':row.paystack_reference})]
        for p in self.patches:p.start()
        self.client=TestClient(main.app)
    def tearDown(self):
        self.client.close();main.app.dependency_overrides.clear()
        for p in self.patches:p.stop()
        self.engine.dispose()
    def send(self,**extra):
        return self.client.post('/onboarding/email/verify',json={'email':'owner@example.com','plan':'starter','billing_interval':'monthly',**extra})
    def confirm(self,c,**extra):
        return self.client.post('/onboarding/email/verify/confirm',json={'challenge_id':c,**extra})
    def token(self,email='owner@example.com',issued=None):
        t='h.'+secrets.token_urlsafe(24)+'.s';self.tokens[t]=(email,(issued or datetime.utcnow()).replace(microsecond=0));return t
    def click(self,c,**kw):
        return self.confirm(c,email_token=self.token(**kw))
    def legacy_click(self,c):
        # A PKCE link emailed before CB-001: its code is bound to the challenge's verifier.
        v=main._onboarding_verifier(c);code=secrets.token_hex(20)
        self.codes[code]=(base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip('='),'owner@example.com')
        return self.confirm(c,code=code)
    def payment(self,c='',key=None,**extra):
        return self.client.post('/onboarding/payment/init',headers={'Idempotency-Key':key or secrets.token_hex(12)},json={'email':'owner@example.com','plan':'starter','billing_interval':'monthly','challenge_id':c,**extra})
    def test_previously_confirmed_still_sends_and_poll_is_pending(self):
        r=self.send();self.assertEqual(r.status_code,200);c=r.json()['challenge_id']
        self.assertEqual(r.json()['status'],'sent');self.assertEqual(len(self.sends),1)
        self.assertEqual(self.confirm(c,verified=True,access_token='old-session').json()['status'],'pending')
        self.assertEqual(self.payment(c).status_code,403)
        self.assertNotIn('code_verifier',r.text)
    def test_valid_proof_and_bound_one_time_payment(self):
        c=self.send().json()['challenge_id'];self.assertEqual(self.click(c).json()['status'],'verified')
        self.assertEqual(self.payment(c,email='other@example.com').status_code,403)
        self.assertEqual(self.payment(c,plan='core').status_code,403)
        self.assertEqual(self.payment(c,billing_interval='annual').status_code,403)
        key=secrets.token_hex(12);r=self.payment(c,key=key);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.payment(c,key=key).status_code,409)
        self.assertEqual(self.payment(c).status_code,403)
        self.assertEqual(self.confirm(c).status_code,409)
        with self.Session() as db:
            row=db.query(main.OnboardingAuthorization).one();proof=db.query(main.OnboardingEmailChallenge).one()
            self.assertEqual(row.email_challenge_id,c);self.assertEqual(row.email,proof.email)
            self.assertIsNotNone(proof.consumed_at)
    def test_wrong_challenge_code_cannot_verify_new_attempt(self):
        first=self.send().json()['challenge_id']
        with self.Session() as db:
            db.get(main.OnboardingEmailChallenge,first).created_at=datetime.utcnow()-timedelta(minutes=2);db.commit()
        old=self.token(issued=datetime.utcnow()-timedelta(seconds=90))  # a link opened before the second email existed
        second=self.send().json()['challenge_id']
        r=self.confirm(second,email_token=old);self.assertEqual(r.status_code,400);self.assertIn('earlier verification email',r.text)
        self.assertEqual(self.confirm(second).json()['status'],'pending')
        self.assertEqual(self.confirm(first,email_token=old).json()['status'],'verified')
        # a legacy PKCE code for one challenge cannot verify another
        v=main._onboarding_verifier(first);self.codes['old-code']=(base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip('='),'owner@example.com')
        self.assertEqual(self.confirm(second,code='old-code').status_code,400)
    def test_cb001_send_does_not_start_a_pkce_flow(self):
        self.send();body,params=self.sends[-1]
        self.assertNotIn('code_challenge',body);self.assertNotIn('code_challenge_method',body)
        self.assertEqual(body,{'email':'owner@example.com','create_user':True})
        self.assertIn('/auth/email-verified?',params['redirect_to'])
    def test_cb001_verifies_across_the_whole_window(self):
        # Before CB-001 Supabase's sign-up flow state died 300 s after the send.
        for minutes,expected in ((0,200),(3,200),(7,200),(19.5,200),(20.5,410)):
            c=self.send(email=f'owner{int(minutes*10)}@example.com').json()['challenge_id']
            with self.Session() as db:
                row=db.get(main.OnboardingEmailChallenge,c)
                row.created_at=datetime.utcnow()-timedelta(minutes=minutes);row.expires_at=row.created_at+timedelta(minutes=main.ONBOARDING_EMAIL_CHALLENGE_MINUTES);db.commit()
            r=self.click(c,email=f'owner{int(minutes*10)}@example.com')
            self.assertEqual(r.status_code,expected,(minutes,r.text))
            if expected==200: self.assertEqual(r.json()['status'],'verified')
    def test_cb001_token_proof_rules(self):
        c=self.send().json()['challenge_id']
        self.assertEqual(self.click(c,email='someone-else@example.com').status_code,403)
        self.assertEqual(self.confirm(c).json()['status'],'pending')
        for bad in ('not-a-token','a.b','a.b.c.d','a.b.c<script>','x'*5000):
            self.assertEqual(self.confirm(c,email_token=bad).status_code,400,bad[:20])
        self.assertEqual(self.confirm(c,email_token='h.unknown.s').status_code,400)
        self.assertEqual(self.confirm(c).json()['status'],'pending')
        ok=self.click(c);self.assertEqual(ok.json()['status'],'verified')
        again=self.confirm(c,email_token='h.unknown.s')  # already verified: idempotent state, no re-proof
        self.assertEqual(again.json()['status'],'verified')
        self.assertNotIn('email_token',ok.text);self.assertNotIn('access_token',ok.text)
    def test_cb001_small_clock_skew_tolerated_but_not_older_sessions(self):
        c=self.send().json()['challenge_id']
        self.assertEqual(self.click(c,issued=datetime.utcnow()-timedelta(seconds=45)).status_code,400)
        self.assertEqual(self.click(c,issued=datetime.utcnow()-timedelta(seconds=10)).json()['status'],'verified')
    def test_cb001_link_session_revoked_whatever_the_outcome(self):
        # Rejections must not leave a live Supabase session behind (found in QA Test D).
        c=self.send().json()['challenge_id']
        with self.Session() as db:
            row=db.get(main.OnboardingEmailChallenge,c);row.expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
        expired=self.token();self.assertEqual(self.confirm(c,email_token=expired).status_code,410)
        unknown=self.token();self.assertEqual(self.confirm('b'*64,email_token=unknown).status_code,403)
        malformed=self.token();self.assertEqual(self.confirm('bad',email_token=malformed).status_code,403)
        d=self.send(email='fresh@example.com').json()['challenge_id']
        wrong=self.token();self.assertEqual(self.confirm(d,email_token=wrong).status_code,403)
        ok=self.token(email='fresh@example.com');self.assertEqual(self.confirm(d,email_token=ok).json()['status'],'verified')
        again=self.token(email='fresh@example.com');self.assertEqual(self.confirm(d,email_token=again).json()['status'],'verified')
        self.assertEqual(self.revoked,[expired,unknown,malformed,wrong,ok,again])
        # a non-token value is never forwarded to Supabase; polls without a token revoke nothing
        self.revoked.clear()
        self.assertEqual(self.confirm(d,email_token='not-a-token').status_code,200)  # verified: state returned
        self.confirm(d);self.assertEqual(self.revoked,[])
    def test_cb001_legacy_pkce_link_still_verifies(self):
        c=self.send().json()['challenge_id']
        self.assertEqual(self.legacy_click(c).json()['status'],'verified')
    def test_expiry_tamper_boolean_and_missing_challenge(self):
        c=self.send().json()['challenge_id']
        self.assertEqual(self.confirm('tampered',verified=True).status_code,403)
        self.assertEqual(self.payment(verified=True).status_code,403)
        with self.Session() as db:
            db.get(main.OnboardingEmailChallenge,c).expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
        self.assertEqual(self.confirm(c).status_code,410)
        self.assertEqual(self.payment(c).status_code,410)
    def test_resend_cooldown_rotation_and_invalidation(self):
        c=self.send().json()['challenge_id'];self.assertEqual(self.send(challenge_id=c).status_code,429)
        with self.Session() as db:
            db.get(main.OnboardingEmailChallenge,c).created_at=datetime.utcnow()-timedelta(minutes=2);db.commit()
        fresh=self.send(challenge_id=c);self.assertEqual(fresh.status_code,200,fresh.text)
        self.assertNotEqual(c,fresh.json()['challenge_id']);self.assertEqual(self.confirm(c).status_code,410)
        d=fresh.json()['challenge_id'];self.assertEqual(self.client.post('/onboarding/email/invalidate',json={'challenge_id':d}).status_code,200)
        self.assertEqual(self.confirm(d).status_code,410)
    def test_platform_binding_and_purpose(self):
        c=self.send(platform='native_android').json()['challenge_id'];r=self.click(c).json()
        self.assertEqual(r['platform'],'native_android');self.assertEqual(r['return_target'],'cauldra://auth/email-verified')
        mismatch=self.confirm(c,platform='web',return_target='https://evil.example')
        self.assertEqual(mismatch.status_code,409)
        self.assertEqual(mismatch.json()['detail']['reason'],'platform_mismatch')
        self.assertEqual(self.confirm(c,platform='native_android').json()['status'],'verified')
        callback=self.client.get('/auth/email-verified?verified=true')
        self.assertEqual(callback.status_code,200);self.assertIn('text/html',callback.headers['content-type'])
        self.assertEqual(callback.headers['referrer-policy'],'no-referrer')
    def test_verified_attempt_reuse_only_exact_values(self):
        c=self.send().json()['challenge_id'];self.click(c)
        r=self.send(challenge_id=c);self.assertEqual(r.json()['status'],'verified');self.assertEqual(len(self.sends),1)

    def test_web_challenge_uses_only_configured_frontend_origin(self):
        row=self.send(platform='web').json()
        self.assertEqual(row['platform'],'web')
        self.assertEqual(row['return_target'],'https://web.example.com/')
        self.assertEqual(row['expected_return_origin'],'https://web.example.com')
        self.assertNotEqual(row['return_target'],'cauldra://auth/email-verified')
    def test_registration_rejects_old_unbound_payment(self):
        with self.Session() as db:
            db.add(main.OnboardingAuthorization(paystack_reference='legacy',email='owner@example.com',plan='starter',billing_interval='monthly',amount_kobo=5000,status='verified',verified_at=datetime.utcnow(),expires_at=datetime.utcnow()+timedelta(hours=1)));db.commit()
        r=self.client.post('/auth/register-business',json={'company_name':'Test','email':'owner@example.com','phone':'+2348031234567','firstname':'Test','lastname':'Owner','owner_email':'owner@example.com','owner_phone':'+2348031234567','username':'owner','password':'Test-password-123!','payment_reference':'legacy'})
        self.assertEqual(r.status_code,403,r.text)
    def test_registration_owner_must_match_bound_challenge(self):
        c=self.send().json()['challenge_id'];self.click(c);reference=self.payment(c).json()['reference']
        with self.Session() as db:
            row=db.query(main.OnboardingAuthorization).one();row.status='verified';row.verified_at=datetime.utcnow();db.commit()
        payload={'company_name':'Test','email':'owner@example.com','phone':'+2348031234567','firstname':'Test','lastname':'Owner','owner_email':'different@example.com','owner_phone':'+2348031234567','username':'owner','password':'Test-password-123!','payment_reference':reference}
        self.assertEqual(self.client.post('/auth/register-business',json=payload).status_code,400)
        with self.Session() as db:
            db.get(main.OnboardingEmailChallenge,c).email='changed@example.com';db.commit()
        self.assertEqual(self.client.post('/auth/register-business',json=payload).status_code,403)
    def test_native_cookie_policy_preserves_web_attributes(self):
        marker=main._native_cookie_request.set(True)
        try:
            r=Response();main.set_refresh_cookie(r,'test-only')
            self.assertIn('SameSite=none',r.headers['set-cookie']);self.assertIn('Secure',r.headers['set-cookie']);self.assertIn('HttpOnly',r.headers['set-cookie'])
        finally: main._native_cookie_request.reset(marker)
        r=Response();main.set_refresh_cookie(r,'test-only');self.assertIn('SameSite=lax',r.headers['set-cookie'])
        self.assertEqual(self.client.post('/auth/refresh',headers={'Origin':'https://untrusted.example'}).status_code,403)

class EmailTokenProofTests(unittest.TestCase):
    """The real Supabase token check behind CB-001, with HTTP mocked."""
    def setUp(self):
        import supabase_client, json as _json
        self.settings=type('S',(),{'url':'https://proj.supabase.co','secret_key':'service-test-key'})()
        self.p=patch.object(supabase_client.SupabaseSettings,'from_environment',return_value=self.settings);self.p.start()
        claims=base64.urlsafe_b64encode(_json.dumps({'iat':1789000000,'sub':'u'}).encode()).decode().rstrip('=')
        self.tok='hdr.'+claims+'.sig'
    def tearDown(self): self.p.stop()
    def resp(self,status,body=None):
        r=type('R',(),{})();r.status_code=status;r.json=lambda:(body if body is not None else {});return r
    def test_valid_token_reads_user_and_iat(self):
        user={'email':'Owner@Example.com','email_confirmed_at':'2026-09-19T00:00:00Z'}
        with patch('requests.get',return_value=self.resp(200,user)) as g:
            got,issued=main._onboarding_email_token_proof(self.tok)
        self.assertEqual(got,user);self.assertEqual(issued,datetime.utcfromtimestamp(1789000000))
        self.assertEqual(g.call_args.args[0],'https://proj.supabase.co/auth/v1/user')
        self.assertEqual(g.call_args.kwargs['headers']['Authorization'],'Bearer '+self.tok)
    def test_revoke_helper_calls_logout_with_the_link_token(self):
        with patch('requests.post',return_value=self.resp(204)) as post:
            main._onboarding_revoke_link_session(self.tok)
        self.assertEqual(post.call_args.args[0],'https://proj.supabase.co/auth/v1/logout')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'],'Bearer '+self.tok)
        self.assertEqual(post.call_args.kwargs['params'],{'scope':'local'})
    def test_rejected_or_unreadable_tokens(self):
        for status,code in ((401,400),(403,400),(500,502),(503,502)):
            with patch('requests.get',return_value=self.resp(status)),patch('requests.post'):
                with self.assertRaises(HTTPException) as e: main._onboarding_email_token_proof(self.tok)
            self.assertEqual(e.exception.status_code,code,status)
        with patch('requests.get',return_value=self.resp(200,{'email':'a@b.c'})),patch('requests.post'):
            with self.assertRaises(HTTPException) as e: main._onboarding_email_token_proof('hdr.bm90LWpzb24.sig')
        self.assertEqual(e.exception.status_code,400)
    def test_logout_failure_is_swallowed(self):
        import requests
        with patch('requests.post',side_effect=requests.ConnectionError()):
            main._onboarding_revoke_link_session(self.tok)  # must not raise

if __name__=='__main__':unittest.main(verbosity=2)
