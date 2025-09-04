from odoo import http
from odoo.http import request
import json


class CartAPI(http.Controller):

    @http.route('/api/v1/cart/add', type='json', methods=['POST'], auth='none', csrf=False)
    def add_to_cart(self, **kwargs):
        # Token Authentication
        headers = request.httprequest.headers
        token = headers.get('Authorization')
        if not token or not token.startswith('Bearer '):
            return {'error': 'Invalid token'}, 401

        user = request.env['res.users'].sudo().search([('api_token', '=', token[7:])], limit=1)
        if not user:
            return {'error': 'Invalid token'}, 401

        # Parse request body
        try:
            data = json.loads(request.httprequest.data)
        except:
            return {'error': 'Invalid JSON data'}, 400

        product_id = data.get('product_id')
        quantity = data.get('quantity')
        variant_id = data.get('variant_id')

        # Validate required fields
        if not product_id or not quantity or quantity < 1:
            return {'error': 'Missing product_id or invalid quantity'}, 400

        # Retrieve product and variant
        Product = request.env['product.product'].sudo().with_user(user)
        product = Product.browse(product_id)
        if not product.exists():
            return {'error': 'Product not found'}, 404

        # Use variant if provided, else check product itself is a variant
        if variant_id:
            variant = Product.browse(variant_id)
            if not variant.exists() or variant.product_tmpl_id.id != product_id:
                return {'error': 'Invalid variant'}, 400
            product_to_add = variant
        else:
            # If the product is a template, try to find a single variant
            if product.product_tmpl_id.product_variant_count > 1:
                return {'error': 'Variant required for product with multiple variants'}, 400
            product_to_add = product

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
            })

        # Update or create order line
        OrderLine = request.env['sale.order.line'].with_user(user)
        existing_line = OrderLine.search([
            ('order_id', '=', cart.id),
            ('product_id', '=', product_to_add.id)
        ], limit=1)

        if existing_line:
            existing_line.product_uom_qty += quantity
        else:
            OrderLine.create({
                'order_id': cart.id,
                'product_id': product_to_add.id,
                'product_uom_qty': quantity,
            })

        # Calculate totals
        cart._amount_all()
        total_items = sum(cart.order_line.mapped('product_uom_qty'))
        total_price = cart.amount_total

        return {
            'cart_id': cart.id,
            'total_items': total_items,
            'total_price': total_price,
        }
