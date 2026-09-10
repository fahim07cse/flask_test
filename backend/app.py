import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, jsonify, request, session
from flask_cors import CORS
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": "https://fahim07cse.github.io"}},
    supports_credentials=True,
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-before-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True

TABLE_ALIASES = {
    "ai_faculty_data": "ai_faculty_data",
    "admin_login_users": "admin_login_users",
    # Old front-end name is transparently mapped to the new Azure table.
    "admin_activity_log_simple": "admin_activity_log_simple",
}

TABLE_COLUMNS = {
    "ai_faculty_data": {
        "id", "start_time", "completion_time", "email", "name", "last_modified_time",
        "department", "research_groups", "keywords", "showcase_interest", "ai_research",
        "enterprise_projects", "future_directions", "external_organisations", "support_training",
        "additional_comments", "consent", "is_deleted", "deleted_at", "deleted_by"
    },
    "admin_login_users": {
        "id", "username", "password_hash", "role", "is_active", "created_at", "updated_at"
    },
    "admin_activity_log_simple": {
        "id", "admin_username", "action", "record_id", "details", "created_at"
    },
}

PUBLIC_TABLES = {"ai_faculty_data"}
ADMIN_ONLY_TABLES = {"admin_login_users", "admin_activity_log_simple"}


def get_conn():
    connection_string = os.environ.get("AZURE_POSTGRES_CONNECTION_STRING") or os.environ.get("DATABASE_URL")
    if connection_string:
        return psycopg.connect(connection_string, row_factory=dict_row)

    required = {
        "host": os.environ.get("SUPABASE_POSTGRES_HOST"),
        "dbname": os.environ.get("SUPABASE_POSTGRES_DATABASE", "postgres"),
        "user": os.environ.get("SUPABASE_POSTGRES_USER", "postgres"),
        "password": os.environ.get("SUPABASE_POSTGRES_PASSWORD"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing Supabase PostgreSQL settings: " + ", ".join(missing))

    return psycopg.connect(
        host=required["host"],
        dbname=required["dbname"],
        user=required["user"],
        password=required["password"],
        port=int(os.environ.get("SUPABASE_POSTGRES_PORT", "5432")),
        sslmode=os.environ.get("SUPABASE_POSTGRES_SSLMODE", "require"),
        row_factory=dict_row,
    )


def json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def clean_rows(rows):
    return [{key: json_safe(value) for key, value in row.items()} for row in rows]


def resolve_table(frontend_name):
    table = TABLE_ALIASES.get(frontend_name)
    if not table:
        raise ValueError("Table is not allowed")
    return table


def is_admin_logged_in():
    return bool(session.get("admin_username"))


def validate_columns(table, columns):
    allowed = TABLE_COLUMNS[table]
    for col in columns:
        if col not in allowed:
            raise ValueError(f"Column not allowed: {col}")


def parse_filters(table):
    allowed = TABLE_COLUMNS[table]
    clauses = []
    values = []
    for key in request.args:
        if key.startswith("eq_"):
            col = key[3:]
            if col not in allowed:
                raise ValueError(f"Filter column not allowed: {col}")
            raw = request.args.get(key)
            if raw == "null":
                clauses.append(sql.SQL("{} IS NULL").format(sql.Identifier(col)))
            else:
                if raw.lower() == "true": value = True
                elif raw.lower() == "false": value = False
                else: value = raw
                clauses.append(sql.SQL("{} = %s").format(sql.Identifier(col)))
                values.append(value)
        elif key.startswith("ilike_"):
            col = key[6:]
            if col not in allowed:
                raise ValueError(f"Filter column not allowed: {col}")
            clauses.append(sql.SQL("{} ILIKE %s").format(sql.Identifier(col)))
            values.append(request.args.get(key))
    return clauses, values


def require_admin_for_table(table):
    if table in ADMIN_ONLY_TABLES and not is_admin_logged_in():
        return jsonify(error="Admin login required"), 401
    return None


@app.get("/")
def api_home():
    return jsonify(status="ok", service="Faculty AI Flask API")


@app.get("/health")
def health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                row = cur.fetchone()
        return jsonify(status="ok", database=row["ok"] == 1)
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


@app.route("/api/table/<frontend_table>", methods=["GET", "POST", "PATCH"])
def table_api(frontend_table):
    try:
        table = resolve_table(frontend_table)
        blocked = require_admin_for_table(table)
        if blocked:
            return blocked

        if request.method == "GET":
            requested = request.args.get("select", "*")
            if requested == "*":
                select_sql = sql.SQL("*")
            else:
                cols = [c.strip() for c in requested.split(",") if c.strip()]
                validate_columns(table, cols)
                # Never expose password hashes to the browser.
                if table == "admin_login_users" and "password_hash" in cols:
                    raise ValueError("password_hash cannot be selected")
                select_sql = sql.SQL(", ").join(map(sql.Identifier, cols))

            clauses, values = parse_filters(table)
            query = sql.SQL("SELECT {} FROM public.{}").format(select_sql, sql.Identifier(table))
            if clauses:
                query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)

            order_arg = request.args.get("order")
            if order_arg:
                parts = order_arg.rsplit(".", 1)
                col = parts[0]
                direction = parts[1].lower() if len(parts) > 1 else "asc"
                validate_columns(table, [col])
                query += sql.SQL(" ORDER BY {} {}").format(
                    sql.Identifier(col), sql.SQL("DESC" if direction == "desc" else "ASC")
                )

            limit = request.args.get("limit")
            if limit:
                query += sql.SQL(" LIMIT %s")
                values.append(max(1, min(int(limit), 5000)))

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, values)
                    rows = cur.fetchall()
            return jsonify(data=clean_rows(rows))

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not payload:
            return jsonify(error="A JSON object is required"), 400

        allowed = TABLE_COLUMNS[table]
        safe_payload = {k: v for k, v in payload.items() if k in allowed}
        if table == "admin_login_users":
            safe_payload.pop("password_hash", None)
        if not safe_payload:
            return jsonify(error="No allowed columns supplied"), 400

        if request.method == "POST":
            if table == "admin_activity_log_simple" and not is_admin_logged_in():
                return jsonify(error="Admin login required"), 401
            cols = list(safe_payload.keys())
            vals = [safe_payload[c] for c in cols]
            returning = request.args.get("returning", "false").lower() == "true"
            query = sql.SQL("INSERT INTO public.{} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() for _ in cols),
            )
            if returning:
                query += sql.SQL(" RETURNING *")
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, vals)
                    rows = cur.fetchall() if returning else []
                conn.commit()
            return jsonify(data=clean_rows(rows))

        # PATCH
        clauses, filter_values = parse_filters(table)
        if not clauses:
            return jsonify(error="Update requires a filter"), 400

        cols = list(safe_payload.keys())
        update_values = [safe_payload[c] for c in cols]
        set_sql = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(c)) for c in cols
        )
        returning = request.args.get("returning", "false").lower() == "true"
        query = sql.SQL("UPDATE public.{} SET {} WHERE {}").format(
            sql.Identifier(table), set_sql, sql.SQL(" AND ").join(clauses)
        )
        if returning:
            query += sql.SQL(" RETURNING *")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, update_values + filter_values)
                rows = cur.fetchall() if returning else []
            conn.commit()
        return jsonify(data=clean_rows(rows))

    except Exception as exc:
        return jsonify(error=str(exc)), 400


RPC_FUNCTIONS = {
    "verify_admin_login": ["p_username", "p_password"],
    "list_admin_login_users": ["p_admin_username", "p_admin_password"],
    "add_admin_login_user": ["p_admin_username", "p_admin_password", "p_username", "p_password", "p_role"],
    "set_admin_login_user_active": ["p_admin_username", "p_admin_password", "p_user_id", "p_active"],
    "change_admin_password": ["p_admin_username", "p_admin_password", "p_new_password"],
}


@app.post("/api/rpc/<function_name>")
def rpc_api(function_name):
    if function_name not in RPC_FUNCTIONS:
        return jsonify(error="RPC function is not allowed"), 404

    args = request.get_json(silent=True) or {}
    names = RPC_FUNCTIONS[function_name]
    try:
        values = [args.get(name) for name in names]
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in names)
        query = sql.SQL("SELECT * FROM public.{}({})").format(
            sql.Identifier(function_name), placeholders
        )
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
                rows = cur.fetchall()

        data = clean_rows(rows)
        if function_name == "verify_admin_login":
            if data:
                session["admin_username"] = data[0]["username"]
                session["admin_role"] = data[0]["role"]
                session["admin_id"] = data[0]["id"]
            else:
                session.clear()
        return jsonify(data=data)
    except Exception as exc:
        return jsonify(error=str(exc)), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
