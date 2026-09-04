"""Company letterhead for print documents.

Every one of Flask's 12 print templates reads the CompanyProfile
configured in System Administration for its header:

    {{ company.company_name if company else 'Company Name' }}
    {{ company.address_line1 }} {{ company.city }}

React's print views had this hardcoded to "Enterprise Fleet Management
System" instead, so a client's own configured company name never
appeared on any document they printed.

Embedded in each detail endpoint's own payload rather than fetched
separately from /admin/company: that endpoint is gated on
company.view, a System Administration permission ordinary staff who
print documents have no reason to hold. Gating a printed letterhead
behind an admin permission would mean either handing out that
permission far too widely or shipping documents with no letterhead at
all for most users.

Shared here rather than copy-pasted per module so a change to what a
letterhead contains happens once, and so no module can drift into
reading a field name the model does not have -- which is exactly what
had already happened in ATD's own print payload (it read
`address_line`, a column that does not exist; the real one is
`address_line1`, so its printed address had always been blank).
"""


def company_letterhead():
    """The configured company, or {} when none is set up yet.

    Empty dict rather than None so every client can treat it the same
    way (`company.company_name || fallback`) without a null check, and
    so a fresh install with no CompanyProfile row prints a neutral
    placeholder rather than crashing.
    """
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    company = CompanyProfileService().get()
    if company is None:
        # The full SHAPE with nulls, not a bare {}.
        #
        # A print view reading company.company_name off an empty dict
        # gets undefined and renders a blank header; with an explicit
        # null it renders its "Company Name" placeholder, which is a
        # prompt to go and configure one. Same reasoning as
        # _company_json, and the two now agree.
        return {
            "company_name": None,
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "country": None,
            "phone": None,
            "email": None,
            "tin": None,
            "logo_url": None,
        }
    return {
        "company_name": company.company_name,
        "address_line1": company.address_line1,
        "address_line2": company.address_line2,
        "city": company.city,
        "country": company.country,
        "phone": company.phone,
        "email": company.email,
        "tin": company.tin,
        # The configured logo, as a URL every print view can render.
        #
        # Served by /api/v1/company/logo, which requires authentication
        # but NO permission code -- exactly the reasoning above. Gating
        # a printed letterhead behind company.view would mean handing
        # that admin permission to everyone who prints, or shipping
        # documents with a blank header for most users.
        #
        # None when unset, so a client renders its own fallback rather
        # than a broken image.
        "logo_url": ("/api/v1/company/logo"
                     if getattr(company, "logo_attachment_id", None)
                     else None),
    }
