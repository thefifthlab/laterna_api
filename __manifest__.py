# -*- coding: utf-8 -*-
{
    'name': "Laterna Books",

    'summary': "",

    'description': """
    """,

    'author': "The Fifthlab",
    'website': "https://thefifthlab.com/",
    'category': 'Customization',
    'version': '18.0',

    # any module necessary for this one to work correctly
    'depends': ['base', 'web', 'website', 'sale', 'website_sale', 'stock'],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/nigeria_states.xml',
        'views/mail_template_data.xml',
        #'views/product_public_category_views.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        #'demo/demo.xml',
    ],
}

