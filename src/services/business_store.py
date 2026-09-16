"""Business State Store - Manages business profiles, FAQs, and pause states."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
import threading


class FAQItem(BaseModel):
    """FAQ question and answer pair."""
    id: int
    question: str
    answer: str


class BusinessProfile(BaseModel):
    """Business configuration profile for voice bot and admin management."""
    business_id: str
    chat_id: Optional[int] = None
    business_name: str = "Vani Reception"
    industry: str = "general"
    description: str = "AI-powered phone receptionist assisting customers with inquiries."
    agent_name: str = "Vani"
    hours: str = "Mon-Fri 9:00 AM - 6:00 PM"
    phone_number: Optional[str] = None
    escalation_number: Optional[str] = None
    is_paused: bool = False
    faqs: List[FAQItem] = Field(default_factory=list)
    onboarding_completed: bool = False
    onboarding_step: int = 0


class BusinessStore:
    """Thread-safe in-memory store for business profiles.
    
    Acts as an abstraction layer ready to be swapped with PostgreSQL/pgvector in Phase 4.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._businesses: Dict[str, BusinessProfile] = {}
        self._chat_to_biz: Dict[int, str] = {}
        self._next_faq_id = 1

    def get_business(self, business_id: str) -> Optional[BusinessProfile]:
        """Retrieve business by business_id."""
        with self._lock:
            return self._businesses.get(business_id)

    def get_by_chat_id(self, chat_id: int) -> Optional[BusinessProfile]:
        """Retrieve business associated with a Telegram chat_id."""
        with self._lock:
            biz_id = self._chat_to_biz.get(chat_id)
            if biz_id:
                return self._businesses.get(biz_id)
            return None

    def get_or_create_business(self, business_id: str, chat_id: Optional[int] = None) -> BusinessProfile:
        """Retrieve existing or initialize a new business profile."""
        with self._lock:
            if business_id not in self._businesses:
                self._businesses[business_id] = BusinessProfile(
                    business_id=business_id,
                    chat_id=chat_id
                )
            profile = self._businesses[business_id]
            if chat_id is not None:
                profile.chat_id = chat_id
                self._chat_to_biz[chat_id] = business_id
            return profile

    def link_chat_id(self, chat_id: int, business_id: str) -> None:
        """Map a Telegram chat_id to a business_id."""
        with self._lock:
            self._chat_to_biz[chat_id] = business_id
            if business_id in self._businesses:
                self._businesses[business_id].chat_id = chat_id

    def update_profile(self, business_id: str, **kwargs) -> Optional[BusinessProfile]:
        """Update fields of a business profile."""
        with self._lock:
            biz = self._businesses.get(business_id)
            if not biz:
                return None
            for key, val in kwargs.items():
                if hasattr(biz, key):
                    setattr(biz, key, val)
            return biz

    def set_paused(self, business_id: str, is_paused: bool) -> bool:
        """Set pause flag atomically. Returns new paused state."""
        with self._lock:
            biz = self._businesses.get(business_id)
            if not biz:
                biz = BusinessProfile(business_id=business_id, is_paused=is_paused)
                self._businesses[business_id] = biz
            else:
                biz.is_paused = is_paused
            return biz.is_paused

    def is_paused(self, business_id: str) -> bool:
        """Check if business is paused."""
        with self._lock:
            biz = self._businesses.get(business_id)
            return biz.is_paused if biz else False

    def add_faq(self, business_id: str, question: str, answer: str) -> Optional[FAQItem]:
        """Add a FAQ pair to the business profile."""
        with self._lock:
            biz = self._businesses.get(business_id)
            if not biz:
                return None
            faq_id = self._next_faq_id
            self._next_faq_id += 1
            item = FAQItem(id=faq_id, question=question.strip(), answer=answer.strip())
            biz.faqs.append(item)
            return item

    def remove_faq(self, business_id: str, faq_id: int) -> bool:
        """Remove a FAQ by ID."""
        with self._lock:
            biz = self._businesses.get(business_id)
            if not biz:
                return False
            initial_len = len(biz.faqs)
            biz.faqs = [f for f in biz.faqs if f.id != faq_id]
            return len(biz.faqs) < initial_len

    def list_faqs(self, business_id: str) -> List[FAQItem]:
        """List all FAQs for a business."""
        with self._lock:
            biz = self._businesses.get(business_id)
            return list(biz.faqs) if biz else []

    def reset(self) -> None:
        """Clear all stored state (primarily for test isolation)."""
        with self._lock:
            self._businesses.clear()
            self._chat_to_biz.clear()
            self._next_faq_id = 1


business_store = BusinessStore()
