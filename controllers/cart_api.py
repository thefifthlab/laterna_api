# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
import json
import jwt
import logging
from odoo.http import Response

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    def _error_response(self, message, status):
        """Helper function for consistent error responses."""
        _logger.warning(f"[CartAPI] Error: {message}, IP: {request.httprequest.remote_addr}")
        return Response(json.dumps({'status': 'error', 'message': message}), status=status, content_type='application/json')

    @http.route('/api/v1/cart/add', type='json', methods=['POST'], auth='none', csrf=False)
    def add_to_cart(self, **kwargs):
        client_ip = request.httprequest.remote_addr
        request_time = request.env.cr.now().isoformat()

        # Token Authentication
        headers = request.httprequest.headers
        token = headers.get('Authorization')
        if not token or not token.startswith('Bearer '):
            return self._error_response('Invalid or missing token', 401)

        # Validate JWT
        secret_key = request.env['ir.config_parameter'].sudo().get_param('auth_token.secret_key')
        if not secret_key:
            _logger.error(f"[{request_time}] No secret key configured, IP: {client_ip}")
            return self._error_response('Server configuration error', 500)

        try:
            payload = jwt.decode(token[7:], secret_key, algorithms=['HS256'])
            user_id = payload.get('user_id')
            user = request.env['res.users'].with_user(user_id).search([('id', '=', user_id)], limit=1)
            if not user:
                return self._error_response('Invalid token: user not found', 401)
        except jwt.ExpiredSignatureError:
            return self._error_response('Token has expired', 401)
        except jwt.InvalidTokenError:
            return self._error_response('Invalid token', 401)

        # Parse request body
        try:
            data = json.loads(request.httprequest.data)
        except json.JSONDecodeError:
            _logger.warning(f"[{request_time}] Invalid JSON from IP: {client_ip}")
            return self._error_response('Invalid JSON data', 400)

        # Validate input
        try:
            product_id = int(data.get('product_id'))
            quantity = int(data.get('quantity'))
            variant_id = int(data.get('variant_id')) if data.get('variant_id') else None
            if not product_id or quantity < 1:
                return self._error_response('Missing product_id or invalid quantity', 400)
        except (TypeError, ValueError):
            return self._error_response('Invalid product_id, quantity, or variant_id format', 400)

        # Retrieve product
        Product = request.env['product.product'].with_user(user)
        product = Product.browse(product_id)
        if not product.exists() or not product.sale_ok:
            return self._error_response('Product not found or not saleable', 404)

        # Handle variant
        if variant_id:
            variant = Product.browse(variant_id)
            if not variant.exists() or variant.product_tmpl_id != product.product_tmpl_id:
                return self._error_response('Invalid variant', 400)
            product_to_add = variant
        else:
            product_to_add = product
            if product.product_tmpl_id.product_variant_count > 1:
                return self._error_response('Variant required for product with multiple variants', 400)

        # Get or create draft sale order (cart)
        SaleOrder = request.env['sale.order'].with_user(user)
        cart = SaleOrder.search([
            ('user_id', '=', user.id),
            ('state', '=', 'draft')
        ], limit=1)

        if not cart:
            cart = SaleOrder.create({
                'user_id': user.id,
                'partner_id': user.partner_id.id,
                'pricelist_id': user.partner_id.property_product_pricelist.id,
            })

        # Update or create order line
        OrderLine = request.env['sale.order.line'].with_user(user)
        existing_line = OrderLine.search([
            ('order_id', '=', cart.id),
            ('product_id', '=', product_to_add.id)
        ], limit=1)

        try:
            if existing_line:
                existing_line.product_uom_qty += quantity
            else:
                OrderLine.create({
                    'order_id': cart.id,
                    'product_id': product_to_add.id,
                    'product_uom_qty': quantity,
                })
        except ValidationError as ve:
            _logger.error(f"[{request_time}] Validation error for user {user.id}, IP: {client_ip}: {str(ve)}")
            return self._error_response(str(ve), 400)

        # Calculate totals with proper context
        cart.with_context(pricelist=cart.pricelist_id.id)._amount_all()
        total_items = sum(cart.order_line.mapped('product_uom_qty'))
        total_price = cart.amount_total

        _logger.info(f"[{request_time}] Added {quantity} of product {product_to_add.id} to cart {cart.id} for user {user.id}, IP: {client_ip}")

        return Response(json.dumps({
            'status': 'success',
            'cart_id': cart.id,
            'total_items': float(total_items),  # Ensure JSON-serializable
            'total_price': float(total_price),  # Ensure JSON-serializable
            'product_added': {
                'product_id': product_to_add.id,
                'name': product_to_add.name,
                'quantity': quantity
            }
        }), status=200, content_type='application/json')