# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied, ValidationError
import logging
import datetime
import json
import base64
import hmac
import hashlib

_logger = logging.getLogger(__name__)

class LoginAuthenticationAPI(http.Controller):

    @http.route('/api/v1/auth/login', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def auth_login(self):
        client_ip = request.httprequest.remote_addr
        request_time = datetime.datetime.now().isoformat()

        # Handle OPTIONS preflight for CORS
        if request.httprequest.method == 'OPTIONS':
            response = Response()
            response.headers['Access-Control-Allow-Origin'] = request.httprequest.headers.get('Origin', '*')
            response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            response.headers['Access-Control-Max-Age'] = '86400'  # 24 hours cache
            return response

        try:
            # Parse JSON payload
            try:
                params = request.httprequest.get_json()
            except Exception:
                _logger.warning(f"[{request_time}] Invalid JSON from IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': 'Invalid JSON payload'},
                    status=400
                )

            required_fields = {'login', 'password'}

            if not isinstance(params, dict):
                _logger.warning(f"[{request_time}] Invalid JSON structure from IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': 'Invalid JSON payload'},
                    status=400
                )

            missing_fields = required_fields - set(params.keys())
            if missing_fields:
                _logger.warning(f"[{request_time}] Missing fields {', '.join(missing_fields)} from IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': f'Missing required fields: {", ".join(missing_fields)}'},
                    status=400
                )

            # Sanitize input
            login = str(params['login']).strip()  # removed .lower() – consider if needed
            password = str(params['password']).strip()

            if not all([login, password]):
                _logger.warning(f"[{request_time}] Empty fields detected from IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': 'Empty fields are not allowed'},
                    status=400
                )

            # Get current database
            db = request.env.cr.dbname

            # Authenticate (this sets session if successful)
            credential = {'login': login, 'password': password, 'type': 'password'}
            uid = request.session.authenticate(db, credential)

            # Fetch user and partner
            user = request.env['res.users'].sudo().search([('login', '=', login)], limit=1)
            if not user:
                _logger.warning(f"[{request_time}] No user found for login: {login}, IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': 'Invalid credentials'},
                    status=401
                )

            partner = user.partner_id  # unused here, but kept for future

            # Generate JWT (manual – consider switching to pyjwt later)
            secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
            if not secret_key:
                _logger.error(f"[{request_time}] No secret key configured, IP: {client_ip}")
                return request.make_json_response(
                    {'status': 'error', 'message': 'Server configuration error'},
                    status=500
                )

            expires_in = int(request.env['ir.config_parameter'].sudo().get_param('auth_token.expires_in', 3600))
            payload = {
                'user_id': user.id,
                'exp': int((datetime.datetime.now() + datetime.timedelta(seconds=expires_in)).timestamp()),
                'iat': int(datetime.datetime.now().timestamp())
            }
            payload_str = json.dumps(payload)
            header = base64.urlsafe_b64encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode()).decode().rstrip('=')
            payload_b64 = base64.urlsafe_b64encode(payload_str.encode()).decode().rstrip('=')
            signature = base64.urlsafe_b64encode(
                hmac.new(secret_key.encode(), f'{header}.{payload_b64}'.encode(), hashlib.sha256).digest()
            ).decode().rstrip('=')
            token = f'{header}.{payload_b64}.{signature}'

            # Session token (only if you really need it – many APIs drop this)
            session_token = request.session.sid

            _logger.info(f"[{request_time}] Login success: {login}, UID: {uid}, IP: {client_ip}")

            result = {
                'status': 'success',
                'session_token': session_token,
                'user_id': user.id,
                'expires_in': expires_in,
                'token': token
            }

            return request.make_json_response(result, status=200)

        except AccessDenied:
            _logger.warning(f"[{request_time}] Access denied for login: {params.get('login', 'unknown')}, IP: {client_ip}")
            return request.make_json_response(
                {'status': 'error', 'message': 'Invalid credentials'},
                status=401
            )

        except ValidationError as ve:
            _logger.error(f"[{request_time}] Validation error from IP: {client_ip}: {str(ve)}")
            return request.make_json_response(
                {'status': 'error', 'message': str(ve)},
                status=400
            )

        except Exception as e:
            _logger.exception(f"[{request_time}] Unexpected error from IP: {client_ip}: {str(e)}")
            return request.make_json_response(
                {'status': 'error', 'message': 'Internal server error'},
                status=500
            )