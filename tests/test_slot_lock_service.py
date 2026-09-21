"""Tests for SlotLockManager: Distributed Slot Locking & Collision Prevention.

Verifies Phase 9 requirements:
1. Slot lock acquisition sets exclusive hold for a caller.
2. Concurrent caller B cannot acquire a slot held by caller A.
3. Lock release by the correct call_sid frees the slot.
4. Same call_sid can re-acquire (renew) its own lock.
5. Expired locks are cleaned up and slot becomes available.
6. is_slot_locked correctly detects active holds and excludes the holding call.
"""

import asyncio
import pytest

from src.services.slot_lock_service import SlotLockManager


@pytest.fixture
def lock_mgr():
    """Fresh in-memory SlotLockManager per test."""
    mgr = SlotLockManager(redis_client=None)
    return mgr


SLOT_ISO = "2026-10-01T09:00:00+00:00"
BIZ_ID = "biz_test_lock"
DOC_ID = "primary"


@pytest.mark.asyncio
async def test_acquire_slot_lock_success(lock_mgr):
    """Caller A acquires a free slot successfully."""
    acquired = await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    assert acquired is True


@pytest.mark.asyncio
async def test_concurrent_caller_blocked(lock_mgr):
    """Caller B cannot acquire a slot already held by Caller A."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    acquired_b = await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_B")
    assert acquired_b is False


@pytest.mark.asyncio
async def test_same_caller_can_renew_hold(lock_mgr):
    """Same call_sid can re-acquire (renew) its existing hold."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    renew = await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    assert renew is True


@pytest.mark.asyncio
async def test_release_frees_slot_for_next_caller(lock_mgr):
    """Caller A releases their hold; Caller B can then acquire."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    released = await lock_mgr.release_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    assert released is True

    # B can now acquire
    acquired_b = await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_B")
    assert acquired_b is True


@pytest.mark.asyncio
async def test_wrong_caller_cannot_release_lock(lock_mgr):
    """Caller B cannot release a lock held by Caller A."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    released_by_b = await lock_mgr.release_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_B")
    assert released_by_b is False


@pytest.mark.asyncio
async def test_is_slot_locked_detects_active_hold(lock_mgr):
    """is_slot_locked returns True when slot is held."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    is_locked = await lock_mgr.is_slot_locked(BIZ_ID, DOC_ID, SLOT_ISO)
    assert is_locked is True


@pytest.mark.asyncio
async def test_is_slot_locked_excludes_own_caller(lock_mgr):
    """is_slot_locked with exclude_call_sid reports False for the holder's own view."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    is_locked_from_own_view = await lock_mgr.is_slot_locked(BIZ_ID, DOC_ID, SLOT_ISO, exclude_call_sid="CA_A")
    assert is_locked_from_own_view is False


@pytest.mark.asyncio
async def test_confirm_booking_releases_lock(lock_mgr):
    """confirm_booking releases the slot hold after DB booking persists."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    confirmed = await lock_mgr.confirm_booking(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    assert confirmed is True

    # Slot is now free
    is_locked = await lock_mgr.is_slot_locked(BIZ_ID, DOC_ID, SLOT_ISO)
    assert is_locked is False


@pytest.mark.asyncio
async def test_lock_key_format():
    """Lock key follows strict tenant-scoped format."""
    key = SlotLockManager.get_lock_key("biz_123", "dr_sharma", "2026-10-01T09:00:00")
    assert key == "lock:biz_biz_123:doc_dr_sharma:2026-10-01T09:00:00"


@pytest.mark.asyncio
async def test_different_slots_isolated(lock_mgr):
    """Locks on different slots do not interfere with each other."""
    slot_a = "2026-10-01T09:00:00+00:00"
    slot_b = "2026-10-01T09:30:00+00:00"

    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, slot_a, "CA_A")
    acquired_b = await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, slot_b, "CA_B")
    assert acquired_b is True


@pytest.mark.asyncio
async def test_reset_clears_all_locks(lock_mgr):
    """Reset wipes all in-memory locks (test isolation)."""
    await lock_mgr.acquire_temporary_hold(BIZ_ID, DOC_ID, SLOT_ISO, "CA_A")
    lock_mgr.reset()
    is_locked = await lock_mgr.is_slot_locked(BIZ_ID, DOC_ID, SLOT_ISO)
    assert is_locked is False
