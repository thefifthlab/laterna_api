from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class CartAPI(http.Controller):

    @http.route('/api/website/cart/add', type='json', auth='public', methods=['POST'], csrf=False)
    def api_add_to_cart(self, product_id=None, product_template_id=None, quantity=1, **kw):
        """
        Public JSON API to add product to cart.
        Expects JSON body like: {"product_id": 25, "quantity": 2}
        or {"product_template_id": 10, "quantity": 1}
        """
        if not product_id and not product_template_id:
            return {
                "success": False,
                "message": "product_id or product_template_id is required"
            }

        try:
            # Manually get the current website (fixes the AttributeError)
            website = request.env['website'].get_current_website()
            if not website:
                return {"success": False, "message": "No website found"}

            # Get current sales order (cart) or create one, using the website
            sale_order = website.sale_get_order(force_create=True)

            # Determine the correct product_id
            if product_template_id:
                product = request.env['product.product'].sudo().search([
                    ('product_tmpl_id', '=', int(product_template_id))
                ], order='id', limit=1)
                if not product:
                    return {"success": False, "message": "Product not found"}
                product_id = product.id
            else:
                product_id = int(product_id)

            # Check if product exists and is published on the website
            product = request.env['product.product'].sudo().browse(product_id)
            if not product.exists() or not product.website_published or not product.is_published:
                return {"success": False, "message": "Product not available on this website"}

            quantity = float(quantity or 1)
            if quantity <= 0:
                return {"success": False, "message": "Quantity must be positive"}

            # Add to cart using the website's _cart_update method
            sale_order._cart_update(
                product_id=product_id,
                add_qty=quantity,
                set_qty=0  # 0 means add (don't replace existing quantity)
            )

            # Refresh the order to get updated totals
            sale_order = website.sale_get_order()

            # Calculate cart quantity
            cart_quantity = sum(line.product_uom_qty for line in sale_order.order_line)

            _logger.info(f"Product {product_id} added to cart {sale_order.id} for partner {request.env.user.partner_id.id if request.env.user.partner_id else 'guest'}")

            return {
                "success": True,
                "message": "Product added to cart successfully",
                "cart_quantity": int(cart_quantity),
                "order_id": sale_order.id,
                "currency": sale_order.currency_id.name,
                "amount_total": float(sale_order.amount_total),
                "amount_untaxed": float(sale_order.amount_untaxed)
            }

        except Exception as e:
            _logger.error(f"Error adding to cart: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to add product: {str(e)}"
            }