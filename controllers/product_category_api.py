# controllers/shop_api.py
from odoo import http
from odoo.http import request
import math

class ShopApiController(http.Controller):

    @http.route('/shop/category/<int:category_id>', type='json', auth='public', methods=['GET'], csrf=False)
    def get_category_products(self, category_id, **kwargs):
        """
        New clean URL:
        GET /shop/category/10   → returns all products under category ID 10 (e.g. Components)
        Optional query params:
            ?page=1&per_page=24&sort=price_low_to_high&min_price=295&max_price=2100
        """
        try:
            # === 1. Query parameters ===
            page       = int(kwargs.get('page', 1))
            per_page   = int(kwargs.get('per_page', 24))
            sort       = kwargs.get('sort', 'featured').lower()
            min_price  = kwargs.get('min_price')
            max_price  = kwargs.get('max_price')
            min_price  = float(min_price) if min_price else None
            max_price  = float(max_price) if max_price else None

            # === 2. Fetch category ===
            category = request.env['product.public.category'].browse(category_id)
            if not category.exists():
                return {'success': False, 'error': 'Category not found'}

            parent = category.parent_id

            # === 3. Build product domain ===
            domain = [('public_categ_ids', 'child_of', category.id)]

            if min_price is not None:
                domain += [('list_price', '>=', min_price)]
            if max_price is not None:
                domain += [('list_price', '<=', max_price)]

            # === 4. Sorting ===
            order_map = {
                'featured':          'sequence desc',
                'price_low_to_high': 'list_price asc',
                'price_high_to_low': 'list_price desc',
                'newest':            'create_date desc',
            }
            order = order_map.get(sort, 'sequence desc')

            # === 5. Fetch products + count ===
            offset = (page - 1) * per_page
            products = request.env['product.template'].search(domain, order=order, limit=per_page, offset=offset)
            total    = request.env['product.template'].search_count(domain)

            # === 6. Price range for filter UI ===
            all_prices = request.env['product.template'].search_read(domain, ['list_price'])
            prices = [p['list_price'] for p in all_prices]
            price_min = min(prices) if prices else 0.0
            price_max = max(prices) if prices else 0.0

            # === 7. Serialize products ===
            product_list = []
            base_url = request.httprequest.host_url.rstrip('/')

            for p in products:
                product_list.append({
                    'id': p.id,
                    'name': p.name,
                    'slug': p.website_url.split('/')[-1] if p.website_url else p.name.lower().replace(' ', '-'),
                    'price': p.list_price,
                    'image': f"{base_url}web/image/product.template/{p.id}/image_1024" if p.image_1920 else False,
                    'thumbnail': f"{base_url}web/image/product.template/{p.id}/image_256" if p.image_1920 else False,
                    'in_stock': p.qty_available > 0 if hasattr(p, 'qty_available') else True,
                })

            # === 8. Breadcrumbs ===
            breadcrumbs = [
                {'name': 'Shop', 'url': '/shop'},
            ]
            if parent:
                breadcrumbs.append({'name': parent.name, 'url': f'/shop/category/{parent.id}'})
            breadcrumbs.append({'name': category.name, 'url': f'/shop/category/{category.id}'})

            # === 9. Final response ===
            return {
                'success': True,
                'data': {
                    'category': {
                        'id': category.id,
                        'name': category.name,
                        'parent': parent.name if parent else None,
                        'breadcrumbs': breadcrumbs,
                    },
                    'filters': {
                        'price_range': {
                            'min': round(price_min, 2),
                            'max': round(price_max, 2),
                            'current_min': min_price or round(price_min, 2),
                            'current_max': max_price or round(price_max, 2),
                        },
                        'sort_options': ['Featured', 'Price: Low to High', 'Price: High to Low', 'Newest'],
                    },
                    'products': product_list,
                    'pagination': {
                        'current_page': page,
                        'per_page': per_page,
                        'total': total,
                        'total_pages': math.ceil(total / per_page) if per_page > 0 else 0,
                    },
                }
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}