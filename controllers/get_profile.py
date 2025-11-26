import json
import base64
import re
import time, datetime
import logging
from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError
import jwt

_logger = logging.getLogger(__name__)


class ProfileAPI(http.Controller):

    # -------------------------------------------------------------
    # GET /api/v1/profile  →  Return logged-in user profile
    # -------------------------------------------------------------
    @http.route('/api/v1/profile', type='http', auth='user', methods=['GET'], csrf=False, cors="*")
    def get_profile(self, **kwargs):
        user = request.env.user
        profile = {
            "id": user.id,
            "name": user.name,
            "login": user.login,
            "email": user.email or False,
            "phone": user.phone or False,
            "mobile": user.mobile or False,
            "image_1920": user.image_1920.decode() if user.image_1920 else False,
            "partner": {
                "street": user.partner_id.street or False,
                "street2": user.partner_id.street2 or False,
                "city": user.partner_id.city or False,
                "zip": user.partner_id.zip or False,
                "country": user.partner_id.country_id.name or False,
                "state": user.partner_id.state_id.name or False,
            }
        }

        return request.make_response(
            json.dumps(profile),
            headers=[('Content-Type', 'application/json')]
        )

    # -------------------------------------------------------------
    # PUT/PATCH /api/v1/update/profile  →  Update user profile
    # -------------------------------------------------------------
    @http.route('/api/v1/update/profile', type='json', auth='user',
                methods=['PUT', 'PATCH', 'POST'], csrf=False, cors="*")
    def update_profile(self, **payload):
        user = request.env.user

        allowed_fields = {
            'name', 'email', 'phone', 'mobile',
            'street', 'street2', 'city', 'zip', 'country_id', 'state_id',
            'image_1920'
        }

        update_vals = {}
        partner_vals = {}

        for key, value in payload.items():
            if key not in allowed_fields:
                continue

            if key in {'street', 'street2', 'city', 'zip', 'country_id', 'state_id'}:
                if key in ('country_id', 'state_id') and value:
                    value = int(value)
                partner_vals[key] = value or False
            else:
                if key == 'image_1920' and value:
                    raw = value.split(',')[1] if ',' in value else value
                    value = base64.b64decode(raw)
                update_vals[key] = value or False

        try:
            if update_vals:
                user.sudo().write(update_vals)
            if partner_vals:
                user.partner_id.sudo().write(partner_vals)

            return {
                "success": True,
                "message": "Profile updated successfully",
                "user_id": user.id
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -------------------------------------------------------------
    # POST /api/v1/profile/change-password  →  Change password
    # -------------------------------------------------------------
    def _validate_jwt_token(self, token):
        """Validate JWT token and return user_id or False"""
        secret = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
        if not secret:
            return False

        try:
            payload = jwt.decode(token, secret, algorithms=['HS256'])
            user_id = payload.get('user_id')
            if not user_id:
                return False
            # Optional: check expiration is handled by jwt.decode
            return user_id
        except jwt.ExpiredSignatureError:
            return False
        except jwt.InvalidTokenError:
            return False

    def _authenticate_by_token(self):
        """Extract and validate Bearer token from Authorization header"""
        auth_header = request.httprequest.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return False

        token = auth_header.split(' ')[1]
        user_id = self._validate_jwt_token(token)
        if not user_id:
            return False

        # Set environment with authenticated user
        request.uid = user_id
        request.env = request.env(user=user_id)
        return user_id

    def _strong_password(self, password):
        """Enforce strong password policy"""
        if len(password) < 8:
            return False, "Password must be at least 8 characters long"
        if not re.search(r"[A-Z]", password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r"[a-z]", password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r"[0-9]", password):
            return False, "Password must contain at least one digit"
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            return False, "Password must contain at least one special character"
        return True, ""


    def _authenticate_by_token(self):
        """Extract Bearer token and authenticate user (Odoo 17/18 compatible)"""
        auth_header = request.httprequest.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return False

        token = auth_header.split(' ', 1)[1].strip()
        secret = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
        if not secret:
            return False

        try:
            payload = jwt.decode(token, secret, algorithms=['HS256'])
            user_id = int(payload['user_id'])

            # Critical: Use update_env() instead of request.uid =
            request.update_env(user=user_id)

            # Optional: re-check that user exists and is active
            user = request.env['res.users'].browse(user_id)
            if not user.exists() or not user.active:
                return False

            return user_id

        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError, KeyError):
            return False


    @http.route('/api/v1/profile/change-password', type='json', auth='none', methods=['POST'], csrf=False, cors="*")
    def change_password(self):
        client_ip = request.httprequest.remote_addr
        request_time = datetime.datetime.now().isoformat()

        # Authenticate via JWT
        user_id = self._authenticate_by_token()
        if not user_id:
            _logger.warning(f"[{request_time}] Unauthorized change-password attempt from {client_ip}")
            return self._error_response("Invalid or missing token", 401)

        # Now request.env is correctly set to the authenticated user
        user = request.env.user  # This works because update_env() was called

        try:
            data = request.httprequest.get_json()
            current_password = data.get('current_password', '').strip()
            new_password = data.get('new_password', '').strip()
            confirm_password = data.get('confirm_password', '').strip()

            if not all([current_password, new_password, confirm_password]):
                return self._error_response("All fields are required", 400)

            if new_password != confirm_password:
                return self._error_response("New passwords do not match", 400)

            if current_password == new_password:
                return self._error_response("New password must be different", 400)

            # Validate strength
            valid, msg = self._strong_password(new_password)
            if not valid:
                return self._error_response(msg, 400)

            # Check current password
            try:
                user._check_credentials(current_password, raise_exception=True)
            except AccessDenied:
                _logger.warning(f"[{request_time}] Wrong current password for {user.login} from {client_ip}")
                return self._error_response("Current password is incorrect", 401)

            # Change password
            user.write({'password': new_password})

            _logger.info(f"[{request_time}] Password changed successfully for {user.login} (ID: {user_id})")

            return {
                'status': 'success',
                'message': 'Password changed successfully'
            }

        except Exception as e:
            _logger.exception(f"[{request_time}] Change password error: {str(e)}")
            return self._error_response("Internal server error", 500)

    def _error_response(self, message, status=400):
        return Response(
            json.dumps({'status': 'error', 'message': message}),
            status=status,
            headers=[('Content-Type', 'application/json')]
        )