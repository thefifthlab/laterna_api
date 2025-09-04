from odoo import models, fields, api


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _get_price_from_pricelist(self, pricelist):
        self.ensure_one()
        product = self.with_context(
            quantity=1,
            pricelist=pricelist.id,
            uom=self.uom_id.id
        )
        return product.price