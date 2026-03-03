from odoo import models, fields, api
import time

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ------------------------------
    # Create new cart
    # ------------------------------
    def create_cart(self, partner_id=None):
        partner = None
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
        else:
            partner = self.env['res.partner'].create({
                'name': f'Guest_{int(time.time())}',
                'customer_rank': 1
            })
        return self.create({'partner_id': partner.id, 'state': 'draft'})

    # ------------------------------
    # Add product to cart
    # ------------------------------
    def add_product_to_cart(self, product_id, quantity):
        product = self.env['product.product'].browse(product_id)
        if not product.exists():
            raise ValueError("Product not found")

        line = self.order_line.filtered(lambda l: l.product_id.id == product_id)
        if line:
            line.product_uom_qty += quantity
        else:
            self.write({
                'order_line': [(0, 0, {
                    'product_id': product.id,
                    'product_uom_qty': quantity,
                    'price_unit': product.lst_price,
                })]
            })
        return True

    # ------------------------------
    # Update or remove line
    # ------------------------------
    def update_cart_line(self, line_id, quantity=None, remove=False):
        line = self.env['sale.order.line'].browse(line_id)
        if not line.exists():
            raise ValueError("Line not found")
        if remove:
            line.unlink()
        elif quantity is not None:
            line.product_uom_qty = quantity
        return self._get_totals()

    # ------------------------------
    # Apply discount
    # ------------------------------
    def apply_discount(self, code):
        if code == 'SAVE10':
            discount = 10
            self.amount_total = self.amount_total * 0.9
            return {'discount_applied': True, 'discount_percent': discount, 'new_total': self.amount_total}
        return {'discount_applied': False, 'message': 'Invalid code'}

    # ------------------------------
    # Confirm checkout
    # ------------------------------
    def confirm_checkout(self, address_data=None):
        if address_data:
            self.partner_id.write(address_data)
        self.action_confirm()
        return {'order_id': self.id, 'status': self.state}

    # ------------------------------
    # Compute totals
    # ------------------------------
    def _get_totals(self):
        return {
            'subtotal': self.amount_untaxed,
            'tax': self.amount_tax,
            'total': self.amount_total,
        }
