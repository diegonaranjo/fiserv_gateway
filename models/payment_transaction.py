from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from decimal import Decimal, getcontext
from werkzeug import urls
from odoo.http import request 
from datetime import datetime
from .. import const
import hashlib
import logging
import json
import pprint
import hmac

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    provider_code = fields.Selection(related='provider_id.code')
    
    # Fiserv Specific Fields
    fiserv_txn_id = fields.Char(
        'Fiserv Transaction ID', 
        readonly=True)
        
    fiserv_approval_code = fields.Char(
        'Approval Code', 
        readonly=True)
        
    fiserv_card_brand = fields.Selection(
        selection=lambda self: [(code, data['name']) 
                              for code, data in const.SUPPORTED_CARD_BRANDS.items()],
        string='Card Brand',
        readonly=True
    )
    
    fiserv_card_number = fields.Char(
        'Card Number',
        size=4,
        readonly=True,
        help='Last 4 digits of the card'
    )
    
    fiserv_installments = fields.Integer(
        'Installments', 
        readonly=True)
    
    fiserv_total_with_interest = fields.Monetary(
        'Total with Interest', 
        readonly=True)
    
    fiserv_interest_amount = fields.Monetary(
        string='Interest Amount',
        readonly=True,
        store=True,
        help='Interest amount calculated for installment payments'
    )

    fiserv_interest_rate = fields.Float(
        string='Tasa de interes',
        digits=(16, 4),
        readonly=True,
        copy=False,
        help='Tasa de interés aplicada a la transacción',
        default=0.0
    )
    
    fiserv_interest_rate_display = fields.Float(
        string='Interest Rate %',
        compute='_compute_interest_rate_display',
        store=False
    )

    fiserv_card_holder = fields.Char(
        string='Card Holder Name',
        readonly=True,
        copy=False
    )
    
    fiserv_error_message = fields.Text(
        string='Error Message',
        readonly=True,
        help='Mensaje de error detallado de Fiserv'
    )
    
    fiserv_response_code = fields.Char(
        string='Response Code',
        readonly=True,
        help='Response code received from Fiserv gateway'
    )
    
    def _get_specific_rendering_values(self, processing_values):
        """
        Generates specific values needed for Fiserv redirection.
        - Calculates amounts with interest if applicable
        - Generates security hash
        - Prepares payload with customer and order data
        - Validates and logs data before sending
        - Handles decimal precision in calculations
        """
        res = super()._get_specific_rendering_values(processing_values)
        
        if self.provider_code != 'fiserv':
            return res
        try:
            # Get base URL of request
            base_url = request.httprequest.url_root
            if base_url.startswith('http://'):
                base_url = base_url.replace('http://', 'https://')
             
            total_with_interest = processing_values.get('total_with_interest')
            amount_original = self._parse_fiserv_amount(self.amount)
            
            if total_with_interest:
                amount_with_interest = self._parse_fiserv_amount(total_with_interest)
                
                # Calculate interest amount
                interest_amount = float(amount_with_interest - amount_original)
                
                # Update transaction values
                self.write({
                    'amount': float(amount_with_interest),
                    'fiserv_total_with_interest': float(amount_with_interest),
                    'fiserv_interest_amount': interest_amount
                })
            
                # Important: Update processing_values ​​directly
                processing_values.update({
                    'amount': float(amount_with_interest),
                    'fiserv_total_with_interest': float(amount_with_interest),
                    'fiserv_interest_amount': interest_amount
                })
                
                # Force update
                self.env.cr.commit()
                self.invalidate_recordset(['amount', 'fiserv_total_with_interest'])
                
                charge_total = '{:.0f}'.format(float(amount_with_interest))
            else:
                amount_with_interest = amount_original
                charge_total = '{:.0f}'.format(float(amount_original))
                self.fiserv_total_with_interest = float(amount_original)
                processing_values['amount'] = float(amount_original)
            
            # Get the partner and shipping address
            partner = self.partner_id
            sale_order = self.sale_order_ids and self.sale_order_ids[0] or False
            shipping_partner = sale_order and sale_order.partner_shipping_id or partner
            
            current_datetime = datetime.now().strftime('%Y:%m:%d-%H:%M:%S')
            store_name = self.provider_id.fiserv_store_name
            shared_secret = self.provider_id.fiserv_shared_secret
            currency = '032'

            # Capture interest_rate from processing_values
            interest_rate = float(processing_values.get('interest_rate', 0.0))
            self.fiserv_interest_rate = interest_rate
            
            #U pdate the value only if it is different from 0
            if interest_rate > 0:
                self.write({'fiserv_interest_rate': interest_rate})            
                        
            # Generate hash
            hash_value = self._generate_fiserv_hash(
                store_name=store_name,
                datetime_str=current_datetime,
                charge_total=charge_total,
                currency = currency,
                shared_secret=shared_secret
            )
            
            phone = (self.partner_id.phone or '').replace('+54', '0').replace(' ', '')
            street = (self.partner_id.street or '').replace('.', '')
            
            # Simplify payload to match Fiserv
            payload = {
                'timezone': 'America/Buenos_Aires',
                'txndatetime': current_datetime,
                'hash': hash_value,
                'currency': '032',
                'mode': 'payonly',
                'storename': store_name,
                'paymentMethod': self.fiserv_card_brand,
                'numberOfInstallments': str(self.fiserv_installments),
                'installments_interest': 'true',
                'chargetotal': charge_total,
                'language': 'es_AR',

                # Response URLs
                'responseSuccessURL': urls.url_join(base_url, '/payment/fiserv/success'),
                'responseFailURL': urls.url_join(base_url, '/payment/fiserv/fail'),
                'transactionNotificationURL': urls.url_join(base_url, '/payment/fiserv/notify'),
                
                # Transaction setup
                'txntype': 'sale',
                'checkoutoption': 'combinedpage',
                'dynamicMerchantName': 'Company',
                'authenticateTransaction': 'true',
                'oid': sale_order.name if sale_order else str(self.reference),
                'dccSkipOffer': 'false',
                'threeDSRequestorChallengeIndicator': '1',
                'mobileMode': 'false',
                
                # Billing information
                'bname': partner.name,
                'bcompany': partner.commercial_company_name or '',
                'baddr1': partner.street or '',
                # 'baddr2': partner.street2 or '',
                'bcity': partner.city or '',
                'bstate': partner.state_id.code or '',
                'bcountry': partner.country_id.code or '',
                'bzip': partner.zip or '',
                'phone': partner.phone or partner.mobile or '',
                'email': partner.email or '',
                
                # Shipping information
                'sname': shipping_partner.name,
                'saddr1': shipping_partner.street or '',
                #'saddr2': shipping_partner.street2 or '',
                'scity': shipping_partner.city or '',
                'sstate': shipping_partner.state_id.code or '',
                'scountry': shipping_partner.country_id.code or '',
                'szip': shipping_partner.zip or ''

            }

            # Validate and register
            self._validate_redirect_data(payload, self.provider_id._get_fiserv_redir_url())
            
            # Add saddr2 only if it exists
            if shipping_partner.street2:
                payload['saddr2'] = shipping_partner.street2.replace('.', '')
                
            # Save the logs before sending them.
            self.env['fiserv.transaction.log'].save_transaction_log(
                payload,
                filename_prefix='fiserv_rendering_values'
            )
            
            return {
                'amount': float(amount_with_interest),
                'api_url': self.provider_id._get_fiserv_redir_url(),
                'payment_params': payload
            }
                 
        except Exception as e:
            _logger.exception("Error preparing Fiserv redirect values")
            raise ValidationError(str(e))
        
    def _get_specific_processing_values(self, processing_values):
        """
        Prepares specific processing values for redirect flow.
        - Validates and obtains transaction reference
        - Checks existing transactions to avoid duplicates
        - Records events in transaction log
        - Returns API URL and expected post-processing URL
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        amount = processing_values.get('amount', self.amount)        
        res = super()._get_specific_processing_values(processing_values)

        reference = processing_values.get('reference')
        if not reference:
            logger.log_error({
                'error_type': 'missing_reference',
                'processing_values': processing_values
            })
            return res

        # Check for existing transaction
        existing_tx = self.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'fiserv'),
            ('state', 'in', ['draft', 'pending']),
            ('id', '!=', self.id)
        ], limit=1)

        if existing_tx:
            # Log existing transaction
            logger.save_transaction_log({
                'transaction_reference': reference,
                'existing_transaction': {
                    'id': existing_tx.id,
                    'state': existing_tx.state,
                    'amount': float(existing_tx.amount)
                },
                'action': 'using_existing_transaction'
            })
            
            return {
                'api_url': self.provider_id._get_fiserv_redir_url(),
                'amount': amount,
                'expected_url': '/shop/payment/validate'
            }

        # Log new transaction processing
        logger.save_transaction_log({
            'transaction_reference': reference,
            'amount': float(amount),
            'action': 'creating_new_transaction',
            'api_url': self.provider_id._get_fiserv_redir_url()
        })

        return {
            'api_url': self.provider_id._get_fiserv_redir_url(),
            'expected_url': '/shop/payment/validate'
        }

    def _generate_fiserv_hash(self, store_name, datetime_str, charge_total, currency, shared_secret):
        """
        Generates security hash required by Fiserv.
        - Concatenates required fields in specific order
        - Converts to ASCII hexadecimal
        - Applies SHA1 hash
        - Logs process (omitting sensitive data)
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            # Log hash generation attempt
            logger.log_debug({
                'method': '_generate_fiserv_hash',
                'store_name': store_name,
                'datetime': datetime_str,
                'charge_total': charge_total,
                'currency': currency
                # Note: shared_secret is intentionally omitted for security
            })
            
            string_to_hash = f"{store_name}{datetime_str}{charge_total}{currency}{shared_secret}"
            ascii_hex = string_to_hash.encode().hex()
            hash_value = hashlib.sha1(ascii_hex.encode()).hexdigest()
            
            # Log successful hash generation
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'hash_generated': True,
                'hash_components': {
                    'store_name': store_name,
                    'datetime': datetime_str,
                    'charge_total': charge_total,
                    'currency': currency
                }
            })
            
            return hash_value
                
        except Exception as e:
            # Log error details
            logger.log_error({
                'transaction_reference': self.reference,
                'error_type': 'hash_generation_error',
                'error_message': str(e),
                'hash_components': {
                    'store_name': store_name,
                    'datetime': datetime_str,
                    'charge_total': charge_total,
                    'currency': currency
                }
            })
            raise ValidationError(_("Error generating security hash")) from e
            

    def _fiserv_format_number(self, number):
        """
        Formats numbers to Fiserv required format.
        - Converts strings to float if needed
        - Removes decimals
        - Returns formatted number as string
        """
        if isinstance(number, str):
            try:
                number = float(number)
            except ValueError:
                return number
                
        # Forzar formato sin decimales
        formatted = '{:.0f}'.format(float(number))
        return formatted
    
    def _validate_redirect_data(self, payload, api_url):
        """
        Validates data before redirection.
        - Verifies API URL presence
        - Validates required fields
        - Verifies security hash
        - Raises exceptions if critical data is missing
        """
        if not api_url:
            raise ValidationError(_("API URL not configured"))

        required_fields = [
            'storename', 'txndatetime', 'chargetotal', 
            'currency', 'hash', 'oid'
        ]
        
        missing_fields = [f for f in required_fields if f not in payload]
        if missing_fields:
            raise ValidationError(_("Missing required fields: %s") % ', '.join(missing_fields))

        if not payload.get('hash'):
            raise ValidationError(_("Security hash not generated"))

    def _get_fiserv_redir_url(self):
        """
        Gets redirect URL based on configured environment.
        - Returns test or production URL according to configuration
        """
        self.ensure_one()
        return const.REDIR_URLS[self.provider_id.fiserv_environment]
                
    def _validate_required_fields(self, payment_data):
        """
        Validates presence of required fields in payment data.
        - Checks fields defined in REQUIRED_PAYMENT_PARAMS
        - Raises exception if fields are missing
        """
        missing_fields = [field for field in const.REQUIRED_PAYMENT_PARAMS 
                         if field not in payment_data]
        if missing_fields:
            raise ValidationError(_(
                "Missing required fields for Fiserv payment: %s"
            ) % ', '.join(missing_fields))
            
    def _validate_transaction_currency(self):
        """
        Validates that transaction currency is supported.
        - Checks against supported currencies list
        - Raises exception if currency is not valid
        """
        if self.currency_id.name not in const.SUPPORTED_CURRENCIES:
            raise ValidationError(_("Currency not supported by Fiserv"))    

    def _is_approval_code(self, code):
        """
        Verifies if a code is an approval code.
        - Returns True if code starts with 'Y:'
        """
        return code and code.startswith('Y:')

    def _get_fiserv_error_message(self, error_code):
        """
        Gets error message based on response code.
        - Maps error codes to descriptive messages
        - Handles approval codes
        - Provides generic message if code is not mapped
        """
        if not error_code:
            return _("Error en el procesamiento del pago")
            
        # Si el código comienza con Y, es una aprobación
        if self._is_approval_code(error_code):
            return _("Pago realizado exitosamente")
            
        if ':' in error_code:
            error_code = ':'.join(error_code.split(':')[:2])
        
        error_message = const.ERROR_MESSAGE_MAPPING.get(error_code)
        if not error_message:
            error_message = _("Error en la transacción: %s") % error_code
            
        return error_message

    def _handle_notification_data(self, provider_code, notification_data):
        """
        Processes Fiserv notification data.
        - Verifies notification signature
        - Validates transaction state
        - Updates transaction data
        - Processes payment status
        - Logs entire process
        """
        if provider_code != 'fiserv':
            return super()._handle_notification_data(provider_code, notification_data)
        
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            # Log incoming notification
            logger.log_notification({
                'transaction_reference': notification_data.get('oid'),
                'raw_notification': notification_data,
                'stage': 'received'
            })

            if not self._verify_fiserv_signature(notification_data):
                logger.log_error({
                    'transaction_reference': notification_data.get('oid'), 
                    'error_type': 'invalid_signature',
                    'notification_data': notification_data
                })
                raise ValidationError(_("Invalid notification signature"))

            self._validate_transaction_state()
            self._update_transaction_data(notification_data)
            self._process_fiserv_status(notification_data)
            
            # Log successful processing
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'status': 'success',
                'notification_processed': True,
                'transaction_state': self.state
            })
                
        except Exception as e:
            # Log error details
            logger.log_error({
                'transaction_reference': notification_data.get('oid'),
                'error_type': 'notification_processing_error',
                'error_message': str(e),
                'notification_data': notification_data
            })
            raise ValidationError(_("Error processing payment notification: %s") % str(e))

    def _handle_feedback_data(self, provider_code, data):
        """Handle the feedback data received from Fiserv on success return URL.
        
        This method processes the feedback data with less strict validation than
        notification handling, focusing on updating transaction state and data.
        
        Args:
            provider_code (str): The payment provider code ('fiserv')
            data (dict): The feedback data from the payment gateway
            
        Returns:
            bool: True if successful, raises error otherwise
        """
        self.ensure_one()
        
        if provider_code != 'fiserv':
            return super()._handle_feedback_data(provider_code, data)
            
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            # Log feedback receipt
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'feedback_data': data,
                'stage': 'feedback_received'
            })
            
            # Basic validation
            if not data.get('approval_code'):
                raise ValidationError(_("Missing approval code in feedback data"))
                
            # Extract status from approval code
            status_code = data['approval_code'].split(':')[0] if ':' in data['approval_code'] else data['approval_code']
            
            # Update transaction data
            feedback_data = {
                'fiserv_approval_code': data.get('approval_code'),
                'fiserv_card_number': data.get('cardnumber', '').replace('X', '*'),
                'fiserv_response_code': status_code,
                'fiserv_error_message': data.get('fail_reason') or data.get('status_message')
            }
            
            self.write(feedback_data)
            
            # Map status to Odoo transaction state
            if status_code == 'Y':
                self._set_done()
                # If payment successful, confirm the order
                if hasattr(self, 'sale_order_ids') and self.sale_order_ids:
                    self.sale_order_ids._confirm_fiserv_payment()
            elif status_code == 'N':
                self._set_canceled("Payment declined by Fiserv")
            else:
                self._set_error("Invalid payment status received")
                
            # Log successful processing
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'status': 'success',
                'feedback_processed': True,
                'transaction_state': self.state
            })
            
            return True
            
        except Exception as e:
            logger.log_error({
                'transaction_reference': self.reference,
                'error_type': 'feedback_processing_error',
                'error_message': str(e),
                'feedback_data': data
            })
            raise ValidationError(_("Error processing payment feedback: %s") % str(e))
    
    def _update_transaction_data(self, notification_data):
        """
        Updates transaction with notification data.
        - Processes approval code
        - Extracts card information
        - Updates installment and interest details
        - Logs changes
        """
        self.ensure_one()
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            approval_code = notification_data.get('approval_code', '')
            card_info = approval_code.split(':') if approval_code else []
            
            card_brand = notification_data.get('paymentMethod', '')
            if card_brand and card_brand not in const.SUPPORTED_CARD_BRANDS:
                logger.log_error({
                    'transaction_reference': self.reference,
                    'error_type': 'unsupported_card',
                    'card_brand': card_brand
                })
                card_brand = False

            # Process card number
            card_number = None
            raw_card_number = notification_data.get('cardnumber', '')
            if '...' in raw_card_number:
                card_parts = raw_card_number.split('...')
                if len(card_parts) > 1:
                    last_part = ''.join(filter(str.isdigit, card_parts[-1]))
                    if len(last_part) >= 4:
                        card_number = last_part[-4:]

            # Process amount and installments
            charge_total = notification_data.get('chargetotal', '0')
            charge_total = float(charge_total.replace(',', '.'))
            installments = int(notification_data.get('number_of_installments', '1'))

            values = {
                'fiserv_txn_id': notification_data.get('txnid') or card_info[2] if len(card_info) > 2 else None,
                'fiserv_approval_code': approval_code,
                'fiserv_card_brand': card_brand,
                'fiserv_card_holder': notification_data.get('bname', self.fiserv_card_holder),
                'provider_reference': notification_data.get('txnid'),
                'fiserv_card_number': card_number,
                'amount': charge_total,
                'fiserv_installments': installments,
                'fiserv_total_with_interest': charge_total,
                'fiserv_interest_amount': charge_total - self.amount
            }

            # Log transaction update
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'update_values': values,
                'notification_data': notification_data
            })

            # Update transaction fields
            self.write(values)
            
            # Force update cache
            self.invalidate_recordset([
                'amount', 
                'fiserv_total_with_interest',
                'fiserv_installments'
            ])
            
        except Exception as e:
            logger.log_error({
                'transaction_reference': self.reference,
                'error_type': 'update_transaction_error',
                'error_message': str(e),
                'notification_data': notification_data
            })
            raise

    def _process_fiserv_status(self, notification_data):
        """
        Processes transaction status from notification.
        - Determines final status (approved/pending/error)
        - Records payment attempts
        - Updates transaction state
        - Handles errors and logging
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        status = notification_data.get('status', 'REJECTED')
        approval_code = notification_data.get('approval_code', '')

        try:
            error_code = ':'.join(approval_code.split(':')[:2]) if ':' in approval_code else None
            
            # Log initial status processing
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'status': status,
                'approval_code': approval_code,
                'error_code': error_code
            })

            self._log_payment_attempt(status, error_code)

            if status == 'APROBADO':
                if self.state == 'done':
                    logger.log_debug({
                        'transaction_reference': self.reference,
                        'message': 'Transaction already processed',
                        'state': self.state
                    })
                    return
                    
                logger.save_transaction_log({
                    'transaction_reference': self.reference,
                    'status': 'approved',
                    'approval_code': approval_code
                })
                self._process_approved_payment(notification_data)
                
            elif status in const.PAYMENT_STATUS_MAPPING['pending']:
                logger.save_transaction_log({
                    'transaction_reference': self.reference,
                    'status': 'pending'
                })
                self._set_pending()
                
            else:
                error_msg = self._get_fiserv_error_message(error_code if error_code else status)
                logger.log_error({
                    'transaction_reference': self.reference,
                    'error_type': 'payment_rejected',
                    'error_message': error_msg,
                    'status': status,
                    'approval_code': approval_code
                })
                self.write({
                    'state': 'error',
                    'fiserv_error_message': error_msg,
                    'fiserv_approval_code': approval_code
                })
                
        except Exception as e:
            logger.log_error({
                'transaction_reference': self.reference,
                'error_type': 'status_processing_error',
                'error_message': str(e),
                'notification_data': notification_data
            })
            self._set_error(str(e))

    def _log_payment_attempt(self, status, error_code=None):
        """
        Records payment attempts in order chatter and logs.
        - Creates descriptive attempt message
        - Includes status and error details
        - Records in order history
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        
        message = f"""
        Intento de pago con Fiserv:
        Estado: {status} |
        Referencia: {self.reference} |
        Monto: {self.currency_id.symbol}{self.amount} |
        {'Cuotas: ' + str(self.fiserv_installments) if self.fiserv_installments > 1 else ''} |
        {'Respuesta Fiserv: ' + self._get_fiserv_error_message(error_code) if error_code else ''}
        """

        logger.save_transaction_log({
            'transaction_reference': self.reference,
            'payment_attempt': {
                'status': status,
                'amount': float(self.amount),
                'currency': self.currency_id.name,
                'installments': self.fiserv_installments,
                'error_code': error_code
            }
        })

        for order in self.sale_order_ids:
            order.message_post(body=message)

    def _process_approved_payment(self, notification_data):
        """
        Processes approved payments and confirms orders.
        Updates transaction and order amounts with interest calculations.
        """
        self.ensure_one()
        logger = self.env['fiserv.transaction.log'].sudo()
        
        try:
            logger.save_transaction_log({
                'transaction_reference': self.reference,
                'status': 'processing_approved_payment',
                'approval_code': notification_data.get('approval_code')
            })

            # Get and parse charge total from notification
            charge_total_str = notification_data.get('chargetotal', '0')
            charge_total = self._parse_fiserv_amount(charge_total_str)
            
            # Get installments
            installments = int(notification_data.get('number_of_installments', '1'))

            # Update transaction values
            values = {
                'state': 'done',
                'fiserv_approval_code': notification_data.get('approval_code'),
                'amount': float(charge_total),
                'fiserv_total_with_interest': float(charge_total),
                'fiserv_installments': installments
            }

            self.write(values)
            self.invalidate_recordset(['amount', 'fiserv_total_with_interest', 'fiserv_installments'])
            
            # Process related orders
            for order in self.sale_order_ids.filtered(lambda o: o.state in ['draft', 'sent']):
                # Force update order amounts with interest
                order.with_context(
                    fiserv_adjusting_interest=True,
                    fiserv_transaction_id=self.id,
                    fiserv_final_amount=float(charge_total)
                )._update_amounts_with_interest()
                
                logger.save_transaction_log({
                    'transaction_reference': self.reference,
                    'order_id': order.id,
                    'status': 'confirming_order',
                    'amount_with_interest': float(charge_total)
                })
                
                order.with_context(bypass_follower_check=True).action_confirm()
                
        except Exception as e:
            logger.log_error({
                'transaction_reference': self.reference,
                'error_type': 'approved_payment_processing_error',
                'error_message': str(e),
                'notification_data': notification_data
            })
            raise
    
    def _parse_fiserv_amount(self, amount):
        """
        Parses amounts to Fiserv required format.
        - Handles different input formats
        - Converts to Decimal for precision
        - Handles decimal and thousand separators
        """
        if isinstance(amount, (int, float)):
            return Decimal(str(amount))
            
        if isinstance(amount, str):
            # Handle Argentine format (comma as decimal separator)
            # First replace thousands separator if present
            amount = amount.replace('.', '')
            # Then replace comma with period for decimal
            amount = amount.replace(',', '.')
            # Remove any whitespace
            amount = amount.strip()
            
            try:
                return Decimal(amount)
            except InvalidOperation as e:
                _logger.error("Error converting amount %s: %s", amount, str(e))
                raise ValidationError(_(
                    "Invalid amount format: %s. Please use correct number format."
                ) % amount)
                
        return Decimal('0')
        
    def _verify_fiserv_signature(self, notification_data):
        """
        Verifies Fiserv notification signatures.
        - Collects signature components
        - Calculates expected hash
        - Compares with received hash
        - Logs verification
        """
        logger = self.env['fiserv.transaction.log'].sudo()

        if not notification_data.get('oid'):
            logger.log_error({
                'error_type': 'missing_order_id',
                'notification_data': notification_data
            })
            return False

        try:
            # Collect signature components
            components = {
                'chargetotal': notification_data.get('chargetotal', ''),
                'currency': notification_data.get('currency', ''),
                'txndatetime': notification_data.get('txndatetime', ''),
                'approval_code': notification_data.get('approval_code', ''),
                'storename': self.provider_id.fiserv_store_name
            }

            logger.log_debug({
                'transaction_reference': notification_data.get('oid'),
                'verification_type': 'signature',
                'components': {k:v for k,v in components.items() if k != 'sharedsecret'}
            })

            if 'notification_hash' in notification_data:
                concat_string = components['chargetotal'] + self.provider_id.fiserv_shared_secret + \
                                components['currency'] + components['txndatetime'] + \
                                components['storename'] + components['approval_code']
                received_hash = notification_data['notification_hash']
            else:
                concat_string = self.provider_id.fiserv_shared_secret + components['approval_code'] + \
                                components['chargetotal'] + components['currency'] + \
                                components['txndatetime'] + components['storename']
                received_hash = notification_data.get('response_hash', '')

            bin_hex = concat_string.encode('utf-8').hex()
            calculated_hash = hashlib.sha1(bin_hex.encode('utf-8')).hexdigest()
            
            matches = calculated_hash == received_hash
            
            logger.save_transaction_log({
                'transaction_reference': notification_data.get('oid'),
                'signature_verification': 'success' if matches else 'failed'
            })
            
            return matches
            
        except Exception as e:
            logger.log_error({
                'transaction_reference': notification_data.get('oid'),
                'error_type': 'signature_verification_error',
                'error_message': str(e)
            })
            return False
                    
    def _validate_transaction_state(self):
        """
        Validates transaction is in correct state.
        - Checks against valid states
        - Raises exception if state is not valid
        """
        valid_states = ['draft', 'pending', 'error']
        if self.state not in valid_states:
            raise ValidationError(_(
                "Transaction %s is in invalid state %s for processing. Expected one of: %s",
                self.reference, self.state, ', '.join(valid_states)
            ))

    def get_card_brand_display(self):
        """
        Gets descriptive name for card brand.
        - Maps card codes to readable names
        """
        self.ensure_one()
        if not self.fiserv_card_brand:
            return ''
        return const.SUPPORTED_CARD_BRANDS.get(self.fiserv_card_brand, {}).get('name', self.fiserv_card_brand)                
   
    @api.model
    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """
        Locates existing transaction based on notification data.
        - Searches by exact reference
        - Searches by sale order if reference not found
        - Validates and logs search
        """
        logger = self.env['fiserv.transaction.log'].sudo()
        
        reference = notification_data.get('oid')
        if not reference:
            logger.log_error({
                'error_type': 'missing_reference',
                'notification_data': notification_data
            })
            raise ValidationError(_("No transaction reference found in notification data"))

        logger.log_debug({
            'method': '_get_tx_from_notification_data',
            'reference': reference,
            'provider_code': provider_code
        })

        # Search transaction by exact reference
        tx = self.sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', provider_code)
        ], limit=1, order='create_date DESC')

        if not tx:
            logger.log_error({
                'error_type': 'transaction_not_found',
                'reference': reference,
                'provider_code': provider_code
            })
            raise ValidationError(_("No transaction found matching reference %s") % reference)

        logger.save_transaction_log({
            'transaction_reference': reference,
            'transaction_found': {
                'id': tx.id,
                'state': tx.state,
                'create_date': tx.create_date
            }
        })

        return tx
        
    @api.depends('amount', 'fiserv_total_with_interest')
    def _compute_interest_amount(self):
        """
        Computes interest amount for the transaction.
        - Calculates difference between total with interest and original amount
        - Updates interest amount field
        """
        for record in self:
            if record.fiserv_total_with_interest and record.amount:
                record.fiserv_interest_amount = record.fiserv_total_with_interest - record.amount
            else:
                record.fiserv_interest_amount = 0.0
                
    @api.depends('fiserv_interest_rate')
    def _compute_interest_rate_display(self):
        """
        Computes display value for interest rate.
        - Converts decimal rate to percentage for display
        - Updates display field
        """
        for record in self:
            record.fiserv_interest_rate_display = record.fiserv_interest_rate * 100

