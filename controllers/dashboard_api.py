# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import json
from datetime import datetime

class EcommerceDashboardAPI(http.Controller):

    @http.route('/api/v1/dashboard', type='json', auth='user', methods=['POST'], csrf=False)
    def get_dashboard(self, **kwargs):
        """
        Returns ecommerce dashboard metrics:
        - total carts
        - total orders
        - abandoned carts
        - total revenue
        - total products
        """

        try:
            # === TOTAL PRODUCTS ===
            total_products = request.env['product.product'].sudo().search_count([])

            # === ORDERS ===
            orders = request.env['sale.order'].sudo()

            total_orders = orders.search_count([
                ('state', 'in', ['sale', 'done'])
            ])

            # === REVENUE ===
            paid_orders = orders.search([
                ('state', 'in', ['sale', 'done'])
            ])
            total_revenue = sum(paid_orders.mapped('amount_total'))

            # === CARTS ===
            all_carts = orders.search([
                ('state', '=', 'draft'),
                ('is_cart', '=', True)
            ])
            total_carts = len(all_carts)

            # === ABANDONED CARTS ===
            abandoned_carts = all_carts.filtered(
                lambda c: (datetime.now() - c.create_date).days >= 1
            )
            total_abandoned = len(abandoned_carts)

            # === RESPONSE PAYLOAD ===
            data = {
                "metrics": {
                    "total_carts": total_carts,
                    "total_orders": total_orders,
                    "abandoned_carts": total_abandoned,
                    "total_revenue": total_revenue,
                    "total_products": total_products,
                },

                "latest_orders": [{
                    "id": o.id,
                    "name": o.name,
                    "customer": o.partner_id.name,
                    "amount_total": o.amount_total,
                    "state": o.state,
                    "date_order": o.date_order,
                } for o in orders.search([], order="id desc", limit=10)]
            }

            return {
                "status": "success",
                "data": data
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
