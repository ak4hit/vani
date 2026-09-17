"""Privacy REST API Routes - GDPR / DPDP 'Forget Me' Endpoint.

Enables programmatic caller record deletion for external CRM and compliance audits.
"""

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel
from typing import Optional

import src.database.session as db_session
from src.services.db_business_repository import BusinessRepository
from src.utils.logger import get_logger

logger = get_logger("vani.api.privacy")

router = APIRouter(prefix="/api/privacy", tags=["Privacy & Compliance"])


class ForgetMeResponse(BaseModel):
    success: bool
    business_id: str
    caller_number: str
    deleted_records: int
    message: str


@router.delete("/caller/{caller_number}", response_model=ForgetMeResponse)
async def delete_caller_records_endpoint(
    caller_number: str,
    x_business_id: str = Header(..., alias="X-Business-ID", description="Business Tenant Identifier")
):
    """Permanently delete all call logs, summaries, and transcripts for a caller number.

    Enforces strict tenant isolation: only records belonging to the business specified in
    `X-Business-ID` are deleted.
    """
    clean_number = caller_number.strip()
    digits = [c for c in clean_number if c.isdigit()]
    if len(digits) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number: must contain at least 5 digits."
        )

    try:
        session_maker = db_session.get_session_maker()
        async with session_maker() as session:
            result = await BusinessRepository.delete_caller_records(
                session=session,
                business_id=x_business_id,
                caller_number=clean_number
            )

        deleted = result["deleted_call_logs"]
        return ForgetMeResponse(
            success=True,
            business_id=x_business_id,
            caller_number=clean_number,
            deleted_records=deleted,
            message=f"Successfully purged {deleted} records for caller {clean_number}."
        )
    except Exception as e:
        logger.error(f"Error processing privacy deletion endpoint for {clean_number}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute privacy deletion: {str(e)}"
        )
