from odoo.addons.payment import setup_provider, reset_payment_provider
import logging
from . import models
from . import controllers

_logger = logging.getLogger(__name__)

def post_init_hook(env):
    setup_provider(env, 'fiserv')
    env['sale.order'].ensure_fiserv_fields_exist()

def uninstall_hook(env):
    reset_payment_provider(env, 'fiserv')
