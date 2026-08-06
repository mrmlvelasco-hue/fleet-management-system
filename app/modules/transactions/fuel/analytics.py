"""Fuel analytics and anomaly detection.

Consumption reporting is the visible part; anomaly detection is usually
where the money is. Fuel is the line item where fleet losses concentrate,
and the patterns worth catching are specific:

  * a fill larger than the tank can physically hold
  * two fills close together in time (a card used twice, or shared)
  * efficiency suddenly worsening against the vehicle's own history
  * a price per litre well outside the prevailing rate

Every check compares a vehicle against ITS OWN history rather than a
fleet-wide average. A 3-tonne truck and a sedan have nothing to say about
each other's efficiency, and a fleet-wide threshold would flag every
truck and miss every sedan.

Anomalies are advisory. They flag a transaction for a human to look at;
nothing is blocked or reversed automatically, because the honest
explanations (a long uphill route, a genuinely replaced odometer, a
legitimate jerrycan fill) are common enough that automatic action would
generate more harm than the losses it prevents.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.core.request_cache import request_cached


class FuelAnomalyCodes:
    OVER_TANK = "OVER_TANK"
    RAPID_REFILL = "RAPID_REFILL"
    EFFICIENCY_DROP = "EFFICIENCY_DROP"
    PRICE_OUTLIER = "PRICE_OUTLIER"
    ODOMETER_SUSPECT = "ODOMETER_SUSPECT"

    LABELS = {
        OVER_TANK: "Fill exceeds tank capacity",
        RAPID_REFILL: "Refilled again very soon after",
        EFFICIENCY_DROP: "Efficiency well below this vehicle's norm",
        PRICE_OUTLIER: "Price per litre outside the usual range",
        ODOMETER_SUSPECT: "Odometer reading not trustworthy",
    }


class FuelAnalyticsService:

    # A fill this far above the vehicle's largest previous fill is
    # physically questionable. Generous, because tanks are refilled from
    # varying levels and a jerrycan top-up is legitimate.
    TANK_TOLERANCE = Decimal("1.35")
    RAPID_REFILL_HOURS = 6
    # Efficiency must fall this far below the vehicle's own average
    # before it is worth a person's attention. Tight enough to catch
    # siphoning, loose enough to ignore a hilly week.
    EFFICIENCY_DROP_RATIO = Decimal("0.65")
    PRICE_TOLERANCE = Decimal("0.25")   # ±25% of the fleet median

    def detect_anomalies(self, txn, commit=True):
        """Flag a single transaction. Returns the list of codes set."""
        from app.modules.transactions.fuel.models import FuelTransaction

        flags = []

        if txn.odometer_status == "SUSPECT":
            flags.append(FuelAnomalyCodes.ODOMETER_SUSPECT)

        history = (FuelTransaction.query
                  .filter(FuelTransaction.vehicle_id == txn.vehicle_id,
                          FuelTransaction.id != (txn.id or -1),
                          FuelTransaction.transaction_date
                          < txn.transaction_date)
                  .order_by(FuelTransaction.transaction_date.desc())
                  .limit(20).all())

        if history and txn.litres:
            biggest = max((Decimal(str(h.litres)) for h in history
                          if h.litres), default=None)
            if biggest and Decimal(str(txn.litres)) > biggest * self.TANK_TOLERANCE:
                flags.append(FuelAnomalyCodes.OVER_TANK)

            previous = history[0]
            gap = txn.transaction_date - previous.transaction_date
            if gap < timedelta(hours=self.RAPID_REFILL_HOURS):
                flags.append(FuelAnomalyCodes.RAPID_REFILL)

            # Compared against the vehicle's OWN average, not the fleet's.
            past_efficiency = [Decimal(str(h.km_per_litre)) for h in history
                              if h.km_per_litre]
            if txn.km_per_litre and len(past_efficiency) >= 3:
                average = sum(past_efficiency) / len(past_efficiency)
                if (average > 0 and Decimal(str(txn.km_per_litre))
                        < average * self.EFFICIENCY_DROP_RATIO):
                    flags.append(FuelAnomalyCodes.EFFICIENCY_DROP)

        if txn.price_per_litre:
            median = self._recent_median_price(txn.transaction_date)
            if median:
                price = Decimal(str(txn.price_per_litre))
                if (price > median * (1 + self.PRICE_TOLERANCE)
                        or price < median * (1 - self.PRICE_TOLERANCE)):
                    flags.append(FuelAnomalyCodes.PRICE_OUTLIER)

        txn.anomaly_flags = ",".join(flags) if flags else None
        if commit:
            db.session.commit()
        return flags

    def _recent_median_price(self, on_date, window_days=30):
        """Fleet median price around this date.

        Median rather than mean: a single mistyped price of 9,999 would
        drag a mean far enough to mask every real outlier.
        """
        from app.modules.transactions.fuel.models import FuelTransaction
        start = on_date - timedelta(days=window_days)
        prices = [Decimal(str(p[0])) for p in
                 db.session.query(FuelTransaction.price_per_litre)
                 .filter(FuelTransaction.price_per_litre.isnot(None),
                        FuelTransaction.transaction_date >= start,
                        FuelTransaction.transaction_date <= on_date).all()]
        if len(prices) < 5:
            return None          # too little data to call anything an outlier
        prices.sort()
        middle = len(prices) // 2
        if len(prices) % 2:
            return prices[middle]
        return (prices[middle - 1] + prices[middle]) / 2

    def exceptions_count(self, user=None) -> dict:
        """How many transactions the Exceptions view will actually show.

        Deliberately ALL TIME, not scoped to the 30-day summary() window:
        an unresolved SUSPECT reading from two months ago is still a real
        open problem (arguably a more urgent one, since it's been sitting
        longer), and the count driving a badge must match what clicking
        through actually reveals. Using summary()'s 30-day figure here
        previously meant an older flagged transaction could show "0" on
        the badge while the Exceptions tab still listed it -- confirmed
        directly: a flagged row from 60 days back showed 0+0 on the badge
        while the list itself returned 1.
        """
        from app.modules.transactions.fuel.models import FuelTransaction
        query = FuelTransaction.query.filter(db.or_(
            FuelTransaction.anomaly_flags.isnot(None),
            FuelTransaction.odometer_status.in_(("SUSPECT", "MISSING"))))
        return {"count": query.count()}

    @request_cached("fuel_summary")
    def summary(self, user=None, days=30) -> dict:
        """Fleet-wide fuel summary for the given window.

        If the window contains nothing but the fleet DOES have fuel
        history, this falls back to reporting ALL of it rather than
        showing 0 fills / P0 spend next to a transaction list that
        plainly has rows in it -- which is exactly the kind of
        misleading zero this project avoids everywhere else (a missing
        odometer reading shows as missing, not 0; a KPI card with no
        baseline shows no percentage, not 0%). A demo or a freshly
        imported statement dated slightly more than `days` ago must not
        look like the import silently failed.

        `is_all_time` tells the caller whether the fallback engaged, so
        the UI can label the figure honestly ("All time" instead of
        "Last 30 days") rather than let a wider window pass silently as
        if it were the one that was asked for.
        """
        from app.modules.transactions.fuel.models import FuelTransaction
        from datetime import date
        start = date.today() - timedelta(days=days)

        rows = (FuelTransaction.query
               .filter(FuelTransaction.transaction_date >= start).all())
        is_all_time = False
        if not rows and FuelTransaction.query.first() is not None:
            rows = FuelTransaction.query.all()
            is_all_time = True
        if not rows:
            return {"fills": 0, "litres": 0, "spend": 0, "avg_price": 0,
                   "avg_kmpl": None, "flagged": 0, "untrusted_odometer": 0,
                   "needs_review": 0, "is_all_time": False}

        litres = sum(Decimal(str(r.litres or 0)) for r in rows)
        spend = sum(Decimal(str(r.total_amount or 0)) for r in rows)
        efficiencies = [Decimal(str(r.km_per_litre)) for r in rows
                       if r.km_per_litre]
        return {
            "fills": len(rows),
            "litres": round(litres, 2),
            "spend": round(spend, 2),
            "avg_price": round(spend / litres, 2) if litres else 0,
            # None, not 0, when nothing is measurable -- 0 km/L would read
            # as catastrophic efficiency rather than "not yet known".
            "avg_kmpl": (round(sum(efficiencies) / len(efficiencies), 2)
                        if efficiencies else None),
            "flagged": sum(1 for r in rows if r.anomaly_flags),
            "untrusted_odometer": sum(
                1 for r in rows
                if r.odometer_status in ("SUSPECT", "MISSING")),
            # A row can carry BOTH an anomaly flag and a suspect odometer
            # at once, so flagged + untrusted_odometer would double-count
            # it. This is the genuine "needs a person to look at it"
            # total, used by the dashboard tile.
            "needs_review": sum(
                1 for r in rows
                if r.anomaly_flags or r.odometer_status in ("SUSPECT", "MISSING")),
            "is_all_time": is_all_time,
        }

    @request_cached("fuel_by_vehicle")
    def by_vehicle(self, user=None, days=90, limit=50) -> list:
        """Per-vehicle consumption, worst efficiency first."""
        from app.modules.transactions.fuel.models import FuelTransaction
        from datetime import date
        start = date.today() - timedelta(days=days)

        buckets = {}
        for row in (FuelTransaction.query
                   .filter(FuelTransaction.transaction_date >= start).all()):
            bucket = buckets.setdefault(row.vehicle_id, {
                "vehicle": row.vehicle, "fills": 0,
                "litres": Decimal(0), "spend": Decimal(0),
                "distance": 0, "flagged": 0})
            bucket["fills"] += 1
            bucket["litres"] += Decimal(str(row.litres or 0))
            bucket["spend"] += Decimal(str(row.total_amount or 0))
            bucket["distance"] += row.distance_km or 0
            if row.anomaly_flags:
                bucket["flagged"] += 1

        out = []
        for data in buckets.values():
            kmpl = (Decimal(data["distance"]) / data["litres"]
                   if data["litres"] and data["distance"] else None)
            out.append({
                "vehicle": data["vehicle"],
                "fills": data["fills"],
                "litres": round(data["litres"], 2),
                "spend": round(data["spend"], 2),
                "distance": data["distance"],
                "km_per_litre": round(kmpl, 2) if kmpl else None,
                "cost_per_km": (round(data["spend"] / data["distance"], 2)
                               if data["distance"] else None),
                "flagged": data["flagged"],
            })
        # Vehicles with no measurable efficiency sort last: they are a
        # data problem, not an efficiency problem, and mixing them into
        # the "worst performers" list would be misleading.
        out.sort(key=lambda r: (r["km_per_litre"] is None,
                               r["km_per_litre"] or 0))
        return out[:limit]

    def flagged_transactions(self, limit=100):
        from app.modules.transactions.fuel.models import FuelTransaction
        return (FuelTransaction.query
               .filter(db.or_(FuelTransaction.anomaly_flags.isnot(None),
                             FuelTransaction.odometer_status.in_(
                                 ("SUSPECT", "MISSING"))))
               .order_by(FuelTransaction.transaction_date.desc())
               .limit(limit).all())
