"""
================================================================================
  BACHS PLAN RESOLUTION  —  app/routers/bachs_plans.py
================================================================================

  WHAT THIS FIXES

  Hosted checkout sends metadata.plan_id. Payment links do not. On 23 September
  alone, four people paid and got nothing automatically:

      08:00  sebifrancu@gmail.com        $9.99   plan_id=''
      10:37  kampofomanu@gmail.com       $9.99   plan_id=''
      17:37  yusufmahamood10@gmail.com   $9.99   plan_id=''
      20:39  dakalo293@gmail.com         $9.99   plan_id=''

  The webhook was right to refuse — guessing a duration is worse than a manual
  key. But refusing every link payment means every link customer waits on a
  human. This gives the webhook a way to be TOLD what a link means, instead of
  guessing or giving up.

  ── ORDER OF RESOLUTION ──────────────────────────────────────────────────────

    1. metadata.plan_id         authoritative, checkout sets it
    2. payment link id          via BACHS_LINK_PLANS   (precise)
    3. settlement amount        via BACHS_AMOUNT_PLANS (blunt, opt-in)
    4. refuse                   no licence, 200 + action_required

  Rules 2 and 3 are EMPTY BY DEFAULT. With no env vars set this module behaves
  exactly like the old PLAN_DAYS lookup, so deploying it changes nothing until
  you decide what a link means. That is the point: the mapping is a decision you
  make, never an inference this code makes for you.

  Rule 3 is blunter than rule 2 and is deliberately second. An amount is not an
  identity — if you ever run a $9.99 annual promo alongside a $9.99 monthly one,
  amounts can no longer tell them apart and rule 2 is the only correct answer.
  It is offered because right now you have one live link at one price, and
  waiting for a link id would leave tonight's customers unserved.

  ── SETUP ────────────────────────────────────────────────────────────────────

  The four live links, as at 24 September 2026:

      15 days   $9.99    pl_28c868d8f88b
      30 days   $18.99   pl_6e12a159e723
      1 year    $79.99   pl_458d0a58b52d
      lifetime  $170.00  pl_d57eca0307f7

  In Render -> Environment, set BOTH:

      BACHS_LINK_PLANS   = pl_28c868d8f88b:15days, pl_6e12a159e723:30days,
                           pl_458d0a58b52d:1year, pl_d57eca0307f7:lifetime

      BACHS_AMOUNT_PLANS = 9.99:15days, 18.99:30days, 79.99:1year, 170:lifetime

  (BACHS_LINK_PLANS goes on ONE line — wrapped here only to fit.)

  Both, not one, because nobody has yet seen a link payment's actual payload.
  The field name payment_link_id came from a dashboard screenshot, and the
  webhook does not log the body. If the id is in the payload, rule 2 handles it
  precisely. If it is not, rule 3 catches it on price. Setting both means the
  next customer is served either way instead of finding out the hard way.

  The amount map is safe here ONLY because the four prices are distinct. If you
  ever run two plans at one price — a $9.99 annual promo beside the $9.99
  15-day — amounts stop being an identity and BACHS_LINK_PLANS becomes the only
  correct answer. Delete the matching amount pair if that happens.

  Neither needs a code deploy. Add a link in Bachs, add a pair here, save.
================================================================================
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("payments.plans")


# plan name -> days. The authority for duration.
#
# Four real durations: 15 days, 30 days, 1 year, lifetime. Everything below is
# one of those four under a different name.
#
# The names from license.py's calculate_expiry() are included deliberately
# ("15days", "1month", "1year", "lifetime"), so the value you pick in the admin
# dropdown and the value you type into a Bachs plan mean the same thing. Having
# two vocabularies for one set of durations is how a customer ends up with the
# wrong expiry.
#
# "lifetime" is 36500 days here and there, so a bought key and a hand-issued
# key behave identically.
PLAN_DAYS = {
    # 15 days
    "15days": 15,
    "15day": 15,

    # 30 days
    "1month": 30,
    "monthly": 30,
    "month": 30,
    "30days": 30,

    # 1 year
    "1year": 365,
    "annual": 365,
    "yearly": 365,
    "year": 365,

    # lifetime
    "lifetime": 36500,
}


def _parse_pairs(raw: str) -> dict:
    """Parse "a:monthly, b:annual" into {"a": "monthly", "b": "annual"}.

    Forgiving on purpose. This gets typed into a Render form field at speed,
    probably on a phone. A stray space or a $ sign must not silently disable
    payment delivery.

    A pair naming a plan that isn't in PLAN_DAYS is DROPPED and logged, not
    kept. Keeping it would push the failure to 2am on a Sunday when someone
    pays; dropping it fails now, in the deploy log, where it is visible.
    """
    out = {}
    if not raw:
        return out

    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            logger.error("BACHS plan map: ignoring %r (expected key:plan)", chunk)
            continue

        key, plan = chunk.split(":", 1)
        key = key.strip().strip("$").strip().lower()
        plan = plan.strip().lower()

        if not key:
            logger.error("BACHS plan map: ignoring %r (empty key)", chunk)
            continue
        if plan not in PLAN_DAYS:
            logger.error("BACHS plan map: ignoring %r — unknown plan %r. "
                         "Valid plans: %s", chunk, plan, ", ".join(sorted(PLAN_DAYS)))
            continue

        out[key] = plan

    return out


def _norm_amount(value) -> Optional[str]:
    """Normalise an amount to a stable string key: 9.99, 170.00 -> "170".

    Bachs may send "9.99", 9.99 or "9.990". Render env holds "9.99". Comparing
    floats with == would make 9.99 != 9.99 on a bad day, so both sides are
    rounded to 2dp and formatted the same way before comparison.
    """
    if value is None or value == "":
        return None
    try:
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        num = round(float(cleaned), 2)
    except (TypeError, ValueError):
        return None
    # 170.0 -> "170", 9.99 -> "9.99", so env "170" and "170.00" both match.
    return f"{num:.2f}".rstrip("0").rstrip(".")


LINK_PLANS = _parse_pairs(os.getenv("BACHS_LINK_PLANS", ""))
AMOUNT_PLANS = {k: v for k, v in
                ((_norm_amount(k), v)
                 for k, v in _parse_pairs(os.getenv("BACHS_AMOUNT_PLANS", "")).items())
                if k}


def find_link_id(data: dict, meta: dict) -> Optional[str]:
    """Pull the payment link id out of the event, wherever Bachs put it.

    Their checkout events carry metadata.plan_id; the link events in the
    dashboard showed metadata.payment_link_id. Both shapes are checked, plus the
    two other places a provider commonly puts it, because the cost of guessing
    wrong here is another night of manual keys.
    """
    candidates = [
        meta.get("payment_link_id"),
        meta.get("payment_link"),
        meta.get("link_id"),
        data.get("payment_link_id"),
    ]

    link_obj = data.get("payment_link")
    if isinstance(link_obj, dict):
        candidates.append(link_obj.get("id"))
    elif isinstance(link_obj, str):
        candidates.append(link_obj)

    for c in candidates:
        if c and isinstance(c, (str, int)):
            return str(c).strip()
    return None


def resolve_plan(data: dict, meta: dict) -> Tuple[Optional[str], Optional[int], str]:
    """Work out which plan this payment bought.

    Returns (plan, days, how). plan and days are None when it cannot be
    determined, and the caller must then create NO licence.

    "how" names the rule that fired, so the log says why a customer got 30 days
    rather than leaving you to reverse-engineer it later.
    """
    plan_id = (meta.get("plan_id") or "").strip().lower()
    link_id = find_link_id(data, meta)
    amount_key = _norm_amount(data.get("settlement_amount"))

    # 1. Checkout told us outright.
    if plan_id and plan_id in PLAN_DAYS:
        return plan_id, PLAN_DAYS[plan_id], "plan_id"

    # A plan_id that is present but unrecognised is a genuine problem — a typo
    # in the checkout config, or a new plan nobody mapped. Do not fall through
    # to weaker rules and paper over it.
    if plan_id:
        logger.error("plan_id %r is not a known plan — refusing. Known: %s",
                     plan_id, ", ".join(sorted(PLAN_DAYS)))
        return None, None, "unknown_plan_id"

    # 2. A configured payment link.
    if link_id:
        mapped = LINK_PLANS.get(link_id.lower())
        if mapped:
            return mapped, PLAN_DAYS[mapped], f"link:{link_id}"

    # 3. A configured amount.
    if amount_key:
        mapped = AMOUNT_PLANS.get(amount_key)
        if mapped:
            if link_id:
                # Worth saying out loud: the precise rule was available and
                # unconfigured, so this fell back to the blunt one.
                logger.warning(
                    "resolved by AMOUNT (%s -> %s) but this payment has link id "
                    "%s. Add  BACHS_LINK_PLANS = %s:%s  to map it precisely.",
                    amount_key, mapped, link_id, link_id, mapped)
            return mapped, PLAN_DAYS[mapped], f"amount:{amount_key}"

    # 4. Refuse — and log everything needed to fix it in one edit.
    logger.error(
        "CANNOT RESOLVE PLAN — no licence created.\n"
        "    charge        : %s\n"
        "    link id       : %s\n"
        "    amount        : %s\n"
        "    metadata      : %s\n"
        "    TO FIX, add ONE of these in Render -> Environment:\n"
        "        BACHS_LINK_PLANS   = %s:monthly\n"
        "        BACHS_AMOUNT_PLANS = %s:monthly\n"
        "    (replace 'monthly' with the correct plan: %s)",
        data.get("charge_id"),
        link_id or "(none in payload)",
        amount_key or "(none)",
        meta,
        link_id or "<link-id-from-payload-above>",
        amount_key or "<amount>",
        ", ".join(sorted(PLAN_DAYS)),
    )
    return None, None, "unresolved"


def config_summary() -> dict:
    """What the running service actually has configured.

    For the GET health endpoint, so you can confirm an env var took effect
    without making a real payment to find out.
    """
    return {
        "plans": sorted(set(PLAN_DAYS)),
        "link_map": LINK_PLANS or "not configured",
        "amount_map": AMOUNT_PLANS or "not configured",
    }
