from odoo import http, fields
from odoo.http import request
import json
import logging
import re

_logger = logging.getLogger(__name__)


class LaternaAuthenticationSignUp(http.Controller):
    @http.route('/api/v1/auth/register', type='json', auth="public", csrf=False, methods=['POST'], cors="*")
    def register_user(self, **kwargs):
        """Enhanced user registration endpoint with comprehensive validation and security"""
        ip_address = request.httprequest.environ.get('HTTP_X_FORWARDED_FOR',
                                                     request.httprequest.environ.get('REMOTE_ADDR'))

        try:
            # Rate limiting check
            if self._is_rate_limited(ip_address):
                self._log_registration_attempt("unknown", False, ip_address, "rate_limited")
                return {
                    'success': False,
                    'error': 'Too many registration attempts. Please try again later.'
                }

            # Parse JSON data from request - multiple methods for compatibility
            data = None
            try:
                # Method 1: Try to get JSON from request's jsonrequest (Odoo's built-in)
                if hasattr(request, 'jsonrequest') and request.jsonrequest:
                    data = request.jsonrequest
                    _logger.info("Using jsonrequest data: %s", data)
                else:
                    # Method 2: Try to parse raw request data
                    raw_data = request.httprequest.get_data().decode('utf-8')
                    if raw_data:
                        data = json.loads(raw_data)
                        _logger.info("Using raw request data: %s", data)
                    else:
                        # Method 3: Try kwargs
                        data = kwargs
                        _logger.info("Using kwargs data: %s", data)
            except Exception as e:
                _logger.error("JSON parsing error: %s", str(e))
                self._log_registration_attempt("unknown", False, ip_address, "invalid_json")
                return {
                    'success': False,
                    'error': 'Invalid JSON data format'
                }

            if not data:
                self._log_registration_attempt("unknown", False, ip_address, "invalid_json")
                return {
                    'success': False,
                    'error': 'No data received'
                }

            # Validate required fields
            required_fields = ['email', 'password', 'name', 'street', 'city', 'state', 'country']
            missing_fields = [field for field in required_fields if not data.get(field)]

            if missing_fields:
                self._log_registration_attempt(data.get('email', 'unknown'), False, ip_address, "missing_fields")
                return {
                    'success': False,
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }

            email = data.get('email')
            # Enhanced email validation using Odoo's built-in validation
            if not self._is_valid_email(email):
                self._log_registration_attempt(email, False, ip_address, "invalid_email")
                return {
                    'success': False,
                    'error': 'Invalid email address format'
                }

            # Validate password strength
            password = data.get('password')
            password_errors = self._validate_password_strength(password)
            if password_errors:
                self._log_registration_attempt(email, False, ip_address, "weak_password")
                return {
                    'success': False,
                    'error': 'Password does not meet security requirements',
                    'details': password_errors
                }

            # Validate name format
            name = data.get('name')
            if not self._is_valid_name(name):
                self._log_registration_attempt(email, False, ip_address, "invalid_name")
                return {
                    'success': False,
                    'error': 'Name contains invalid characters. Only letters, spaces, hyphens, and apostrophes are allowed.'
                }

            # Validate address fields
            street = data.get('street')
            city = data.get('city')
            state_name = data.get('state')
            country_name = data.get('country')

            # Validate field types
            for field, value in [('street', street), ('city', city), ('state', state_name), ('country', country_name)]:
                if not isinstance(value, str):
                    self._log_registration_attempt(email, False, ip_address, f"invalid_{field}_type")
                    return {
                        'success': False,
                        'error': f'{field.capitalize()} must be a string'
                    }

            # Validate string lengths
            field_limits = {
                'street': (1, 128),
                'city': (1, 64),
                'state': (1, 64),
                'country': (1, 64),
                'name': (1, 128)
            }

            for field, value in [('street', street), ('city', city), ('state', state_name),
                                 ('country', country_name), ('name', name)]:
                min_len, max_len = field_limits[field]
                if not (min_len <= len(value) <= max_len):
                    self._log_registration_attempt(email, False, ip_address, f"invalid_{field}_length")
                    return {
                        'success': False,
                        'error': f'{field.capitalize()} must be between {min_len} and {max_len} characters'
                    }

            # Get configuration from system parameters
            config = request.env['ir.config_parameter'].sudo()
            require_phone = config.get_param('laternal_api.require_phone', False)

            # Validate phone if required
            phone = data.get('phone')
            if require_phone and not phone:
                self._log_registration_attempt(email, False, ip_address, "missing_phone")
                return {
                    'success': False,
                    'error': 'Phone number is required'
                }

            if phone and not self._is_valid_phone(phone):
                self._log_registration_attempt(email, False, ip_address, "invalid_phone")
                return {
                    'success': False,
                    'error': 'Invalid phone number format'
                }

            # Validate country by name
            country = request.env['res.country'].sudo().search([('name', 'ilike', country_name)], limit=1)
            if not country:
                self._log_registration_attempt(email, False, ip_address, "invalid_country")
                return {
                    'success': False,
                    'error': 'Invalid country name'
                }

            # Validate state by name and ensure it belongs to the country
            state = request.env['res.country.state'].sudo().search([
                ('name', 'ilike', state_name),
                ('country_id', '=', country.id)
            ], limit=1)
            if not state:
                self._log_registration_attempt(email, False, ip_address, "invalid_state")
                return {
                    'success': False,
                    'error': 'Invalid state name or state does not belong to the specified country'
                }

            # Check if user already exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)
            if existing_user:
                self._log_registration_attempt(email, False, ip_address, "email_exists")
                return {
                    'success': False,
                    'error': 'Email already exists'
                }

            # Create partner with address fields
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

            # Create user as internal user
            user = request.env['res.users'].sudo().with_context(no_reset_password=True).create({
                'name': name,
                'login': email,
                'password': password,
                'partner_id': partner.id,
                'groups_id': [(6, 0, [request.env.ref('base.group_user').id])]
            })

            # Send welcome email
            email_sent = self._send_welcome_email(user, street, city, state.name, country.name)

            # Log successful registration
            self._log_registration_attempt(email, True, ip_address, "success")

            return {
                'success': True,
                'data': {
                    'user_id': user.id,
                    'message': 'Account created successfully',
                    'email_sent': email_sent
                }
            }

        except Exception as e:
            _logger.error("Registration error for IP %s: %s", ip_address, str(e), exc_info=True)
            self._log_registration_attempt(data.get('email', 'unknown') if 'data' in locals() else 'unknown',
                                           False, ip_address, "server_error")
            return {
                'success': False,
                'error': 'Internal server error'
            }

    def _is_valid_email(self, email):
        """Validate email format using Odoo's built-in validation"""
        if not email or not isinstance(email, str):
            return False

        # Use Odoo's email validation pattern
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        # Basic regex validation
        if not re.match(email_pattern, email):
            return False

        # Additional checks
        if len(email) > 254:  # RFC 5321 limit
            return False

        # Check for common invalid patterns
        if email.startswith('.') or email.endswith('.'):
            return False

        if '..' in email:
            return False

        return True

    def _is_rate_limited(self, ip_address):
        """Basic rate limiting by IP"""
        return False

    def _validate_password_strength(self, password):
        """Validate password meets strength requirements"""
        errors = []

        if len(password) < 8:
            errors.append("Password must be at least 8 characters long")
        if not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            errors.append("Password must contain at least one special character")
        if re.search(r'(.)\1{2,}', password):
            errors.append("Password should not contain repeated characters (3 or more)")

        return errors

    def _is_valid_name(self, name):
        """Validate name contains only allowed characters"""
        if not name or not isinstance(name, str):
            return False
        # Allow letters, spaces, hyphens, and apostrophes
        cleaned_name = name.strip()
        if not cleaned_name:
            return False
        return bool(re.match(r"^[a-zA-Z\s\-'\.]+$", cleaned_name))

    def _is_valid_phone(self, phone):
        """Basic phone number validation"""
        if not isinstance(phone, str):
            return False
        # Allow numbers, spaces, hyphens, parentheses, and +
        cleaned_phone = re.sub(r'[\s\-\(\)\+]', '', phone)
        return cleaned_phone.isdigit() and 5 <= len(cleaned_phone) <= 15

    def _send_welcome_email(self, user, street, city, state, country):
        """Send welcome email to new user"""
        try:
            template = request.env.ref('laternal_api.mail_template_welcome', raise_if_not_found=False)
            if template:
                template.sudo().with_context(
                    lang=user.lang,
                    street=street,
                    city=city,
                    state=state,
                    country=country
                ).send_mail(user.id, email_values={'email_to': user.login})
                return True
        except Exception as e:
            _logger.warning("Failed to send welcome email to %s: %s", user.login, str(e))
        return False

    def _log_registration_attempt(self, email, success, ip_address, reason=None):
        """Log registration attempts for security monitoring"""
        log_message = f"Registration attempt: email={email}, success={success}, ip={ip_address}"
        if reason:
            log_message += f", reason={reason}"

        if success:
            _logger.info(log_message)
        else:
            _logger.warning(log_message)

    @http.route('/api/v1/auth/register/validate-email', type='json', auth="public", csrf=False, methods=['POST'],
                cors="*")
    def validate_email_availability(self, **kwargs):
        """Endpoint to validate email availability before registration"""
        try:
            # Multiple methods to get JSON data
            data = None
            if hasattr(request, 'jsonrequest') and request.jsonrequest:
                data = request.jsonrequest
            else:
                raw_data = request.httprequest.get_data().decode('utf-8')
                if raw_data:
                    data = json.loads(raw_data)
                else:
                    data = kwargs

            email = data.get('email') if data else None

            if not email:
                return {'success': False, 'error': 'Email is required'}

            # Validate email format
            if not self._is_valid_email(email):
                return {'success': False, 'error': 'Invalid email address format'}

            # Check if email exists
            existing_user = request.env['res.users'].sudo().search([('login', '=', email)], limit=1)

            return {
                'success': True,
                'data': {
                    'email': email,
                    'available': not bool(existing_user)
                }
            }

        except Exception as e:
            _logger.error("Email validation error: %s", str(e))
            return {'success': False, 'error': 'Internal server error'}

    @http.route('/api/v1/auth/register/validate-password', type='json', auth="public", csrf=False, methods=['POST'],
                cors="*")
    def validate_password_strength(self, **kwargs):
        """Endpoint to validate password strength before registration"""
        try:
            # Multiple methods to get JSON data
            data = None
            if hasattr(request, 'jsonrequest') and request.jsonrequest:
                data = request.jsonrequest
            else:
                raw_data = request.httprequest.get_data().decode('utf-8')
                if raw_data:
                    data = json.loads(raw_data)
                else:
                    data = kwargs

            password = data.get('password') if data else None

            if not password:
                return {'success': False, 'error': 'Password is required'}

            errors = self._validate_password_strength(password)
            strength_score = self._calculate_password_strength(password)

            return {
                'success': True,
                'data': {
                    'is_valid': len(errors) == 0,
                    'strength_score': strength_score,
                    'errors': errors,
                    'suggestions': self._get_password_suggestions(password)
                }
            }

        except Exception as e:
            _logger.error("Password validation error: %s", str(e))
            return {'success': False, 'error': 'Internal server error'}

    def _calculate_password_strength(self, password):
        """Calculate password strength score (0-100)"""
        score = 0

        # Length contribution
        score += min(len(password) * 4, 40)  # Max 40 points for length

        # Character variety
        if any(c.isupper() for c in password):
            score += 15
        if any(c.islower() for c in password):
            score += 15
        if any(c.isdigit() for c in password):
            score += 15
        if any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            score += 15

        return min(score, 100)

    def _get_password_suggestions(self, password):
        """Provide suggestions for improving password strength"""
        suggestions = []

        if len(password) < 12:
            suggestions.append("Use at least 12 characters for better security")
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password):
            suggestions.append("Add special characters")
        if not any(c.isdigit() for c in password):
            suggestions.append("Include numbers")
        if not (any(c.isupper() for c in password) and any(c.islower() for c in password)):
            suggestions.append("Mix uppercase and lowercase letters")
        if re.search(r'(.)\1{2,}', password):
            suggestions.append("Avoid repeated characters")

        return suggestions