from odoo import http, fields
from odoo.http import request
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ApiShopCheckout(http.Controller):

    def _get_current_website(self):
        """Safely get current website"""
        return request.env['website'].sudo().get_current_website()

    def _get_order(self, force_create=False):
        """Get or create sales order for current website"""
        website = self._get_current_website()
        order = website.sale_get_order(force_create=force_create)
        return order.sudo() if order else None

    def _get_product_detail(self, product_id):
        """Get basic product info"""
        product = request.env['product.product'].sudo().browse(product_id)
        return {
            "id": product.id,
            "name": product.name,
            "price": product.lst_price,
        } if product.exists() else None

    def _json_error_response(self, message, code=400):
        return {"status": "error", "message": message, "code": code}

    # ====================== ONE-STEP CHECKOUT CONFIRM ======================
    @http.route('/api/v1/shop/order/confirm', type='json', auth='public',
                methods=['POST'], csrf=False, cors='*', website=True)
    def api_confirm_order(self, **kw):
        data = request.httprequest.get_json() or kw
        payment_method_ref = data.get('payment_method', 'bank')
        products = data.get('products', [])

        if not products:
            return self._json_error_response("At least one product is required", 400)

        try:
            with request.env.cr.savepoint():
                sale_order = self._get_order(force_create=True)
                if not sale_order:
                    raise UserError("Could not create or retrieve sales order")

                # ====================== Add Products ======================
                sale_order.sudo().order_line.unlink()

                order_lines = []
                for item in products:
                    product_id = item.get('product_id')
                    qty = float(item.get('qty', 1))

                    if not product_id or not self._get_product_detail(product_id):
                        raise UserError(f"Product ID {product_id} not found")

                    order_lines.append((0, 0, {
                        'product_id': product_id,
                        'product_uom_qty': qty,
                    }))

                sale_order.sudo().write({'order_line': order_lines})

                # ====================== Addresses ======================
                website = self._get_current_website()
                public_partner = website.partner_id

                # Billing Address
                billing_vals = self._prepare_partner_vals(data.get('billing', {}))
                if billing_vals:
                    if sale_order.partner_id.id == public_partner.id:
                        # Create new partner (first time checkout)
                        partner = request.env['res.partner'].sudo().create(billing_vals)
                        sale_order.sudo().write({
                            'partner_id': partner.id,
                            'partner_invoice_id': partner.id,
                        })
                    else:
                        # Update existing partner
                        sale_order.partner_id.sudo().write(billing_vals)

                # Shipping Address
                shipping_vals = self._prepare_partner_vals(data.get('shipping', {}))
                if shipping_vals:
                    shipping_partner = request.env['res.partner'].sudo().create(shipping_vals)
                    sale_order.sudo().write({'partner_shipping_id': shipping_partner.id})
                elif not sale_order.partner_shipping_id:
                    sale_order.sudo().write({'partner_shipping_id': sale_order.partner_id.id})

                # ====================== Carrier ======================
                if data.get('carrier_id'):
                    try:
                        carrier = request.env['delivery.carrier'].sudo().browse(int(data['carrier_id']))
                        if carrier.exists():
                            sale_order.sudo().write({'carrier_id': carrier.id})
                            # Correct method in Odoo 18
                            sale_order.sudo()._compute_delivery_price()
                            _logger.info("Carrier applied: %s | Delivery Price: %s",
                                        carrier.name, sale_order.delivery_price)
                        else:
                            _logger.warning("Carrier ID %s not found", data['carrier_id'])
                    except (ValueError, TypeError):
                        _logger.warning("Invalid carrier_id: %s", data.get('carrier_id'))
                    except Exception as carrier_error:
                        _logger.warning("Failed to compute delivery price: %s", str(carrier_error))

                # ====================== Confirm Order ======================
                sale_order.sudo().action_confirm()

                # ====================== Create & Post Invoices ======================
                invoices = sale_order.sudo()._create_invoices()
                if not invoices:
                    raise UserError("No invoices were generated for this order.")

                invoices.sudo().action_post()

                # ====================== Register Payment ======================
                company = sale_order.company_id
                journal_type = 'cash' if payment_method_ref.lower() == 'cash' else 'bank'

                payment_method_line = request.env['account.payment.method.line'].sudo().search([
                    ('payment_type', '=', 'inbound'),
                    ('journal_id.type', '=', journal_type),
                    ('journal_id.company_id', '=', company.id),
                ], limit=1)

                if not payment_method_line:
                    return self._json_error_response(f"No inbound '{journal_type}' payment method found.", 500)

                unpaid_invoices = invoices.filtered(
                    lambda inv: inv.state == 'posted' and inv.payment_state not in ('paid', 'in_payment')
                )

                if unpaid_invoices:
                    payment_register = request.env['account.payment.register'].sudo().with_company(company).with_context(
                        active_model='account.move',
                        active_ids=unpaid_invoices.ids,
                    ).create({
                        'payment_date': fields.Date.today(),
                        'journal_id': payment_method_line.journal_id.id,
                        'payment_method_line_id': payment_method_line.id,
                        'amount': sum(unpaid_invoices.mapped('amount_residual')),
                    })
                    payment_register.action_create_payments()

                    # ====================== Reconcile Payments ======================
                    # Force reconciliation so payment_state moves from 'in_payment' → 'paid'
                    for invoice in unpaid_invoices:
                        receivable_accounts = invoice.line_ids.filtered(
                            lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
                        )
                        if not receivable_accounts:
                            continue

                        account_id = receivable_accounts[:1].account_id.id

                        payment_lines = request.env['account.move.line'].sudo().search([
                            ('move_id.move_type', '=', 'entry'),
                            ('account_id', '=', account_id),
                            ('reconciled', '=', False),
                            ('partner_id', '=', invoice.partner_id.commercial_partner_id.id),
                            ('amount_residual', '!=', 0),
                        ])

                        invoice_lines = invoice.line_ids.filtered(
                            lambda l: l.account_id.id == account_id and not l.reconciled
                        )

                        if payment_lines and invoice_lines:
                            (payment_lines | invoice_lines).sudo().reconcile()

                # ====================== Build Response ======================
                items = [{
                    'line_id': line.id,
                    'product_id': line.product_id.id,
                    'product_name': line.product_id.name,
                    'quantity': line.product_uom_qty,
                    'price_unit': line.price_unit,
                    'tax_amount': line.price_tax,
                    'subtotal': line.price_subtotal,
                    'total': line.price_total,
                } for line in sale_order.order_line]

                invoice_data = [{
                    "invoice_id": inv.id,
                    "invoice_reference": inv.name,
                    "invoice_state": inv.state,
                    "payment_state": inv.payment_state,
                    "amount_due": inv.amount_residual,
                } for inv in invoices]

                payload = {
                    "status": "success",
                    "order_overview": {
                        "cart_id": sale_order.id,
                        "status": sale_order.state,
                        "order_reference": sale_order.name,
                        "currency": sale_order.currency_id.name,
                        "summary": {
                            "untaxed_amount": sale_order.amount_untaxed,
                            "tax_amount": sale_order.amount_tax,
                            "total_amount": sale_order.amount_total,
                            "items_count": len(sale_order.order_line),
                        },
                        "items": items,
                    },
                    "invoices": invoice_data,
                }

                _logger.info("? Checkout successful - Order: %s | Invoices: %s",
                             sale_order.name,
                             [(inv.get("invoice_reference"), inv.get("payment_state")) for inv in invoice_data])

                return payload

        except UserError as e:
            return self._json_error_response(str(e), 400)
        except Exception as e:
            _logger.error("Checkout Error: %s", str(e), exc_info=True)
            return self._json_error_response("Internal server error. Please contact support.", 500)

    # ====================== HELPER METHOD ======================
    def _prepare_partner_vals(self, values):
        if not values:
            return {}

        vals = {
            'name': (values.get('name') or '').strip() or False,
            'email': (values.get('email') or '').strip() or False,
            'phone': (values.get('phone') or '').strip() or False,
            'street': (values.get('street') or '').strip() or False,
            'street2': (values.get('street2') or '').strip() or False,
            'city': (values.get('city') or '').strip() or False,
            'zip': (values.get('zip') or '').strip() or False,
            'country_id': int(values.get('country_id')) if str(values.get('country_id') or '').isdigit() else False,
        }

        # Handle State
        state_input = values.get('state_id') or values.get('state_name')
        if state_input and vals.get('country_id'):
            domain = [('country_id', '=', vals['country_id'])]
            try:
                domain += [('id', '=', int(state_input))]
            except ValueError:
                domain += ['|', ('name', '=ilike', state_input), ('code', '=ilike', state_input)]

            state = request.env['res.country.state'].sudo().search(domain, limit=1)
            if state:
                vals['state_id'] = state.id

        return {k: v for k, v in vals.items() if v}