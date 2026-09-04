"""Cauldra Platform Owner account setup / recovery - interactive, server-side
only. There is no API endpoint for either of these and there never will be
(see main.py's "CAULDRA PLATFORM OWNER CONTROL PANEL" section): a Platform
Owner is internal, Cauldra-level access, not a business's Customer Admin, and
is never created through the public /auth/register-business flow or any
public sign-up page. The Platform Owner Control Panel's login screen has no
"Sign Up" / "Register" link at all - this script is the only way an account
comes into existence, and it must be run directly on the server with the real
.env already in place (the same trust level required to read the production
database).

USAGE (from the project root):

    Create the FIRST (or an additional) Platform Owner account:
        venv/Scripts/python.exe scripts/create_platform_owner.py

    Recover an existing account whose password or MFA device was lost:
        venv/Scripts/python.exe scripts/create_platform_owner.py --recover

See platform/PLATFORM_OWNER_SETUP.md for the full first-time setup procedure
(which email becomes the login, how the password is set, how to verify it,
how to enroll MFA, the private login URL, and how recovery works).

MFA is mandatory: this script always provisions a TOTP secret and enables it
before the account can be used - there is no password-only Platform Owner
login (see get_platform_owner() / platform_login() in main.py).
"""
import sys
import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402


def _verify_email_via_supabase(email: str):
    """Real, backend-verified email ownership - reuses the EXACT same trusted
    Supabase Auth integration and _supabase_email_confirmed() check the
    existing customer email-verification/email-change flows use (see
    main.py). Returns True (verified), False (declined/failed), or None
    (Supabase is not configured on this server at all, so this step is
    structurally impossible here - the caller decides how to proceed)."""
    try:
        client = main.get_supabase_client(required=False)
    except Exception:
        client = None
    if client is None:
        return None

    already = main._supabase_email_confirmed(email)
    if already is True:
        print(f"Supabase already shows {email} as a confirmed address.")
        return True

    try:
        client.auth.sign_in_with_otp({"email": email, "options": {"should_create_user": True}})
    except Exception as exc:
        print(f"Could not send a verification email via Supabase: {exc}")
        return False

    print(f"\nA verification link has been sent to {email}.")
    print("Open it, then come back here.")
    while True:
        ans = input("Press Enter once verified (or type 'skip' to cancel verification): ").strip().lower()
        if ans == "skip":
            return False
        confirmed = main._supabase_email_confirmed(email)
        if confirmed is True:
            print("Email verified.")
            return True
        print("Not verified yet - check the inbox (and spam folder), then try again.")


def create() -> None:
    db = main.SessionLocal()
    try:
        email = input("Platform Owner email: ").strip().lower()
        if not email or "@" not in email:
            print("A valid email is required.")
            return
        existing = db.query(main.PlatformOwner).filter(main.func.lower(main.PlatformOwner.email) == email).first()
        if existing:
            print(f"A Platform Owner already exists for {email} (id={existing.id}).")
            print("This creates NEW accounts only. To recover a lost password or MFA device,")
            print("run:  venv/Scripts/python.exe scripts/create_platform_owner.py --recover")
            return

        password = getpass.getpass("Password: ")
        try:
            main.validate_password_strength(password)
        except Exception as exc:
            print("Weak password:", getattr(exc, "detail", str(exc)))
            return
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            return

        print("\n--- Email verification (recommended) ---")
        result = _verify_email_via_supabase(email)
        if result is False:
            print("Aborted: email was not verified.")
            return
        if result is None:
            print("Supabase is not configured on this server (SUPABASE_URL / a Supabase key")
            print("is not set), so email verification cannot be performed here. Creating this")
            print("account already required direct server + database access, which is itself")
            print("a strong control - but set Supabase up to get real verification for future")
            print("accounts, and see platform/PLATFORM_OWNER_SETUP.md.")
            if input("Type YES to proceed without email verification: ").strip() != "YES":
                print("Aborted.")
                return
            email_verified_at = None
        else:
            email_verified_at = main.datetime.utcnow()

        secret = main._totp_new_secret()
        owner = main.PlatformOwner(
            email=email,
            password=main.hash_password(password),
            totp_secret=secret,
            totp_enabled=True,
            email_verified_at=email_verified_at,
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)

        uri = main._totp_uri(secret, email)
        print(f"\nPlatform Owner account created (id={owner.id}).")
        print("\nAdd this to an authenticator app (Google Authenticator / Authy / 1Password /")
        print("Microsoft Authenticator) by generating a QR code from the URI below, or by")
        print("choosing \"Enter setup key manually\":\n")
        print(f"  Account name : Cauldra Platform ({email})")
        print(f"  Secret key   : {secret}")
        print("  Type         : Time-based, 6 digits, 30 seconds\n")
        print("otpauth URI (paste into any QR generator; some apps accept it directly):")
        print(f"  {uri}\n")
        print(f"Control Panel URL path: {main.PLATFORM_PANEL_PATH}")
        print("(Configurable via the PLATFORM_PANEL_PATH environment variable - change it")
        print(" any time without a code change. Never link this path from the customer app.)")
        if not __import__("os").getenv("PLATFORM_OWNER_SECRET_KEY", "").strip():
            print("\nNote: PLATFORM_OWNER_SECRET_KEY is not set. A key was derived automatically")
            print("from SUPPLY_AI_SECRET_KEY, which works, but setting a dedicated")
            print("PLATFORM_OWNER_SECRET_KEY in production is recommended for defense in depth.")
    finally:
        db.close()


def recover() -> None:
    """Break-glass recovery for a lost password or MFA device. Requires the
    SAME trust level as account creation (server + database access) - there
    is deliberately no self-service "forgot password" email flow for
    something this sensitive. Immediately invalidates every existing session
    for the account (bumps platform_auth_version) and writes a permanent
    entry to platform_audit_logs so the reset is visible from the Control
    Panel's own history, not just this terminal."""
    db = main.SessionLocal()
    try:
        email = input("Email of the Platform Owner account to recover: ").strip().lower()
        owner = db.query(main.PlatformOwner).filter(main.func.lower(main.PlatformOwner.email) == email).first()
        if not owner:
            print("No Platform Owner account found for that email.")
            return

        print(f"\nFound account id={owner.id}, created {owner.created_at}, "
              f"last login {owner.last_login_at or 'never'}, disabled={owner.disabled}.")
        print("Recovering it will invalidate ALL of its existing sessions immediately.")
        if input("Type RESET to continue: ").strip() != "RESET":
            print("Aborted.")
            return

        print("\nWhat do you want to reset?")
        print("  1. Password only")
        print("  2. MFA (TOTP) only")
        print("  3. Both")
        choice = input("Choice [1/2/3]: ").strip()

        changed = []
        if choice in ("1", "3"):
            pw = getpass.getpass("New password: ")
            try:
                main.validate_password_strength(pw)
            except Exception as exc:
                print("Weak password:", getattr(exc, "detail", str(exc)))
                return
            if pw != getpass.getpass("Confirm new password: "):
                print("Passwords do not match.")
                return
            owner.password = main.hash_password(pw)
            changed.append("password")

        new_secret = None
        if choice in ("2", "3"):
            new_secret = main._totp_new_secret()
            owner.totp_secret = new_secret
            owner.totp_enabled = True
            changed.append("MFA")

        if not changed:
            print("Nothing selected. Aborted.")
            return

        owner.disabled = False
        owner.platform_auth_version = int(owner.platform_auth_version or 1) + 1
        db.add(main.PlatformAuditLog(
            platform_owner_id=owner.id, platform_owner_email=owner.email,
            action="PLATFORM_OWNER_RECOVERED",
            description=(f"Recovered via server CLI (scripts/create_platform_owner.py --recover): "
                         f"reset {', '.join(changed)}. All existing sessions invalidated."),
        ))
        db.commit()

        print(f"\nDone. Reset: {', '.join(changed)}. Every previous session for this account is now invalid.")
        if new_secret:
            uri = main._totp_uri(new_secret, email)
            print(f"\nNew MFA secret key: {new_secret}")
            print(f"otpauth URI: {uri}")
    finally:
        db.close()


if __name__ == "__main__":
    if "--recover" in sys.argv:
        recover()
    else:
        create()
