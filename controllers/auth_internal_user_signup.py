import json
import logging
import re
from datetime import datetime, timezone

import jwt
from odoo import http, _
from odoo.exceptions import AccessDenied, ValidationError
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

# Regex patterns (compile once at module level)
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
PASSWORD_REGEX = re.compile(
    r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$'
)


def decode_and_validate_token(token):
    """
    Decode JWT and do basic validation.
    Returns payload or raises exception.
    """
    # Load secret from config parameter (recommended)
    secret = request.env['ir.config_parameter'].sudo().get_param(
        'auth_token.secret_key',  # ← change to your actual key name
        default=None
    )
    if not secret:
        raise ValidationError("Server misconfiguration: JWT secret not set")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "iat"]},
        )
        # Optional: check issuer, audience, etc. if you set them
        # if payload.get("iss") != "your-app":
        #     raise AccessDenied("Invalid issuer")
        return payload
    except jwt.ExpiredSignatureError:
        raise AccessDenied("Token has expired")
    except jwt.InvalidTokenError as e:
        raise AccessDenied(f"Invalid token: {str(e)}")


class AdminAuthController(http.Controller):

    @http.route(
        "/api/v1/admin/auth/register",
        type="http",
        auth="public",           # we handle auth manually
        methods=["POST"],
        csrf=False,
        cors="*"
    )
    def admin_register_user(self):
        """
        Admin-only endpoint to create new internal users.
        Requires valid Bearer JWT from an admin user.
        """
        # 1. Extract & validate JWT
        auth_header = request.httprequest.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return self._json_response(
                {"success": False, "error": "Authorization header with Bearer token required"},
                status=401
            )

        token = auth_header.split(" ", 1)[1]

        try:
            payload = decode_and_validate_token(token)
            uid = int(payload.get("sub"))
            if not uid:
                raise AccessDenied("Token missing subject (sub)")

            # Switch environment to the authenticated user (no sudo yet)
            request.update_env(user=uid, su=False)
            current_user = request.env.user

            if current_user._is_public():
                raise AccessDenied("Public user cannot perform this action")

            # Must be in base.group_system (Settings → Technical Features / Administrator)
            if not current_user.has_group("base.group_system"):
                return self._json_response(
                    {"success": False, "error": "Administrator access required"},
                    status=403
                )

        except (AccessDenied, ValueError, TypeError) as e:
            _logger.warning("Admin register auth failed: %s", str(e))
            return self._json_response(
                {"success": False, "error": str(e) or "Authentication failed"},
                status=401
            )
        except Exception as e:
            _logger.exception("Unexpected error during JWT validation")
            return self._json_response(
                {"success": False, "error": "Internal authentication error"},
                status=500
            )

        # 2. Parse request body
        if request.httprequest.mimetype not in ("application/json", "application/json;charset=utf-8"):
            return self._json_response(
                {"success": False, "error": "Content-Type must be application/json"},
                status=415
            )

        try:
            data = request.httprequest.get_json() or {}
        except Exception:
            return self._json_response(
                {"success": False, "error": "Invalid or malformed JSON"},
                status=400
            )

        # 3. Validate required fields
        required_fields = ["name", "email", "password"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return self._json_response(
                {"success": False, "error": f"Missing required fields: {', '.join(missing)}"},
                status=400
            )

        name = data["name"].strip()
        email = data["email"].strip().lower()
        password = data["password"]

        # Basic format validation
        if not EMAIL_REGEX.match(email):
            return self._json_response(
                {"success": False, "error": "Invalid email format"},
                status=400
            )

        if not PASSWORD_REGEX.match(password):
            return self._json_response(
                {
                    "success": False,
                    "error": (
                        "Password must be at least 8 characters long and contain: "
                        "uppercase letter, lowercase letter, number, special character (@$!%*?&)"
                    )
                },
                status=400
            )

        # Optional: check if email already exists
        existing = request.env["res.users"].sudo().search([("login", "=", email)], limit=1)
        if existing:
            return self._json_response(
                {"success": False, "error": "Email address already in use"},
                status=409  # Conflict
            )

        # ───────────────────────────────────────────────────────────────
        #               YOUR BUSINESS LOGIC GOES HERE
        # ───────────────────────────────────────────────────────────────
        try:
            # Example minimal creation (customize heavily!)
            user_vals = {
                "name": name,
                "login": email,
                "email": email,
                "password": password,
                # "groups_id": [(6, 0, [group_internal.id])],  # example
                # "company_id": current_user.company_id.id,
            }

            new_user = request.env["res.users"].sudo().create(user_vals)

            # Optional: create partner, employee record, send welcome email, etc.
            # partner = request.env["res.partner"].sudo().create({...})
            # employee = request.env["hr.employee"].sudo().create({...})

            # Example welcome email (pseudo-code)
            # template = request.env.ref("your_module.email_template_welcome")
            # template.send_mail(new_user.id, force_send=True)

            return self._json_response(
                {
                    "success": True,
                    "message": "User created successfully",
                    "user_id": new_user.id,
                    "email": new_user.email,
                },
                status=201
            )

        except ValidationError as ve:
            return self._json_response(
                {"success": False, "error": str(ve)},
                status=400
            )
        except Exception as e:
            _logger.exception("Failed to create user")
            return self._json_response(
                {"success": False, "error": "Failed to create user"},
                status=500
            )

    # ───────────────────────────────────────────────────────────────
    # Helper: consistent JSON response
    # ───────────────────────────────────────────────────────────────
    def _json_response(self, data, status=200):
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=status,
            headers={
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
            },
        )