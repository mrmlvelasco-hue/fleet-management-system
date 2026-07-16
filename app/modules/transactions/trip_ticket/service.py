"""Trip Ticket service: creation (with driver-from-master toggle) plus
the shared submit/approve/reject/return/cancel lifecycle, plus the
release/complete physical-lifecycle actions specific to Trip Tickets."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.trip_ticket.models import TripTicket


class DriverRequiredError(Exception):
    pass


class InvalidTripStateError(Exception):
    pass


class TripTicketService(BaseTransactionService):
    model = TripTicket
    document_type_code = "TT"
    reference_table = "trip_tickets"

    def create(self, *, vehicle_id, destination, purpose, departure_datetime,
               odometer_out, user, driver_id=None, driver_name_manual=None,
               passengers=None):
        require_master = SystemParameterService().get(
            "REQUIRE_DRIVER_FROM_MASTER", default="YES")
        require_master = str(require_master).upper() in ("YES", "TRUE", "1")

        if require_master and not driver_id:
            raise DriverRequiredError(
                "Driver must be selected from Driver Master "
                "(REQUIRE_DRIVER_FROM_MASTER=YES).")
        if not require_master and not driver_id:
            driver_id = None  # manual mode: no master record used/created
        elif not require_master and driver_id:
            pass  # still allowed to pick a master driver even in manual mode

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        trip = TripTicket(
            document_number=doc_number, vehicle_id=vehicle_id,
            driver_id=driver_id,
            driver_name_manual=None if driver_id else driver_name_manual,
            destination=destination, purpose=purpose,
            departure_datetime=departure_datetime,
            odometer_out=odometer_out, passengers=passengers,
            status="DRAFT", requested_by=user.id if user else None)
        db.session.add(trip)
        db.session.commit()
        return trip

    def release(self, trip_id: int):
        trip = db.session.get(TripTicket, trip_id)
        if trip.approval_instance and trip.approval_instance.status != "APPROVED":
            raise InvalidTripStateError(
                "Trip Ticket must be APPROVED before release.")
        trip.status = "RELEASED"
        db.session.commit()
        return trip

    def complete(self, trip_id: int, odometer_in: int, return_datetime):
        trip = db.session.get(TripTicket, trip_id)
        trip.odometer_in = odometer_in
        trip.return_datetime = return_datetime
        trip.status = "COMPLETED"
        # Vehicle Master's Current Odometer priority (per the Vehicle
        # Module enhancement spec): Latest Completed Trip Ticket first —
        # same non-regressing safety check Maintenance Order already uses.
        if trip.vehicle and (trip.vehicle.current_odometer is None or
                             odometer_in > trip.vehicle.current_odometer):
            trip.vehicle.current_odometer = odometer_in
        db.session.commit()
        return trip
