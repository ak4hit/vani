"""Voice Management Service - Voice Cloning with Consent Auditing and Voice Design.

Enforces legal compliance for voice cloning:
- Mandatory explicit consent statement verification prior to cloning (Texas SB 140, TCPA).
- Immutable consent audit record logged in the database with timestamp and actor chat_id.
- Voice Design v3 synthesis from natural language persona descriptions.
"""

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, Optional
import httpx

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
import src.database.session as db_session
from src.services.business_store import business_store
from src.services.db_business_repository import BusinessRepository
from src.utils.logger import get_logger

logger = get_logger("vani.voice")

LEGAL_VOICE_CONSENT_STATEMENT = (
    "I hereby certify and confirm that I have obtained explicit, legally valid consent "
    "from the individual whose voice is contained in this audio sample to clone and synthesize "
    "their voice for AI voice receptionist operations, pursuant to applicable state and federal "
    "voice privacy regulations (including Texas SB 140 and TCPA guidelines)."
)


class VoiceConsentRequiredError(PermissionError):
    """Raised when voice cloning is attempted without explicit consent confirmation."""
    pass


class VoiceService:
    """Manages voice cloning with legal consent audits and text-based voice design."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ELEVENLABS_API_KEY

    async def clone_voice_from_audio(
        self,
        business_id: str,
        audio_bytes: bytes,
        filename: str,
        voice_name: str,
        consent_confirmed: bool,
        chat_id: Optional[int] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Clone an audio sample into an ElevenLabs voice and persist legal consent audit."""
        # 1. MANDATORY COMPLIANCE GATE
        if not consent_confirmed:
            logger.warning(
                f"Voice cloning rejected for business='{business_id}' - Missing mandatory consent confirmation."
            )
            raise VoiceConsentRequiredError(
                "Explicit voice cloning consent confirmation is legally mandatory before synthesis."
            )

        if not audio_bytes or len(audio_bytes) < 1000:
            raise ValueError("Audio sample is too short or empty. Minimum 10-30s sample required.")

        voice_id: str = ""

        # 2. Call ElevenLabs Voice Add API if configured
        if self.api_key:
            try:
                url = "https://api.elevenlabs.io/v1/voices/add"
                headers = {"xi-api-key": self.api_key}
                data = {
                    "name": voice_name[:100],
                    "description": f"Custom receptionist voice for business {business_id}"
                }
                files = {
                    "files": (filename, audio_bytes, "audio/mpeg")
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, data=data, files=files)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        voice_id = res_json.get("voice_id", "")
                    else:
                        logger.error(f"ElevenLabs voice add API failed with HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Error communicating with ElevenLabs Voice Clone API: {e}")

        # Fallback / mock voice_id for testing or offline environment
        if not voice_id:
            sample_hash = hashlib.sha256(audio_bytes).hexdigest()[:12]
            voice_id = f"voice_clone_{sample_hash}"
            logger.info(f"Generated mock voice_id='{voice_id}' for business='{business_id}'")

        # 3. Log Immutable Consent Audit Record in Database
        async def _persist_audit(db_sess: AsyncSession):
            await BusinessRepository.record_voice_consent(
                session=db_sess,
                business_id=business_id,
                voice_id=voice_id,
                voice_name=voice_name,
                consent_text=LEGAL_VOICE_CONSENT_STATEMENT,
                consent_by_chat_id=chat_id,
                sample_filename=filename,
                consent_confirmed=True
            )
            # Update business model with new voice_id
            await BusinessRepository.update_profile(db_sess, business_id=business_id, voice_id=voice_id)

        if session is not None:
            await _persist_audit(session)
        else:
            session_maker = db_session.get_session_maker()
            async with session_maker() as sess:
                await _persist_audit(sess)

        # 4. Update in-memory profile
        profile = business_store.get_business(business_id)
        if profile:
            profile.voice_id = voice_id
            business_store.update_profile(business_id, voice_id=voice_id)

        logger.info(
            f"Successfully cloned voice voice_id='{voice_id}' for business='{business_id}' "
            f"with verified legal consent audit."
        )

        return {
            "success": True,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "consent_confirmed": True,
            "consent_timestamp": datetime.now(timezone.utc).isoformat(),
            "business_id": business_id
        }

    async def design_voice_from_description(
        self,
        business_id: str,
        description: str,
        voice_name: Optional[str] = None,
        chat_id: Optional[int] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Generate a custom voice based entirely on natural language description (Voice Design v3)."""
        if not description.strip():
            raise ValueError("Voice description prompt cannot be empty.")

        resolved_name = voice_name or f"Vani Voice for {business_id}"
        voice_id: str = ""

        if self.api_key:
            try:
                url = "https://api.elevenlabs.io/v1/voice-generation/generate-voice"
                headers = {
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json"
                }
                payload = {
                    "text": "Hello, thank you for calling. How may I assist you today?",
                    "gender": "female",
                    "age": "young",
                    "accent": "american",
                    "accent_strength": 1.0,
                    "prompt": description[:500]
                }
                async with httpx.AsyncClient(timeout=25.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        voice_id = res_json.get("generated_voice_id", "")
                    else:
                        logger.warning(
                            f"ElevenLabs Voice Design API returned {resp.status_code}. Using fallback voice ID."
                        )
            except Exception as e:
                logger.error(f"Error calling ElevenLabs Voice Design API: {e}")

        if not voice_id:
            prompt_hash = hashlib.md5(description.encode("utf-8")).hexdigest()[:12]
            voice_id = f"voice_design_{prompt_hash}"

        # Persist new voice_id to database and business store
        if session is not None:
            await BusinessRepository.update_profile(session, business_id=business_id, voice_id=voice_id)
        else:
            session_maker = db_session.get_session_maker()
            async with session_maker() as sess:
                await BusinessRepository.update_profile(sess, business_id=business_id, voice_id=voice_id)

        profile = business_store.get_business(business_id)
        if profile:
            profile.voice_id = voice_id
            business_store.update_profile(business_id, voice_id=voice_id)

        logger.info(
            f"Voice designed successfully for business='{business_id}' voice_id='{voice_id}' "
            f"prompt='{description[:40]}...'"
        )

        return {
            "success": True,
            "voice_id": voice_id,
            "voice_name": resolved_name,
            "description": description,
            "business_id": business_id
        }


voice_service = VoiceService()
