# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
import jwt  # pip install PyJWT
import logging
from datetime import datetime
import json

_logger = logging.getLogger(__name__)

class CouponAPI(http.Controller):

    @http.route('/api/coupon/create', type='json', auth='public', methods=['POST'], csrf=False, cors='*')
    def create_coupon(self, **kwargs):
        """
        Create coupon for authenticated user using JWT from /api/v1/auth/login
        Headers:
            Authorization: Bearer <jwt_token>
        Body:
            {
                "program_id": 5,
                "partner_id": 42   # optional - defaults to logged-in user's partner
            }
        """
        auth_header = request.httprequest.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self._error_response('Missing or invalid Authorization header', 401)

        token = auth_header.split(' ')[1]

        # Get secret key (same as in login controller)
        secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
        if not secret_key:
            _logger.error("JWT secret key not configured")
            return self._error_response('Server configuration error', 500)

        try:
            # Decode and verify JWT
            payload = jwt.decode(token, secret_key, algorithms=['HS256'])
            user_id = payload.get('user_id')
            exp = payload.get('exp')

            if not user_id:
                return self._error_response('Invalid token: missing user_id', 401)

            # Optional: check expiration manually (PyJWT does it by default with 'exp')
            if exp and datetime.utcfromtimestamp(exp) < datetime.utcnow():
                return self._error_response('Token expired', 401)

        except jwt.ExpiredSignatureError:
            return self._error_response('Token expired', 401)
        except jwt.InvalidTokenError as e:
            _logger.warning(f"Invalid JWT token attempt: {e}")
            return self._error_response('Invalid token', 401)

        # Switch to the authenticated user (NO sudo()!)
        request.uid = user_id
        request.env = request.env(user=user_id)

        try:
            params = request.params  # Odoo auto-parses JSON when type='json'

            program_id = params.get('program_id')
            partner_id = params.get('partner_id')  # optional

            if not program_id or not isinstance(program_id, int):
                return self._error_response('Invalid or missing program_id')

            # Let Odoo handle access rights naturally
            program = request.env['sale.coupon.program'].search([
                ('id', '=', program_id),
                ('active', '=', True)
            ], limit=1)

            if not program:
                return self._error_response('Loyalty program not found or not active')

            # Optional: Add a boolean field on program to allow/disallow API generation
            if not getattr(program, 'allow_api_creation', True):
                return self._error_response('This program does not allow API coupon generation', 403)

            # Default to current user's partner if not specified
            if not partner_id:
                partner_id = request.env.user.partner_id.id

            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return self._error_response('Customer not found')

            # Optional: Check if user has rights to generate for other partners
            if partner != request.env.user.partner_id and not request.env.user.has_group('sales_team.group_sale_salesman_all_leads'):
                return self._error_response('You can only generate coupons for yourself', 403)

            # Create coupon - Odoo will enforce all rules (max usage, per customer, etc.)
            coupon = request.env['sale.coupon'].create({
                'program_id': program.id,
                'partner_id': partner_id,
            })

            # Force code generation if not auto-generated
            if not coupon.code:
                coupon._generate_code()

            _logger.info(f"Coupon {coupon.code} created via API by user {request.env.user.name} (ID: {user_id})")

            return {
                'status': 'success',
                'coupon': {
                    'id': coupon.id,
                    'code': coupon.code,
                    'program_name': program.name,
                    'partner_name': partner.name,
                    'expiration_date': coupon.expiration_date.strftime('%Y-%m-%d') if coupon.expiration_date else None,
                    'state': coupon.state,
                },
                'message': 'Coupon created successfully'
            }

        except AccessError as e:
            _logger.warning(f"Access denied for user {user_id}: {e}")
            return self._error_response('Access denied', 403)
        except ValidationError as e:
            return self._error_response(f'Validation error: {e.name or str(e)}', 400)
        except Exception as e:
            _logger.exception(f"Coupon API error for user {user_id}")
            return self._error_response('Internal server error', 500)

    def _error_response(self, message, status=400):
        return request.make_response(
            json.dumps({'status': 'error', 'message': message}),
            headers={'Content-Type': 'application/json'},
            status=status
        )