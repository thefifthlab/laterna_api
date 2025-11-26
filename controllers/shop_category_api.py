# -*- coding: utf-8 -*-
"""
Public API – Category Tree + Products in Subcategories
Endpoint: GET /api/shop/category/<int:category_id>/tree
Query params: ?limit=50&offset=0&search=keyword

Example:
https://yourshop.com/api/shop/category/5/tree?limit=20&search=laptop
"""

import json
from odoo import http
from odoo.http import request

# Correct & official import in Odoo 18
from odoo.addons.website.models.website import slug

from odoo.tools.pycompat import to_text
from odoo.tools import html_sanitize


class ShopCategoryTreeAPI(http.Controller):

    @http.route(
        '/api/shop/category/<int:category_id>/tree',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
        cors='*',            # Restrict in production!
        website=True,
        sitemap=False,
    )
    def get_category_tree(self, category_id, **kwargs):
        try:
            Category = request.env['product.public.category'].with_context(active_test=False)
            Product = request.env['product.template']

            # 1. Load parent category + visibility check
            parent_category = Category.browse(category_id)
            if not parent_category.exists() or not parent_category.can_access_from_current_website():
                return request.not_found(json.dumps({
                    'success': False,
                    'error': 'Category not found or not published'
                }))

            # 2. Get direct children only (not recursive)
            subcategories = parent_category.child_id.filtered(
                lambda c: c.can_access_from_current_website()
            ).sorted('sequence')

            # 3. Build list of subcategory IDs (including their descendants for products)
            subcategory_ids = subcategories.ids
            all_subcategory_tree = Category.search([('id', 'child_of', subcategory_ids)]) if subcategory_ids else Category

            # 4. Secure product domain: products in any subcategory (recursive)
            product_domain = [
                ('website_published', '=', True),
                ('sale_ok', '=', True),
                ('public_categ_ids', 'in', all_subcategory_tree.ids),
                '|',
                ('website_id', '=', request.website.id),
                ('website_id', '=', False),
            ]

            # Optional search
            search = kwargs.get('search')
            if search:
                product_domain += ['|', ('name', 'ilike', search), ('description_sale', 'ilike', search)]

            # Pagination
            limit = max(1, min(int(kwargs.get('limit', 50)), 200))
            offset = max(0, int(kwargs.get('offset', 0)))

            products = Product.search(product_domain, limit=limit, offset=offset,
                                    order='website_sequence DESC, name ASC')
            total_products = Product.search_count(product_domain)

            # Website context
            website = request.website
            pricelist = website.get_current_pricelist()
            base_url = website.get_base_url().rstrip('/')

            # 5. Build subcategories data
            subcategories_data = []
            for cat in subcategories:
                image_url = (
                    f"{base_url}/web/image/product.public.category/{cat.id}/image_1920"
                    if cat.image_1920 else None
                )

                subcategories_data.append({
                    'id': cat.id,
                    'name': cat.name or '',
                    'description': html_sanitize(cat.description) or '',
                    'image_url': image_url,
                    'url': f"/shop/category/{slug(cat)}",
                    'product_count': Product.search_count([
                        ('public_categ_ids', 'in', cat.search([('id', 'child_of', cat.id)]).ids)
                    ]),
                })

            # 6. Build products data
            products_data = []
            for product in products:
                image_url = (
                    f"{base_url}/web/image/product.template/{product.id}/image_1920"
                    if product.image_1920 else None
                )

                price = pricelist._get_product_price(product, 1.0) if pricelist else product.list_price

                products_data.append({
                    'id': product.id,
                    'name': product.name or '',
                    'display_name': product.display_name or '',
                    'description_sale': html_sanitize(product.description_sale) or '',
                    'list_price': product.list_price,
                    'price': price,
                    'currency': website.currency_id.name,
                    'image_url': image_url,
                    'url': f"/shop/product/{slug(product)}",
                    'default_code': product.default_code or False,
                    'category_ids': product.public_categ_ids.filtered(
                        lambda c: c.id in all_subcategory_tree.ids
                    ).ids,
                })

            # 7. Final response
            response = {
                'success': True,
                'category': {
                    'id': parent_category.id,
                    'name': parent_category.name,
                    'url': f"/shop/category/{slug(parent_category)}",
                },
                'subcategories': subcategories_data,
                'products': products_data,
                'pagination': {
                    'limit': limit,
                    'offset': offset,
                    'total': total_products,
                    'has_more': (offset + limit) < total_products,
                }
            }

            return request.make_response(
                json.dumps(response, ensure_ascii=False, indent=2 if request.debug else None),
                headers=[
                    ('Content-Type', 'application/json; charset=utf-8'),
                    ('Cache-Control', 'max-age=300, public'),
                    ('Access-Control-Allow-Origin', '*'),
                ]
            )

        except Exception as e:
            return request.make_response(
                json.dumps({'success': False, 'error': 'Internal server error'}),
                status=500,
                headers=[('Content-Type', 'application/json')]
            )