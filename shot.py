import sys, threading, time
from werkzeug.serving import make_server
from app import create_app
out=sys.argv[1]
app=create_app("development")
srv=make_server("127.0.0.1",5082,app,threaded=True)
threading.Thread(target=srv.serve_forever,daemon=True).start(); time.sleep(1.5)
from playwright.sync_api import sync_playwright
with app.app_context():
    from app.modules.master_data.vehicle.models import Vehicle
    VID=Vehicle.query.filter_by(plate_number="DEMO-1234").first().id
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_context(viewport={"width":1100,"height":1400}).new_page()
    pg.goto("http://127.0.0.1:5082/login")
    pg.fill('input[name="username"]',"admin"); pg.fill('input[name="password"]',"Testpass123!")
    pg.click('button[type="submit"]'); pg.wait_for_load_state("networkidle")
    pg.goto(f"http://127.0.0.1:5082/master/vehicles/{VID}/print")
    pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(600)
    pg.screenshot(path=out, full_page=True)
    b.close()
srv.shutdown()
