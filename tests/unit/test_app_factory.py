def test_app_factory_creates_testing_app(app):
    assert app.testing is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
