from odoo import http
from odoo.http import request
import json
import time

class CheckoutAPI(http.Controller):

    # ------------------------------
    # Helper: API Key validation
    # ------------------------------
    def _check_api_key(self):
        api_key = request.httprequest.headers.get('X-API-Key')
        expected_key = request.env['ir.config_parameter'].sudo().get_param('checkout_api.secret_key')
        if not api_key or api_key != expected_key:
            return request.make_response(json.dumps({'error': 'Unauthorized'}), status=401)
        return None

    # ------------------------------
    # 1. Add product to cart
    # ------------------------------
    @http.route('/api/checkout/cart', type='http', auth='none', methods=['POST'], csrf=False)
    def add_to_cart(self, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = json.loads(request.httprequest.data or '{}')
            product_id = data.get('product_id')
            quantity = data.get('quantity', 1)
            partner_id = data.get('partner_id')
            email = data.get('email')

            if not product_id:
                return request.make_response(json.dumps({'error': 'Product ID is required'}), status=400)

            order = request.env['sale.order'].sudo().create_cart(partner_id=partner_id)
            if not partner_id and email:
                order.partner_id.email = email
            order.add_product_to_cart(product_id, quantity)

            totals = order._get_totals()
            items = [{
                'id': l.id,
                'name': l.product_id.name,
                'qty': l.product_uom_qty,
                'price': l.price_unit
            } for l in order.order_line]

            return request.make_response(json.dumps({
                'success': True,
                'cart_id': order.id,
                'user_type': 'guest' if not partner_id else 'partner',
                'items': items,
                **totals
            }), headers={'Content-Type': 'application/json'})

        except Exception as e:
            return request.make_response(json.dumps({'error': str(e)}), status=400)

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
                return request.make_response(json.dumps({'error': 'Cart not found'}), status=404)

            totals = order._get_totals()
            items = [{
                'id': l.id,
                'name': l.product_id.name,
                'qty': l.product_uom_qty,
                'subtotal': l.price_subtotal
            } for l in order.order_line]

            return request.make_response(json.dumps({
                'success': True,
                'cart_id': cart_id,
                'user_type': 'guest' if order.partner_id.name.startswith('Guest_') else 'partner',
                'items': items,
                **totals
            }), headers={'Content-Type': 'application/json'})

        except Exception as e:
            return request.make_response(json.dumps({'error': str(e)}), status=400)

    # ------------------------------
    # 3. Update Cart Line
    # ------------------------------
    @http.route('/api/checkout/update/<int:cart_id>', type='http', auth='none', methods=['POST'], csrf=False)
    def update_cart(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = json.loads(request.httprequest.data or '{}')
            line_id = data.get('line_id')
            quantity = data.get('quantity')
            remove = data.get('remove')

            if not line_id:
                return request.make_response(json.dumps({'error': 'Line ID is required'}), status=400)

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return request.make_response(json.dumps({'error': 'Cart not found'}), status=404)

            totals = order.update_cart_line(line_id, quantity, remove)
            return request.make_response(json.dumps({
                'success': True,
                'cart_id': cart_id,
                **totals
            }), headers={'Content-Type': 'application/json'})

        except Exception as e:
            return request.make_response(json.dumps({'error': str(e)}), status=400)

    # ------------------------------
    # 4. Apply Discount Code
    # ------------------------------
    @http.route('/api/checkout/discount/<int:cart_id>', type='http', auth='none', methods=['POST'], csrf=False)
    def apply_discount(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = json.loads(request.httprequest.data or '{}')
            code = data.get('code')
            if not code:
                return request.make_response(json.dumps({'error': 'Discount code is required'}), status=400)

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return request.make_response(json.dumps({'error': 'Cart not found'}), status=404)

            result = order.apply_discount(code)
            return request.make_response(json.dumps({
                'success': True,
                **result
            }), headers={'Content-Type': 'application/json'})

        except Exception as e:
            return request.make_response(json.dumps({'error': str(e)}), status=400)

    # ------------------------------
    # 5. Confirm Checkout
    # ------------------------------
    @http.route('/api/checkout/confirm/<int:cart_id>', type='http', auth='none', methods=['POST'], csrf=False)
    def confirm_order(self, cart_id, **kw):
        try:
            auth_error = self._check_api_key()
            if auth_error:
                return auth_error

            data = json.loads(request.httprequest.data or '{}')
            address_data = data.get('address')

            order = request.env['sale.order'].sudo().browse(cart_id)
            if not order.exists():
                return request.make_response(json.dumps({'error': 'Cart not found'}), status=404)

            result = order.confirm_checkout(address_data)
            return request.make_response(json.dumps({
                'success': True,
                'user_type': 'guest' if order.partner_id.name.startswith('Guest_') else 'partner',
                **result
            }), headers={'Content-Type': 'application/json'})

        except Exception as e:
            return request.make_response(json.dumps({'error': str(e)}), status=400)
