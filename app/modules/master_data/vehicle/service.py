"""Vehicle master service."""
from app.extensions import db
from app.modules.master_data.vehicle.models import Vehicle


class DuplicateVehicleError(Exception):
    pass


class VehicleService:
    def create(self, vehicle_type_id, brand, model, year,
               branch_id, conduction_number=None, plate_number=None,
               **kwargs):
        if conduction_number and Vehicle.query.filter_by(
                conduction_number=conduction_number).first():
            raise DuplicateVehicleError(
                f"Conduction number '{conduction_number}' already exists.")
        if plate_number and Vehicle.query.filter_by(
                plate_number=plate_number).first():
            raise DuplicateVehicleError(
                f"Plate number '{plate_number}' already exists.")
        obj = Vehicle(
            vehicle_type_id=vehicle_type_id, brand=brand, model=model,
            year=year, branch_id=branch_id,
            conduction_number=conduction_number,
            plate_number=plate_number, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def assign_plate(self, vehicle_id, plate_number):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.plate_number = plate_number
            db.session.commit()
        return obj

    def update_odometer(self, vehicle_id, reading):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.current_odometer = reading
            db.session.commit()
        return obj

    def assign_driver(self, vehicle_id, driver_id):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.assigned_driver_id = driver_id
            db.session.commit()
        return obj

    def get(self, record_id, include_inactive=True):
        obj = db.session.get(Vehicle, record_id)
        if obj is None or (not include_inactive and not obj.is_active):
            return None
        return obj

    def list(self, include_inactive=False, branch_id=None):
        q = Vehicle.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        return q.order_by(Vehicle.brand, Vehicle.model).all()

    def deactivate(self, record_id):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            obj.is_active = False
            obj.status = "INACTIVE"
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            obj.is_active = True
            obj.status = "ACTIVE"
            db.session.commit()
