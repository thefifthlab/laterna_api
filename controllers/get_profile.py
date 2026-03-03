import json
import base64
import logging
import datetime
import jwt

from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class ProfileAPI(http.Controller):

    # -------------------------------------------------------------
    # Helper: Return JSON response with status code & CORS
    # -------------------------------------------------------------
    def _json_response(self, data, status=200):
        headers = {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
        }
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=status,
            headers=headers
        )

    # -------------------------------------------------------------
    # Bearer Token Authentication (shared)
    # -------------------------------------------------------------
    def _authenticate_bearer(self):
        auth_header = request.httprequest.headers.get('Authorization')

        if not auth_header or not auth_header.startswith('Bearer '):
            raise AccessDenied("Bearer token missing or invalid format")

        token = auth_header.split(' ')[1]

        secret_key = request.env['ir.config_parameter'].sudo().get_param(
            'auth_token.secret_key'
        )
        if not secret_key:
            raise AccessDenied("Server misconfiguration: missing secret key")

        try:
            payload = jwt.decode(
                token,
                secret_key,
                algorithms=['HS256']
            )
        except jwt.ExpiredSignatureError:
            raise AccessDenied("Token has expired")
        except jwt.InvalidTokenError as e:
            raise AccessDenied(f"Invalid token: {str(e)}")

        user_id = payload.get('user_id')
        if not user_id:
            raise AccessDenied("Token payload missing user_id")

        user = request.env['res.users'].sudo().browse(int(user_id))
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
    # POST /api/v1/profile  →  Get user profile
    # -------------------------------------------------------------
    @http.route(
        '/api/v1/profile',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
        cors="*"
    )
    def get_profile(self):
        try:
            user = self._authenticate_bearer()
            profile = self._build_profile(user)

            return self._json_response({
                "success": True,
                "profile": profile
            }, status=200)

        except AccessDenied as e:
            return self._json_response({
                "success": False,
                "error": "Unauthorized",
                "message": str(e)
            }, status=401)

        except Exception as e:
            _logger.exception("Get profile error")
            return self._json_response({
                "success": False,
                "error": "Internal error",
                "message": str(e)
            }, status=500)

    # -------------------------------------------------------------
    # PUT/PATCH/POST /api/v1/update/profile  →  Update profile
    # -------------------------------------------------------------
    @http.route(
        '/api/v1/update/profile',
        type='http',
        auth='none',           # using manual JWT → changed from 'user'
        methods=['PUT', 'PATCH', 'POST'],
        csrf=False,
        cors="*"
    )
    def update_profile(self):
        try:
            user = self._authenticate_bearer()
        except AccessDenied as e:
            return self._json_response({
                "success": False,
                "error": "Unauthorized",
                "message": str(e)
            }, status=401)

        try:
            # Parse JSON body
            try:
                payload = request.httprequest.get_json() or {}
            except:
                return self._json_response({
                    "success": False,
                    "error": "Invalid JSON body"
                }, status=400)

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
                        try:
                            value = int(value)
                        except (ValueError, TypeError):
                            continue
                    partner_vals[key] = value or False
                else:
                    if key == 'image_1920' and value:
                        try:
                            if ',' in value:  # data:image/...;base64,
                                value = value.split(',')[1]
                            value = base64.b64decode(value)
                        except Exception:
                            continue
                    update_vals[key] = value or False

            if update_vals:
                user.sudo().write(update_vals)
            if partner_vals:
                user.partner_id.sudo().write(partner_vals)

            return self._json_response({
                "success": True,
                "message": "Profile updated successfully",
                "user_id": user.id
            }, status=200)

        except Exception as e:
            _logger.exception("Profile update error")
            return self._json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    # -------------------------------------------------------------
    # POST /api/v1/profile/change-password  →  Change password
    # -------------------------------------------------------------
    @http.route(
        '/api/v1/profile/change-password',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
        cors="*"
    )
    def change_password(self):
        client_ip = request.httprequest.remote_addr
        request_time = datetime.datetime.now().isoformat()

        try:
            user = self._authenticate_bearer()
        except AccessDenied as e:
            _logger.warning(f"[{request_time}] Unauthorized change-password attempt from {client_ip}: {str(e)}")
            return self._json_response({
                "status": "error",
                "message": str(e)
            }, status=401)

        try:
            # Parse JSON body
            try:
                data = request.httprequest.get_json() or {}
            except:
                return self._json_response({
                    "status": "error",
                    "message": "Invalid or missing JSON body"
                }, status=400)

            current_password = data.get('current_password', '').strip()
            new_password    = data.get('new_password', '').strip()
            confirm_password = data.get('confirm_password', '').strip()

            if not all([current_password, new_password, confirm_password]):
                return self._json_response({
                    "status": "error",
                    "message": "All fields are required: current_password, new_password, confirm_password"
                }, status=400)

            if new_password != confirm_password:
                return self._json_response({
                    "status": "error",
                    "message": "New password and confirmation do not match"
                }, status=400)

            if current_password == new_password:
                return self._json_response({
                    "status": "error",
                    "message": "New password must be different from current password"
                }, status=400)

            # Replace with your real password strength check
            def _strong_password(pwd):
                if len(pwd) < 8:
                    return False, "Password must be at least 8 characters"
                # Add digits, uppercase, special chars checks if needed
                return True, ""

            valid, msg = _strong_password(new_password)
            if not valid:
                return self._json_response({
                    "status": "error",
                    "message": msg
                }, status=400)

            # Verify current password
            try:
                user._check_credentials(current_password, raise_exception=True)
            except AccessDenied:
                _logger.warning(f"[{request_time}] Wrong password for {user.login} from {client_ip}")
                return self._json_response({
                    "status": "error",
                    "message": "Current password is incorrect"
                }, status=401)

            # Update password
            user.sudo().write({'password': new_password})

            _logger.info(f"[{request_time}] Password changed for {user.login} (ID: {user.id}) from {client_ip}")

            return self._json_response({
                "status": "success",
                "message": "Password changed successfully"
            }, status=200)

        except Exception as e:
            _logger.exception(f"[{request_time}] Change password error: {str(e)}")
            return self._json_response({
                "status": "error",
                "message": "Internal server error"
            }, status=500)