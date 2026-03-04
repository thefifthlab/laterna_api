import json
import base64
import logging
import datetime
import jwt

from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError

_logger = logging.getLogger(__name__)


class ProfileAPI(http.Controller):

    # -------------------------------------------------------------
    # Helper: JSON response with CORS headers
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
    # Bearer Token Authentication
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
            _logger.error("JWT secret key not configured in ir.config_parameter 'auth_token.secret_key'")
            raise AccessDenied("Server configuration error")

        try:
            payload = jwt.decode(
                token,
                secret_key,
                algorithms=['HS256'],
                options={"require": ["exp", "iat"]},
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
    # Build profile payload (image as URL)
    # -------------------------------------------------------------
    def _build_profile(self, user):
        partner = user.partner_id

        return {
            "id": user.id,
            "name": user.name,
            "login": user.login,
            "email": user.email or False,
            "phone": user.phone or False,
            "mobile": user.mobile or False,
            "image_url": f"/web/image/res.users/{user.id}/image_1920" if user.image_1920 else False,
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
    # GET/POST /api/v1/profile → Fetch user profile
    # -------------------------------------------------------------
    @http.route(
        '/api/v1/profile',
        type='http',
        auth='none',
        methods=['GET', 'POST'],
        csrf=False,
        cors="*"
    )
    def profile(self):
        try:
            user = self._authenticate_bearer()
            profile_data = self._build_profile(user)

            return self._json_response({
                "success": True,
                "profile": profile_data
            }, status=200)

        except AccessDenied as e:
            return self._json_response({
                "success": False,
                "error": "Unauthorized",
                "message": str(e)
            }, status=401)

        except Exception as e:
            _logger.exception("Profile fetch error")
            return self._json_response({
                "success": False,
                "error": "Internal error",
                "message": str(e)
            }, status=500)

    # -------------------------------------------------------------
    # PUT/PATCH/POST /api/v1/profile/update → Update profile
    # -------------------------------------------------------------
    @http.route(
        '/api/v1/profile/update',
        type='http',
        auth='none',
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
            payload = request.httprequest.get_json() or {}
        except:
            return self._json_response({
                "success": False,
                "error": "Invalid JSON body"
            }, status=400)

        allowed_user_fields = {'name', 'email', 'phone', 'mobile', 'image_1920'}
        allowed_partner_fields = {'street', 'street2', 'city', 'zip', 'country_id', 'state_id'}

        user_vals = {}
        partner_vals = {}

        for key, value in payload.items():
            if key in allowed_user_fields:
                if key == 'image_1920' and value:
                    try:
                        if ',' in value:  # data:image/...;base64,
                            value = value.split(',', 1)[1]
                        raw_image = base64.b64decode(value)
                        if len(raw_image) > 4 * 1024 * 1024:  # 4MB limit
                            return self._json_response({
                                "success": False,
                                "error": "Image file too large (max 4MB)"
                            }, status=400)
                        # Very basic format check
                        if not raw_image.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF8')):
                            return self._json_response({
                                "success": False,
                                "error": "Unsupported image format"
                            }, status=400)
                        user_vals[key] = raw_image
                    except Exception:
                        continue
                else:
                    user_vals[key] = value or False

            elif key in allowed_partner_fields:
                if key in ('country_id', 'state_id') and value:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        continue
                partner_vals[key] = value or False

        # Email uniqueness check
        if 'email' in user_vals and user_vals['email']:
            duplicate = request.env['res.users'].sudo().search([
                ('email', '=ilike', user_vals['email']),
                ('id', '!=', user.id),
            ], limit=1)
            if duplicate:
                return self._json_response({
                    "success": False,
                    "error": "Email address already in use by another user"
                }, status=400)

        if user_vals:
            user.sudo().write(user_vals)
        if partner_vals:
            user.partner_id.sudo().write(partner_vals)

        return self._json_response({
            "success": True,
            "message": "Profile updated successfully",
            "user_id": user.id
        }, status=200)

    # -------------------------------------------------------------
    # POST /api/v1/profile/change-password
    # -------------------------------------------------------------
    @http.route('/api/v1/profile/change-password', type='http', auth='none', methods=['POST'], csrf=False, cors="*")
    def change_password(self):
        client_ip = request.httprequest.remote_addr
        now = datetime.datetime.now().isoformat()

        # 1. Authenticate Request
        try:
            user = self._authenticate_bearer()
        except AccessDenied as e:
            _logger.warning(f"[{now}] Unauthorized access attempt from {client_ip}: {str(e)}")
            return self._json_response({"success": False, "error": "Unauthorized"}, status=401)

        # 2. Parse JSON Body
        try:
            # Ensure the client sends 'Content-Type: application/json'
            data = request.httprequest.get_json() or {}
        except Exception:
            return self._json_response({
                "success": False,
                "error": "Invalid JSON format. Ensure Content-Type is application/json"
            }, status=400)

        # 3. Extract and Clean Data
        current_pwd = data.get('current_password', '').strip()
        new_pwd = data.get('new_password', '').strip()
        confirm_pwd = data.get('confirm_password', '').strip()

        # 4. Preliminary Validations
        if not all([current_pwd, new_pwd, confirm_pwd]):
            return self._json_response({
                "success": False,
                "error": "All fields required: current_password, new_password, confirm_password"
            }, status=400)

        if new_pwd != confirm_pwd:
            return self._json_response({"success": False, "error": "New passwords do not match"}, status=400)

        if current_pwd == new_pwd:
            return self._json_response({"success": False, "error": "New password must be different from current"},
                                       status=400)

        if len(new_pwd) < 8:
            return self._json_response({"success": False, "error": "Password must be at least 8 characters"},
                                       status=400)

        # 5. Verify Current Credentials (Odoo 18 Fix)
        try:
            # Odoo 18 expects a credential dictionary, not a string
            credentials = {
                'type': 'password',
                'password': current_pwd
            }
            # We check against the user's specific environment
            user.with_user(user)._check_credentials(credentials, {'interactive_login': True})
        except AccessDenied:
            _logger.warning(f"[{now}] Incorrect current password for {user.login} from {client_ip}")
            return self._json_response({"success": False, "error": "Current password is incorrect"}, status=401)
        except Exception as e:
            _logger.error(f"Error during credential check: {str(e)}")
            return self._json_response({"success": False, "error": "Authentication system error"}, status=500)

        # 6. Update Password and Persist
        try:
            # .sudo() is required to write to the 'password' field via controller
            user.sudo().write({'password': new_pwd})

            # Manual commit ensures the DB is updated before the response is sent
            request.env.cr.commit()

            _logger.info(f"[{now}] Password successfully changed for {user.login} (ID: {user.id})")
            return self._json_response({"success": True, "message": "Password changed successfully"}, status=200)

        except Exception as e:
            _logger.error(f"Database error during password update: {str(e)}")
            return self._json_response({"success": False, "error": "Internal server error"}, status=500)