"""Finite resource calendar arithmetic, independent of sales and tender rules."""
from datetime import timedelta
from decimal import Decimal, localcontext
from .models import ScheduleResult


def production_workdays(quantity: Decimal, units_per_workday: Decimal, setup_workdays: Decimal, *, rate_factor: Decimal) -> Decimal:
    values = (quantity, units_per_workday, setup_workdays, rate_factor)
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
        raise ValueError('Finite Decimal production facts required')
    if quantity < 0 or units_per_workday <= 0 or setup_workdays < 0 or rate_factor <= 0:
        raise ValueError('Invalid production rate or quantity')
    return quantity / (units_per_workday * rate_factor) + setup_workdays


def schedule_work(jobs, resources, *, capacity) -> ScheduleResult:
    result = ScheduleResult()
    for job in sorted(jobs, key=lambda item: (item['deadline'], item['position_id'])):
        key, group, work = job['position_id'], job['resource_group_id'], job['work']
        day = job['release']
        while day < job['deadline'] and work > 0:
            slot = group, day
            available = capacity(resources[group], day) if group in resources else None
            if available is None:
                result.unknown.add(key)
            else:
                if not isinstance(available, Decimal) or not available.is_finite() or available < 0:
                    raise ValueError('Capacity must be a nonnegative finite Decimal or None')
                result.original_capacity[slot] = available
                result.remaining.setdefault(slot, available)
                used = min(work, result.remaining[slot])
                if used > 0:
                    result.schedule.append(dict(position_id=key, resource_group_id=group, date=day.isoformat(), work=used))
                    result.remaining[slot] -= used
                    work -= used
            if work == 0:
                result.ready_at[key] = day + timedelta(days=1)
                break
            day += timedelta(days=1)
        if work > 0:
            result.unplaced[key] = work
    return result
