# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessDenied, ValidationError
import logging
import datetime
import jwt

_logger = logging.getLogger(__name__)

class LoginAuthenticationAPI(http.Controller):

    @http.route('/api/smerp/v1/auth/logout', type='json', auth="public", csrf=False, methods=['POST'], cors="*")
    def auth_logout(self):
        client_ip = request.httprequest.remote_addr
        request_time = datetime.datetime.now().isoformat()

        try:
            # Extract token from Authorization header
            auth_header = request.httprequest.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                _logger.warning(f"[{request_time}] Invalid or missing Authorization header from IP: {client_ip}")
                return {'status': 'error', 'message': 'Authorization header with Bearer token is required'}, 400

            token = auth_header.replace('Bearer ', '').strip()
            if not token:
                _logger.warning(f"[{request_time}] Empty token in Authorization header from IP: {client_ip}")
                return {'status': 'error', 'message': 'Token is required'}, 400

            # Verify JWT token
            secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
            if not secret_key:
                _logger.error(f"[{request_time}] No secret key configured, IP: {client_ip}")
                return {'status': 'error', 'message': 'Server configuration error'}, 500

            try:
                payload = jwt.decode(token, secret_key, algorithms=['HS256'])
                user_id = payload.get('user_id')
                if not user_id:
                    _logger.warning(f"[{request_time}] Invalid token payload from IP: {client_ip}")
                    return {'status': 'error', 'message': 'Invalid token'}, 401
            except jwt.ExpiredSignatureError:
                _logger.warning(f"[{request_time}] Expired token from IP: {client_ip}")
                return {'status': 'error', 'message': 'Token has expired'}, 401
            except jwt.InvalidTokenError:
                _logger.warning(f"[{request_time}] Invalid token from IP: {client_ip}")
                return {'status': 'error', 'message': 'Invalid token'}, 401

            # Verify user exists
            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists():
                _logger.warning(f"[{request_time}] User ID {user_id} not found, IP: {client_ip}")
                return {'status': 'error', 'message': 'User not found'}, 401

            # Invalidate session
            if request.session.sid:
                request.session.logout(keep_db=True)  # Keep DB connection for logging
                _logger.info(f"[{request_time}] Logout success for user ID: {user_id}, IP: {client_ip}")
            else:
                _logger.warning(f"[{request_time}] No active session found for user ID: {user_id}, IP: {client_ip}")

            return {'status': 'success', 'message': 'Logged out successfully'}

        except ValidationError as ve:
            _logger.error(f"[{request_time}] Validation error from IP: {client_ip}: {str(ve)}")
            return {'status': 'error', 'message': str(ve)}, 400

        except Exception as e:
            _logger.exception(f"[{request_time}] Unexpected error from IP: {client_ip}: {str(e)}")
            return {'status': 'error', 'message': 'Internal server error'}, 500