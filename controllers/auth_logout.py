from odoo import http, fields
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)


class LaternaAuthenticationLogout(http.Controller):

    @http.route('/api/v1/auth/logout', type='http', auth='public', csrf=False, methods=['POST'], cors='*')
    def api_logout_http(self):
        try:
            request.session.logout(keep_db=True)
            resp = request.make_response(
                json.dumps({'status': 'success', 'message': 'Logged out'}),
                headers={
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    # Optional: expire session cookie immediately
                    # 'Set-Cookie': 'session_id=; Path=/; Expires=Thu, 01-Jan-1970 00:00:00 GMT; HttpOnly'
                },
                status=200
            )
            return resp
        except Exception as e:
            _logger.error("Logout failed: %s", str(e))
            return request.make_response(
                json.dumps({'status': 'error', 'message': 'Logout failed'}),
                headers={'Content-Type': 'application/json'},
                status=500
            )