from odoo import http
from odoo.http import request
import json
import re
import unicodedata


# --- CUSTOM SLUG (Odoo 18 Community no longer provides slug()) ---
def slug(record):
    name = record.name or ""
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[^a-zA-Z0-9-]+', '-', name).strip('-').lower()
    return f"{record.id}-{name}"


class ShopCategoryAPI(http.Controller):

    @http.route('/api/shop/category', type='http', auth='public', methods=['GET'], csrf=False, cors="*")
    def get_shop_category(self, **params):
        try:
            # Get params
            category_id = int(params.get('category_id', 0))
            limit = int(params.get('limit', 20))
            offset = int(params.get('offset', 0))

            Category = request.env['product.public.category'].sudo()
            Product = request.env['product.template'].sudo()

            parent_category = Category.browse(category_id)
            if not parent_category.exists():
                return http.Response(
                    json.dumps({'success': False, 'message': 'Invalid category_id'}),
                    status=404,
                    content_type='application/json'
                )

            # Subcategories
            subcategories = Category.search([('parent_id', '=', parent_category.id)])
            subcategories_data = [
                {
                    'id': c.id,
                    'name': c.name,
                    'url': f"/shop/category/{slug(c)}",
                }
                for c in subcategories
            ]

            # Products
            domain = [('public_categ_ids', 'child_of', parent_category.id)]
            total_products = Product.search_count(domain)
            product_records = Product.search(domain, limit=limit, offset=offset)

            products_data = [
                {
                    'id': p.id,
                    'name': p.name,
                    'price': p.list_price,
                    'currency': p.currency_id.name,
                    'image_url': f"/web/image/product.template/{p.id}/image_1024",
                    'url': f"/shop/product/{slug(p)}",
                }
                for p in product_records
            ]

            response = {
                'success': True,
                'category_id': parent_category.id,
                'category': {
                    'id': parent_category.id,
                    'name': parent_category.name,
                    'url': f"/shop/category/{slug(parent_category)}",
                },
                'subcategories': subcategories_data,
                'products': products_data,
                'pagination': {
                    # 'limit": limit,
                    'offset': offset,
                    'total': total_products,
                    'has_more': (offset + limit) < total_products,
                }
            }

            return http.Response(
                json.dumps(response),
                status=200,
                content_type='application/json'
            )

        except Exception as e:
            return http.Response(
                json.dumps({'success': False, 'error': str(e)}),
                status=500,
                content_type='application/json'
            )
