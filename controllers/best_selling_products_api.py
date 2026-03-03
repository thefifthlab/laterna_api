from odoo import http
from odoo.http import request
import json
from datetime import datetime
from dateutil.relativedelta import relativedelta
import logging

_logger = logging.getLogger(__name__)


class BestSellersApiController(http.Controller):

    @http.route('/api/best-sellers', type='json', auth='public', csrf=False, methods=['POST'], cors="*")
    def get_best_sellers(self, **kwargs):
        """
        Get best-selling products within a date range
        Parameters:
        - limit: Number of products to return (max 50, default 10)
        - date_from: Start date (YYYY-MM-DD), default 30 days ago
        - date_to: End date (YYYY-MM-DD), default today
        - include_unpublished: Include unpublished products (default false)
        - category_id: Filter by product category
        """
        try:
            # Parse and validate parameters
            limit = self._validate_limit(kwargs.get('limit', 10))
            date_from, date_to = self._validate_dates(
                kwargs.get('date_from'),
                kwargs.get('date_to')
            )
            include_unpublished = kwargs.get('include_unpublished', False)
            category_id = kwargs.get('category_id')

            # Build query and parameters
            query, params = self._build_best_sellers_query(
                date_from, date_to, limit, include_unpublished, category_id
            )

            # Execute query
            request.env.cr.execute(query, params)
            results = request.env.cr.dictfetchall()

            # Enrich with product data
            best_sellers = self._enrich_product_data(results, include_unpublished)

            return self._json_success_response(best_sellers, date_from, date_to, limit)

        except ValueError as e:
            _logger.warning(f"Best sellers API validation error: {e}")
            return self._json_error_response(str(e), 400)
        except Exception as e:
            _logger.error(f"Best sellers API error: {e}")
            return self._json_error_response("Internal server error", 500)

    def _validate_limit(self, limit_param):
        """Validate and sanitize limit parameter"""
        try:
            limit = int(limit_param)
            if limit <= 0:
                raise ValueError("Limit must be positive")
            return min(limit, 50)  # Cap at 50 for performance
        except (TypeError, ValueError):
            raise ValueError("Invalid limit parameter")

    def _validate_dates(self, date_from_str, date_to_str):
        """Validate and parse date parameters"""
        date_to = datetime.now().date()
        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValueError("Invalid date_to format, use YYYY-MM-DD")

        date_from = date_to - relativedelta(days=30)
        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValueError("Invalid date_from format, use YYYY-MM-DD")

        if date_from > date_to:
            raise ValueError("date_from cannot be after date_to")

        return date_from, date_to

    def _build_best_sellers_query(self, date_from, date_to, limit, include_unpublished, category_id):
        """Build the SQL query for best sellers"""
        query = """
            SELECT 
                pp.id as product_id,
                pt.name as product_name,  -- FIXED: Use pt.name instead of pp.name
                pt.list_price as list_price,  -- FIXED: Use pt.list_price instead of pp.list_price
                pt.website_published as is_published,  -- FIXED: Use pt.website_published
                pt.categ_id as category_id,
                COALESCE(SUM(sol.product_uom_qty), 0) as total_sold,
                COALESCE(SUM(sol.price_total), 0) as total_revenue
            FROM sale_order_line sol
            JOIN sale_order so ON sol.order_id = so.id
            JOIN product_product pp ON sol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            WHERE so.state IN ('sale', 'done')
              AND so.date_order >= %s
              AND so.date_order < %s + INTERVAL '1 day'
              AND pt.detailed_type = 'product'
        """

        params = [date_from, date_to]

        # Add optional filters
        if not include_unpublished:
            query += " AND pt.website_published = true"  # FIXED: Use pt.website_published

        if category_id:
            try:
                category_id = int(category_id)
                query += " AND pt.categ_id = %s"
                params.append(category_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid category_id")

        # Add grouping and ordering - FIXED: Updated GROUP BY to match SELECT
        query += """
            GROUP BY pp.id, pt.name, pt.list_price, pt.website_published, pt.categ_id
            HAVING SUM(sol.product_uom_qty) > 0
            ORDER BY total_sold DESC
            LIMIT %s
        """
        params.append(limit)

        return query, params

    def _enrich_product_data(self, results, include_unpublished):
        """Enrich product data with template information"""
        if not results:
            return []

        product_ids = [r['product_id'] for r in results]

        # Build domain for product search
        domain = [('product_variant_ids', 'in', product_ids)]
        if not include_unpublished:
            domain.append(('website_published', '=', True))

        products = request.env['product.template'].sudo().search(domain).read([
            'name', 'list_price', 'image_1920', 'website_url', 'product_variant_ids'
        ])

        # Create mapping for quick lookup
        product_map = {}
        for product in products:
            for variant_id in product['product_variant_ids']:
                product_map[variant_id] = product

        # Build response
        best_sellers = []
        for row in results:
            product_template = product_map.get(row['product_id'])
            if product_template:
                best_sellers.append({
                    'product_id': product_template['id'],
                    'variant_id': row['product_id'],
                    'name': product_template['name'] or row['product_name'],
                    'price': float(product_template['list_price'] or row['list_price']),
                    'image': bool(product_template['image_1920']),
                    'image_url': f'/web/image/product.template/{product_template["id"]}/image_1920',
                    'url': product_template['website_url'],
                    'total_sold': int(row['total_sold']),
                    'total_revenue': float(row['total_revenue']),
                    'is_published': row['is_published'],
                    'category_id': row['category_id']
                })

        return best_sellers

    def _json_success_response(self, products, date_from, date_to, limit):
        """Format successful JSON response"""
        return {
            'status': 'success',
            'data': {
                'date_from': date_from.strftime('%Y-%m-%d'),
                'date_to': date_to.strftime('%Y-%m-%d'),
                'limit': limit,
                'count': len(products),
                'products': products
            }
        }

    def _json_error_response(self, message, status_code=400):
        """Format error JSON response"""
        return {
            'status': 'error',
            'message': message,
            'code': status_code
        }