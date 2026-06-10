import asyncio
import os
import random
import smtplib
import string
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..database import get_db
from ..config import get_settings

settings = get_settings()


# ── OTP generation ────────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generate a random numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


def _format_e164(phone: str) -> str:
    """Convert raw phone number to E.164 format (+91XXXXXXXXXX for India)."""
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace(".", "")
    if cleaned.startswith("+"):
        return cleaned
    if len(cleaned) == 10:
        return f"+91{cleaned}"
    default_code = getattr(settings, "twilio_default_country_code", "+91").strip()
    if not default_code.startswith("+"):
        default_code = f"+{default_code}"
    return f"{default_code}{cleaned}"


# ── OTP storage / verification ────────────────────────────────────────────────

async def store_otp(identifier: str, otp: str, expires_in_minutes: int = 10) -> bool:
    """Store OTP in MongoDB keyed by identifier (phone OR email)."""
    db = get_db()
    expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
    await db.otp_store.update_one(
        {"phone": identifier},   # field name kept for backward compat
        {
            "$set": {
                "phone":      identifier,
                "otp":        otp,
                "expires_at": expires_at,
                "attempts":   0,
                "created_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    return True


async def verify_otp(identifier: str, otp: str) -> bool:
    """Verify OTP. Falls back to static OTP for dev/testing."""
    # Static OTP bypass (dev / demo)
    if otp == settings.static_otp:
        return True

    db = get_db()
    record = await db.otp_store.find_one({"phone": identifier})
    if not record:
        return False

    if record["expires_at"] < datetime.utcnow():
        await db.otp_store.delete_one({"phone": identifier})
        return False

    if record.get("attempts", 0) >= 3:
        return False

    await db.otp_store.update_one(
        {"phone": identifier},
        {"$inc": {"attempts": 1}},
    )

    if record["otp"] == otp:
        await db.otp_store.delete_one({"phone": identifier})
        return True

    return False


# ── Email sending (non-blocking) ──────────────────────────────────────────────

def _send_email_sync(sender_email: str, sender_password: str, recipient: str, otp: str) -> None:
    """
    Synchronous SMTP send — runs in a thread pool via asyncio.to_thread
    so it never blocks the FastAPI event loop.
    """
    msg = MIMEMultipart()
    msg["From"]    = sender_email
    msg["To"]      = recipient
    msg["Subject"] = f"{otp} is your Claimit verification code"

    body = (
        f"Hello,\n\n"
        f"Your Claimit OTP is: {otp}\n\n"
        f"This code is valid for 10 minutes. Do not share it with anyone.\n\n"
        f"— The Claimit Team"
    )
    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
    server.starttls()
    server.login(sender_email, sender_password)
    server.sendmail(sender_email, recipient, msg.as_string())
    server.quit()


async def send_otp_sms(identifier: str, otp: str) -> bool:
    """
    Send OTP via email (if identifier looks like an email) or via Twilio for phone.
    smtplib is synchronous — run it in asyncio.to_thread so it never blocks the event loop.
    """
    if "@" in identifier:
        sender_email    = os.environ.get("SMTP_USERNAME") or settings.smtp_username
        sender_password = os.environ.get("SMTP_PASSWORD") or settings.smtp_password

        if not sender_email or not sender_password:
            # SMTP not configured — OTP is still stored; user can use static OTP.
            print(f"⚠️  SMTP not configured. OTP for {identifier}: {otp}")
            return True

        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    _send_email_sync, sender_email, sender_password, identifier, otp
                ),
                timeout=14.0,
            )
            print(f"📧 OTP email sent to {identifier}")
            return True

        except asyncio.TimeoutError:
            print(f"❌ SMTP timeout for {identifier} — OTP: {otp}")
            return False

        except Exception as exc:
            print(f"❌ SMTP error for {identifier}: {exc} — OTP: {otp}")
            return False

    else:
        # ── WhatsApp via Twilio ───────────────────────────────────────────────
        e164_phone = _format_e164(identifier)
        message_body = (
            f"🔐 *Your Claimit OTP is: {otp}*\n\n"
            f"Valid for 10 minutes. Do not share this code with anyone."
        )

        account_sid = os.environ.get("TWILIO_ACCOUNT_SID") or settings.twilio_account_sid
        auth_token  = os.environ.get("TWILIO_AUTH_TOKEN")  or settings.twilio_auth_token
        wa_from     = os.environ.get("TWILIO_WHATSAPP_NUMBER", "").strip()
        sms_from    = os.environ.get("TWILIO_PHONE_NUMBER", "").strip() or settings.twilio_phone_number.strip()

        if account_sid and auth_token:
            try:
                from twilio.rest import Client as TwilioClient
                client = TwilioClient(account_sid, auth_token)

                if wa_from:
                    msg = client.messages.create(
                        body=message_body,
                        from_=f"whatsapp:{wa_from}",
                        to=f"whatsapp:{e164_phone}",
                    )
                    print(f"✅ WhatsApp OTP sent to {e164_phone} | SID: {msg.sid}")
                    return True

                if sms_from:
                    msg = client.messages.create(
                        body=f"Your Claimit OTP is: {otp}. Valid for 10 minutes.",
                        from_=sms_from,
                        to=e164_phone,
                    )
                    print(f"✅ SMS OTP sent to {e164_phone} | SID: {msg.sid}")
                    return True

            except Exception as exc:
                print(f"❌ Twilio failed for {e164_phone}: {exc} — OTP: {otp}")
                return True

        print(f"⚠️  Twilio not configured — OTP for {e164_phone}: {otp}")
        return True
