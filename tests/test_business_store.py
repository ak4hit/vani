"""Unit tests for BusinessStore state manager."""

import pytest
from src.services.business_store import BusinessStore, BusinessProfile


def test_business_store_create_and_get():
    store = BusinessStore()
    biz = store.get_or_create_business("biz_test_1", chat_id=12345)
    assert biz.business_id == "biz_test_1"
    assert biz.chat_id == 12345
    assert biz.is_paused is False

    retrieved = store.get_business("biz_test_1")
    assert retrieved is not None
    assert retrieved.business_id == "biz_test_1"

    by_chat = store.get_by_chat_id(12345)
    assert by_chat is not None
    assert by_chat.business_id == "biz_test_1"


def test_business_store_update_profile():
    store = BusinessStore()
    store.get_or_create_business("biz_update")

    updated = store.update_profile(
        "biz_update",
        business_name="Apex Care",
        industry="clinic",
        hours="Mon-Sat 8am-8pm",
        escalation_number="+15559876543"
    )
    assert updated.business_name == "Apex Care"
    assert updated.industry == "clinic"
    assert updated.hours == "Mon-Sat 8am-8pm"
    assert updated.escalation_number == "+15559876543"


def test_business_store_pause_resume():
    store = BusinessStore()
    biz_id = "biz_pause_test"
    store.get_or_create_business(biz_id)

    assert store.is_paused(biz_id) is False

    store.set_paused(biz_id, True)
    assert store.is_paused(biz_id) is True

    store.set_paused(biz_id, False)
    assert store.is_paused(biz_id) is False


def test_business_store_faq_operations():
    store = BusinessStore()
    biz_id = "biz_faq_test"
    store.get_or_create_business(biz_id)

    # Initially empty
    assert len(store.list_faqs(biz_id)) == 0

    # Add FAQ 1
    faq1 = store.add_faq(biz_id, "Do you have parking?", "Yes, free parking in back.")
    assert faq1.id == 1
    assert faq1.question == "Do you have parking?"

    # Add FAQ 2
    faq2 = store.add_faq(biz_id, "Accept insurance?", "Yes, all major plans.")
    assert faq2.id == 2

    faqs = store.list_faqs(biz_id)
    assert len(faqs) == 2

    # Remove FAQ 1
    assert store.remove_faq(biz_id, 1) is True
    faqs_after = store.list_faqs(biz_id)
    assert len(faqs_after) == 1
    assert faqs_after[0].id == 2

    # Remove non-existent FAQ
    assert store.remove_faq(biz_id, 999) is False
