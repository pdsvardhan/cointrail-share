"""Iteration 203, verifier round 2: history repairs for the fixes made after the independent review.

Every step is `step_<name>(conn, dry_run=True) -> dict`, generic (no owner data), idempotent, and
writes nothing when dry_run is true. The iteration-203 post-deploy runner calls them:

  step_merge_double_recorded_payments  E8  payments an older build recorded twice (the bank's two
                                           wordings of one payment, two days apart) become one
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def step_merge_double_recorded_payments(conn, dry_run=True):
    """E8: merge cc_payments rows that today's payment identity rules call one payment (same card and
    amount within +/-2 days, evidence not saying two payments); the kept row takes the other's evidence."""
    from services.cards_service import dedup_payments_logic
    return dedup_payments_logic(conn, dry_run=dry_run)
