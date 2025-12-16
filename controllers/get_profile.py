import json
import base64
import logging
import datetime
import jwt

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class ProfileAPI(http.Controller):

    # -------------------------------------------------------------
    # POST /api/v1/profile → Return logged-in user profile
    # -------------------------------------------------------------
    @http.route('/api/v1/profile', type='json', auth='none', methods=['POST'], csrf=False)
    def get_profile_jsonrpc(self, **kwargs):
        try:
            user = self._authenticate_bearer()

            profile = self._build_profile(user)

            return {
                "jsonrpc": "2.0",
                "result": {
                    "success": True,
                    "profile": profile
                },
                "id": None
            }

        except AccessDenied as e:
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": 401,
                    "message": "Unauthorized",
                    "data": str(e)
                },
                "id": None
            }

        except Exception as e:
            _logger.exception("Profile API error")
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": str(e)
                },
                "id": None
            }

    # -------------------------------------------------------------
    # Bearer Token Authentication
    # -------------------------------------------------------------
    def _authenticate_bearer(self):
        auth_header = request.httprequest.headers.get('Authorization')

        if not auth_header or not auth_header.startswith('Bearer '):
            raise AccessDenied("Bearer token missing")

        token = auth_header.split(' ')[1]

        secret_key = request.env['ir.config_parameter'].sudo().get_param(
            'auth_token.secret_key'
        )
        if not secret_key:
            raise AccessDenied("Server misconfiguration")

        try:
            payload = jwt.decode(
                token,
                secret_key,
                algorithms=['HS256']
            )
        except jwt.ExpiredSignatureError:
            raise AccessDenied("Token expired")
        except jwt.InvalidTokenError:
            raise AccessDenied("Invalid token")

        user_id = payload.get('user_id')
        if not user_id:
            raise AccessDenied("Invalid token payload")

        user = request.env['res.users'].sudo().browse(user_id)
        if not user.exists():
            raise AccessDenied("User not found")

        return user

    # -------------------------------------------------------------
    # Build Profile Payload
    # -------------------------------------------------------------
    def _build_profile(self, user):
        partner = user.partner_id

        image_1920 = False
        if user.image_1920:
            image_1920 = (
                user.image_1920.decode('utf-8')
                if isinstance(user.image_1920, bytes)
                else user.image_1920
            )

        return {
            "id": user.id,
            "name": user.name,
            "login": user.login,
            "email": user.email or False,
            "phone": user.phone or False,
            "mobile": user.mobile or False,
            "image_1920": image_1920,
            "partner": {
                "partner_id": partner.id,
                "street": partner.street or False,
                "street2": partner.street2 or False,
                "city": partner.city or False,
                "zip": partner.zip or False,
                "state": partner.state_id.name if partner.state_id else False,
                "country": partner.country_id.name if partner.country_id else False,
            }
        }

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