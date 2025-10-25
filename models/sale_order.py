from odoo import models, fields, api
import time

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def create_cart(self, partner_id=False, website_id=1):
        """Create or get a cart for a partner or guest."""
        if not partner_id:
            # Create guest partner if none provided
            guest_partner = self.env['res.partner'].sudo().create({
                'name': f'Guest_{request.httprequest.remote_addr}_{int(time.time())}',
                'is_company': False,
                'type': 'delivery',
                'email': f'guest_{int(time.time())}@example.com',
            })
            partner_id = guest_partner.id
        order = self.env['sale.order'].sudo().search([
            ('partner_id', '=', partner_id),
            ('website_id', '=', website_id),
            ('state', '=', 'draft')
        ], limit=1)
        if not order:
            order = self.create({
                'partner_id': partner_id,
                'website_id': website_id,
            })
        return order

    def add_product_to_cart(self, product_id, quantity=1):
        """Add product to cart with validation."""
        product = self.env['product.product'].browse(product_id)
        if not product.exists():
            raise ValueError("Product not found")
        line = self.order_line.filtered(lambda l: l.product_id.id == product_id)
        if line:
            line.write({'product_uom_qty': line.product_uom_qty + quantity})
        else:
            self.write({'order_line': [(0, 0, {
                'product_id': product_id,
                'product_uom_qty': quantity,
                'price_unit': product.lst_price,
            })]})
        return self._get_totals()

    def update_cart_line(self, line_id, quantity=None, remove=False):
        """Update or remove a cart line."""
        line = self.order_line.browse(line_id)
        if not line.exists():
            raise ValueError("Cart line not found")
        if remove:
            line.unlink()
        elif quantity is not None:
            line.product_uom_qty = quantity
        return self._get_totals()

    def _get_totals(self):
        """Compute subtotal, taxes, and total."""
        return {
            'subtotal': sum(self.order_line.mapped('price_subtotal')),
            'taxes': self.amount_tax,
            'total': self.amount_total,
        }

    def apply_discount(self, code):
        """Apply discount code (simplified example)."""
        if code == 'SAVE10':
            self._compute_amount_all()  # Recalculate amounts
            return {'discount_applied': True, 'total': self.amount_total * 0.9, **self._get_totals()}
        raise ValueError("Invalid discount code")

    def confirm_checkout(self, address_data=None):
        """Confirm order with optional address update."""
        if address_data:
            self.partner_id.write(address_data)
        self.action_confirm()
        return {'order_id': self.id, 'state': self.state, 'tracking_url': f'/my/orders/{self.id}'}