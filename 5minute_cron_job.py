# async_mongo_cost_deduction_fixed.py
"""
Asynchronous + concurrent version (hard-coded, no CLI args).
Fixes:
 - correct monthly cost summation bug
 - apply tiny deductions reliably using $inc instead of rounding new balance
 - improved logging for very-small deductions
"""
import asyncio
from datetime import datetime, timezone
from app.config import settings
from app.services.mail_util import send_low_credit_message, send_credits_expired_message
import motor.motor_asyncio

# -------------------- HARD-CODED CONFIG ----------------------------------
MONGODB_URL = settings.mongodb_url
DATABASE_NAME = settings.database_name
USERS_COLLECTION = 'users'
COSTS_COLLECTION = 'costs'

BYTES_PER_GB = 1024 ** 3  # GiB
FIVE_MIN_INTERVALS_PER_MONTH = 30 * 24 * 12  # = 8640

MAX_CONCURRENCY = 50
CHUNK_SIZE = 200
# -------------------------------------------------------------------------


def compute_tiered_cost(gb_used: float, tiers: list) -> float:
    if gb_used <= 0:
        return 0.0

    tiers_sorted = sorted(tiers, key=lambda t: float(t.get('min_gb', 0)))
    remaining = gb_used
    cost = 0.0

    for t in tiers_sorted:
        t_min = float(t.get('min_gb', 0))
        t_max = t.get('max_gb')
        t_max = float(t_max) if t_max is not None else None
        price = float(t['cost_per_gb'])

        if t_max is None:
            span = max(0.0, remaining if remaining > 0 else 0.0)
        else:
            tier_capacity = max(0.0, t_max - t_min)
            span = min(remaining, tier_capacity)

        if span > 0:
            cost += span * price
            remaining -= span

        if remaining <= 1e-12:
            break

    return cost


def get_user_storage_gb(user_doc: dict) -> tuple:
    s = float(user_doc.get('storage_used_standard', 0) or 0)
    s_del = float(user_doc.get('storage_used_standard_deleted', 0) or 0)
    a = float(user_doc.get('storage_used_archived', 0) or 0)
    a_del = float(user_doc.get('storage_used_archived_deleted', 0) or 0)

    total_standard = s + s_del
    total_archive = a + a_del

    gb_standard = total_standard / BYTES_PER_GB
    gb_archive = total_archive / BYTES_PER_GB

    return gb_standard, gb_archive


def combine_monthly_costs(standard_monthly: float, archive_monthly: float) -> float:
    """
    Placeholder to combine the two monthly costs.
    Keep this function in case you later want special correction logic.
    For now, it's simply the sum.
    """
    return standard_monthly + archive_monthly


async def process_user(user: dict, db, standard_tiers, archive_tiers, sem: asyncio.Semaphore, results: dict):
    """Compute deduction for a single user and update the DB.
    Uses semaphore to limit concurrent DB update operations.
    """
    user_id = user.get('_id')
    email = user.get('email')
    try:
        credits = float(user.get('credits', 0) or 0)

        gb_standard, gb_archive = get_user_storage_gb(user)

        monthly_cost_standard = compute_tiered_cost(gb_standard, standard_tiers)
        monthly_cost_archive = compute_tiered_cost(gb_archive, archive_tiers)

        # corrected: combine the monthly costs properly
        total_monthly_cost = combine_monthly_costs(monthly_cost_standard, monthly_cost_archive)

        # convert monthly -> per 5-min
        cost_per_5min = float(total_monthly_cost) / FIVE_MIN_INTERVALS_PER_MONTH

        # Use $inc to ensure tiny floats are applied as a decrement in-place.
        update_doc = {
            '$inc': {'credits': -cost_per_5min},
            '$set': {'updated_at': datetime.now(timezone.utc)}
        }

        async with sem:
            res = await db[USERS_COLLECTION].update_one({'_id': user_id}, update_doc)

        results['processed'] += 1
        if res.modified_count > 0:
            results['updated'] += 1

        # For logging: compute the new credit *locally* for display (not used to write DB)
        new_credits = credits - cost_per_5min
        cost_per_week = (cost_per_5min/5)  * 60 * 24 * 7 
        if new_credits < cost_per_week and new_credits > 0:
            await send_low_credit_message(email, user.get("full_name", "unknown"), new_credits)
        if new_credits <= 0:
            await send_credits_expired_message(email, user.get("full_name", "unknown"))
        # Show high precision so very small deductions are visible in logs
        print(
            f"Updated user {user_id} ({email}): "
            f"{credits:.12f} -> {new_credits:.12f}  (deducted {cost_per_5min:.12e})"
        )

    except Exception as e:
        results['errors'] += 1
        print(f"Error processing user {user_id} ({email}): {e}")


async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]

    costs = await db[COSTS_COLLECTION].find_one({})
    if not costs:
        raise RuntimeError(f'No document found in collection \"{COSTS_COLLECTION}\"')

    standard_tiers = costs.get('standard_storage', {}).get('tiers', [])
    archive_tiers = costs.get('archive_storage', {}).get('tiers', [])

    cursor = db[USERS_COLLECTION].find({})

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    chunk = []
    results = {'processed': 0, 'updated': 0, 'errors': 0}

    async for user in cursor:
        chunk.append(user)
        if len(chunk) >= CHUNK_SIZE:
            tasks = [asyncio.create_task(process_user(u, db, standard_tiers, archive_tiers, sem, results)) for u in chunk]
            await asyncio.gather(*tasks)
            chunk = []

    if chunk:
        tasks = [asyncio.create_task(process_user(u, db, standard_tiers, archive_tiers, sem, results)) for u in chunk]
        await asyncio.gather(*tasks)

    client.close()

    print('Done.')
    print(f"Users processed: {results['processed']}, updates applied: {results['updated']}, errors: {results['errors']}")


if __name__ == '__main__':
    asyncio.run(main())
