# Faculty AI — GitHub Pages → Flask → Supabase

## Architecture
- `frontend/` → publish to GitHub Pages
- `backend/` → deploy as a Python web service (for example Render or Azure App Service)
- Supabase PostgreSQL → database only; DB password stays in backend environment variables

## 1. Deploy backend
Deploy `backend/` and set:
- `DATABASE_URL` = Supabase PostgreSQL URI
- `FLASK_SECRET_KEY` = long random secret

Start command:
`gunicorn --bind 0.0.0.0:$PORT app:app`

Test:
- `/` should return API status JSON
- `/health` should return database status

## 2. Connect GitHub frontend
Edit only `frontend/config.js` and replace:
`https://YOUR-FLASK-APP.onrender.com`
with your deployed Flask URL.

Then publish the contents of `frontend/` to the GitHub Pages repository root.

Expected URLs:
- Main: `https://fahim07cse.github.io/flask_test/`
- Admin: `https://fahim07cse.github.io/flask_test/admin/`

## Security
- Never put `DATABASE_URL`, database password, or a Supabase service-role key in GitHub frontend files.
- The CORS configuration only allows requests from `https://fahim07cse.github.io`.
- Admin session cookies are configured for HTTPS cross-site requests (`SameSite=None`, `Secure`).

## What changed
1. Removed direct Supabase browser connection from both HTML pages (already converted in the Azure version).
2. Both pages use `azure_db_client.js`, now pointed to the Flask URL in `config.js`.
3. Fetch uses `credentials: include` so admin session cookies work across GitHub Pages and the Flask host.
4. Flask CORS allows the GitHub Pages origin.
5. Flask uses `DATABASE_URL` to connect privately to Supabase PostgreSQL.
6. Legacy `admin_activity_log_simple` remains the database table name for this Supabase test.
