# -*- coding: utf-8 -*-
from odoo.tests.common import HttpCase
from odoo import exceptions
import json
import jwt
import datetime
from unittest.mock import patch

class TestLoginAuthenticationAPI(HttpCase):
    def setUp(self):
        super(TestLoginAuthenticationAPI, self).setUp()
        # Set up test user
        self.test_user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'testuser@example.com',
            'password': 'testpassword',
        })
        self.test_db = self.env.cr.dbname
        self.secret_key = 'test-secret-key'
        self.expires_in = 3600

        # Mock ir.config_parameter
        self.env['ir.config_parameter'].sudo().set_param('auth_token.secret_key', self.secret_key)
        self.env['ir.config_parameter'].sudo().set_param('auth_token.expires_in', str(self.expires_in))

        # URL for the endpoint
        self.url = '/api/v1/auth/login'

    def _make_json_request(self, payload):
        """Helper to make JSON POST request and return response."""
        return self.urlopen(
            self.url,
            method='POST',
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload).encode('utf-8')
        )

    def test_successful_login(self):
        """Test successful login with valid credentials."""
        payload = {
            'login': 'testuser@example.com',
            'password': 'testpassword',
            'db': self.test_db
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 200)

        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'success')
        self.assertIn('session_token', response_data)
        self.assertIn('user_id', response_data)
        self.assertEqual(response_data['user_id'], self.test_user.id)
        self.assertIn('expires_in', response_data)
        self.assertEqual(response_data['expires_in'], self.expires_in)
        self.assertIn('token', response_data)
        self.assertIn('username', response_data)
        self.assertEqual(response_data['username'], self.test_user.name)

        # Verify JWT
        decoded = jwt.decode(response_data['token'], self.secret_key, algorithms=['HS256'])
        self.assertEqual(decoded['user_id'], self.test_user.id)
        self.assertEqual(decoded['sub'], self.test_user.login)
        self.assertTrue(decoded['exp'] > decoded['iat'])

    def test_invalid_json(self):
        """Test invalid JSON payload."""
        response = self.urlopen(
            self.url,
            method='POST',
            headers={'Content-Type': 'application/json'},
            data='invalid json'.encode('utf-8')
        )
        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Invalid JSON payload')

    def test_missing_fields(self):
        """Test missing required fields."""
        payload = {'login': 'testuser@example.com', 'password': 'testpassword'}
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Missing required fields: db')

    def test_empty_fields(self):
        """Test empty fields in payload."""
        payload = {'login': '', 'password': 'testpassword', 'db': self.test_db}
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Empty fields are not allowed')

    def test_invalid_credentials(self):
        """Test login with invalid credentials."""
        payload = {
            'login': 'testuser@example.com',
            'password': 'wrongpassword',
            'db': self.test_db
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 401)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Invalid credentials')

    def test_invalid_database(self):
        """Test login with invalid database name."""
        payload = {
            'login': 'testuser@example.com',
            'password': 'testpassword',
            'db': 'invalid$db#name'
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 400)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Invalid database name')

    def test_no_secret_key(self):
        """Test login when secret key is not configured."""
        self.env['ir.config_parameter'].sudo().set_param('auth_token.secret_key', False)
        payload = {
            'login': 'testuser@example.com',
            'password': 'testpassword',
            'db': self.test_db
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 500)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Server configuration error')

    def test_invalid_expires_in(self):
        """Test login with invalid expires_in configuration."""
        self.env['ir.config_parameter'].sudo().set_param('auth_token.expires_in', 'invalid')
        payload = {
            'login': 'testuser@example.com',
            'password': 'testpassword',
            'db': self.test_db
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 500)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'error')
        self.assertEqual(response_data['message'], 'Server configuration error')

    def test_case_insensitive_login(self):
        """Test login with case-insensitive login."""
        payload = {
            'login': 'TestUser@Example.com',  # Mixed case
            'password': 'testpassword',
            'db': self.test_db
        }
        response = self._make_json_request(payload)
        self.assertEqual(response.status_code, 200)
        response_data = json.loads(response.text)
        self.assertEqual(response_data['status'], 'success')
        self.assertEqual(response_data['user_id'], self.test_user.id)