# -*- coding: utf-8 -*-
import json
from odoo import http
from odoo.http import request, Response
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from datetime import datetime, timedelta


class WebsiteCustomerDashboardAPI(http.Controller):

    @http.route('/api/v1/dashboard', type='http', auth='user', methods=['POST'], csrf=False, cors="*")
    def get_dashboard(self, **kwargs):
        try:
            user = request.env.user
            partner = user.partner_id
            Sale = request.env['sale.order'].sudo()
            Invoice = request.env['account.move'].sudo()

            # 1. Order & Revenue Stats (Using read_group for speed)
            order_stats = Sale.read_group(
                [('partner_id', '=', partner.id), ('state', 'in', ['sale', 'done'])],
                ['amount_total'], ['partner_id']
            )
            total_revenue = order_stats[0]['amount_total'] if order_stats else 0.0
            total_orders = order_stats[0]['partner_id_count'] if order_stats else 0

            # 2. Cart Stats
            cart_domain = [('partner_id', '=', partner.id), ('state', '=', 'draft'), ('website_id', '!=', False)]
            total_carts = Sale.search_count(cart_domain)

            abandon_cutoff = datetime.now() - timedelta(hours=24)
            abandoned_count = Sale.search_count(cart_domain + [('create_date', '<=', abandon_cutoff)])

            # 3. New Buyer Metrics: Financial & Product Variety
            # Total Unpaid Amount
            unpaid_invoices = Invoice.search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ['not_paid', 'partial']),
                ('move_type', '=', 'out_invoice')
            ])
            total_due = sum(unpaid_invoices.mapped('amount_residual'))

            # Unique Products Count (Breadth of purchase)
            unique_products_count = len(request.env['sale.order.line'].sudo().read_group(
                [('order_id.partner_id', '=', partner.id), ('order_id.state', 'in', ['sale', 'done'])],
                ['product_id'], ['product_id']
            ))

            # 4. Latest 10 Orders
            latest_recs = Sale.search(
                [('partner_id', '=', partner.id), ('state', 'in', ['sale', 'done'])],
                limit=10, order='date_order desc'
            )
            latest_orders = [{
                "id": o.id,
                "name": o.name,
                "amount_total": o.amount_total,
                "state": o.state,
                "date": o.date_order.strftime(DEFAULT_SERVER_DATETIME_FORMAT) if o.date_order else False,
            } for o in latest_recs]

            # 5. Build Final Response
            payload = {
                "status": "success",
                "data": {
                    "user": user.name,
                    "metrics": {
                        "total_carts": total_carts,
                        "total_orders": total_orders,
                        "abandoned_carts": abandoned_count,
                        "total_revenue": total_revenue,
                        "total_due_amount": total_due,
                        "unique_products_purchased": unique_products_count,
                    },
                    "latest_orders": latest_orders
                }
            }

            return Response(
                json.dumps(payload),
                content_type='application/json;charset=utf-8',
                status=200
            )

        except Exception as e:
            return Response(
                json.dumps({"status": "error", "message": str(e)}),
                content_type='application/json;charset=utf-8',
                status=500
            )