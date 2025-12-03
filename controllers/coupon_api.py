# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
import jwt  # pip install PyJWT
import logging
from datetime import datetime

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
        # --- 1. JWT Authentication ---
        auth_header = request.httprequest.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self._error_response('Missing or invalid Authorization header', 401)

        token = auth_header.split(' ')[1]

        secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
        if not secret_key:
            _logger.error("JWT secret key not configured")
            return self._error_response('Server configuration error', 500)

        try:
            payload = jwt.decode(token, secret_key, algorithms=['HS256'])
            user_id = payload.get('user_id')
            exp = payload.get('exp')

            if not user_id:
                return self._error_response('Invalid token: missing user_id', 401)

            now_ts = int(datetime.utcnow().timestamp())
            if exp and int(exp) < now_ts:
                return self._error_response('Token expired', 401)

        except jwt.ExpiredSignatureError:
            return self._error_response('Token expired', 401)
        except jwt.InvalidTokenError as e:
            _logger.warning(f"Invalid JWT token attempt: {e}")
            return self._error_response('Invalid token', 401)

        # --- 2. Switch to authenticated user ---
        request.update_env(user=user_id)

        try:
            params = request.params  # JSON body automatically parsed

            program_id = params.get('program_id')
            partner_id = params.get('partner_id')  # optional

            if not program_id or not isinstance(program_id, int):
                return self._error_response('Invalid or missing program_id')

            # --- 3. Check program ---
            program = request.env['loyalty.program'].search([
                ('id', '=', program_id),
                ('active', '=', True)
            ], limit=1)

            if not program:
                return self._error_response('Loyalty program not found or not active')

            if not getattr(program, 'allow_api_creation', True):
                return self._error_response('This program does not allow API coupon generation', 403)

            # --- 4. Determine partner ---
            if not partner_id:
                partner_id = request.env.user.partner_id.id

            partner = request.env['res.partner'].browse(partner_id)
            if not partner.exists():
                return self._error_response('Customer not found')

            if partner != request.env.user.partner_id and not request.env.user.has_group('sales_team.group_sale_salesman_all_leads'):
                return self._error_response('You can only generate coupons for yourself', 403)

            # --- 5. Create coupon ---
            coupon = request.env['sale.coupon'].create({
                'program_id': program.id,
                'partner_id': partner_id,
            })

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

    # --- Standard JSON error response ---
    def _error_response(self, message, status=400):
        """
        Returns a JSON dictionary for errors. Works properly for type='json' routes.
        """
        return {
            "status": "error",
            "message": message,
            "code": status
        }
