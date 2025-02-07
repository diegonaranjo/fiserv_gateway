from odoo import _, api, fields, models, modules
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import file_path
from werkzeug import urls
from .. import const
import base64
import hashlib
import logging
import requests
import os

_logger = logging.getLogger(__name__)

class FiservCardInstallment(models.Model):
    _name = 'fiserv.card.installment'
    _description = 'Cuotas para Configuración de Tarjetas Fiserv'
    _order = 'installments'

    card_config_id = fields.Many2one('fiserv.card.config', string="Tarjeta", required=True, ondelete="cascade")
    installments = fields.Integer(string="Número de Cuotas", required=True)
    interest_rate = fields.Float(string="Tasa de interés", digits=(16, 2), default=0.0)
    installment_to_send = fields.Char(string="Cuota a enviar", required=True)
    active = fields.Boolean(string="Activo", default=True)

    _sql_constraints = [
        ('unique_installment_per_card', 'unique(card_config_id, installments)',
         'Ya existe esta cantidad de cuotas para esta tarjeta.')
    ]
    
    def name_get(self):
        result = []
        for record in self:
            name = f"{record.installments} cuota(s) - {record.interest_rate}%"
            result.append((record.id, name))
        return result

class FiservCardConfig(models.Model):
    """
    This model manages the configuration of credit/debit card brands for the Fiserv payment gateway.
    
    It stores essential card brand information and their payment configurations, including:
    - Basic card brand details (name, code)
    - Card type capabilities (credit/debit support)
    - Installment payment settings
    - Interest rate configurations
    
    The model automatically initializes supported card brands from const.SUPPORTED_CARD_BRANDS
    on module installation.
    
    Key fields:
        - code: Unique identifier for the card brand
        - name: Display name of the card brand
        - credit: Whether credit payments are supported
        - debit: Whether debit payments are supported
        - active: Whether this card brand is currently enabled
        - installments: Number of available installments
        - installment_to_send: Value to send to Fiserv gateway for installments
        - interest_rate: Applied interest rate for installment payments
        
    This configuration is used both in website checkout and POS environments.
    """
    _name = 'fiserv.card.config'
    _description = 'Configuración de Tarjetas Fiserv'
    _order = 'sequence, id'

    sequence = fields.Integer(string='Sequence', default=10)
    code = fields.Char(required=True)
    name = fields.Char(required=True)
    credit = fields.Boolean(default=True)
    debit = fields.Boolean(default=True)
    active = fields.Boolean(default=True)
    installments = fields.One2many('fiserv.card.installment', 'card_config_id', string="Cuotas Disponibles")
    installment_to_send = fields.Char(string='Cuotas a enviar')
    interest_rate = fields.Float(
        string='Tasa de interés',
        digits=(16, 4),
        default=0.0
    )
    note = fields.Text(string='Notas')
    provider_ids = fields.Many2many(
        'payment.provider', 
        'payment_provider_fiserv_cards_rel',
        'card_id',
        'provider_id',
        string='Proveedores'
    )
    
    def name_get(self):
        result = []
        for record in self:
            name = f"{record.installments} cuota(s) - {record.interest_rate}%"
            result.append((record.id, name))
        return result

    @api.model
    def init(self):
        """Initialize card configurations from const data"""
        for code, card_data in const.SUPPORTED_CARD_BRANDS.items():
            existing = self.search([('code', '=', code)])
            if not existing:
                card_config = self.create({
                    'code': code,
                    'name': card_data['name'],
                    'credit': card_data.get('credit', True),
                    'debit': card_data.get('debit', True),
                })

                # Create installment by default
                default_installments = [
                    (1, 0.0),  # (Installments, interest_rate)
                    (3, 10.0),
                    (6, 18.0),
                    (9, 32.0),
                    (12, 44.0),
                ]
                
                for num, rate in default_installments:
                    self.env['fiserv.card.installment'].create({
                        'card_config_id': card_config.id,
                        'installments': num,
                        'interest_rate': rate,
                        'installment_to_send': str(num),
                    })

    def name_get(self):
        return [(record.id, f"{record.name} ({'Crédito' if record.credit else 'Débito'})") 
                for record in self]

    _sql_constraints = [
        ('unique_code', 'unique(code)', 'El código de tarjeta debe ser único')
    ]

    def action_open_card_config(self):
        self.ensure_one()
        return {
            'name': f'Configuración de {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'fiserv.card.config',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('fiserv_gateway.view_fiserv_card_config_form').id,
            'target': 'new',
            'context': {'default_code': self.code}
        }
    
class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('fiserv', "Fiserv Argentina")],
        ondelete={'fiserv': 'set default'}
    )
    
    fiserv_store_name = fields.Char(
        string="Store Name",
        help="Store ID provided by Fiserv",
        required_if_provider='fiserv'
    )
    
    fiserv_shared_secret = fields.Char(
        string="Shared Secret",
        help="Shared secret key provided by Fiserv",
        required_if_provider='fiserv',
        groups='base.group_system'
    )
        
    description = fields.Text(
        string='Description',
        translate=True,
        help='Description visible to customers on the payment form.'
    )

    display_as = fields.Char(
        string='Display As', 
        translate=True,
        help='Title displayed on payment form'
    )
    
    fiserv_environment = fields.Selection([
        ('test', 'Test'),
        ('prod', 'Production')
    ], string='Environment', default='test')

    fiserv_enable_3ds = fields.Boolean(
        string="Enable 3D Secure",
        default=True,
        help="Enable 3D Secure authentication for supported cards"
    )
    
    fiserv_card_brands = fields.Many2many(
        'fiserv.card.config',
        'payment_provider_fiserv_cards_rel',
        'provider_id',
        'card_id',
        string='Marcas de Tarjeta',
        domain=[('active', '=', True)]
    )

    transaction_ids = fields.One2many(
        'payment.transaction', 
        'provider_id',
        string='Transactions'
    )
        
    fiserv_enable_installments = fields.Boolean(
        string="Enable Installments",
        default=True,
        help="Enable payment in installments"
    )
    
    fiserv_checkout_mode = fields.Selection(
        selection=lambda self: list(const.CHECKOUT_MODES.items()),
        default='combinedpage',
        required_if_provider='fiserv'
    )
    
    fiserv_payment_mode = fields.Selection(
        selection=lambda self: list(const.PAYMENT_MODES.items()),
        default='payonly',
        required_if_provider='fiserv'
    )

    fiserv_redir_url = fields.Char(
        string='Redirection URL',
        compute='_compute_fiserv_redir_url',
        store=True
    )
    
    fiserv_dynamic_descriptor = fields.Char(
        string="Dynamic Descriptor",
        help="Name that will appear on cardholder's statement (max 25 chars)",
        size=25
    )

    reference = fields.Char(
        string='Reference',
        required=False,
        readonly=True,
        default=lambda self: self._default_reference()
    )

    fiserv_success_url = fields.Char(
        string="Success URL",
        help="URL where customers will be redirected after successful payment"
    )
    
    fiserv_fail_url = fields.Char(
        string="Fail URL",
        help="URL where customers will be redirected after failed payment"
    )
    
    fiserv_notification_url = fields.Char(
        string="Notification URL",
        help="URL for receiving payment notifications from Fiserv"
    )
    
    payment_method_ids = fields.Many2many(
        'payment.method',
        'payment_method_provider_rel',
        'provider_id',
        'method_id',
        string='Supported Payment Methods',
        domain=[('is_primary', '=', True)],
        readonly=True,
    )
    
    fiserv_authorization = fields.Selection([
        ('hosted', 'Hosted Payment'),       
    ], string='Authorization Method', default='hosted',
        help="If 'Hosted Payment' is chosen, the customer will be redirected to a secure "
            "payment page hosted by Fiserv Gateway to enter their payment details. "
            "If 'Hidden Authorization' is chosen, you will host the initial payment "
            "page on your site.")

    support_manual_capture = fields.Boolean(
        compute='_compute_support_manual_capture'
    )

    support_tokenization = fields.Boolean(
        compute='_compute_support_tokenization'
    )
    
    support_authorize = fields.Boolean(
        compute='_compute_support_authorize'
    )

    image_128 = fields.Image(
        "Logo", 
        max_width=128, 
        max_height=128,
        help="Provider logo displayed in the payment form and portal"
    )
        
    def _get_default_payment_flow(self):
        """
        Defines the default payment flow for Fiserv as 'redirect'.
        Returns the parent class flow for non-Fiserv providers.
        """
        self.ensure_one()
        if self.code == 'fiserv':
            return 'redirect'
        return super()._get_default_payment_flow()

    def _should_build_inline_form(self, is_validation=False):
        """
        Determines if an inline form should be built.
        Always returns False for Fiserv as it uses redirect flow.
        """
        if self.code == 'fiserv':
            return False
        return super()._should_build_inline_form(is_validation=is_validation)
    
    def _get_supported_currencies(self):
        """
        Returns list of currencies supported by Fiserv.
        Filters parent class currencies against SUPPORTED_CURRENCIES constant.
        """
        currencies = super()._get_supported_currencies()
        if self.code != 'fiserv':
            return currencies
        return currencies.filtered(lambda c: c.name in const.SUPPORTED_CURRENCIES)

    def _get_supported_card_types(self):
        """
        Returns dictionary of card types supported by Fiserv.
        Maps card codes to their display names and supported features.
        """
        card_types = const.SUPPORTED_CARD_BRANDS
        _logger.info("Returning card types: %s", card_types)
        return card_types
    
    def _validate_fiserv_configuration(self):
        """
        Validates Fiserv provider configuration.
        Checks required credentials and field length constraints.
        """
        self.ensure_one()
        if not self.fiserv_store_name or not self.fiserv_shared_secret:
            raise ValidationError(_("Missing credentials"))
            
        if len(self.fiserv_store_name) > 15:
            raise ValidationError(_("Store name must not exceed 15 characters"))
            
        if self.fiserv_dynamic_descriptor and len(self.fiserv_dynamic_descriptor) > 25:
            raise ValidationError(_("Dynamic descriptor must not exceed 25 characters"))
            
        return True

    def get_installment_options(self, card_brand):
        """
        Retrieves available installment options for specified card brand.
        Returns formatted list of installment plans with rates and amounts.
        """
        self.ensure_one()
        if not self.fiserv_enable_installments:
            return []

        card_config = self.env['fiserv.card.config'].search([('code', '=', card_brand)], limit=1)
        if not card_config:
            return []

        return self._format_installment_options(card_config)

    def _format_installment_options(self, card_config):
        """
        Formats installment options into display-ready format.
        """
        options = []
        for installment in card_config.installments.filtered(lambda x: x.active):
            coefficient = 1 + (installment.interest_rate / 100)
            options.append({
                'value': str(installment.installments),
                'label': self._get_installment_label(installment.installments),
                'coefficient': coefficient,
                'installment_to_send': installment.installment_to_send,
                'interest_rate': installment.interest_rate
            })
        return sorted(options, key=lambda x: int(x['value']) if x['value'] != 'Plan Z' else 999)

    def _get_installment_label(self, installment):
        """
        Generates human-readable label for installment option.
        Handles special cases like 'Plan Z' and plural forms.
        """
        if installment == 'Plan Z':
            return 'Plan Z'
        installment_int = int(installment)
        return f"{installment} cuota{'s' if installment_int > 1 else ''}"

    def _calculate_interest_rate(self, coefficient):
        """
        Calculates interest rate percentage from coefficient.
        Converts multiplier to percentage with 2 decimal precision.
        """
        return round((float(coefficient) - 1) * 100, 2)

    def get_card_brand_display(self, code=None):
        """
        Returns descriptive name for card brand code.
        Uses either provided code or stored card brand value.
        """
        self.ensure_one()
        if not code and hasattr(self, 'fiserv_card_brand'):
            code = self.fiserv_card_brand
        mapping = self._get_fiserv_card_brand_mapping()
        return mapping.get(code, code)
                        
    def _get_fiserv_redir_url(self):
        """
        Returns appropriate Fiserv API URL based on environment.
        Validates environment configuration before returning URL.
        """
        self.ensure_one()        
        if not self.fiserv_environment:
            raise ValidationError(_("Payment environment not configured"))            
        if self.fiserv_environment not in ['test', 'prod']:
            raise ValidationError(_("Invalid payment environment"))        
        try:
            return const.REDIR_URLS[self.fiserv_environment]
        except KeyError:
            _logger.error("URL no encontrada para el ambiente: %s", self.fiserv_environment)
            raise ValidationError(_("Configuration error: URL not found for environment"))
        
            
    def _get_default_payment_method_codes(self):
        """
        Returns default payment method codes for Fiserv provider.
        Adds Fiserv-specific codes to parent class defaults.
        """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'fiserv':
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES
    
    def _get_default_payment_method(self):
        """
        Creates or retrieves the default 'Tarjetas' payment method.
        Sets up child payment methods for each supported card type.
        """
        self.ensure_one()
        if self.code != 'fiserv':
            return super()._get_default_payment_method()

        module_path = modules.get_module_path('fiserv_gateway')
        if not module_path:
            _logger.error("No se pudo encontrar el path del módulo fiserv_gateway")
            return False

        default_method = self.env['payment.method'].search([('code', '=', 'tarjetas')], limit=1)
        if not default_method:
            image_path = os.path.join(module_path, 'static', 'images', 'tarjetas.webp')
            image_base64 = None
            if os.path.exists(image_path):
                with open(image_path, 'rb') as f:
                    image_base64 = base64.b64encode(f.read())

            default_method = self.env['payment.method'].create({
                'name': 'Tarjetas',
                'code': 'tarjetas',
                'active': True,
                'support_tokenization': True,
                'is_primary': True,
                'provider_ids': [(4, self.id)],
                'image': image_base64,
            })

        card_image_mapping = {
            'visa': 'visa.png',
            'mastercard': 'mastercard.png',
            'maestro': 'maestro.png',
            'cabal': 'cabal.png',
            'naranja': 'naranja.png',
            'tuya': 'tuya.png',
        }

        for method_code in const.DEFAULT_PAYMENT_METHOD_CODES - {'tarjetas'}:
            child_method = self.env['payment.method'].search([('code', '=', method_code)], limit=1)
            formatted_name = method_code.replace('_', ' ').title()
            
            image_base64 = None
            if method_code in card_image_mapping:
                image_path = os.path.join(module_path, 'static', 'images', card_image_mapping[method_code])
                if os.path.exists(image_path):
                    with open(image_path, 'rb') as f:
                        image_base64 = base64.b64encode(f.read())

            values = {
                'name': formatted_name,
                'code': method_code,
                'primary_payment_method_id': default_method.id,
                'active': True,
                'support_tokenization': True,
                'is_primary': False,
                'image': image_base64,
            }

            if not child_method:
                self.env['payment.method'].create(values)
            else:
                child_method.write(values)

        return default_method

    def _update_existing_payment_methods(self):
        """
        Updates existing payment methods to match current configuration.
        Assigns methods as children of main 'Tarjetas' method.
        """
        tarjetas_method = self.env['payment.method'].search([('code', '=', 'tarjetas')], limit=1)
        if not tarjetas_method:
            _logger.warning("El método principal 'Tarjetas' no existe. Saltando actualización.")
            return

        existing_methods = self.env['payment.method'].search([
            ('code', 'in', list(const.DEFAULT_PAYMENT_METHOD_CODES - {'tarjetas'})),
            ('parent_id', '=', False)
        ])

        for method in existing_methods:
            _logger.info("Actualizando método de pago: %s", method.name)
            method.write({
                'parent_id': tarjetas_method.id,
                'available_provider_ids': [(4, self.id)]
            })

    def _ensure_payment_method_assignment(self):
        """
        Ensures 'Tarjetas' payment method is assigned to Fiserv provider.
        Creates association if not already present.
        """
        self.ensure_one()
        if self.code != 'fiserv':
            return

        tarjetas_method = self.env['payment.method'].search([
            ('code', '=', 'tarjetas'),
            ('is_primary', '=', True)
        ], limit=1)

        if tarjetas_method and tarjetas_method.id not in self.payment_method_ids.ids:
            self.write({
                'payment_method_ids': [(4, tarjetas_method.id)]
            })
    
    @api.depends('code')
    def _compute_support_authorize(self):
        """
        Computes whether provider supports payment authorization.
        True for Fiserv, inherited value for others.
        """
        for provider in self:
            provider.support_authorize = provider.code == 'fiserv'
        
    @api.depends('code')
    def _compute_support_manual_capture(self):
        """
        Computes whether provider supports manual capture.
        True for Fiserv, inherited value for others.
        """
        for provider in self:
            provider.support_manual_capture = provider.code == 'fiserv'

    @api.depends('code')
    def _compute_support_tokenization(self):
        """
        Computes whether provider supports payment tokenization.
        True for Fiserv, inherited value for others.
        """
        for provider in self:
            provider.support_tokenization = provider.code == 'fiserv'

    @api.depends('fiserv_environment')
    def _compute_fiserv_redir_url(self):
        """
        Computes redirection URL based on environment setting.
        Sets appropriate test or production URL from constants.
        """
        for provider in self:
            if provider.fiserv_environment == 'test':
                provider.fiserv_redir_url = const.REDIR_URLS['test']
            else:
                provider.fiserv_redir_url = const.REDIR_URLS['prod']              
                        
    @api.model
    def _default_reference(self):
        """
        Generates unique provider reference using sequence.
        Returns empty string if sequence not found.
        """
        return self.env['ir.sequence'].next_by_code('payment.provider.reference') or ''

    @api.constrains('fiserv_store_name')
    def _check_store_name(self):
        """
        Validates store name length constraint.
        Raises error if exceeds 15 characters.
        """
        for record in self:
            if record.fiserv_store_name and len(record.fiserv_store_name) > 15:
                raise ValidationError(_('Store name must not exceed 15 characters.'))
            
    @api.model_create_multi
    def create(self, vals_list):
        """
        Extends creation to set up Fiserv-specific configuration.
        Loads provider logo and sets up payment methods.
        """
        providers = super().create(vals_list)
        
        for provider in providers:
            if provider.code == 'fiserv':
                # Cargar y redimensionar la imagen
                module_path = modules.get_module_path('fiserv_gateway')
                image_path = os.path.join(module_path, 'static', 'description', 'icon.png')
                
                if os.path.exists(image_path):
                    with open(image_path, 'rb') as f:
                        image_content = f.read()
                        provider.write({
                            'image_128': base64.b64encode(image_content),
                            'support_refund': False,
                            'support_tokenization': True,
                            'available_country_ids': [(6, 0, [self.env.ref('base.ar').id])]
                        })
                
                if not provider.reference:
                    provider.reference = provider._default_reference()
                
                provider._get_default_payment_method()
                provider._ensure_payment_method_assignment()
        
        return providers

    @api.model
    def _get_fiserv_card_brand_mapping(self):
        """
        Returns mapping of card brand codes to display names.
        Used for consistent card brand display across module.
        """
        return {
            'V': 'Visa',
            'M': 'Mastercard',
            'MA': 'Maestro',
            'CABAL_ARGENTINA': 'Cabal',
            'TUYA': 'Tuya',
            'NARANJA': 'Naranja',
        }
        
        
    @api.model
    def _update_payment_methods(self):
        """
        Updates payment methods to ensure all required ones exist.
        Creates missing methods based on DEFAULT_PAYMENT_METHOD_CODES.
        """
        fiserv_provider = self.search([('code', '=', 'fiserv')], limit=1)
        if fiserv_provider:
            existing_methods = self.env['payment.method'].search([
                ('code', 'in', list(const.DEFAULT_PAYMENT_METHOD_CODES))
            ]).mapped('code')
            
            methods_to_create = const.DEFAULT_PAYMENT_METHOD_CODES - set(existing_methods)
            if methods_to_create:
                fiserv_provider._get_default_payment_method()
                
