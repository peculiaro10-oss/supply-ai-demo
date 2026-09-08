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
        self.codes={};self.sends=[]
        def provider(path,body,params=None):
            if path=='otp':
                self.sends.append((body,params));return {}
            expected=self.codes.get(body['auth_code'])
            actual=base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'].encode()).digest()).decode().rstrip('=')
            if not expected or expected[0]!=actual: raise HTTPException(400,'Invalid code')
            self.codes.pop(body['auth_code'])
            return {'user':{'email':expected[1],'email_confirmed_at':'2026-09-08T00:00:00Z'},'refresh_token':'never-return-to-browser'}
        self.patches=[patch.object(main,'_onboarding_provider_post',side_effect=provider),
            patch.object(main,'_supabase_email_confirmed',side_effect=AssertionError('Global confirmation must not be queried')),
            patch.object(main,'SUPABASE_EMAIL_REDIRECT_URL','https://web.example.com'),
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
    def click(self,c):
        code=secrets.token_hex(20);self.codes[code]=(self.sends[-1][0]['code_challenge'],'owner@example.com')
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
        first=self.send().json()['challenge_id'];body=self.sends[-1][0]
        second=self.send().json()['challenge_id'];self.codes['old-code']=(body['code_challenge'],'owner@example.com')
        self.assertEqual(self.confirm(second,code='old-code').status_code,400)
        self.assertEqual(self.confirm(second).json()['status'],'pending')
        self.assertEqual(self.confirm(first,code='old-code').json()['status'],'verified')
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
        r=self.confirm(c,platform='web',return_target='https://evil.example').json()
        self.assertEqual(r['platform'],'native_android')
        callback=self.client.get('/auth/email-verified?verified=true')
        self.assertEqual(callback.status_code,200);self.assertIn('text/html',callback.headers['content-type'])
        self.assertEqual(callback.headers['referrer-policy'],'no-referrer')
    def test_verified_attempt_reuse_only_exact_values(self):
        c=self.send().json()['challenge_id'];self.click(c)
        r=self.send(challenge_id=c);self.assertEqual(r.json()['status'],'verified');self.assertEqual(len(self.sends),1)
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

if __name__=='__main__':unittest.main(verbosity=2)
