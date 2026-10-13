# NetPulse: broadband recharge and revenue platform

Flask + SQLite. Separate sign-in pages for customers, staff, and admin, plus customer self-registration.

## Run locally
    pip install -r requirements.txt
    python app.py            # data is kept between restarts
    python app.py --reset    # start over with fresh demo data

Open http://127.0.0.1:5000. Sign-in pages: /customer/login, /staff/login, /admin/login. Register: /customer/register.

Demo logins: NP1001 / welcome123 (customer), staff1 / staff123, admin / admin123.

## Deploy (Render)
1. Push this folder to a GitHub repo.
2. Render > New > Web Service > connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app --workers 1`
5. Add environment variable `SECRET_KEY` with a long random value.

Free hosts reset the disk on redeploy or sleep, so SQLite data (registrations) may be lost. Use a paid disk or a hosted database for real use.
