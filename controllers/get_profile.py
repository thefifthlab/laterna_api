import json
import base64
import re
import time, datetime
import logging
from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError

_logger = logging.getLogger(__name__)


class ProfileAPI(http.Controller):

    # -------------------------------------------------------------
    # GET /api/v1/profile  →  Return logged-in user profile
    # -------------------------------------------------------------

    @http.route('/api/v1/profile', type='json', auth='user', methods=['GET'], csrf=False, cors="*")
    def get_profile_http(self, **kwargs):
        try:
            user = request.env.user

            # Build profile data (same as before)
            country_name = user.partner_id.country_id.name if user.partner_id.country_id else False
            state_name = user.partner_id.state_id.name if user.partner_id.state_id else False

            image_1920 = False
            if user.image_1920:
                image_1920 = user.image_1920.decode('utf-8') if isinstance(user.image_1920, bytes) else user.image_1920

            profile = {
                "id": user.id,
                "name": user.name,
                "login": user.login,
                "email": user.email or False,
                "phone": user.phone or False,
                "mobile": user.mobile or False,
                "image_1920": image_1920,
                "partner": {
                    "street": user.partner_id.street or False,
                    "street2": user.partner_id.street2 or False,
                    "city": user.partner_id.city or False,
                    "zip": user.partner_id.zip or False,
                    "country": country_name,
                    "state": state_name,
                    "partner_id": user.partner_id.id,
                }
            }

            response_data = {
                "success": True,
                "profile": profile
            }

            return response_data

        except Exception as e:
            _logger.error("Failed to get user profile: %s", str(e))
            error_response = {
                "success": False,
                "error": "Failed to get user profile",
                "message": str(e)
            }
            return error_response

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