from app.core.security.password import hash_password
from app.modules.document_config.models import DocumentType
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, permission_codes=()):
    role = Role(name="CfgRole")
    for code in permission_codes:
        module, action = code.split(".")
        perm = Permission(code=code, module=module, action=action)
        db.session.add(perm)
        role.permissions.append(perm)
    user = User(username="iris", email="i@example.com",
                password_hash=hash_password("pw123456"))
    user.roles.append(role)
    db.session.add_all([role, user])
    db.session.commit()
    client.post("/login", data={"username": "iris", "password": "pw123456"})
    return user


def test_doctypes_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/admin/document-types").status_code == 403


def test_doctypes_200_with_permission(client, db):
    _login(client, db, permission_codes=["doctype.view"])
    assert client.get("/admin/document-types").status_code == 200


def test_create_doctype_via_post(client, db):
    _login(client, db, permission_codes=["doctype.view", "doctype.create"])
    resp = client.post("/admin/document-types/new", data={
        "code": "TT", "name": "Trip Ticket", "requires_approval": "y",
        "auto_numbering": "y", "printable": "y",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert DocumentType.query.filter_by(code="TT").count() == 1


def test_schemes_pages(client, db):
    _login(client, db, permission_codes=["numbering.view", "numbering.create"])
    assert client.get("/admin/numbering-schemes").status_code == 200
    assert client.get("/admin/numbering-schemes/new").status_code == 200
