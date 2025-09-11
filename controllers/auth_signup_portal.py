from odoo import http, fields
from odoo.http import request
import json
import logging
import re
from werkzeug.wrappers import Response


_logger = logging.getLogger(__name__)


class LaternaAuthenticationSignUp(http.Controller):
    @http.route('/api/v1/auth/register', type='http', auth='public', methods=['POST'], csrf=False, cors='*')
    def register_user(self, **kwargs):
        try:
            # Parse JSON data from request
            try:
                data = json.loads(request.httprequest.data)
            except json.JSONDecodeError:
                return self._make_response(
                    {'error': 'Invalid JSON data'},
                    status=400
                )

            # Validate required fields
            required_fields = ['email', 'password', 'name']
            if not all(data.get(field) for field in required_fields):
                return self._make_response(
                    {'error': 'Missing required fields: email, password, and name are required'},
                    status=400
                )

            email = data.get('email')
            # Validate email format
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                return self._make_response(
                    {'error': 'Invalid email format'},
                    status=400
                )

            # Validate password length
            password = data.get('password')
            if len(password) < 8:
                return self._make_response(
                    {'error': 'Password must be at least 8 characters long'},
                    status=400
                )

            # Check if user already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                return self._make_response(
                    {'error': 'Email already exists'},
                    status=400
                )

            # Create partner
            partner = request.env['res.partner'].sudo().create({
                'name': data.get('name'),
                'email': email,
                'phone': data.get('phone', False),
            })

            # Create user
            user = request.env['res.users'].sudo().with_context(no_reset_password=True).create({
                'name': data.get('name'),
                'login': email,
                'password': password,
                'partner_id': partner.id,
                'groups_id': [(6, 0, [request.env.ref('base.group_portal').id])]
            })

            # Send welcome email
            try:
                template = request.env.ref('laterna_auth.mail_template_welcome', raise_if_not_found=False)
                if template:
                    template.sudo().with_context(
                        lang=user.lang
                    ).send_mail(user.id, email_values={'email_to': email})
            except Exception as e:
                # Log email error but don't fail registration
                _logger.warning("Failed to send welcome email: %s", str(e))

            return self._make_response({
                'user_id': user.id,
                'message': 'Account created successfully'
            }, status=201)

        except Exception as e:
            _logger.error("Registration error: %s", str(e))
            return self._make_response(
                {'error': 'Internal server error'},
                status=500
            )

    def _make_response(self, data, status=200, headers=None):
        """Helper method to create consistent JSON responses"""
        headers = headers or []
        headers.append(('Content-Type', 'application/json'))
        headers.append(('Access-Control-Allow-Origin', '*'))
        return request.make_response(
            json.dumps(data),
            headers=headers,
            status=status
        )


