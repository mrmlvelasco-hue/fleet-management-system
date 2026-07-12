from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def test_vehicle_brand_and_model_links_appear_in_sidebar(client, db):
    """Regression: same pattern as the missing Maintenance Types link —
    Vehicle Brand/Model had working routes/permissions but no sidebar link."""
    role = Role(name="BrandSidebarRole")
    for code in ["vehiclebrand.view", "vehiclemodel.view"]:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="salma", email="salma@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "salma", "password": "pw123456"})

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Vehicle Brands" in resp.data
    assert b"Vehicle Models" in resp.data
    assert b'href="/master/vehicle-brands"' in resp.data
    assert b'href="/master/vehicle-models"' in resp.data
