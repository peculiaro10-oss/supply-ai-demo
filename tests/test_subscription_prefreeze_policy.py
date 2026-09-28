"""Pre-freeze subscription policy (owner decisions, 2026-09-28).

1. No early manual renewal: Pay Now is refused while the current paid period
   is active; the first automatic attempt starts at the exact paid-through
   time; while Paystack is confirming nothing is unlocked or started; a late
   renewal starts at Paystack's own confirmation time (SUB-RENEW-TS-001), an
   on-time one at the previous paid-through; whole-second audit times.
2. Subscription emails are plain transactional account notices in the
   reader's launch language (Arabic right-to-left): no links, buttons, images
   or marketing copy; a plain-text part; automatic-notice headers.
3. Email volume: 24 h and 5 h before only; the first failed automatic charge
   (with a safe Paystack reason); intermediate retry failures in-app only; one
   final email when the 3-day window ends; one confirmation per renewal.

Paystack and email are simulated (the fixture of test_subscription_renewal_engine).
"""
import json
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import test_subscription_renewal_engine as engine_tests
from test_subscription_renewal_engine import DAY, HOUR, MIN, main

ENGLISH_SENTENCES = re.compile(r"automatic account notice|renewal failed|Paystack confirmed the payment|is paid through|"
                               r"will charge your saved card|business data|Pay Now|No reply is needed")
MARKETING = re.compile(r"\b(offer|discount|% off|upgrade now|limited time|deal|promo|subscribe now|unsubscribe|newsletter)\b", re.I)


class PreFreezePolicyTests(unittest.TestCase):
    setUp = engine_tests.RenewalEngineTests.setUp
    tearDown = engine_tests.RenewalEngineTests.tearDown
    business = engine_tests.RenewalEngineTests.business
    auth = engine_tests.RenewalEngineTests.auth
    sub = engine_tests.RenewalEngineTests.sub
    run_engine = engine_tests.RenewalEngineTests.run_engine
    autos = engine_tests.RenewalEngineTests.autos
    audits = engine_tests.RenewalEngineTests.audits
    pay_now = engine_tests.RenewalEngineTests.pay_now
    reminders = engine_tests.RenewalEngineTests.reminders

    def biz(self, biz):
        self.db.expire_all()
        return self.db.get(main.BusinessProfile, biz.id)

    def lifecycle_pass(self, biz, now):
        self.db.expire_all()
        main.subscription_lifecycle_pass(self.db, self.biz(biz), now)

    def emails_by_title(self, title):
        return [e for e in self.emails if e['subject'].startswith(title + ' – ')]

    def usage(self, user):
        r = self.client.get('/subscription/usage', headers=self.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def stamp(self, reference, when):
        self.ps.tx[reference]['paid_at'] = when.isoformat(timespec='milliseconds') + 'Z'

    # --- 1. PAYMENT POLICY -----------------------------------------------------
    def test_no_pay_now_while_the_paid_period_is_active_including_the_reminder_period(self):
        for offset in (10 * DAY, 23 * HOUR, 4 * HOUR, 2 * MIN):         # 7d .. 1h reminders and just before expiry
            biz, anchor, admins, staff = self.business(anchor_offset=offset)
            self.assertFalse(self.usage(admins[0])['pay_now_available'])
            r = self.pay_now(admins[0])
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()['detail']['code'], 'RENEWAL_NOT_DUE')
            self.assertIn('Pay Now becomes available at that time', r.json()['detail']['message'])
            self.assertEqual(self.pay_now(staff).status_code, 403, 'Staff still cannot pay at all')
        self.assertEqual(self.ps.inits, [], 'no Paystack checkout was ever started')
        self.assertGreaterEqual(self.audits(biz, 'SUBSCRIPTION_EARLY_RENEWAL_REFUSED'), 1)
        # Without a saved card the rule is the same.
        biz2, _, admins2, _ = self.business(anchor_offset=2 * DAY)
        sub2 = self.sub(biz2); sub2.paystack_authorization_code = None; sub2.card_verified = False; self.db.commit()
        self.assertEqual(self.pay_now(admins2[0]).json()['detail']['code'], 'RENEWAL_NOT_DUE')

    def test_pay_now_opens_at_expiry_and_after_a_failed_automatic_attempt(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        self.assertTrue(self.usage(admins[0])['pay_now_available'])
        self.ps.charge_outcome = 'failed'
        self.run_engine(biz, datetime.utcnow())
        r = self.pay_now(admins[0])
        self.assertEqual(r.status_code, 200, r.text)
        again = self.pay_now(admins[0])
        self.assertEqual(again.json()['reference'], r.json()['reference'], 'duplicate protection unchanged: one checkout')

    def test_the_first_automatic_attempt_starts_at_the_exact_paid_through_time(self):
        now = datetime.utcnow().replace(microsecond=0)
        biz, anchor, _, _ = self.business(anchor_offset=90 * MIN)
        later, _, _, _ = self.business(anchor_offset=5 * DAY)
        self.assertEqual(main.next_lifecycle_boundary(self.db, now), anchor, 'the loop knows the next paid-through time')
        wait = main.lifecycle_sleep_seconds(anchor, anchor - timedelta(seconds=20), 300)
        self.assertAlmostEqual(wait, 20.5, delta=0.01, msg='it wakes half a second after the boundary')
        self.assertEqual(main.lifecycle_sleep_seconds(anchor, now, 300), main.LIFECYCLE_BOUNDARY_CHECK_SECONDS,
                         'and re-checks at least once a minute for boundaries created meanwhile')
        self.assertEqual([b.id for b in main.businesses_at_lifecycle_boundary(self.db, anchor - timedelta(seconds=30), anchor + timedelta(seconds=1))],
                         [biz.id], 'only the business whose boundary just passed is processed')
        self.lifecycle_pass(biz, anchor + timedelta(seconds=1))
        attempts = self.autos(biz)
        self.assertEqual([(a.renewal_attempt_slot, a.renewal_period_end) for a in attempts], [(0, anchor)])
        self.assertEqual(len(self.ps.charges), 1)
        self.assertEqual(self.autos(later), [], 'a business not yet due is untouched')

    def test_an_unpaid_upgrade_checkout_never_holds_up_the_first_automatic_attempt(self):
        # Found in live QA: an Admin opened an upgrade checkout and closed it.
        # Paystack reports that checkout "abandoned", which the upgrade
        # reconciliation treats as still open, so every engine pass waited on it
        # and the automatic renewal never started. Upgrades cannot start at or
        # after the paid-through time, so at that time the checkout is closed.
        biz, anchor, admins, _ = self.business(anchor_offset=10 * MIN)
        ref = f'cauldra_upgrade_{biz.business_code}_abandoned'
        created = anchor - 3 * MIN
        quote = main.SubscriptionUpgradeQuote(
            quote_reference=f'q-{biz.id}', business_id=biz.id, from_plan='starter', from_interval='monthly', to_plan='business',
            to_interval='annual', current_price_kobo=2000000, new_price_kobo=50000000, unused_credit_kobo=1000, amount_due_kobo=49999000,
            current_period_end_snapshot=anchor, status='issued', paystack_reference=ref, expires_at=created + 15 * MIN, created_at=created)
        self.db.add(quote)
        self.db.add(main.PaymentRecord(business_id=biz.id, subscription_id=self.sub(biz).id, plan='business', billing_interval='annual',
                                       amount_kobo=49999000, currency='NGN', paystack_reference=ref, status='initialized',
                                       purpose='subscription_upgrade', created_at=created,
                                       transaction_metadata=json.dumps({'purpose': 'subscription_upgrade', 'quote_reference': f'q-{biz.id}'})))
        self.db.commit()
        self.ps._tx(ref, 'abandoned', 49999000, admins[0].email)
        self.assertEqual(self.run_engine(biz, anchor - MIN), 'waiting', 'before the paid-through time the checkout stays open')
        self.lifecycle_pass(biz, anchor + timedelta(seconds=1))
        self.assertEqual([(a.renewal_attempt_slot, a.status) for a in self.autos(biz)], [(0, 'success')],
                         'the first automatic attempt ran at the paid-through time')
        self.db.expire_all()
        upgrade = self.db.query(main.PaymentRecord).filter_by(paystack_reference=ref).one()
        self.assertEqual(upgrade.status, 'failed')
        self.assertEqual(self.db.query(main.SubscriptionUpgradeQuote).filter_by(quote_reference=f'q-{biz.id}').one().status, 'expired',
                         'a late payment on that checkout can never change the plan')
        self.assertEqual(self.audits(biz, 'SUBSCRIPTION_UPGRADE_CHECKOUT_CLOSED'), 1)
        self.assertEqual(self.sub(biz).plan, 'starter')
        self.assertEqual(self.emails_by_title('Automatic renewal failed'), [], 'a closed upgrade checkout is not a renewal failure')
        # A checkout still being paid for the current, unchanged period stays open.
        biz2, anchor2, admins2, _ = self.business(anchor_offset=2 * HOUR)
        ref2 = f'cauldra_upgrade_{biz2.business_code}_open'
        self.db.add(main.SubscriptionUpgradeQuote(
            quote_reference=f'q-{biz2.id}', business_id=biz2.id, from_plan='starter', from_interval='monthly', to_plan='business',
            to_interval='annual', current_price_kobo=2000000, new_price_kobo=50000000, unused_credit_kobo=1000, amount_due_kobo=49999000,
            current_period_end_snapshot=anchor2, status='issued', paystack_reference=ref2, expires_at=anchor2, created_at=anchor2 - HOUR))
        self.db.add(main.PaymentRecord(business_id=biz2.id, subscription_id=self.sub(biz2).id, plan='business', billing_interval='annual',
                                       amount_kobo=49999000, currency='NGN', paystack_reference=ref2, status='initialized',
                                       purpose='subscription_upgrade', created_at=anchor2 - HOUR,
                                       transaction_metadata=json.dumps({'purpose': 'subscription_upgrade', 'quote_reference': f'q-{biz2.id}'})))
        self.db.commit()
        self.ps._tx(ref2, 'abandoned', 49999000, admins2[0].email)
        self.assertEqual(self.run_engine(biz2, anchor2 - 30 * MIN), 'pending')
        self.db.expire_all()
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(paystack_reference=ref2).one().status, 'initialized')

    def test_while_paystack_is_confirming_nothing_is_unlocked_paid_or_started(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        self.ps.charge_outcome = 'pending'
        self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'pending')
        sub = self.sub(biz)
        self.assertEqual(sub.current_period_end, anchor, 'no new period')
        self.assertNotEqual(sub.payment_status, 'paid')
        self.assertIsNotNone(main.subscription_access_state(sub, datetime.utcnow())[1], 'still paused')
        self.assertEqual(self.client.get('/products/', headers=self.auth(admins[0])).status_code, 402)
        usage = self.usage(admins[0])
        self.assertTrue(usage['renewal_in_progress'], 'Billing shows "Renewal being confirmed"')
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(business_id=biz.id, status='success').count(), 0)
        # Paystack confirms: access is restored at once, one period, one success.
        ref = self.autos(biz)[0].paystack_reference
        self.ps.pay(ref)
        confirmed = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=5)
        self.stamp(ref, confirmed)
        r = self.client.post('/subscription/checkout/confirm', json={'reference': ref}, headers=self.auth(admins[0]))
        self.assertEqual(r.json()['status'], 'success', r.text)
        self.assertEqual(self.client.get('/products/', headers=self.auth(admins[0])).status_code, 200, 'unlocked immediately')
        sub = self.sub(biz)
        self.assertEqual(sub.current_period_start, anchor, 'an on-time (slot 0) renewal confirmed later keeps the paid-through anchor')
        self.assertEqual(sub.paid_at, confirmed)

    def test_a_late_automatic_renewal_starts_at_paystacks_confirmation_time_not_the_pass_start(self):
        biz, anchor, _, _ = self.business(anchor_offset=-13 * HOUR)
        pass_started = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=20)
        confirmed = pass_started + timedelta(seconds=16, milliseconds=400)
        original = self.ps.request

        def charge(method, path, body=None, timeout=15):
            out = original(method, path, body, timeout)
            self.stamp(body['reference'], confirmed)
            return out
        with patch.object(main, 'paystack_request', side_effect=charge):
            self.assertEqual(self.run_engine(biz, pass_started), 'charged')
        sub = self.sub(biz)
        exact = confirmed.replace(microsecond=0)
        self.assertEqual((sub.current_period_start, sub.paid_at), (exact, exact), 'Paystack confirmation time, whole seconds')
        self.assertEqual(sub.current_period_end, main.add_billing_interval(exact, 'monthly'))
        self.assertEqual((sub.paused_from, sub.resumed_at), (anchor, exact))
        record = self.autos(biz)[0]
        self.assertEqual((record.renewal_attempt_slot, record.paid_at), (1, exact))
        log = self.db.query(main.AuditLog).filter_by(business_id=biz.id, action='SUBSCRIPTION_RESTORED').one().description
        self.assertIn('period starts at confirmation', log)
        self.assertNotRegex(log, r'\d{2}:\d{2}:\d{2}\.\d', 'no microseconds in the audit text')

    def test_a_confirmation_time_can_never_be_in_the_future_or_shorten_an_on_time_period(self):
        now = datetime.utcnow().replace(microsecond=0)
        future = {'paid_at': (now + DAY).isoformat() + 'Z'}
        self.assertLessEqual(main.provider_confirmed_at(future, now), datetime.utcnow())
        self.assertEqual(main.provider_confirmed_at({}, now), now)
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        self.run_engine(biz, datetime.utcnow())
        self.assertEqual(self.sub(biz).current_period_start, anchor)

    def test_a_late_pay_now_starts_at_paystacks_confirmation_time(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-4 * DAY)
        ref = self.pay_now(admins[0]).json()['reference']
        self.ps.pay(ref)
        self.ps.tx[ref]['metadata'] = {'business_id': biz.id, 'plan': 'starter', 'billing_interval': 'monthly', 'purpose': 'subscription'}
        self.ps.tx[ref]['customer']['email'] = biz.email.casefold()
        confirmed = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=40)
        self.stamp(ref, confirmed)
        r = self.client.post('/subscription/checkout/confirm', json={'reference': ref}, headers=self.auth(admins[0]))
        self.assertEqual(r.status_code, 200, r.text)
        sub = self.sub(biz)
        self.assertEqual((sub.current_period_start, sub.paid_at), (confirmed, confirmed))
        log = self.db.query(main.AuditLog).filter_by(business_id=biz.id, action='SUBSCRIPTION_RESTORED').one().description
        self.assertNotRegex(log, r'\d{2}:\d{2}:\d{2}\.\d')

    def test_a_failed_payment_leaves_the_business_paused_with_no_new_period(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        original = self.ps.request

        def declined(method, path, body=None, timeout=15):
            out = original(method, path, body, timeout)
            out['data']['gateway_response'] = 'Insufficient Funds'
            return out
        self.ps.charge_outcome = 'failed'
        with patch.object(main, 'paystack_request', side_effect=declined):
            self.assertEqual(self.run_engine(biz, datetime.utcnow()), 'failed')
        sub = self.sub(biz)
        self.assertEqual((sub.current_period_end, sub.payment_status), (anchor, 'failed'))
        self.assertIsNotNone(main.subscription_access_state(sub, datetime.utcnow())[1])
        self.assertEqual(self.client.get('/products/', headers=self.auth(admins[0])).status_code, 402)
        self.assertEqual(json.loads(self.autos(biz)[0].transaction_metadata)['failure_reason'], 'insufficient_funds')

    def test_duplicate_success_stays_impossible(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        self.run_engine(biz, datetime.utcnow())
        end = self.sub(biz).current_period_end
        record = self.autos(biz)[0]
        again = main.reconcile_renewal_payment(self.db, record, self.ps.verify(record.paystack_reference), datetime.utcnow())
        self.assertTrue(again['already_processed'])
        self.assertIsNone(self.run_engine(biz, datetime.utcnow()))
        self.assertEqual(self.sub(biz).current_period_end, end)
        self.assertEqual(self.db.query(main.PaymentRecord).filter_by(business_id=biz.id, status='success').count(), 1)
        self.assertEqual(self.pay_now(admins[0]).json()['detail']['code'], 'RENEWAL_NOT_DUE', 'renewed period: no second payment')

    # --- 3. EMAIL SCHEDULE -------------------------------------------------------
    def test_pre_expiry_email_only_at_24h_and_5h(self):
        biz, anchor, _, _ = self.business(anchor_offset=8 * DAY)
        for before in (7 * DAY, 3 * DAY, 24 * HOUR, 5 * HOUR, HOUR):
            for _ in range(2):
                self.lifecycle_pass(biz, anchor - before + MIN)
        self.assertEqual([r.stage for r in self.reminders(biz, 'in_app')], ['7d', '3d', '24h', '5h', '1h'])
        self.assertEqual([r.stage for r in self.reminders(biz, 'email')], ['24h', '5h'])
        self.assertEqual([e['subject'].split(' – ')[0] for e in self.emails], ['Subscription renews soon'] * 2)
        self.assertIn('Cauldra will charge your saved card automatically at that time.', self.emails[0]['text'])

    def test_first_failed_charge_emails_once_retries_are_in_app_and_the_window_end_emails_once(self):
        biz, anchor, _, staff = self.business(anchor_offset=-MIN, admin_emails=['Boss@Example.com', 'boss@example.com'])
        original = self.ps.request

        def declined(method, path, body=None, timeout=15):
            out = original(method, path, body, timeout)
            out['data']['gateway_response'] = 'Declined'
            return out
        self.ps.charge_outcome = 'failed'
        with patch.object(main, 'paystack_request', side_effect=declined):
            for hours in range(0, 76, 2):
                self.lifecycle_pass(biz, anchor + hours * HOUR + MIN)
                self.lifecycle_pass(biz, anchor + hours * HOUR + 2 * MIN)     # a second pass never repeats anything
        self.assertEqual(len(self.ps.charges), 6)
        failed = self.emails_by_title('Automatic renewal failed')
        self.assertEqual(len(failed), 1, 'one email for the first failed charge')
        self.assertEqual(failed[0]['to_email'], 'boss@example.com', 'recipients de-duplicated case-insensitively')
        self.assertIn('The card issuer declined the payment.', failed[0]['text'])
        self.assertIn('every 12 hours until', failed[0]['text'])
        self.assertEqual(len(self.emails_by_title('Automatic renewal attempts have ended')), 1, 'one final email')
        self.assertEqual(len(self.emails), 2, 'no email for intermediate retries, none at the pause')
        in_app = sorted({r.stage for r in self.reminders(biz, 'in_app')})
        self.assertEqual(in_app, sorted({'paused', 'renewal_failed', 'retry_1', 'retry_2', 'retry_3', 'retry_4', 'paused_24h', 'window_end'}))
        self.assertNotIn(staff.email, [e['to_email'] for e in self.emails])
        staff_notes = self.db.query(main.Notification).filter_by(business_id=biz.id, recipient_user_id=staff.id).count()
        self.assertEqual(staff_notes, 0, 'Staff never receive billing notices')

    def test_a_failure_reason_is_shown_only_when_paystack_gives_a_safe_one(self):
        self.assertEqual(main.renewal_failure_reason('Insufficient Funds'), 'insufficient_funds')
        self.assertEqual(main.renewal_failure_reason('Do Not Honor'), 'declined')
        self.assertEqual(main.renewal_failure_reason('Expired Card'), 'expired_card')
        self.assertIsNone(main.renewal_failure_reason('Card reported stolen, pick up'))
        self.assertIsNone(main.renewal_failure_reason(''))
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        self.ps.charge_outcome = 'failed'                                  # no gateway_response at all
        self.lifecycle_pass(biz, datetime.utcnow())
        text = self.emails_by_title('Automatic renewal failed')[0]['text']
        for sentence in main.RENEWAL_FAILURE_SENTENCES.values():
            self.assertNotIn(sentence, text, 'never an invented reason')

    def test_a_confirmed_renewal_sends_one_confirmation_email(self):
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        for _ in range(3):
            self.lifecycle_pass(biz, datetime.utcnow())
        renewed = self.emails_by_title('Subscription renewed')
        self.assertEqual(len(renewed), 1)
        self.assertIn('Paystack confirmed the payment of ₦', renewed[0]['text'])
        self.assertIn('Your subscription is now paid through', renewed[0]['text'])
        self.assertEqual(len(self.emails), 1, 'no pause or failure email for a renewal that succeeded at once')

    def test_email_failure_never_blocks_the_in_app_notice_and_is_retried_once(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        self.ps.charge_outcome = 'failed'
        self.email_failures = 1
        self.lifecycle_pass(biz, datetime.utcnow())
        self.assertEqual([r.stage for r in self.reminders(biz, 'in_app') if r.stage == 'renewal_failed'], ['renewal_failed'])
        row = [r for r in self.reminders(biz, 'email') if r.stage == 'renewal_failed'][0]
        self.assertEqual(row.status, 'failed')
        self.lifecycle_pass(biz, datetime.utcnow() + 5 * MIN)
        self.lifecycle_pass(biz, datetime.utcnow() + 10 * MIN)
        self.assertEqual(len(self.emails_by_title('Automatic renewal failed')), 1)

    def test_a_business_without_a_saved_card_gets_the_expiry_email_instead(self):
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        sub = self.sub(biz); sub.paystack_authorization_code = None; sub.card_verified = False; self.db.commit()
        self.lifecycle_pass(biz, datetime.utcnow())
        self.assertEqual([e['subject'].split(' – ')[0] for e in self.emails], ['Subscription paused'])

    # --- 2. EMAIL PRESENTATION ---------------------------------------------------
    def test_every_email_is_a_plain_transactional_notice_in_each_launch_language(self):
        biz, anchor, admins, _ = self.business(anchor_offset=-MIN)
        self.ps.charge_outcome = 'failed'
        self.run_engine(biz, datetime.utcnow())
        business, sub = self.biz(biz), self.sub(biz)
        for stage in ('24h', '5h', 'renewal_failed', 'window_end', 'renewed', 'paused'):
            for lang in main.SUBSCRIPTION_EMAIL_LANGUAGES:
                subject, html, text = main.build_subscription_email(self.db, sub, business, stage, anchor, lang)
                where = f'{stage}/{lang}'
                self.assertTrue(subject.endswith(' – ' + business.company_name), where)
                for banned in ('<a ', 'href', '<img', 'http', '<button', '<table', 'utm_', 'unsubscribe'):
                    self.assertNotIn(banned, html.lower(), where)
                self.assertNotRegex(html + text, MARKETING, where)
                self.assertIn(f'lang="{lang}"', html)
                self.assertIn('dir="rtl"' if lang == 'ar' else 'dir="ltr"', html, where)
                self.assertTrue(text.strip(), 'a plain-text part')
                if lang != 'en':
                    self.assertNotRegex(subject + text, ENGLISH_SENTENCES, where)
                if lang == 'ar':
                    self.assertRegex(text, r'[؀-ۿ]{3}', where)

    def test_every_subscription_sentence_is_in_the_launch_catalogue(self):
        catalog = main.launch_catalog()
        biz, anchor, _, _ = self.business(anchor_offset=-MIN)
        business = self.biz(biz)
        variants = []
        for card, paid, cancel, consent in [(True, True, False, True), (False, True, False, False), (True, False, False, True),
                                            (False, False, False, False), (True, True, True, True)]:
            sub = self.sub(biz)
            sub.card_verified = card; sub.paystack_authorization_code = 'AUTH_sim' if card else None
            sub.paid_at = (anchor - 30 * DAY) if paid else None; sub.cancel_at_period_end = cancel
            sub.trial_consent_at = (anchor - 60 * DAY) if consent else None
            for stage in ('7d', '3d', '24h', '5h', '1h', 'paused', 'paused_24h', 'window_end', 'renewal_failed', 'retry_1', 'retry_5', 'renewed'):
                variants.append(main.subscription_reminder_parts(sub, business, stage, anchor, self.db))
        variants.append(('Subscription renewed', [(s, {}) for s in main.RENEWAL_FAILURE_SENTENCES.values()]))
        templates = {title for title, _ in variants} | {t for _, parts in variants for t, _ in parts}
        templates |= {'Hello,', 'This is an automatic account notice for {business} on Cauldra.',
                      'You are receiving it because you are an Admin of this business. No reply is needed.',
                      'Paystack confirmed the payment of {amount} on {confirmed}.', 'Your subscription is now paid through {end}.'}
        missing = sorted(t for t in templates if len([x for x in catalog.get(t, []) if x]) != 4)
        self.assertEqual(missing, [])

    def test_the_email_is_sent_in_the_admins_language_with_automatic_notice_headers(self):
        biz, anchor, admins, _ = self.business(anchor_offset=4 * HOUR)
        admins[0].preferred_language = 'fr'; self.db.commit()
        self.lifecycle_pass(biz, datetime.utcnow())
        sent = self.emails[0]
        self.assertTrue(sent['subject'].startswith("L’abonnement se renouvelle bientôt – "))
        self.assertIn('Cauldra débitera automatiquement votre carte enregistrée', sent['text'])
        self.assertEqual(sent['headers']['Auto-Submitted'], 'auto-generated')
        self.assertEqual(sent['headers']['X-Entity-Ref-ID'], sent['idempotency_key'])
        self.assertIn('lang="fr"', sent['html'])


if __name__ == '__main__':
    unittest.main()
