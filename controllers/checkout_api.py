from odoo import http
from odoo.http import request
import json


class CheckoutAPI(http.Controller):

    # ------------------------------
    # Helper to return JSON
    # ------------------------------
    def _json(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers={"Content-Type": "application/json"},
            status=status
        )

    # ------------------------------
    # 1. Add product to cart
    # ------------------------------
    @http.route('/api/checkout/cart', type='json', auth='none', methods=['POST'], csrf=False)
    def add_to_cart(self, **kw):
        try:
            # API key check
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = request.jsonrequest or {}
            product_id = data.get('product_id')
            quantity = data.get('quantity', 1)
            partner_id = data.get('partner_id')
            email = data.get('email')

            if not product_id:
                return self._json({'error': 'Product ID is required'}, 400)

            # Create or get cart
            order = request.env['sale.order'].sudo().create_cart(partner_id=partner_id)

            if not partner_id and email:
                order.partner_id.email = email

            # Add item
            order.add_product_to_cart(product_id, quantity)

            # Prepare return items
            totals = order._get_totals()
            items = [{
                'id': l.id,
                'name': l.product_id.name,
                'qty': l.product_uom_qty,
                'price': l.price_unit,
            } for l in order.order_line]

            return self._json({
                'success': True,
                'cart_id': order.id,
                'user_type': 'guest' if not partner_id else 'partner',
                'items': items,
                **totals
            })

        except Exception as e:
            return self._json({'error': str(e)}, 400)

    # ------------------------------
    # 2. Get Cart Details
    # ------------------------------
    @http.route('/api/checkout/cart/<int:cart_id>', type='http', auth='none', methods=['GET'], csrf=False)
    def get_cart(self, cart_id):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return self._json({'error': 'Cart not found'}, 404)

            totals = order._get_totals()
            items = [{
                'id': l.id,
                'name': l.product_id.name,
                'qty': l.product_uom_qty,
                'subtotal': l.price_subtotal,
            } for l in order.order_line]

            partner_name = order.partner_id.name or ""
            user_type = 'guest' if partner_name.startswith('Guest_') else 'partner'

            return self._json({
                'success': True,
                'cart_id': cart_id,
                'user_type': user_type,
                'items': items,
                **totals
            })

        except Exception as e:
            return self._json({'error': str(e)}, 400)

    # ------------------------------
    # 3. Update Cart Line
    # ------------------------------
    @http.route('/api/checkout/update/<int:cart_id>', type='json', auth='none', methods=['POST'], csrf=False)
    def update_cart(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = request.jsonrequest or {}
            line_id = data.get('line_id')
            quantity = data.get('quantity')
            remove = data.get('remove')

            if not line_id:
                return self._json({'error': 'Line ID is required'}, 400)

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return self._json({'error': 'Cart not found'}, 404)

            totals = order.update_cart_line(line_id, quantity, remove)

            return self._json({
                'success': True,
                'cart_id': cart_id,
                **totals
            })

        except Exception as e:
            return self._json({'error': str(e)}, 400)

    # ------------------------------
    # 4. Apply Discount Code
    # ------------------------------
    @http.route('/api/checkout/discount/<int:cart_id>', type='json', auth='none', methods=['POST'], csrf=False)
    def apply_discount(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = request.jsonrequest or {}
            code = data.get('code')

            if not code:
                return self._json({'error': 'Discount code is required'}, 400)

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return self._json({'error': 'Cart not found'}, 404)

            result = order.apply_discount(code)

            return self._json({
                'success': True,
                **result
            })

        except Exception as e:
            return self._json({'error': str(e)}, 400)

    # ------------------------------
    # 5. Confirm Checkout
    # ------------------------------
    @http.route('/api/checkout/confirm/<int:cart_id>', type='json', auth='none', methods=['POST'], csrf=False)
    def confirm_order(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = request.jsonrequest or {}
            address_data = data.get('address')

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return self._json({'error': 'Cart not found'}, 404)

            result = order.confirm_checkout(address_data)
            partner_name = order.partner_id.name or ""

            return self._json({
                'success': True,
                'user_type': 'guest' if partner_name.startswith('Guest_') else 'partner',
                **result
            })

        except Exception as e:
            return self._json({'error': str(e)}, 400)

