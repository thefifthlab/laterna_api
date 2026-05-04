from odoo import http, fields
from odoo.http import request
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class ApiShopCheckout(http.Controller):

    def _get_current_website(self):
        return request.env['website'].sudo().get_current_website()

    def _get_order(self, force_create=False):
        website = self._get_current_website()
        order = website.sale_get_order(force_create=force_create)
        return order.sudo() if order else None

    def _get_fresh_draft_order(self):
        order = self._get_order(force_create=True)
        if not order:
            raise UserError("Could not create or retrieve a sales order.")

        if order.state not in ('draft', 'sent'):
            request.session.pop('sale_order_id', None)
            request.session.pop('website_sale_current_pl', None)
            order = self._get_order(force_create=True)

        if not order or order.state not in ('draft', 'sent'):
            raise UserError("Could not create a fresh draft sales order.")

        return order.sudo()

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

    def _build_success_response(self, sale_order, invoices):
        items = [{
            'line_id': line.id,
            'product_id': line.product_id.id,
            'product_template_id': line.product_id.product_tmpl_id.id if line.product_id else None,
            'product_name': line.name,
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

        return {
            "status": "success",
            "order_overview": {
                "cart_id": sale_order.id,
                "status": sale_order.state,
                "order_reference": sale_order.name,
                "currency": sale_order.currency_id.name,
                "user_id": request.env.user.id if not request.env.user._is_public() else None,
                "summary": {
                    "untaxed_amount": sale_order.amount_untaxed,
                    "tax_amount": sale_order.amount_tax,
                    "total_amount": sale_order.amount_total,
                    "delivery_amount": sale_order.delivery_price if hasattr(sale_order, 'delivery_price') else 0.0,
                    "items_count": len(sale_order.order_line),
                },
                "items": items,
            },
            "invoices": invoice_data,
        }

    # ====================== ONE-STEP CHECKOUT ======================
    @http.route('/api/v1/shop/order/confirm', type='json', auth='public',
                methods=['POST'], csrf=False, cors='*', website=True)
    def api_confirm_order(self, **kw):
        data = request.httprequest.get_json() or kw or {}
        payment_method_ref = data.get('payment_method', 'bank').lower()

        products = data.get('products', [])
        if not products:
            return {"status": "error", "message": "At least one product is required", "code": 400}

        try:
            with request.env.cr.savepoint():
                sale_order = self._get_fresh_draft_order()

                # Clear existing lines
                sale_order.order_line.unlink()

                # ====================== Add Products ======================
                order_lines = []
                for item in products:
                    template_id = item.get('product_id') or item.get('template_id')
                    qty = float(item.get('qty', 1))

                    if not template_id or qty <= 0:
                        raise UserError(f"Invalid product_id or quantity: {item}")

                    product = request.env['product.product'].sudo().search([
                        ('product_tmpl_id', '=', int(template_id))
                    ], limit=1)

                    if not product:
                        raise UserError(f"Product template {template_id} not found.")

                    order_lines.append((0, 0, {
                        'product_id': product.id,
                        'product_uom_qty': qty,
                    }))

                if order_lines:
                    sale_order.write({'order_line': order_lines})

                # ====================== Addresses ======================
                billing_vals = self._prepare_partner_vals(data.get('billing', {}))
                shipping_vals = self._prepare_partner_vals(data.get('shipping', {}))

                if billing_vals:
                    existing = request.env['res.partner'].sudo().search([
                        ('email', '=ilike', billing_vals.get('email'))
                    ], limit=1)
                    if existing:
                        existing.write(billing_vals)
                        partner = existing
                    else:
                        partner = request.env['res.partner'].sudo().create(billing_vals)

                    sale_order.write({
                        'partner_id': partner.id,
                        'partner_invoice_id': partner.id,
                    })

                if shipping_vals:
                    shipping_partner = request.env['res.partner'].sudo().create(shipping_vals)
                    sale_order.write({'partner_shipping_id': shipping_partner.id})
                elif not sale_order.partner_shipping_id:
                    sale_order.write({'partner_shipping_id': sale_order.partner_id.id})

                # ====================== CARRIER - FIXED ======================
                if data.get('carrier_id'):
                    self._apply_carrier(sale_order, int(data['carrier_id']))

                # ====================== Confirm + Invoice + Payment ======================
                sale_order.action_confirm()

                invoices = sale_order._create_invoices()
                if not invoices:
                    raise UserError("Failed to create invoice.")

                invoices.action_post()

                self._register_payment(invoices, payment_method_ref)

                # ====================== Response ======================
                response = self._build_success_response(sale_order, invoices)
                _logger.info("✅ Checkout successful - Order: %s", sale_order.name)
                return response

        except UserError as e:
            return {"status": "error", "message": str(e), "code": 400}
        except Exception as e:
            _logger.error("Checkout Error", exc_info=True)
            return {"status": "error", "message": "Internal server error. Please contact support.", "code": 500}

    # ====================== IMPROVED CARRIER HANDLING ======================
    def _apply_carrier(self, sale_order, carrier_id):
        """Safely apply carrier and calculate delivery price"""
        carrier = request.env['delivery.carrier'].sudo().browse(carrier_id)
        if not carrier.exists():
            _logger.warning("Carrier ID %s not found", carrier_id)
            return

        try:
            sale_order.write({'carrier_id': carrier.id})

            # Modern way in Odoo 17/18
            if hasattr(carrier, 'rate_shipment'):
                res = carrier.rate_shipment(sale_order)
                if res.get('success'):
                    price = res['price']
                    _logger.info("Carrier %s - Rate: %s (success)", carrier.name, price)
                else:
                    price = carrier.fixed_price if hasattr(carrier, 'fixed_price') else 0.0
                    _logger.warning("Rate shipment failed: %s", res.get('error_message'))
            else:
                # Fallback for fixed price carriers
                price = carrier.fixed_price if hasattr(carrier, 'fixed_price') else 0.0

            # Set delivery line
            sale_order.set_delivery_line(carrier, price)

            _logger.info("✅ Carrier applied: %s | Delivery Price: %s", carrier.name, price)

        except Exception as e:
            _logger.error("Failed to apply carrier %s: %s", carrier.name, str(e), exc_info=True)

    # ====================== PAYMENT ======================
    def _register_payment(self, invoices, payment_method_ref):
        if not invoices:
            return
        company = invoices[0].company_id
        journal_type = 'cash' if payment_method_ref == 'cash' else 'bank'

        payment_method_line = request.env['account.payment.method.line'].sudo().search([
            ('payment_type', '=', 'inbound'),
            ('journal_id.type', '=', journal_type),
            ('journal_id.company_id', '=', company.id),
        ], limit=1)

        if not payment_method_line:
            raise UserError(f"No inbound '{journal_type}' payment method found.")

        unpaid_invoices = invoices.filtered(
            lambda inv: inv.state == 'posted' and inv.payment_state not in ('paid', 'in_payment')
        )
        if not unpaid_invoices:
            return

        invoicing_env = self._get_invoicing_env(company)

        payment_register = invoicing_env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=unpaid_invoices.ids,
        ).create({
            'payment_date': fields.Date.today(),
            'journal_id': payment_method_line.journal_id.id,
            'payment_method_line_id': payment_method_line.id,
            'amount': sum(unpaid_invoices.mapped('amount_residual')),
        })

        payment_register.action_create_payments()
        request.env.cr.flush()

    def _get_invoicing_env(self, company):
        invoice_group = request.env.ref('account.group_account_invoice', raise_if_not_found=False)
        if invoice_group:
            invoice_user = request.env['res.users'].sudo().search([
                ('groups_id', 'in', invoice_group.id),
                ('company_ids', 'in', company.id),
                ('active', '=', True),
                ('share', '=', False),
            ], limit=1)
            if invoice_user:
                return request.env(user=invoice_user.id)

        _logger.warning("Falling back to admin user for payment.")
        admin_user = request.env.ref('base.user_admin')
        return request.env(user=admin_user.id)