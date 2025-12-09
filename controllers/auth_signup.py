# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import json
import logging
import re
import html
import time
from collections import defaultdict
from odoo.tools import email_normalize

_logger = logging.getLogger(__name__)

# In-memory rate limiting (use Redis in production)
_rate_limit_store = defaultdict(list)


class LaternaAuthenticationSignUp(http.Controller):

    # ================================
    # MAIN REGISTRATION ENDPOINT
    # ================================
    @http.route('/api/v1/admin/auth/register', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def register_user(self, **kwargs):
        """Secure admin user registration with full validation and proper HTTP responses"""
        ip_address = request.httprequest.environ.get('HTTP_X_FORWARDED_FOR',
                                                     request.httprequest.remote_addr)

        response_content = {}
        status_code = 200

        try:
            # === Rate Limiting ===
            if self._is_rate_limited(ip_address, limit=5, window=300):
                self._log_registration_attempt("unknown", False, ip_address, "rate_limited")
                response_content = {
                    'success': False,
                    'error': 'Too many registration attempts. Please try again later.'
                }
                status_code = 429
                return self._json_response(response_content, status_code)

            # === Parse JSON Data ===
            data = self._parse_request_data()
            if not data:
                self._log_registration_attempt("unknown", False, ip_address, "invalid_json")
                response_content = {'success': False, 'error': 'Invalid or missing JSON data'}
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Required Fields ===
            required_fields = ['email', 'password', 'name', 'street', 'city', 'state', 'country']
            missing_fields = [f for f in required_fields if not data.get(f)]
            if missing_fields:
                self._log_registration_attempt(data.get('email', 'unknown'), False, ip_address, "missing_fields")
                response_content = {
                    'success': False,
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Extract & Sanitize Inputs ===
            email = html.escape(data.get('email', '').strip().lower())
            password = data.get('password')
            name = html.escape(data.get('name', '').strip())
            street = html.escape(data.get('street', '').strip())
            city = html.escape(data.get('city', '').strip())
            state_input = data.get('state', '').strip()
            country_input = data.get('country', '').strip()
            phone = html.escape(data.get('phone', '').strip()) if data.get('phone') else None

            # === Email Validation ===
            if not self._is_valid_email(email):
                self._log_registration_attempt(email, False, ip_address, "invalid_email")
                response_content = {'success': False, 'error': 'Invalid email address format'}
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Password Strength ===
            password_errors = self._validate_password_strength(password)
            if password_errors:
                self._log_registration_attempt(email, False, ip_address, "weak_password")
                response_content = {
                    'success': False,
                    'error': 'Password does not meet security requirements',
                    'details': password_errors
                }
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Name Validation ===
            if not self._is_valid_name(name):
                self._log_registration_attempt(email, False, ip_address, "invalid_name")
                response_content = {
                    'success': False,
                    'error': 'Name contains invalid characters. Only letters, spaces, hyphens, and apostrophes allowed.'
                }
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Field Length Validation ===
            field_limits = {
                'name': (1, 128),
                'street': (1, 128),
                'city': (1, 64),
                'state': (1, 64),
                'country': (1, 64)
            }
            field_values = {
                'name': name,
                'street': street,
                'city': city,
                'state': state_input,
                'country': country_input
            }
            for field, (min_len, max_len) in field_limits.items():
                value = field_values[field]
                if not (min_len <= len(value) <= max_len):
                    self._log_registration_attempt(email, False, ip_address, f"invalid_{field}_length")
                    response_content = {
                        'success': False,
                        'error': f'{field.capitalize()} must be between {min_len} and {max_len} characters'
                    }
                    status_code = 400
                    return self._json_response(response_content, status_code)

            # === Phone (Optional) ===
            config = request.env['ir.config_parameter'].sudo()
            require_phone = config.get_param('lateral_api.require_phone', default='0') == '1'
            if require_phone and not phone:
                self._log_registration_attempt(email, False, ip_address, "missing_phone")
                response_content = {'success': False, 'error': 'Phone number is required'}
                status_code = 400
                return self._json_response(response_content, status_code)
            if phone and not self._is_valid_phone(phone):
                self._log_registration_attempt(email, False, ip_address, "invalid_phone")
                response_content = {'success': False, 'error': 'Invalid phone number format'}
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Country Validation ===
            country = request.env['res.country'].sudo().search([
                ('name', 'ilike', country_input)
            ], limit=1)
            if not country:
                self._log_registration_attempt(email, False, ip_address, "invalid_country")
                response_content = {
                    'success': False,
                    'error': 'Invalid country name',
                    'debug': {'country_input': country_input}
                }
                status_code = 400
                return self._json_response(response_content, status_code)

            # === State Validation (Flexible: name, short name, code) ===
            state = self._find_state(state_input, country.id)
            if not state:
                self._log_registration_attempt(email, False, ip_address, "invalid_state")
                available_states = request.env['res.country.state'].sudo().search_read(
                    [('country_id', '=', country.id)], ['name', 'code']
                )
                response_content = {
                    'success': False,
                    'error': 'Invalid state name or state does not belong to the specified country',
                    'debug': {
                        'state_input': state_input,
                        'country': country.name,
                        'available_states': available_states
                    }
                }
                status_code = 400
                return self._json_response(response_content, status_code)

            # === Check Duplicate Email ===
            if request.env['res.users'].sudo().search([('login', '=', email)], limit=1):
                self._log_registration_attempt(email, False, ip_address, "email_exists")
                response_content = {'success': False, 'error': 'Email already exists'}
                status_code = 409
                return self._json_response(response_content, status_code)

            # === Create Partner ===
            partner_vals = {
                'name': name,
                'email': email,
                'street': street,
                'city': city,
                'state_id': state.id,
                'country_id': country.id,
            }
            if phone:
                partner_vals['phone'] = phone

            partner = request.env['res.partner'].sudo().create(partner_vals)

            # === Create User as Internal Administrator ===
            try:
                admin_group = request.env.ref('base.group_system')        # Full Administrator
                internal_group = request.env.ref('base.group_user')       # Internal User (backend access)

                user = request.env['res.users'].sudo().with_context(
                    no_reset_password=True,
                    mail_create_nosubscribe=True
                ).create({
                    'name': name,
                    'login': email,
                    'password': password,
                    'partner_id': partner.id,
                    'groups_id': [(6, 0, [internal_group.id, admin_group.id])],
                    'active': True,
                    'company_id': request.env.company.id,
                    'company_ids': [(6, 0, [request.env.company.id])],
                })
            except Exception as e:
                _logger.error("Failed to create admin user: %s", str(e), exc_info=True)
                raise

            # === Send Welcome Email ===
            email_sent = self._send_welcome_email(user, street, city, state.name, country.name)

            # === Success ===
            self._log_registration_attempt(email, True, ip_address, "success")
            response_content = {
                'success': True,
                'data': {
                    'user_id': user.id,
                    'message': 'Admin account created successfully',
                    'email_sent': email_sent,
                    'access': 'full_admin'
                }
            }
            status_code = 201
            return self._json_response(response_content, status_code)

        except Exception as e:
            _logger.error("Registration error for IP %s: %s", ip_address, str(e), exc_info=True)
            self._log_registration_attempt(data.get('email', 'unknown') if 'data' in locals() else 'unknown',
                                           False, ip_address, "server_error")
            response_content = {'success': False, 'error': 'Internal server error'}
            status_code = 500
            return self._json_response(response_content, status_code)

    # ================================
    # HELPER METHODS
    # ================================
    def _json_response(self, data, status=200):
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
            status=status
        )

    def _parse_request_data(self):
        try:
            if hasattr(request, 'jsonrequest') and request.jsonrequest:
                return request.jsonrequest
            raw = request.httprequest.get_data().decode('utf-8')
            if raw:
                return json.loads(raw)
        except Exception as e:
            _logger.error("JSON parsing failed: %s", str(e))
        return {}

    def _is_valid_email(self, email):
        if not email or not isinstance(email, str):
            return False
        normalized = email_normalize(email)
        return normalized and len(normalized) <= 254

    def _is_rate_limited(self, ip_address, limit=5, window=300):
        now = time.time()
        attempts = [t for t in _rate_limit_store[ip_address] if now - t < window]
        _rate_limit_store[ip_address] = attempts
        if len(attempts) >= limit:
            return True
        _rate_limit_store[ip_address].append(now)
        return False

    def _validate_password_strength(self, password):
        errors = []
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long")
        if not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for c in password):
            errors.append("Password must contain at least one special character")
        if re.search(r'(.)\1{2,}', password):
            errors.append("Password should not contain repeated characters (3 or more)")
        return errors

    def _is_valid_name(self, name):
        if not name or not isinstance(name, str):
            return False
        cleaned = name.strip()
        return bool(re.match(r"^[a-zA-Z\s\-\'\.]+$", cleaned)) and len(cleaned) > 0

    def _is_valid_phone(self, phone):
        if not isinstance(phone, str):
            return False
        cleaned = re.sub(r'[\s\-\(\)\+]', '', phone)
        return cleaned.isdigit() and 5 <= len(cleaned) <= 15

    def _find_state(self, state_input, country_id):
        """Flexible state lookup: by code, full name, or short name"""
        if not state_input:
            return False

        state_input = state_input.strip()

        # 1. Try by code (e.g. "LA", "NY")
        if len(state_input) <= 4:
            state = request.env['res.country.state'].sudo().search([
                ('code', '=ilike', state_input.upper()),
                ('country_id', '=', country_id)
            ], limit=1)
            if state:
                return state

        # 2. Try exact name
        state = request.env['res.country.state'].sudo().search([
            ('name', '=ilike', state_input)
        ], limit=1)
        if state and state.country_id.id == country_id:
            return state

        # 3. Try partial match
        state = request.env['res.country.state'].sudo().search([
            ('name', 'ilike', state_input),
            ('country_id', '=', country_id)
        ], limit=1)
        if state:
            return state

        return False

    def _send_welcome_email(self, user, street, city, state, country):
        try:
            template = request.env.ref('lateral_api.mail_template_welcome', raise_if_not_found=False)
            if template:
                template.sudo().with_context(
                    lang=user.lang,
                    street=street, city=city, state=state, country=country
                ).send_mail(user.id, email_values={'email_to': user.login}, force_send=True)
                return True
        except Exception as e:
            _logger.warning("Welcome email failed for %s: %s", user.login, str(e))
        return False

    def _log_registration_attempt(self, email, success, ip_address, reason=None):
        msg = f"Registration: email={email}, ip={ip_address}, success={success}"
        if reason:
            msg += f", reason={reason}"
        if success:
            _logger.info(msg)
        else:
            _logger.warning(msg)

    # ================================
    # VALIDATION ENDPOINTS
    # ================================
    @http.route('/api/v1/auth/register/validate-email', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def validate_email_availability(self, **kwargs):
        data = self._parse_request_data() or {}
        email = html.escape(data.get('email', '').strip().lower())
        if not email:
            return self._json_response({'success': False, 'error': 'Email is required'}, 400)
        if not self._is_valid_email(email):
            return self._json_response({'success': False, 'error': 'Invalid email format'}, 400)
        exists = bool(request.env['res.users'].sudo().search([('login', '=', email)], limit=1))
        return self._json_response({
            'success': True,
            'data': {'email': email, 'available': not exists}
        })

    @http.route('/api/v1/auth/register/validate-password', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def validate_password_strength(self, **kwargs):
        data = self._parse_request_data() or {}
        password = data.get('password')
        if not password:
            return self._json_response({'success': False, 'error': 'Password is required'}, 400)
        errors = self._validate_password_strength(password)
        score = self._calculate_password_strength(password)
        return self._json_response({
            'success': True,
            'data': {
                'is_valid': len(errors) == 0,
                'strength_score': score,
                'errors': errors,
                'suggestions': self._get_password_suggestions(password)
            }
        })

    def _calculate_password_strength(self, password):
        score = min(len(password) * 4, 40)
        if any(c.isupper() for c in password): score += 15
        if any(c.islower() for c in password): score += 15
        if any(c.isdigit() for c in password): score += 15
        if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for c in password): score += 15
        return min(score, 100)

    def _get_password_suggestions(self, password):
        s = []
        if len(password) < 12: s.append("Use at least 12 characters")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for c in password): s.append("Add special characters")
        if not any(c.isdigit() for c in password): s.append("Include numbers")
        if not (any(c.isupper() for c in password) and any(c.islower() for c in password)): s.append("Mix case")
        if re.search(r'(.)\1{2,}', password): s.append("Avoid repeated characters")
        return s

    # ================================
    # COUNTRY & STATE ENDPOINTS
    # ================================
    @http.route('/api/v1/countries', type='http', auth='public', methods=['GET'], cors="*")
    def get_countries(self):
        countries = request.env['res.country'].sudo().search_read([], ['id', 'name'])
        return self._json_response({'success': True, 'data': countries})

    @http.route('/api/v1/countries/<int:country_id>/states', type='http', auth='public', methods=['GET'], cors="*")
    def get_states(self, country_id):
        states = request.env['res.country.state'].sudo().search_read(
            [('country_id', '=', country_id)], ['id', 'name', 'code']
        )
        return self._json_response({'success': True, 'data': states})

    # ================================
    # VALIDATION ENDPOINTS
    # ================================
    @http.route('/api/v1/auth/register/validate-email', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def validate_email_availability(self, **kwargs):
        data = self._parse_request_data() or {}
        email = html.escape(data.get('email', '').strip().lower())
        if not email:
            return self._json_response({'success': False, 'error': 'Email is required'}, 400)
        if not self._is_valid_email(email):
            return self._json_response({'success': False, 'error': 'Invalid email format'}, 400)
        exists = bool(request.env['res.users'].sudo().search([('login', '=', email)], limit=1))
        return self._json_response({
            'success': True,
            'data': {'email': email, 'available': not exists}
        })

    @http.route('/api/v1/auth/register/validate-password', type='http', auth="public", csrf=False, methods=['POST'], cors="*")
    def validate_password_strength(self, **kwargs):
        data = self._parse_request_data() or {}
        password = data.get('password')
        if not password:
            return self._json_response({'success': False, 'error': 'Password is required'}, 400)
        errors = self._validate_password_strength(password)
        score = self._calculate_password_strength(password)
        return self._json_response({
            'success': True,
            'data': {
                'is_valid': len(errors) == 0,
                'strength_score': score,
                'errors': errors,
                'suggestions': self._get_password_suggestions(password)
            }
        })

    def _calculate_password_strength(self, password):
        score = min(len(password) * 4, 40)
        if any(c.isupper() for c in password): score += 15
        if any(c.islower() for c in password): score += 15
        if any(c.isdigit() for c in password): score += 15
        if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for c in password): score += 15
        return min(score, 100)

    def _get_password_suggestions(self, password):
        s = []
        if len(password) < 12: s.append("Use at least 12 characters")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?`~" for c in password): s.append("Add special characters")
        if not any(c.isdigit() for c in password): s.append("Include numbers")
        if not (any(c.isupper() for c in password) and any(c.islower() for c in password)): s.append("Mix case")
        if re.search(r'(.)\1{2,}', password): s.append("Avoid repeated characters")
        return s

    # ================================
    # COUNTRY & STATE ENDPOINTS
    # ================================
    @http.route('/api/v1/countries', type='http', auth='public', methods=['GET'], cors="*")
    def get_countries(self):
        countries = request.env['res.country'].sudo().search_read([], ['id', 'name'])
        return self._json_response({'success': True, 'data': countries})

    @http.route('/api/v1/countries/<int:country_id>/states', type='http', auth='public', methods=['GET'], cors="*")
    def get_states(self, country_id):
        states = request.env['res.country.state'].sudo().search_read(
            [('country_id', '=', country_id)], ['id', 'name', 'code']
        )
        return self._json_response({'success': True, 'data': states})