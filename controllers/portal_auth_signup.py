# -*- coding: utf-8 -*-
import json
import logging
import re
from datetime import datetime
from odoo import http, fields
from odoo.http import request
from odoo.exceptions import ValidationError, AccessError

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Regexes (centralised – easy to tweak)
# ----------------------------------------------------------------------
EMAIL_REGEX = re.compile(
    r"^(?=.{1,254}$)(?=.{1,64}@)[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-zA-Z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
PHONE_REGEX = re.compile(r"^\+?[1-9]\d{1,14}$")  # E.164-ish
PASSWORD_REGEX = re.compile(
    r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
)


class GuestUser(http.Controller):
    """
    Public JSON API – register a portal (e-commerce) user.

    **Endpoint**
        POST /api/v1/portal/auth/register

    **CORS** – ``cors='*'`` (handled by Odoo)

    **Rate-limit** – decorate with ``@http.rate_limit(...)`` if the addon is installed.
    """

    @http.route("/api/v1/portal/auth/register", type="json", auth="public", methods=["POST"], csrf=False, cors="*")
    def portal_auth_signup(self, **kwargs):
        """
        Full request / response spec is documented at the end of the file.
        """
        # --------------------------------------------------------------
        # 1. Content-Type & payload parsing
        # --------------------------------------------------------------
        if request.httprequest.mimetype != "application/json":
            return self._json_error("Unsupported Media Type – expected application/json", 415)

        try:
            data = json.loads(request.httprequest.data)
        except json.JSONDecodeError as exc:
            return self._json_error(f"Invalid JSON: {exc}", 400)

        # --------------------------------------------------------------
        # 2. Required fields
        # --------------------------------------------------------------
        REQUIRED = ("email", "password", "name")
        missing = [f for f in REQUIRED if not data.get(f)]
        if missing:
            return self._json_error(
                f"Missing required fields: {', '.join(missing)}", 400
            )

        email = data["email"].strip().lower()
        password = data["password"]
        name = data["name"].strip()

        # --------------------------------------------------------------
        # 3. Field validation
        # --------------------------------------------------------------
        # ---- email ----------------------------------------------------
        if not EMAIL_REGEX.match(email):
            return self._json_error("Invalid e-mail address", 400)

        # ---- password -------------------------------------------------
        if not PASSWORD_REGEX.match(password):
            return self._json_error(
                "Password must contain at least 8 characters, "
                "one uppercase, one lowercase, one digit and one special character (@$!%*?&)",
                400,
            )

        # ---- optional phone -------------------------------------------
        phone = data.get("phone")
        if phone:
            phone = "".join(filter(str.isdigit, phone.lstrip("+")))
            if not (10 <= len(phone) <= 15):
                return self._json_error("Phone number must be 10–15 digits", 400)
            phone = "+" + phone

        # --------------------------------------------------------------
        # 4. Duplicate check
        # --------------------------------------------------------------
        if request.env["res.users"].sudo().search([("login", "=", email)], limit=1):
            return self._json_error("E-mail already registered", 409)

        # --------------------------------------------------------------
        # 5. Country (optional)
        # --------------------------------------------------------------
        country_id = False
        country_code = data.get("country_code")
        if country_code:
            country = (
                request.env["res.country"]
                .sudo()
                .search([("code", "=ilike", country_code)], limit=1)
            )
            if not country:
                return self._json_error(f"Country code '{country_code}' not found", 400)
            country_id = country.id

        # --------------------------------------------------------------
        # 6. DB transaction (savepoint)
        # --------------------------------------------------------------
        try:
            with request.env.cr.savepoint():
                # ---- partner ------------------------------------------------
                partner_vals = {
                    "name": name,
                    "email": email,
                    "phone": phone or False,
                    "company_type": "person",
                    "customer_rank": 1,
                    "street": data.get("street"),
                    "city": data.get("city"),
                    "zip": data.get("zip"),
                    "country_id": country_id,
                }
                partner = request.env["res.partner"].sudo().create(partner_vals)

                # ---- portal group -------------------------------------------
                portal_group = request.env.ref("base.group_portal")
                if not portal_group:
                    raise ValidationError("Portal group missing – contact administrator")
                # ---- user ---------------------------------------------------

                user = request.env["res.users"].sudo().with_context(no_reset_password=True).create({
                    "name": name,
                    "login": email,
                    "password": password,
                    "partner_id": partner.id,
                    "groups_id": [(6, 0, [portal_group.id])],
                })

                # ---- double-opt-in token (recommended) --------------------
                user.sudo().action_reset_password()                # If you *don't* want the token flow, comment the line above
                # and use the classic welcome template instead.

        except ValidationError as ve:
            _logger.warning("Validation error for %s: %s", email, ve)
            return self._json_error(f"Validation error: {ve}", 400)
        except AccessError as ae:
            _logger.error("Access error for %s: %s", email, ae)
            return self._json_error("Permission denied", 403)
        except Exception as exc:
            _logger.exception("Unexpected error creating portal user %s", email)
            return self._json_error("Internal server error", 500)

        # --------------------------------------------------------------
        # 7. Success response
        # --------------------------------------------------------------
        return {
            "user_id": user.id,
            "partner_id": partner.id,
            "name": user.name,
            "message": "Portal user created – check your inbox to activate the account",
            "created_at": fields.Datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # Helper – uniform JSON error response
    # ------------------------------------------------------------------
    def _json_error(self, error_msg, status=400):
        """Works for both direct HTTP and JSON-RPC calls"""
        response = {
            "success": False,
            "error": error_msg,
            "status": status,
        }

        # If it's a direct HTTP request (not JSON-RPC), return proper Response
        if getattr(request, 'is_jsonrpc', False) or not request.httprequest:
            return response  # JSON-RPC expects plain dict

        # Raw HTTP request → return real Response with correct status
        return request.make_response(
            json.dumps(response, ensure_ascii=False),
            headers=[("Content-Type", "application/json")],
            status=status,
        )