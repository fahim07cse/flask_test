import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, jsonify, request
from flask_cors import CORS
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


app = Flask(__name__)

# GitHub Pages -> Render API
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": "https://fahim07cse.github.io"
        }
    },
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PATCH", "OPTIONS"],
)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-before-production"
)


TABLE_ALIASES = {
    "ai_faculty_data": "ai_faculty_data",
    "admin_login_users": "admin_login_users",
    "admin_activity_log_simple": "admin_activity_log_simple",
}


TABLE_COLUMNS = {
    "ai_faculty_data": {
        "id",
        "start_time",
        "completion_time",
        "email",
        "name",
        "last_modified_time",
        "department",
        "research_groups",
        "keywords",
        "showcase_interest",
        "ai_research",
        "enterprise_projects",
        "future_directions",
        "external_organisations",
        "support_training",
        "additional_comments",
        "consent",
        "is_deleted",
        "deleted_at",
        "deleted_by",
    },

    "admin_login_users": {
        "id",
        "username",
        "password_hash",
        "role",
        "is_active",
        "created_at",
        "updated_at",
    },

    "admin_activity_log_simple": {
        "id",
        "admin_username",
        "action",
        "record_id",
        "details",
        "created_at",
    },
}


ADMIN_ONLY_TABLES = {
    "admin_login_users",
    "admin_activity_log_simple",
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_conn():

    connection_string = os.environ.get("DATABASE_URL")

    if not connection_string:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg.connect(
        connection_string,
        row_factory=dict_row
    )


# ---------------------------------------------------------
# JSON CLEANING
# ---------------------------------------------------------

def json_safe(value):

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return value


def clean_rows(rows):

    return [
        {
            key: json_safe(value)
            for key, value in row.items()
        }
        for row in rows
    ]


# ---------------------------------------------------------
# TABLE VALIDATION
# ---------------------------------------------------------

def resolve_table(frontend_name):

    table = TABLE_ALIASES.get(frontend_name)

    if not table:
        raise ValueError("Table is not allowed")

    return table


def validate_columns(table, columns):

    allowed = TABLE_COLUMNS[table]

    for column in columns:

        if column not in allowed:
            raise ValueError(
                f"Column not allowed: {column}"
            )


# ---------------------------------------------------------
# TOKEN AUTH
# ---------------------------------------------------------

ADMIN_TOKEN_MAX_AGE = int(
    os.environ.get(
        "ADMIN_TOKEN_MAX_AGE",
        "28800"
    )
)


def token_serializer():

    return URLSafeTimedSerializer(
        app.secret_key,
        salt="faculty-ai-admin-token"
    )


def create_admin_token(admin_row):

    return token_serializer().dumps({
        "id": admin_row.get("id"),
        "username": admin_row.get("username"),
        "role": admin_row.get("role"),
    })


def get_admin_from_request():

    auth_header = request.headers.get(
        "Authorization",
        ""
    )

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:].strip()

    if not token:
        return None

    try:

        return token_serializer().loads(
            token,
            max_age=ADMIN_TOKEN_MAX_AGE
        )

    except (
        BadSignature,
        SignatureExpired
    ):

        return None


def is_admin_logged_in():

    return (
        get_admin_from_request()
        is not None
    )


def require_admin_for_table(table):

    if (
        table in ADMIN_ONLY_TABLES
        and not is_admin_logged_in()
    ):

        return jsonify(
            error="Admin login required"
        ), 401

    return None


# ---------------------------------------------------------
# FILTERS
# ---------------------------------------------------------

def parse_filters(table):

    allowed = TABLE_COLUMNS[table]

    clauses = []
    values = []

    for key in request.args:

        if key.startswith("eq_"):

            column = key[3:]

            if column not in allowed:
                raise ValueError(
                    f"Filter column not allowed: {column}"
                )

            raw = request.args.get(key)

            if raw == "null":

                clauses.append(
                    sql.SQL("{} IS NULL").format(
                        sql.Identifier(column)
                    )
                )

            else:

                if raw.lower() == "true":
                    value = True

                elif raw.lower() == "false":
                    value = False

                else:
                    value = raw

                clauses.append(
                    sql.SQL("{} = %s").format(
                        sql.Identifier(column)
                    )
                )

                values.append(value)

        elif key.startswith("ilike_"):

            column = key[6:]

            if column not in allowed:
                raise ValueError(
                    f"Filter column not allowed: {column}"
                )

            clauses.append(
                sql.SQL("{} ILIKE %s").format(
                    sql.Identifier(column)
                )
            )

            values.append(
                request.args.get(key)
            )

    return clauses, values


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------

@app.get("/")
def home():

    return jsonify(
        status="ok",
        service="Faculty AI Flask API"
    )


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    "SELECT 1 AS ok"
                )

                row = cur.fetchone()

        return jsonify(
            status="ok",
            database=row["ok"] == 1
        )

    except Exception as exc:

        return jsonify(
            status="error",
            error=str(exc)
        ), 500


# ---------------------------------------------------------
# TABLE API
# ---------------------------------------------------------

@app.route(
    "/api/table/<frontend_table>",
    methods=[
        "GET",
        "POST",
        "PATCH"
    ]
)
def table_api(frontend_table):

    try:

        table = resolve_table(
            frontend_table
        )

        blocked = require_admin_for_table(
            table
        )

        if blocked:
            return blocked

        # ------------------------
        # SELECT
        # ------------------------

        if request.method == "GET":

            requested = request.args.get(
                "select",
                "*"
            )

            if requested == "*":

                # Protect password hashes
                if table == "admin_login_users":

                    select_sql = sql.SQL(
                        "id, username, role, "
                        "is_active, created_at, updated_at"
                    )

                else:

                    select_sql = sql.SQL("*")

            else:

                columns = [
                    column.strip()
                    for column
                    in requested.split(",")
                    if column.strip()
                ]

                validate_columns(
                    table,
                    columns
                )

                if (
                    table == "admin_login_users"
                    and
                    "password_hash" in columns
                ):

                    raise ValueError(
                        "password_hash cannot be selected"
                    )

                select_sql = sql.SQL(
                    ", "
                ).join(
                    map(
                        sql.Identifier,
                        columns
                    )
                )

            clauses, values = parse_filters(
                table
            )

            query = sql.SQL(
                "SELECT {} FROM public.{}"
            ).format(
                select_sql,
                sql.Identifier(table)
            )

            if clauses:

                query += (
                    sql.SQL(" WHERE ")
                    +
                    sql.SQL(" AND ").join(
                        clauses
                    )
                )

            order_arg = request.args.get(
                "order"
            )

            if order_arg:

                parts = order_arg.rsplit(
                    ".",
                    1
                )

                column = parts[0]

                direction = (
                    parts[1].lower()
                    if len(parts) > 1
                    else "asc"
                )

                validate_columns(
                    table,
                    [column]
                )

                query += sql.SQL(
                    " ORDER BY {} {}"
                ).format(
                    sql.Identifier(column),
                    sql.SQL(
                        "DESC"
                        if direction == "desc"
                        else "ASC"
                    )
                )

            limit = request.args.get(
                "limit"
            )

            if limit:

                query += sql.SQL(
                    " LIMIT %s"
                )

                values.append(
                    max(
                        1,
                        min(
                            int(limit),
                            5000
                        )
                    )
                )

            with get_conn() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        query,
                        values
                    )

                    rows = cur.fetchall()

            return jsonify(
                data=clean_rows(rows)
            )


        # ------------------------
        # INSERT / UPDATE
        # ------------------------

        payload = request.get_json(
            silent=True
        )

        if (
            not isinstance(payload, dict)
            or not payload
        ):

            return jsonify(
                error="A JSON object is required"
            ), 400


        allowed = TABLE_COLUMNS[table]

        safe_payload = {
            key: value
            for key, value
            in payload.items()
            if key in allowed
        }


        if table == "admin_login_users":

            safe_payload.pop(
                "password_hash",
                None
            )


        if not safe_payload:

            return jsonify(
                error="No allowed columns supplied"
            ), 400


        # ------------------------
        # INSERT
        # ------------------------

        if request.method == "POST":

            columns = list(
                safe_payload.keys()
            )

            values = [
                safe_payload[column]
                for column in columns
            ]

            returning = (
                request.args.get(
                    "returning",
                    "false"
                ).lower()
                == "true"
            )

            query = sql.SQL(
                "INSERT INTO public.{} ({}) "
                "VALUES ({})"
            ).format(

                sql.Identifier(table),

                sql.SQL(", ").join(
                    map(
                        sql.Identifier,
                        columns
                    )
                ),

                sql.SQL(", ").join(
                    sql.Placeholder()
                    for _ in columns
                )
            )

            if returning:

                query += sql.SQL(
                    " RETURNING *"
                )


            with get_conn() as conn:

                with conn.cursor() as cur:

                    cur.execute(
                        query,
                        values
                    )

                    rows = (
                        cur.fetchall()
                        if returning
                        else []
                    )

                conn.commit()


            return jsonify(
                data=clean_rows(rows)
            )


        # ------------------------
        # PATCH
        # ------------------------

        clauses, filter_values = parse_filters(
            table
        )

        if not clauses:

            return jsonify(
                error="Update requires a filter"
            ), 400


        columns = list(
            safe_payload.keys()
        )

        update_values = [
            safe_payload[column]
            for column in columns
        ]


        set_sql = sql.SQL(
            ", "
        ).join(

            sql.SQL(
                "{} = %s"
            ).format(
                sql.Identifier(column)
            )

            for column in columns
        )


        returning = (
            request.args.get(
                "returning",
                "false"
            ).lower()
            == "true"
        )


        query = sql.SQL(
            "UPDATE public.{} "
            "SET {} WHERE {}"
        ).format(

            sql.Identifier(table),

            set_sql,

            sql.SQL(
                " AND "
            ).join(
                clauses
            )
        )


        if returning:

            query += sql.SQL(
                " RETURNING *"
            )


        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    query,
                    update_values
                    +
                    filter_values
                )

                rows = (
                    cur.fetchall()
                    if returning
                    else []
                )

            conn.commit()


        return jsonify(
            data=clean_rows(rows)
        )


    except Exception as exc:

        return jsonify(
            error=str(exc)
        ), 400


# ---------------------------------------------------------
# RPC FUNCTIONS
# ---------------------------------------------------------

RPC_FUNCTIONS = {

    "verify_admin_login": [
        "p_username",
        "p_password"
    ],

    "list_admin_login_users": [
        "p_admin_username",
        "p_admin_password"
    ],

    "add_admin_login_user": [
        "p_admin_username",
        "p_admin_password",
        "p_username",
        "p_password",
        "p_role"
    ],

    "set_admin_login_user_active": [
        "p_admin_username",
        "p_admin_password",
        "p_user_id",
        "p_active"
    ],

    "change_admin_password": [
        "p_admin_username",
        "p_admin_password",
        "p_new_password"
    ],
}


# ---------------------------------------------------------
# RPC API
# ---------------------------------------------------------

@app.post(
    "/api/rpc/<function_name>"
)
def rpc_api(function_name):

    if (
        function_name
        not in RPC_FUNCTIONS
    ):

        return jsonify(
            error="RPC function is not allowed"
        ), 404


    # All admin RPCs except login require token
    if (
        function_name
        != "verify_admin_login"
        and not is_admin_logged_in()
    ):

        return jsonify(
            error="Admin login required"
        ), 401


    args = request.get_json(
        silent=True
    ) or {}


    names = RPC_FUNCTIONS[
        function_name
    ]


    try:

        values = [
            args.get(name)
            for name in names
        ]


        placeholders = sql.SQL(
            ", "
        ).join(

            sql.Placeholder()
            for _ in names
        )


        query = sql.SQL(
            "SELECT * FROM public.{}({})"
        ).format(

            sql.Identifier(
                function_name
            ),

            placeholders
        )


        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    query,
                    values
                )

                rows = cur.fetchall()


        data = clean_rows(rows)


        # Successful login -> token
        if (
            function_name
            == "verify_admin_login"
        ):

            if data:

                token = create_admin_token(
                    data[0]
                )

                return jsonify(
                    data=data,
                    admin_token=token
                )

            return jsonify(
                data=[]
            )


        return jsonify(
            data=data
        )


    except Exception as exc:

        return jsonify(
            error=str(exc)
        ), 400


# ---------------------------------------------------------
# RUN
# ---------------------------------------------------------

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                "8000"
            )
        ),
        debug=False
    )
