"""Odometer validation for fuel transactions.

Fleet-card odometers are typed by a driver at a pump, and a meaningful
share are wrong. The failure modes are consistent enough to detect:

  * a DROPPED DIGIT     65,430 typed as 6,543
  * an EXTRA DIGIT      65,430 typed as 654,300
  * TRANSPOSED digits   65,430 typed as 65,340
  * the LITRE figure    45 typed into the odometer box
  * simply BLANK

Why it matters more than it first appears: consumption is distance
between consecutive fills, so ONE bad reading corrupts TWO fills -- its
own and the next. Left unchecked, a handful of typos makes a whole
fleet's efficiency reporting untrustworthy, which is worse than having
none, because people act on it.

Principle throughout: never discard, never silently "fix". The money was
really spent, so the transaction always stands. A suspect reading is
quarantined from CONSUMPTION only, flagged with a reason, and given a
suggested correction where the error pattern is unambiguous -- a person
confirms it. Guessing silently would be indistinguishable from the bug
we are trying to eliminate.
"""
from decimal import Decimal

from app.extensions import db


class OdometerValidationService:

    # A reading below the last known one is either a typo or a replaced
    # instrument cluster. Never computed from, always flagged.
    #
    # Upper bound on plausible distance between two fills. A tank rarely
    # exceeds ~1,200 km of range; 3,000 allows for a missed fill or two
    # without waving through a transposition that adds 100,000 km.
    MAX_KM_BETWEEN_FILLS = 3000

    # Below this, a "distance" is more likely a duplicate entry or the
    # same fill split across two card swipes than real travel.
    MIN_KM_BETWEEN_FILLS = 1

    def _last_good(self, vehicle_id, before_date, exclude_id=None):
        """The most recent trustworthy reading before this fill.

        Deliberately skips SUSPECT rows: chaining from a bad reading
        would propagate the error forward instead of containing it.
        """
        from app.modules.transactions.fuel.models import FuelTransaction
        query = (FuelTransaction.query
                .filter(FuelTransaction.vehicle_id == vehicle_id,
                       FuelTransaction.transaction_date < before_date,
                       FuelTransaction.odometer_used.isnot(None),
                       FuelTransaction.odometer_status.in_(("OK", "CORRECTED")))
                .order_by(FuelTransaction.transaction_date.desc()))
        if exclude_id:
            query = query.filter(FuelTransaction.id != exclude_id)
        return query.first()

    def _suggest_correction(self, reported, previous):
        """Propose a plausible true value, or None.

        Only returns a suggestion when the arithmetic makes the error
        obvious -- appending or removing a digit, or swapping an adjacent
        pair, that lands the reading in a sensible range. Anything less
        clear-cut is left for a person, because a confident wrong guess
        is worse than an honest "check this".
        """
        if reported is None or previous is None:
            return None

        candidates = set()
        text = str(int(reported))

        # Dropped digit: the driver typed 6543 for 65430.
        for digit in "0123456789":
            candidates.add(int(text + digit))          # missing at the end
            candidates.add(int(digit + text))          # missing at the front
        # Extra digit: 654300 for 65430.
        if len(text) > 1:
            candidates.add(int(text[:-1]))
            candidates.add(int(text[1:]))
        # Adjacent transposition: 65340 for 65430.
        for i in range(len(text) - 1):
            swapped = list(text)
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            candidates.add(int("".join(swapped)))

        plausible = [
            c for c in candidates
            if previous + self.MIN_KM_BETWEEN_FILLS
               <= c <= previous + self.MAX_KM_BETWEEN_FILLS
        ]
        if not plausible:
            return None                 # no idea -- a person decides
        if len(plausible) == 1:
            return plausible[0]

        # Several candidates can be effectively the SAME answer. A dropped
        # final digit on 6,543 yields 65,431...65,439 -- nine candidates
        # spanning 8 km, which for consumption purposes is one answer, not
        # nine. But the same reading also admits 66,543 (a dropped LEADING
        # digit), which is a genuinely different hypothesis 1,100 km away.
        #
        # So: group the candidates into tight clusters and accept only
        # when one clearly dominates. A cluster holding most of the
        # candidates and spanning a negligible distance is a real answer;
        # an even split between distant candidates is a coin toss, and
        # this returns None rather than flip it.
        plausible.sort()
        clusters, current = [], [plausible[0]]
        for value in plausible[1:]:
            if value - current[-1] <= 20:
                current.append(value)
            else:
                clusters.append(current)
                current = [value]
        clusters.append(current)

        clusters.sort(key=len, reverse=True)
        biggest = clusters[0]
        if len(biggest) < 0.6 * len(plausible):
            return None            # no dominant hypothesis -- ask a person
        if len(clusters) > 1 and len(clusters[1]) == len(biggest):
            return None            # tied -- picking either would be guessing
        return biggest[len(biggest) // 2]


    def _compute_consumption(self, txn, prev_odo):
        """Distance, km/L and cost/km -- only from a trustworthy pair."""
        txn.distance_km = None
        txn.km_per_litre = None
        txn.cost_per_km = None
        if txn.odometer_used is None or prev_odo is None:
            return
        distance = txn.odometer_used - prev_odo
        if distance < self.MIN_KM_BETWEEN_FILLS:
            return
        txn.distance_km = distance
        litres = Decimal(str(txn.litres or 0))
        if litres > 0:
            txn.km_per_litre = round(Decimal(distance) / litres, 3)
            if txn.total_amount:
                txn.cost_per_km = round(
                    Decimal(str(txn.total_amount)) / distance, 4)

    def validate(self, txn, commit=True):
        """Classify this transaction's odometer and compute consumption.

        Sets odometer_status to one of:
          OK        -- trustworthy, consumption computed
          CORRECTED -- an unambiguous typo, corrected value used
          SUSPECT   -- implausible; kept, but excluded from consumption
          MISSING   -- no reading supplied
        """
        reported = txn.odometer_reported
        previous = self._last_good(txn.vehicle_id, txn.transaction_date,
                                  exclude_id=txn.id)
        prev_odo = previous.odometer_used if previous else None

        # A person has already ruled on this reading. Re-validation may
        # recompute the DISTANCE from a corrected predecessor, but must
        # not overturn their decision about the reading itself.
        if txn.odometer_confirmed:
            txn.odometer_used = txn.odometer_reported
            txn.odometer_status = "CORRECTED"
            self._compute_consumption(txn, prev_odo)
            if commit:
                db.session.commit()
            return txn

        txn.distance_km = None
        txn.km_per_litre = None
        txn.cost_per_km = None

        if reported is None:
            txn.odometer_status = "MISSING"
            txn.odometer_used = None
            txn.odometer_note = ("No odometer reading was captured, so "
                                "consumption cannot be calculated for this "
                                "fill.")
        elif prev_odo is None:
            # First trustworthy fill for this vehicle: nothing to compare
            # against, so it is accepted as a baseline but yields no
            # consumption figure. That is a real limitation, not a fault.
            txn.odometer_status = "OK"
            txn.odometer_used = reported
            txn.odometer_note = ("First recorded fill for this vehicle — "
                                "used as the baseline; consumption starts "
                                "from the next fill.")
        else:
            distance = reported - prev_odo
            if distance < 0:
                suggestion = self._suggest_correction(reported, prev_odo)
                if suggestion:
                    txn.odometer_status = "CORRECTED"
                    txn.odometer_used = suggestion
                    txn.odometer_note = (
                        f"Reading {reported:,} is below the previous "
                        f"{prev_odo:,}. Corrected to {suggestion:,} "
                        f"(likely a mistyped digit).")
                else:
                    txn.odometer_status = "SUSPECT"
                    txn.odometer_used = None
                    txn.odometer_note = (
                        f"Reading {reported:,} is LOWER than the previous "
                        f"{prev_odo:,}. Excluded from consumption until "
                        f"confirmed.")
            elif distance > self.MAX_KM_BETWEEN_FILLS:
                suggestion = self._suggest_correction(reported, prev_odo)
                if suggestion:
                    txn.odometer_status = "CORRECTED"
                    txn.odometer_used = suggestion
                    txn.odometer_note = (
                        f"Reading {reported:,} implies {distance:,} km since "
                        f"the last fill. Corrected to {suggestion:,} "
                        f"(likely a mistyped digit).")
                else:
                    txn.odometer_status = "SUSPECT"
                    txn.odometer_used = None
                    txn.odometer_note = (
                        f"Reading {reported:,} implies {distance:,} km since "
                        f"the last fill, which is implausible. Excluded from "
                        f"consumption until confirmed.")
            else:
                txn.odometer_status = "OK"
                txn.odometer_used = reported
                txn.odometer_note = None

        self._compute_consumption(txn, prev_odo)

        if commit:
            db.session.commit()
        return txn

    def revalidate_vehicle(self, vehicle_id):
        """Re-run validation across a vehicle's whole history, oldest
        first.

        Needed because correcting one reading changes the baseline for
        every fill after it -- fixing a typo in March should repair
        April's consumption too, not leave it wrong.
        """
        from app.modules.transactions.fuel.models import FuelTransaction
        rows = (FuelTransaction.query
               .filter_by(vehicle_id=vehicle_id)
               .order_by(FuelTransaction.transaction_date.asc()).all())
        for row in rows:
            self.validate(row, commit=False)
        db.session.commit()
        return len(rows)

    def accept_reported(self, txn):
        """A person has confirmed a flagged reading is genuinely correct
        (a replaced odometer, or a long trip). Trust them, record that a
        human decided, and repair everything downstream."""
        txn.odometer_used = txn.odometer_reported
        txn.odometer_status = "CORRECTED"
        txn.odometer_confirmed = True
        txn.odometer_note = "Confirmed correct by a user."
        db.session.commit()
        self.revalidate_vehicle(txn.vehicle_id)
        return txn
