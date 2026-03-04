# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request, Response
from odoo.exceptions import AccessDenied
import logging
import datetime
import json
import jwt

_logger = logging.getLogger(__name__)

class LoginAuthenticationAPI(http.Controller):

    @http.route([
        '/api/auth/login',
        '/api/v1/auth/login',
    ], type='http', auth="public", csrf=False, methods=['POST'])
    def auth_login(self, **kwargs):
        """
        Public API endpoint for user authentication using the current database.
        Returns JWT access token on success.
        """
        client_ip = request.httprequest.remote_addr
        now = datetime.datetime.utcnow()  # UTC timestamps for JWT
        request_time = now.isoformat()

        try:
            # Parse JSON body manually (since type='http')
            try:
                data = request.httprequest.get_json() or {}
            except ValueError:
                _logger.warning(f"[{request_time}] Invalid JSON payload from {client_ip}")
                return self._json_response(
                    {'status': 'error', 'message': 'Invalid JSON payload'},
                    status=400
                )

            login = str(data.get('login', '')).strip().lower()
            password = str(data.get('password', '')).strip()

            if not login or not password:
                _logger.warning(f"[{request_time}] Empty credentials from {client_ip}")
                return self._json_response(
                    {'status': 'error', 'message': 'Login and password are required'},
                    status=400
                )

            # Use the CURRENT database automatically
            db = request.env.cr.dbname   # ← This is the active DB for this request

            # Odoo 18+ authentication style
            credential = {
                'login': login,
                'password': password,
                'type': 'password',  # Required for password login
            }
            uid = request.session.authenticate(db, credential)

            # Authentication successful
            user = request.env.user  # Already loaded after successful auth
            partner = user.partner_id

            # Load JWT secret
            secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
            if not secret_key:
                _logger.error(f"[{request_time}] Missing 'auth_token.secret_key' in ir.config_parameter")
                return self._json_response(
                    {'status': 'error', 'message': 'Server configuration error'},
                    status=500
                )

            expires_in = int(request.env['ir.config_parameter'].sudo().get_param(
                'auth_token.expires_in', '3600'
            ))

            payload = {
                'sub': str(user.id),
                'user_id': user.id,
                'iat': int(now.timestamp()),
                'exp': int((now + datetime.timedelta(seconds=expires_in)).timestamp()),
                # Optional: 'db': db   # ← add only if your frontend needs to know/verify the DB name
            }

            access_token = jwt.encode(payload, secret_key, algorithm='HS256')

            _logger.info(f"[{request_time}] Login success → {login} (UID: {uid}) from {client_ip} in DB: {db}")

            response_data = {
                'status': 'success',
                'access_token': access_token,
                'token_type': 'Bearer',
                'expires_in': expires_in,
                'user_id': user.id,
                'name': partner.name or '',
                'email': partner.email or '',
                'phone': partner.phone or '',
            }

            return self._json_response(response_data, status=200)

        except AccessDenied:
            _logger.warning(f"[{request_time}] Auth failed for '{login or 'unknown'}' from {client_ip}")
            return self._json_response(
                {'status': 'error', 'message': 'Invalid credentials'},
                status=401
            )

        except jwt.PyJWTError as jwt_err:
            _logger.error(f"[{request_time}] JWT error from {client_ip}: {str(jwt_err)}")
            return self._json_response(
                {'status': 'error', 'message': 'Internal server error'},
                status=500
            )

        except Exception as e:
            _logger.exception(
                "[%s] Login endpoint error from %s - %s: %s",
                request_time, client_ip, type(e).__name__, str(e)
            )
            return self._json_response(
                {'status': 'error', 'message': 'Internal server error'},
                status=500
            )

    def _json_response(self, data, status=200):
        """Helper for consistent JSON responses"""
        body = json.dumps(data, ensure_ascii=False)
        headers = [
            ('Content-Type', 'application/json; charset=utf-8'),
            ('Cache-Control', 'no-store'),
        ]
        return Response(body, status=status, headers=headers)