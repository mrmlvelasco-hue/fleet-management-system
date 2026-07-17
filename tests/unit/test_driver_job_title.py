from datetime import date

from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService


def test_driver_can_have_job_title(db):
    branch = BranchService().create(code="BR-JOBTITLE", name="Job Title Branch")
    driver = DriverService().create(
        employee_number="EMP-JOBTITLE1", first_name="Juan", last_name="Dela Cruz",
        license_number="LIC-JOBTITLE1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        job_title="Sales Representative")
    assert driver.job_title == "Sales Representative"


def test_job_title_is_optional(db):
    branch = BranchService().create(code="BR-JOBTITLE2", name="Job Title Branch 2")
    driver = DriverService().create(
        employee_number="EMP-JOBTITLE2", first_name="Ana", last_name="Reyes",
        license_number="LIC-JOBTITLE2", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    assert driver.job_title is None
