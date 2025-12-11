# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import datetime, timedelta

class WebsiteCustomerDashboardAPI(http.Controller):

    @http.route('/api/v1/dashboard', type='json', auth='user', methods=['POST'], csrf=False, cors="*")
    def get_dashboard(self, **kwargs):
        """
        Dashboard metrics for logged-in website customer:
        - Their carts
        - Their orders
        - Their abandoned carts
        - Total revenue (from their orders)
        - Total products
        """

        try:
            user = request.env.user
            partner = user.partner_id
            Sale = request.env['sale.order'].sudo()

            # --- Confirmed orders for this customer ---
            user_orders = Sale.search([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['sale', 'done'])
            ])
            total_orders = len(user_orders)
            total_revenue = sum(user_orders.mapped('amount_total'))

            # --- Carts (draft website orders) ---
            carts = Sale.search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'draft'),
                ('website_id', '!=', False)
            ])
            total_carts = len(carts)

            # --- Abandoned carts (older than 24 hours) ---
            abandoned_carts = carts.filtered(
                lambda c: c.create_date <= datetime.now() - timedelta(hours=24)
            )
            total_abandoned = len(abandoned_carts)

            # --- Total products (all visible products) ---
            total_products = request.env['product.product'].sudo().search_count([])

            # Total number of orders
            total_orders = len(user_orders)

            # Total revenue from all confirmed orders
            total_order_amount = sum(user_orders.mapped('amount_total'))


            # --- Latest 10 orders ---
            latest_orders = [{
                "id": o.id,
                "name": o.name,
                "amount_total": o.amount_total,
                "state": o.state,
                "date_order": o.date_order,
            } for o in user_orders.sorted(key=lambda x: x.id, reverse=True)[:10]]

            return {
                "status": "success",
                "data": {
                    "user": user.name,
                    "metrics": {
                        "total_carts": total_carts,
                        "total_orders": total_orders,
                        "abandoned_carts": total_abandoned,
                        "total_revenue": total_revenue,
                        # "total_products": total_products,
                        "total_order_amount": total_order_amount,
                    },
                    "latest_orders": latest_orders
                }
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
