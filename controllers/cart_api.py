# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
import logging
import datetime
import json
import base64
import hmac
import hashlib
from odoo.http import Response

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    def _log_event(self, event, client_ip, request_time, **kwargs):
        """Log structured event data."""
        log_data = {
            'timestamp': request_time,
            'event': event,
            'ip': client_ip,
            **kwargs
        }
        _logger.info(json.dumps(log_data))

    def _validate_jwt(self, token, client_ip, request_time):
        """Validate JWT token and return user_id if valid."""
        try:
            # Validate token existence and basic format
            if not token or not isinstance(token, str):
                self._log_event('invalid_token', client_ip, request_time, error='Token is empty or not a string')
                return None, {'status': 'error', 'message': 'Invalid token: empty or not a string'}, 401

            # Check for correct JWT structure (header.payload.signature)
            if token.count('.') != 2:
                self._log_event('invalid_token_format', client_ip, request_time, token=token[:10] + '...' if token else 'None')
                return None, {'status': 'error', 'message': 'Invalid token format: must contain header.payload.signature'}, 401

            header_b64, payload_b64, signature_b64 = token.split('.')

            # Decode header and payload with robust padding handling
            try:
                # Add padding if needed
                header_b64_padded = header_b64 + '=' * ((-len(header_b64) % 4) if len(header_b64) % 4 else 0)
                payload_b64_padded = payload_b64 + '=' * ((-len(payload_b64) % 4) if len(payload_b64) % 4 else 0)
                header = json.loads(base64.urlsafe_b64decode(header_b64_padded).decode('utf-8'))
                payload = json.loads(base64.urlsafe_b64decode(payload_b64_padded).decode('utf-8'))
            except base64.binascii.Error as e:
                self._log_event('jwt_decode_error', client_ip, request_time, error=str(e), token=token[:10] + '...')
                return None, {'status': 'error', 'message': f'Invalid token encoding: {str(e)}'}, 401
            except (ValueError, UnicodeDecodeError) as e:
                self._log_event('jwt_parse_error', client_ip, request_time, error=str(e), token=token[:10] + '...')
                return None, {'status': 'error', 'message': f'Invalid token structure: {str(e)}'}, 401

            # Verify secret key
            secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
            if not secret_key:
                self._log_event('invalid_secret_key', client_ip, request_time)
                return None, {'status': 'error', 'message': 'Server configuration error'}, 500

            # Verify signature
            expected_signature = base64.urlsafe_b64encode(
                hmac.new(secret_key.encode(), f'{header_b64}.{payload_b64}'.encode(), hashlib.sha256).digest()
            ).decode().rstrip('=')
            if not hmac.compare_digest(signature_b64, expected_signature):
                self._log_event('invalid_signature', client_ip, request_time, token=token[:10] + '...')
                return None, {'status': 'error', 'message': 'Invalid token signature'}, 401

            # Check expiration
            current_time = int(datetime.datetime.now().timestamp())
            token_exp = payload.get('exp', 0)
            if not isinstance(token_exp, (int, float)) or token_exp < current_time:
                self._log_event('token_expired', client_ip, request_time, exp=token_exp, current_time=current_time)
                return None, {'status': 'error', 'message': 'Token expired'}, 401

            # Validate user
            user_id = payload.get('user_id')
            if not user_id or not isinstance(user_id, int):
                self._log_event('missing_user_id', client_ip, request_time, token=token[:10] + '...')
                return None, {'status': 'error', 'message': 'Invalid token: missing or invalid user_id'}, 401

            user = request.env['res.users'].sudo().browse(user_id)
            if not user.exists():
                self._log_event('invalid_user', client_ip, request_time, user_id=user_id)
                return None, {'status': 'error', 'message': 'Invalid user'}, 401

            return user_id, None, 200

        except Exception as e:
            self._log_event('jwt_validation_error', client_ip, request_time, error=str(e), token=token[:10] + '...' if token else 'None')
            return None, {'status': 'error', 'message': f'Invalid token: {str(e)}'}, 401

    @http.route('/api/v1/cart/add', type='json', auth="public", csrf=False, methods=['POST'], cors="*")
    def add_to_cart(self, **kwargs):
        """Add product to cart with JWT authentication."""
        client_ip = request.httprequest.remote_addr
        request_time = datetime.datetime.now().isoformat()

        self._log_event('request_start', client_ip, request_time, endpoint='/api/v1/cart/add')

        try:
            # Parse JSON payload
            try:
                params = request.httprequest.get_json()
            except ValueError as e:
                self._log_event('invalid_json_format', client_ip, request_time, error=str(e))
                return {'status': 'error', 'message': 'Invalid JSON format'}

            # Validate Authorization header
            auth_header = request.httprequest.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                self._log_event('missing_token', client_ip, request_time)
                return {'status': 'error', 'message': 'Authorization token required'}

            token = auth_header[7:]  # Remove 'Bearer ' prefix
            user_id, error_data, status_code = self._validate_jwt(token, client_ip, request_time)
            if error_data:
                self._log_event('auth_failed', client_ip, request_time, message=error_data['message'])
                return error_data

            # Validate required fields
            required_fields = {'product_id', 'quantity'}
            missing_fields = required_fields - set(params.keys())
            if missing_fields:
                self._log_event('missing_fields', client_ip, request_time, fields=list(missing_fields))
                return {'status': 'error', 'message': f'Missing required fields: {", ".join(missing_fields)}'}

            # Sanitize and validate inputs
            try:
                product_id = int(params['product_id'])
                quantity = float(params['quantity'])
                if quantity <= 0:
                    self._log_event('invalid_quantity', client_ip, request_time, quantity=quantity)
                    return {'status': 'error', 'message': 'Quantity must be positive'}
            except (ValueError, TypeError) as e:
                self._log_event('invalid_input_types', client_ip, request_time, error=str(e))
                return {'status': 'error', 'message': 'Invalid product_id or quantity format'}

            # Validate product
            product = request.env['product.product'].sudo().browse(product_id)
            if not product.exists() or not product.sale_ok or not product.active:
                self._log_event('invalid_product', client_ip, request_time, product_id=product_id)
                return {'status': 'error', 'message': 'Invalid or unsaleable product'}

            # Get or create cart
            user = request.env['res.users'].sudo().browse(user_id)
            cart = request.env['sale.order'].sudo().search([
                ('partner_id', '=', user.partner_id.id),
                ('state', '=', 'draft'),
            ], limit=1)

            if not cart:
                cart = request.env['sale.order'].sudo().create({
                    'partner_id': user.partner_id.id,
                    'user_id': user_id,
                    'state': 'draft',
                })

            # Add or update product in cart
            order_line = cart.order_line.filtered(lambda line: line.product_id.id == product_id)
            if order_line:
                order_line[0].product_uom_qty += quantity
            else:
                request.env['sale.order.line'].sudo().create({
                    'order_id': cart.id,
                    'product_id': product_id,
                    'product_uom_qty': quantity,
                    'price_unit': product.list_price,
                })

            # Compute totals
            cart._compute_amounts()

            # Prepare response
            cart_items = [{
                'product_id': line.product_id.id,
                'name': line.product_id.name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'subtotal': line.price_subtotal
            } for line in cart.order_line]

            self._log_event('add_to_cart_success', client_ip, request_time,
                            user_id=user_id, product_id=product_id, quantity=quantity, cart_id=cart.id)
            return {
                'status': 'success',
                'data': {
                    'cart_id': cart.id,
                    'product_id': product_id,
                    'quantity': quantity,
                    'total': cart.amount_total,
                    'currency': cart.currency_id.name,
                    'items': cart_items
                }
            }

        except ValidationError as ve:
            self._log_event('validation_error', client_ip, request_time, error=str(ve))
            return {'status': 'error', 'message': str(ve)}
        except Exception as e:
            self._log_event('unexpected_error', client_ip, request_time, error=str(e))
            return {'status': 'error', 'message': 'Internal server error'}
