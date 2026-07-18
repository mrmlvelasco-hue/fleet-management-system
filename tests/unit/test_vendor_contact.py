from app.modules.master_data.vendor.service import (
    VendorService, VendorContactService)


def test_create_vendor_contact(db):
    vendor = VendorService().create(code="VEND-CONTACT1", name="ABC Motors",
                                    vendor_type="SERVICES")
    contact = VendorContactService().create(
        vendor_id=vendor.id, contact_name="Juan Dela Cruz",
        tel_number="02-8123-4567", cel_number="0917-123-4567",
        email="juan@abcmotors.com", position="Account Manager")
    assert contact.vendor_id == vendor.id
    assert contact.contact_name == "Juan Dela Cruz"
    assert contact.position == "Account Manager"


def test_list_contacts_for_vendor(db):
    vendor = VendorService().create(code="VEND-CONTACT2", name="XYZ Supplies",
                                    vendor_type="GOODS")
    other_vendor = VendorService().create(code="VEND-CONTACT3", name="Other Supplier",
                                          vendor_type="GOODS")
    VendorContactService().create(vendor_id=vendor.id, contact_name="Contact A")
    VendorContactService().create(vendor_id=vendor.id, contact_name="Contact B")
    VendorContactService().create(vendor_id=other_vendor.id, contact_name="Contact C")

    contacts = VendorContactService().list_for_vendor(vendor.id)
    names = [c.contact_name for c in contacts]
    assert "Contact A" in names
    assert "Contact B" in names
    assert "Contact C" not in names


def test_delete_contact(db):
    vendor = VendorService().create(code="VEND-CONTACT4", name="Delete Test Vendor",
                                    vendor_type="GOODS")
    contact = VendorContactService().create(vendor_id=vendor.id, contact_name="To Delete")
    VendorContactService().delete(contact.id)
    remaining = VendorContactService().list_for_vendor(vendor.id)
    assert remaining == []


def test_contact_fields_are_optional_except_name(db):
    vendor = VendorService().create(code="VEND-CONTACT5", name="Minimal Contact Vendor",
                                    vendor_type="GOODS")
    contact = VendorContactService().create(vendor_id=vendor.id, contact_name="Just A Name")
    assert contact.tel_number is None
    assert contact.cel_number is None
    assert contact.email is None
    assert contact.position is None
