"""SMS Confirmation Service.

Dispatches structured multi-channel SMS booking confirmations
(UPGRADE_ADDENDUM.md Section 5). Includes clinic name, provider, date/time,
daily queue token number, and cancellation instructions.
"""

from datetime import datetime
from typing import Dict, List, Optional
from src.config import settings
from src.services.token_service import TokenService
from src.utils.logger import get_logger

logger = get_logger("vani.sms_service")


class SMSService:
    """Manages SMS dispatch for appointment confirmations and notifications."""

    def __init__(self, account_sid: Optional[str] = None, auth_token: Optional[str] = None, from_phone: Optional[str] = None):
        self.account_sid = account_sid or settings.TWILIO_ACCOUNT_SID
        self.auth_token = auth_token or settings.TWILIO_AUTH_TOKEN
        self.from_phone = from_phone or settings.TWILIO_PHONE_NUMBER
        self.dispatched_messages: List[Dict[str, str]] = []

    def format_confirmation_message(
        self,
        business_name: str,
        provider_name: str,
        slot_time: datetime,
        token_number: Optional[int] = None,
        clinic_phone: Optional[str] = None,
        location: Optional[str] = None
    ) -> str:
        """Constructs standardized patient booking confirmation SMS message."""
        date_str = slot_time.strftime("%A, %b %d, %Y")
        time_str = slot_time.strftime("%I:%M %p")
        token_str = TokenService.format_token_string(token_number) if token_number else "N/A"
        loc_str = location or "Main Clinic"
        contact = clinic_phone or "our front desk"

        msg = (
            f"✅ Appointment Confirmed!\n"
            f"🏥 Clinic: {business_name}\n"
            f"🩺 Provider: {provider_name}\n"
            f"📅 Date: {date_str}\n"
            f"⏰ Time: {time_str}\n"
            f"🔢 Token Number: {token_str}\n"
            f"📍 Location: {loc_str}\n"
            f"⚠️ Please arrive 10 minutes prior to your slot.\n"
            f"To cancel or reschedule, call {contact}."
        )
        return msg.strip()

    async def send_appointment_confirmation(
        self,
        to_phone: str,
        business_name: str,
        provider_name: str,
        slot_time: datetime,
        token_number: Optional[int] = None,
        clinic_phone: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict[str, str]:
        """Dispatches appointment confirmation SMS via Twilio or test mock."""
        body = self.format_confirmation_message(
            business_name=business_name,
            provider_name=provider_name,
            slot_time=slot_time,
            token_number=token_number,
            clinic_phone=clinic_phone,
            location=location
        )

        record = {
            "to": to_phone,
            "body": body,
            "timestamp": slot_time.isoformat()
        }
        self.dispatched_messages.append(record)

        if self.account_sid and self.auth_token and self.from_phone:
            try:
                import httpx
                url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
                data = {
                    "From": self.from_phone,
                    "To": to_phone,
                    "Body": body
                }
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, data=data, auth=(self.account_sid, self.auth_token))
                    if resp.status_code in [200, 201]:
                        logger.info(f"Twilio SMS sent to {to_phone} for booking at {slot_time}")
                        return {"status": "sent", "to": to_phone, "body": body}
                    else:
                        logger.error(f"Twilio SMS failed with status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error sending Twilio SMS: {e}")

        logger.info(f"Mock SMS dispatched to {to_phone}: {body[:60]}...")
        return {"status": "mock_sent", "to": to_phone, "body": body}

    def reset(self) -> None:
        """Clears logged messages (used for test isolation)."""
        self.dispatched_messages.clear()


# Global SMS service singleton
sms_service = SMSService()
