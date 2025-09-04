from odoo import http, fields
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)

class LaternaAuthenticationLogout(http.Controller):
    @http.route('/api/v1/auth/logout', type='json', auth='public', methods=['POST'])
    def logout(self):
        """Handle logout and update session tracking."""
        session_id = request.session.sid
        user = request.env.user
        ip_address = request.httprequest.remote_addr

        _logger.info(
            "[%s] Logout API endpoint triggered for user: %s, IP: %s", fields.Datetime.now(), user.login, ip_address
        )

        # Close the session in ir.sessions
        session_closed = request.env['ir.sessions'].sudo().close_session(session_id)
        if not session_closed:
            _logger.warning("No active session found for session_id: %s", session_id)

        # Invalidate the Odoo session
        request.session.logout(keep_db=True)

        return {'status': 'success', 'message': 'Logged out successfully'}